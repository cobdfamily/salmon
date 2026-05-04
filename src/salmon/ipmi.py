"""Thin async wrapper around the ipmitool CLI.

salmon shells out to ipmitool for every BMC operation; there's no
binding library in Python that's both well-maintained and covers
the whole IPMI surface. ipmitool is the lingua franca.

Two read paths are cached (TTL configurable via SALMON_CACHE_TTL):
``sdr type Temperature``, ``sdr type Fan``, ``sdr type Voltage``,
and ``sensor list`` -- these can take seconds on cold BMCs and hot-
path Redfish polling would otherwise hammer them. Power state
(``chassis power status``) is fast and uncached. Actions
(``chassis power <on|off|reset|cycle|soft>``) are never cached.

Errors from ipmitool surface as IpmiError with the captured stderr
+ exit code, so route handlers can map them to Redfish error
responses with useful context.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass

from .config import Config


class IpmiError(RuntimeError):
    """ipmitool exited non-zero or otherwise failed."""

    def __init__(self, message: str, exit_code: int, stderr: str) -> None:
        super().__init__(message)
        self.exit_code = exit_code
        self.stderr = stderr


@dataclass
class _CacheEntry:
    value: str
    expires_at: float


class Ipmi:
    """One instance per service. Holds the config + a small TTL cache."""

    def __init__(self, config: Config) -> None:
        self._config = config
        self._cache: dict[tuple[str, ...], _CacheEntry] = {}

    def _connection_args(self) -> list[str]:
        """``-H -U -P -I`` triplet for remote BMCs; ``-I open`` for
        local /dev/ipmi0. The ``-I open`` form intentionally does
        not pass user/host."""
        if self._config.is_remote:
            return [
                "-I", self._config.bmc_interface,
                "-H", self._config.bmc_host or "",
                "-U", self._config.bmc_user or "",
                "-P", self._config.bmc_password or "",
            ]
        return ["-I", self._config.bmc_interface]

    async def run(self, *args: str) -> str:
        """Run ``ipmitool <connection> <args>`` and return stdout.
        Raises IpmiError on non-zero exit."""
        cmd = ["ipmitool", *self._connection_args(), *args]
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout_b, stderr_b = await proc.communicate()
        stdout = stdout_b.decode("utf-8", errors="replace")
        stderr = stderr_b.decode("utf-8", errors="replace")
        if proc.returncode != 0:
            # Don't include the password in error context.
            redacted = [a if a != self._config.bmc_password else "[redacted]" for a in cmd]
            raise IpmiError(
                f"ipmitool failed ({proc.returncode}): {' '.join(redacted)}",
                exit_code=proc.returncode or -1,
                stderr=stderr.strip(),
            )
        return stdout

    async def run_cached(self, *args: str) -> str:
        """Same as run() but cached for ``SALMON_CACHE_TTL`` seconds.
        Use only for read paths; never for actions."""
        if self._config.cache_ttl_seconds <= 0:
            return await self.run(*args)
        key = args
        now = time.monotonic()
        entry = self._cache.get(key)
        if entry is not None and entry.expires_at > now:
            return entry.value
        value = await self.run(*args)
        self._cache[key] = _CacheEntry(
            value=value,
            expires_at=now + self._config.cache_ttl_seconds,
        )
        return value

    # -- convenience wrappers --

    async def power_status(self) -> str:
        """Returns "on", "off", or raises. ipmitool's
        ``chassis power status`` prints e.g.
        ``Chassis Power is on``."""
        out = await self.run("chassis", "power", "status")
        text = out.strip().lower()
        if "is on" in text:
            return "on"
        if "is off" in text:
            return "off"
        raise IpmiError(
            f"unexpected power-status output: {text!r}",
            exit_code=0,
            stderr=text,
        )

    async def power_action(self, action: str) -> None:
        """action in {on, off, reset, cycle, soft, diag}."""
        valid = {"on", "off", "reset", "cycle", "soft", "diag"}
        if action not in valid:
            raise ValueError(f"unknown power action: {action!r}")
        await self.run("chassis", "power", action)

    async def sensors(self) -> list[dict[str, str]]:
        """Parsed output of ``ipmitool sensor list``. Each row is
        a dict with keys: name, value, units, status, lower_nr,
        lower_c, lower_nc, upper_nc, upper_c, upper_nr.

        ipmitool's pipe-delimited format:
            <name> | <value> | <units> | <status> | <lnr> | <lc>
                   | <lnc>   | <unc>  | <uc>     | <unr>
        Cells are space-padded; we strip both ends."""
        raw = await self.run_cached("sensor", "list")
        rows: list[dict[str, str]] = []
        keys = (
            "name", "value", "units", "status",
            "lower_nr", "lower_c", "lower_nc",
            "upper_nc", "upper_c", "upper_nr",
        )
        for line in raw.splitlines():
            if not line.strip():
                continue
            parts = [p.strip() for p in line.split("|")]
            if len(parts) < len(keys):
                continue
            row = dict(zip(keys, parts[: len(keys)]))
            rows.append(row)
        return rows
