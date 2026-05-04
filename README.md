# salmon

[![test](https://github.com/cobdfamily/salmon/actions/workflows/test.yml/badge.svg)](https://github.com/cobdfamily/salmon/actions/workflows/test.yml)

Redfish-flavoured HTTP facade for legacy IPMI BMCs via
`ipmitool`. v0.1.0 -- single-host scope, power +
sensors only. Designed for orchestrators (OpenStack
Ironic, Foreman, Tinkerbell) and any tooling that
prefers Redfish over IPMI on hardware that doesn't
speak it natively.

## What it does

```
GET  /                                              liveness (non-Redfish)
GET  /redfish                                       version document
GET  /redfish/v1/                                   ServiceRoot
GET  /redfish/v1/Systems                            ComputerSystemCollection
GET  /redfish/v1/Systems/{id}                       ComputerSystem
POST /redfish/v1/Systems/{id}/Actions/
                       ComputerSystem.Reset         power actions
GET  /redfish/v1/Chassis                            ChassisCollection
GET  /redfish/v1/Chassis/{id}                       Chassis
GET  /redfish/v1/Chassis/{id}/Power                 Voltages
GET  /redfish/v1/Chassis/{id}/Thermal               Temperatures + Fans
```

`{id}` is `SALMON_SYSTEM_ID` (default `1`). One BMC per
salmon instance in v0.1.0; a multi-host facade is on
the roadmap.

### ResetType actions

POST `Actions/ComputerSystem.Reset` accepts a JSON body
with a `ResetType`. salmon maps each to an `ipmitool
chassis power ...` invocation:

| ResetType        | ipmitool subcommand |
|------------------|---------------------|
| On / ForceOn     | on                  |
| ForceOff         | off                 |
| GracefulShutdown | soft                |
| GracefulRestart  | soft                |
| ForceRestart     | reset               |
| PowerCycle       | cycle               |
| Nmi              | diag                |

## Quick start (local in-band)

```sh
docker compose up -d
# ipmitool talks to /dev/ipmi0 by default; the host
# kernel needs the ipmi_devintf module loaded and the
# container needs --privileged or device passthrough
# (see DEPLOYMENT.md).

curl -s http://localhost:8000/redfish/v1/ | jq

curl -s http://localhost:8000/redfish/v1/Systems/1 \
  | jq '.PowerState'

curl -X POST -H 'Content-Type: application/json' \
  -d '{"ResetType": "GracefulShutdown"}' \
  http://localhost:8000/redfish/v1/Systems/1/Actions/ComputerSystem.Reset
```

## Quick start (remote BMC)

```sh
docker run -d --name salmon \
  -e SALMON_BMC_HOST=10.0.0.42 \
  -e SALMON_BMC_USER=admin \
  -e SALMON_BMC_PASSWORD=admin \
  -p 8000:8000 \
  kibble.apps.blindhub.ca/cobdfamily/salmon:latest
```

## Configuration

All via environment variables; see
[`src/salmon/config.py`](src/salmon/config.py):

- `SALMON_SYSTEM_ID`     -- id used in `/Systems/<id>` and
                            `/Chassis/<id>`. Default `1`.
- `SALMON_SYSTEM_NAME`   -- human-readable name. Default
                            `system-1`.
- `SALMON_BMC_HOST`      -- if set, drive a remote BMC at
                            this host. Otherwise local
                            `/dev/ipmi0`.
- `SALMON_BMC_USER` /
  `SALMON_BMC_PASSWORD`  -- required when `SALMON_BMC_HOST`
                            is set.
- `SALMON_BMC_INTERFACE` -- `ipmitool -I` value. Default
                            `lanplus` (remote) or `open`
                            (local).
- `SALMON_CACHE_TTL`     -- seconds to cache slow sensor
                            reads. Default `10`. `0`
                            disables caching.

## What's NOT in v0.1.0

- **SessionService / X-Auth-Token.** Endpoints are
  unauthenticated. Gate at the reverse proxy.
- **Bios resource** (boot order, settings).
- **EthernetInterfaces, NetworkAdapters, Storage,
  Drives, Volumes, Memory, Processors.**
- **Per-sensor endpoints under
  `/Chassis/<id>/Sensors/<n>`.** Voltages /
  Temperatures / Fans are exposed via Power and
  Thermal collections only.
- **PowerSupplies and PowerControl.** IPMI doesn't
  surface PSU composition reliably; left as `[]` until
  SDR FRU parsing lands.
- **Multi-host orchestration.** One BMC per instance.
- **Strict spec validation.** salmon emits JSON shaped
  for Ironic-class consumers; DMTF protocol-validator
  conformance is a follow-up.

## Architecture

```
HTTP request
   |
   v
FastAPI route          (src/salmon/main.py)
   |
   v
Ipmi.run / run_cached  (src/salmon/ipmi.py)
   |
   v
asyncio subprocess
   |
   v
ipmitool CLI
   |
   v
BMC (in-band /dev/ipmi0 OR remote -H -U -P)
```

Cache layer is in-process, TTL-keyed, only on
sensor-list reads. Power-state queries are fast and
uncached. Action calls are never cached.

## Files

```
src/salmon/__init__.py        package marker + version
src/salmon/main.py            FastAPI app + Redfish routes
src/salmon/config.py          env-var loader
src/salmon/ipmi.py            ipmitool subprocess + cache
tests/                        pytest suite
Dockerfile                    python:3.12-slim + ipmitool
docker-compose.yaml           local-dev / production-shape
.github/workflows/test.yml    CI: yaml + e2e jobs (+ nightly)
.github/workflows/release.yml CI: tag-driven multi-arch push
```

## License

AGPL-3.0 -- see `LICENSE`.
