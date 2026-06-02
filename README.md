# salmon

[![test](https://github.com/cobdfamily/salmon/actions/workflows/test.yml/badge.svg)](https://github.com/cobdfamily/salmon/actions/workflows/test.yml)

Redfish-flavoured HTTP facade for legacy IPMI BMCs.
v0.2.0 reshaped from a hand-written FastAPI app into a
[url2code](https://github.com/cobdfamily/url2code)
project: the Redfish surface is declared in
`config/tools.yaml` and a handful of `bin/ipmi-*`
shell shims wrap `ipmitool`.

> Deploying salmon in production? See
> **[DEPLOYMENT.md](DEPLOYMENT.md)**.

## What it does

Multi-host (v1.0.0): salmon represents N BMCs. The
BMCs it fronts -- and how to reach each one -- live in
`config/bmcs.yaml`, a YAML list of members keyed by
their Redfish `id`. That id is the `{id}` path segment
below; the Systems and Chassis collections list every
member in the file. See
[BMC inventory](#bmc-inventory-bmcsyaml) for the format.

```
GET  /                                              liveness (non-Redfish)
GET  /redfish                                       Redfish version doc
GET  /redfish/v1                                    ServiceRoot
GET  /redfish/v1/odata                              OData service doc
GET  /redfish/v1/Systems                            ComputerSystemCollection (one member per BMC)
GET  /redfish/v1/Systems/{id}                       ComputerSystem + PowerState
POST /redfish/v1/Systems/{id}/Actions/
                  ComputerSystem.Reset              power action
GET  /redfish/v1/Chassis                            ChassisCollection (one member per BMC)
GET  /redfish/v1/Chassis/{id}                       Chassis
GET  /redfish/v1/Chassis/{id}/Power                 Voltages + PowerSupplies
GET  /redfish/v1/Chassis/{id}/Thermal               Temperatures + Fans
```

`{id}` is a member id from `config/bmcs.yaml`. The
baked-in default ships one in-band member, id `"1"`,
so the examples below use `/redfish/v1/Systems/1`.

`/redfish/v1/` (with trailing slash) returns a 307
redirect to `/redfish/v1` -- url2code normalizes
trailing slashes off route declarations. All Redfish
clients that COBD operates against (Ironic, Foreman,
Tinkerbell) follow 307 redirects, so this is
behaviourally transparent. Strict clients that don't
follow redirects should hit the no-trailing-slash
form directly.

### ResetType actions

POST `Actions/ComputerSystem.Reset` accepts a JSON
body with a `ResetType` field. The shim maps each
Redfish value to an `ipmitool chassis power`
subcommand:

| ResetType        | ipmitool subcommand |
|------------------|---------------------|
| On / ForceOn     | on                  |
| ForceOff         | off                 |
| GracefulShutdown | soft                |
| GracefulRestart  | soft                |
| ForceRestart     | reset               |
| PowerCycle       | cycle               |
| Nmi              | diag                |

## What changed in 0.2.0

The HTTP surface and JSON shapes are unchanged --
this is a pure reshape of the implementation. The
table summarises what moved:

| 0.1.x                              | 0.2.0                          |
|------------------------------------|--------------------------------|
| `src/salmon/main.py` (FastAPI app) | `config/tools.yaml` + url2code |
| `src/salmon/ipmi.py` (subprocess)  | `bin/ipmi-*` POSIX sh shims    |
| Redfish JSON built in handlers     | url2code response templates    |
| Per-endpoint pydantic models       | Static JSON literals in YAML   |

Net effect: ~300 lines of Python collapsed into one
YAML file and four shell shims totalling ~150 lines.
The Redfish translation (`ipmitool chassis power
status` -> `"PowerState": "On"`) lives in the shims;
the wrapper JSON (OData ids, types, action targets)
lives in YAML response templates.

## Quick start (in-band, baked-in default)

```sh
docker compose up -d
curl -s http://localhost:8000/redfish/v1/Systems/1 | jq .
```

The image ships one in-band member (id `"1"`,
`interface: open`). In-band requires `/dev/ipmi0` on
the host (`modprobe ipmi_devintf` if missing) and the
compose file's `devices:` block pointing the container
at it.

## Quick start (remote / multiple BMCs)

Write a `bmcs.yaml` describing your fleet and mount it
read-only over the baked-in default:

```yaml
# bmcs.yaml
- id: "1"
  host: 10.0.0.11
  user: admin
  password: changeme
  interface: lanplus
- id: "2"
  host: 10.0.0.12
  user: admin
  password: changeme2
  interface: lanplus
```

```sh
# Uncomment the volumes: block in docker-compose.yaml
# (mounts ./bmcs.yaml at /app/config/bmcs.yaml:ro).
docker compose up -d
curl -s http://localhost:8000/redfish/v1/Systems | jq .   # lists 1 and 2
curl -s http://localhost:8000/redfish/v1/Systems/2 | jq .
```

## BMC inventory (`bmcs.yaml`)

`config/bmcs.yaml` is the source of truth for which
BMCs salmon fronts. It's a YAML list of members:

| Field       | Required | Meaning                                                                 |
|-------------|----------|-------------------------------------------------------------------------|
| `id`        | yes      | Redfish member id -- the `{id}` route segment. URL-safe string.         |
| `host`      | no       | BMC network address. Set -> remote mode; omit -> in-band (`/dev/ipmi0`).|
| `user`      | remote   | BMC username (remote mode).                                             |
| `password`  | remote   | BMC password. Passed via `IPMI_PASSWORD` + `-E`, never on the cmdline / in `ps`. |
| `interface` | no       | `lanplus` (default remote), `lan` (legacy), or `open` (in-band).        |

`bin/ipmi-env` resolves the request's `{id}` against
this file and builds the ipmitool connection args;
`bin/ipmi-collection` builds the collection Members
arrays from it. An id not in the file resolves to a
502.

**Production:** the credentials live in this file --
there is no separate secrets mechanism. Source it from
your secret store and **mount it read-only** over
`/app/config/bmcs.yaml` rather than baking creds into
the image:

```yaml
volumes:
  - ./bmcs.yaml:/app/config/bmcs.yaml:ro
```

The single-host `SALMON_BMC_HOST` / `_USER` /
`_PASSWORD` / `_INTERFACE` env vars are gone as of
1.0.0; move those values into `bmcs.yaml`.

## How the templates work

Every endpoint in `config/tools.yaml` declares an
`output.template` that produces the Redfish JSON
shape. Two kinds of endpoint:

  - **Static-only.** `command.executable: /bin/true`
    + a template with no `{parsed_output.*}`
    references. Used for ServiceRoot, the OData
    document, and the collection envelopes.
  - **ipmitool-backed.** `command.executable:
    /app/bin/ipmi-<thing>` + a template that
    references `{parsed_output.X}`. The shim
    emits a small JSON blob; the template wraps it
    in Redfish boilerplate.

See [url2code's README](https://github.com/cobdfamily/url2code#response-templates)
for the full templating spec.

## Run tests

Two layers, both runnable locally:

```sh
# 1. Structural YAML checks (no docker).
python3 -m venv .venv-tests
.venv-tests/bin/pip install -r tests/requirements.txt
.venv-tests/bin/pytest tests/test_config.py -v

# 2. End-to-end against a mocked-ipmitool container.
docker build -t kibble.apps.blindhub.ca/cobdfamily/salmon:latest .
docker build -f Dockerfile.test -t salmon:test .
docker compose -f docker-compose.test.yaml up -d
.venv-tests/bin/pytest tests/test_e2e.py -v
docker compose -f docker-compose.test.yaml down
```

The e2e suite runs against a derived test image
that swaps `tests/mock/ipmitool` in for
`/usr/bin/ipmitool`. Canned `chassis power status`
/ `sensor list` / action output -- byte-shape
identical to a live BMC's, so the Redfish layer
exercises identically.

CI (GitHub Actions, see
`.github/workflows/test.yml`) runs both layers on
every push, on every PR, and nightly. The nightly
cron catches `url2code:latest` base-image
regressions.

## Licence

AGPL-3.0. See [LICENSE](./LICENSE).
