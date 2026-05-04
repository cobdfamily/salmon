# Deployment

salmon ships as a container image to the kibble
registry on every `git tag v*`. Built on
`python:3.12-slim` + `ipmitool` (apt). Two deployment
shapes:

- **In-band**: salmon runs on the host that has the
  BMC, talking to `/dev/ipmi0` directly.
- **Remote**: salmon runs anywhere; it drives a remote
  BMC over the network via ipmitool's lanplus mode.

## Pre-flight checklist (in-band)

- [ ] Host kernel has `ipmi_devintf` loaded
      (`lsmod | grep ipmi_devintf`; `modprobe
      ipmi_devintf` if not).
- [ ] `/dev/ipmi0` exists and is readable by either
      root or a group salmon's user can join.
- [ ] Container has device passthrough --
      `--device /dev/ipmi0:/dev/ipmi0` on docker run,
      or the `devices:` block in docker-compose.yaml.
- [ ] Public hostname for salmon (e.g.
      `bmc-1.cobd.ca`) with an A record. The service
      speaks plain HTTP on `:8000` behind your
      reverse proxy / TLS terminator.

## Pre-flight checklist (remote)

- [ ] BMC reachable at the configured IP from
      whatever runs salmon. IPMI port: 623/UDP.
- [ ] BMC user has the right privilege level
      (Operator for power actions; User for sensors).
- [ ] Credentials available in env vars (don't
      hardcode in compose; use a secret store or
      `--env-file`).

## Image distribution

`.github/workflows/release.yml` builds and pushes the
image on every `git tag v*`:

```sh
git tag -a v0.1.0 -m "Release 0.1.0"
git push origin v0.1.0
```

Within a couple of minutes:

- `kibble.apps.blindhub.ca/cobdfamily/salmon:0.1.0`
- `kibble.apps.blindhub.ca/cobdfamily/salmon:latest`

Multi-arch (amd64 + arm64).

## No built-in auth

salmon has **no auth** in v0.1.0. Power actions can
reboot machines; gate at your reverse proxy:

```nginx
location / {
    if ($http_x_api_key != "$SALMON_API_KEY") {
        return 401;
    }
    proxy_pass http://127.0.0.1:8000;
    proxy_read_timeout 60s;
}
```

For the openapis.ca marketplace shape, see
`infra/docs/auth-strategy.md` in the workspace root.
A Redfish-native SessionService is on the roadmap.

## Run (in-band)

```yaml
# /opt/salmon/docker-compose.yaml
services:
  salmon:
    image: kibble.apps.blindhub.ca/cobdfamily/salmon:0.1.0
    container_name: salmon
    restart: unless-stopped
    ports:
      - "127.0.0.1:8000:8000"
    devices:
      - /dev/ipmi0:/dev/ipmi0
    environment:
      SALMON_SYSTEM_ID: "1"
      SALMON_SYSTEM_NAME: "rack-7-node-3"
```

```sh
mkdir -p /opt/salmon
cd /opt/salmon
docker compose pull
docker compose up -d
docker compose logs -f salmon
```

## Run (remote)

```yaml
services:
  salmon:
    image: kibble.apps.blindhub.ca/cobdfamily/salmon:0.1.0
    container_name: salmon
    restart: unless-stopped
    ports:
      - "127.0.0.1:8000:8000"
    environment:
      SALMON_BMC_HOST: "10.20.30.42"
      SALMON_BMC_USER: "admin"
      SALMON_BMC_PASSWORD: "${BMC_PASSWORD}"
      SALMON_SYSTEM_ID: "1"
      SALMON_SYSTEM_NAME: "rack-7-node-3"
```

Pass `BMC_PASSWORD` via `--env-file` or your secret
store; don't commit it.

## Verify

```sh
curl -fsS http://127.0.0.1:8000/                  # liveness
curl -fsS http://127.0.0.1:8000/redfish/v1/ | jq  # ServiceRoot

curl -fsS http://127.0.0.1:8000/redfish/v1/Systems/1 \
  | jq '.PowerState'

curl -fsS http://127.0.0.1:8000/redfish/v1/Chassis/1/Thermal \
  | jq '.Temperatures, .Fans'

curl -fsS -X POST -H 'Content-Type: application/json' \
  -d '{"ResetType": "GracefulShutdown"}' \
  http://127.0.0.1:8000/redfish/v1/Systems/1/Actions/ComputerSystem.Reset
```

## Troubleshooting

### `PowerState: Unknown`

salmon returns `Unknown` instead of erroring when
ipmitool fails on a read path. Check the container
logs for the ipmitool stderr. Common causes:

- in-band: `/dev/ipmi0` not passed through, or
  permission denied (the salmon user isn't in the
  device's group).
- remote: BMC unreachable, wrong credentials, or
  the BMC has lanplus disabled.

### Action returns 502

salmon maps non-zero ipmitool exits to HTTP 502 on
action endpoints. The response body's `detail` field
carries the captured stderr.

## Routine operations

### Upgrading

```sh
git tag -a v0.1.1 -m "Release 0.1.1"
git push origin v0.1.1
sed -i 's|salmon:[^ ]*|salmon:0.1.1|' docker-compose.yaml
docker compose pull
docker compose up -d --no-deps salmon
```

### Caching tuning

`SALMON_CACHE_TTL` controls how long salmon caches
slow ipmitool reads (`sensor list`). Default 10s;
0 disables caching.

### Backups

Nothing to back up. Persist your env-file (BMC
credentials) wherever you keep secrets.

## What's NOT in v0.1.0

See README.md "What's NOT in v0.1.0" for the full
list. Highlights: SessionService, Bios, Storage,
EthernetInterfaces, PowerSupplies, per-sensor
endpoints, multi-host orchestration, strict-spec
DMTF protocol-validator conformance.
