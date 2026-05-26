# Deployment

salmon 0.2.0 ships as a container image to the
kibble registry on every `git tag v*`. Built on the
url2code 1.1.0 base image + `ipmitool` (apt) + the
bin/ shims + the YAML.

## Build + tag

```sh
docker build \
  -t kibble.apps.blindhub.ca/cobdfamily/salmon:0.2.0 \
  -t kibble.apps.blindhub.ca/cobdfamily/salmon:latest \
  .
docker push kibble.apps.blindhub.ca/cobdfamily/salmon:0.2.0
docker push kibble.apps.blindhub.ca/cobdfamily/salmon:latest
```

The Dockerfile sanity-checks the YAML at build time
via `url2code.config.load_config('/app/config/
tools.yaml')`, so a malformed YAML fails the build
rather than crashing the first request.

## Two deployment shapes

### In-band

salmon talks to `/dev/ipmi0` on the container host.
Requires:

- The host kernel has `ipmi_devintf` loaded
  (`modprobe ipmi_devintf` if missing).
- `docker-compose.yaml` passes the device through
  with `devices: [/dev/ipmi0:/dev/ipmi0]`.
- The container's runtime user (`url2code`) can
  open `/dev/ipmi0`. Most distributions allow this
  via the `dialout` / `disk` group; if not, you can
  flip the compose `user: "0:0"` for root access.

```sh
docker compose up -d
curl -s http://localhost:8000/redfish/v1/Systems/1
```

### Remote

salmon talks to the BMC over the network using
`ipmitool -I lanplus -H <ip> -U <user> -P <pass>`.
The `bin/ipmi-env` helper picks up the env vars and
assembles the auth args. The password is exported
via `IPMI_PASSWORD` so it doesn't appear in the
container's process list.

```sh
SALMON_BMC_HOST=10.0.0.42 \
SALMON_BMC_USER=admin \
SALMON_BMC_PASSWORD='changeme' \
docker compose up -d
```

Comment out the `devices:` block in
`docker-compose.yaml` for remote mode -- the
container doesn't need /dev/ipmi0 passed in.

## Health checks

- `GET /` (JSON liveness) returns
  `{"service":"salmon","status":"ok","version":"0.2.0"}`.
  url2code's auto-registered liveness handler.
- The compose healthcheck hits `/` every 5s.

## Observability

- url2code emits structured JSON logs on every
  endpoint call -- success, parse failure, CLI
  failure, timeout. Pipe `docker logs salmon` into
  your log aggregator.
- Each Redfish endpoint logs the rendered `ipmitool`
  command + the exit code; ipmitool failures show up
  as a 502 with the stderr in the response body, so
  the failure mode is visible both in the response
  and in the logs.

## Upgrades

`docker compose pull && docker compose up -d` is
idempotent. Pin `SALMON_TAG=0.2.0` in production;
`:latest` moves whenever a new release is cut.

To roll back, set `SALMON_TAG` to the prior version
and re-run. salmon is stateless -- no migration, no
data loss.

## License

AGPL-3.0. See [LICENSE](./LICENSE).
