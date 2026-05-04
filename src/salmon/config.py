"""Runtime configuration for salmon.

Environment variables:

  SALMON_SYSTEM_ID       opaque id used in /redfish/v1/Systems/<id>
                         and /redfish/v1/Chassis/<id>. Default
                         "1". Reflects the convention most BMCs use
                         where the local managed system is "1".

  SALMON_SYSTEM_NAME     human-readable name. Default "system-1".

  SALMON_BMC_HOST        if set, ipmitool drives a remote BMC at
                         this host via -H. Otherwise ipmitool talks
                         to the local /dev/ipmi0 (in-band).

  SALMON_BMC_USER        BMC username for remote drives. Required
                         iff SALMON_BMC_HOST is set.

  SALMON_BMC_PASSWORD    BMC password for remote drives. Required
                         iff SALMON_BMC_HOST is set.

  SALMON_BMC_INTERFACE   ipmitool -I value. Default "lanplus" for
                         remote, "open" for local.

  SALMON_CACHE_TTL       seconds to cache slow ipmitool reads
                         (sensor list, sdr type *). Default 10.
                         Set to 0 to disable caching.
"""

from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Config:
    system_id: str
    system_name: str
    bmc_host: str | None
    bmc_user: str | None
    bmc_password: str | None
    bmc_interface: str
    cache_ttl_seconds: int

    @property
    def is_remote(self) -> bool:
        return self.bmc_host is not None


def load() -> Config:
    bmc_host = os.environ.get("SALMON_BMC_HOST") or None
    is_remote = bmc_host is not None

    if is_remote:
        bmc_user = os.environ.get("SALMON_BMC_USER")
        bmc_password = os.environ.get("SALMON_BMC_PASSWORD")
        if not bmc_user or not bmc_password:
            raise RuntimeError(
                "SALMON_BMC_HOST is set but SALMON_BMC_USER / "
                "SALMON_BMC_PASSWORD are missing -- remote BMC "
                "access needs both."
            )
    else:
        bmc_user = None
        bmc_password = None

    default_iface = "lanplus" if is_remote else "open"
    cache_ttl = int(os.environ.get("SALMON_CACHE_TTL", "10"))

    return Config(
        system_id=os.environ.get("SALMON_SYSTEM_ID", "1"),
        system_name=os.environ.get("SALMON_SYSTEM_NAME", "system-1"),
        bmc_host=bmc_host,
        bmc_user=bmc_user,
        bmc_password=bmc_password,
        bmc_interface=os.environ.get("SALMON_BMC_INTERFACE", default_iface),
        cache_ttl_seconds=cache_ttl,
    )
