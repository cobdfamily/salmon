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

Single-host scope: salmon represents one BMC. The
Systems and Chassis collections always have exactly
one member; its id is hardcoded to `"1"` in
`config/tools.yaml::template_static`. Operators
with a different naming convention edit the YAML
at deploy time -- no rebuild required.

```
GET  /                                              liveness (non-Redfish)
GET  /redfish                                       Redfish version doc
GET  /redfish/v1                                    ServiceRoot
GET  /redfish/v1/odata                              OData service doc
GET  /redfish/v1/Systems                            ComputerSystemCollection
GET  /redfish/v1/Systems/1                          ComputerSystem + PowerState
POST /redfish/v1/Systems/1/Actions/
                  ComputerSystem.Reset              power action
GET  /redfish/v1/Chassis                            ChassisCollection
GET  /redfish/v1/Chassis/1                          Chassis
GET  /redfish/v1/Chassis/1/Power                    Voltages + PowerSupplies
GET  /redfish/v1/Chassis/1/Thermal                  Temperatures + Fans
```

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

## Quick start (in-band)

```sh
docker compose up -d
curl -s http://localhost:8000/redfish/v1/Systems/1 | jq .
```

Requires `/dev/ipmi0` on the host (`modprobe
ipmi_devintf` if missing) and the compose file's
`devices:` block pointing the container at it.

## Quick start (remote BMC)

```sh
export SALMON_BMC_HOST=10.0.0.42
export SALMON_BMC_USER=admin
export SALMON_BMC_PASSWORD='changeme'
# Comment out the `devices:` block in
# docker-compose.yaml first.
docker compose up -d
curl -s http://localhost:8000/redfish/v1/Systems/1 | jq .
```

## Configuration

| Var                     | Default     | Meaning                                  |
|-------------------------|-------------|------------------------------------------|
| `SALMON_BMC_HOST`       | (unset)     | If set, remote mode; else in-band.       |
| `SALMON_BMC_USER`       | (unset)     | BMC username for remote mode.            |
| `SALMON_BMC_PASSWORD`   | (unset)     | Passed via `IPMI_PASSWORD` so it doesn't appear in `ps`. |
| `SALMON_BMC_INTERFACE`  | `lanplus`   | ipmitool interface for remote mode (`lanplus` or `lan`). |

To change the System / Chassis id from the default
`"1"`, edit `config/tools.yaml::template_static.id`
in each endpoint that has it.

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

There are no Python tests in this repo any more --
the original FastAPI test suite was retired with the
hand-coded handlers. End-to-end coverage is provided
by `docker compose up -d` against a real (or
remote) BMC and `curl` against the Redfish surface.
Operators with a target BMC can run:

```sh
# Cycle the whole Redfish surface once.
for path in \
    /redfish /redfish/v1 /redfish/v1/odata \
    /redfish/v1/Systems /redfish/v1/Systems/1 \
    /redfish/v1/Chassis /redfish/v1/Chassis/1 \
    /redfish/v1/Chassis/1/Power \
    /redfish/v1/Chassis/1/Thermal ; do
  echo "=== $path ==="
  curl -fsS "http://localhost:8000$path" | jq .
done
```

A v0.3.0 sprint will add a CI test suite that runs
against a mock-ipmitool wrapper -- the same shape
brl / needle / pandoc use.

## Licence

AGPL-3.0. See [LICENSE](./LICENSE).
