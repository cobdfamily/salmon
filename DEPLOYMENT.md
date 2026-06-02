# Deployment

salmon ships as a container image to the kibble
registry on every `git tag v*`. Built on the url2code
1.7.0 base image (path-param support, required by the
multi-host surface) + `ipmitool` (apt) + the bin/
shims + the YAML.

As of 1.0.0 salmon is **multi-host**: the BMCs it
fronts and their credentials live in
`config/bmcs.yaml` (a list of members keyed by Redfish
`id`), not in environment variables. The image bakes
in a single in-band example member (id `"1"`); real
deployments mount their own inventory over it.

## Build + tag

```sh
docker build \
  -t kibble.apps.blindhub.ca/cobdfamily/salmon:1.0.0 \
  -t kibble.apps.blindhub.ca/cobdfamily/salmon:latest \
  .
docker push kibble.apps.blindhub.ca/cobdfamily/salmon:1.0.0
docker push kibble.apps.blindhub.ca/cobdfamily/salmon:latest
```

The Dockerfile sanity-checks the YAML at build time
via `url2code.config.load_config('/app/config/
tools.yaml')`, so a malformed YAML fails the build
rather than crashing the first request.

## Two deployment shapes

A member's mode is per-entry in `bmcs.yaml`: a member
with a `host` is remote, one without is in-band. A
mixed inventory is fine.

### BMC inventory + credentials

Each member in `bmcs.yaml`:

```yaml
- id: "1"                # Redfish id / {id} route segment (required)
  host: 10.0.0.11        # omit for in-band
  user: admin            # remote mode
  password: changeme     # remote mode; via IPMI_PASSWORD + -E, never argv
  interface: lanplus     # lanplus | lan | open (in-band)
```

`bin/ipmi-env` resolves the request's `{id}` against
this file; `bin/ipmi-collection` builds the Systems /
Chassis collection Members from it. An unknown id is a
502.

The passwords live in this file -- there is no
separate secrets mechanism. **Mount it read-only** from
your secret store rather than baking creds into the
image:

```yaml
# docker-compose.yaml
volumes:
  - ./bmcs.yaml:/app/config/bmcs.yaml:ro
```

### In-band members

A member with no `host` (set `interface: open`) talks
to `/dev/ipmi0` on the container host. Requires:

- The host kernel has `ipmi_devintf` loaded
  (`modprobe ipmi_devintf` if missing).
- `docker-compose.yaml` passes the device through
  with `devices: [/dev/ipmi0:/dev/ipmi0]`.
- The container's runtime user (`url2code`) can
  open `/dev/ipmi0`. Most distributions allow this
  via the `dialout` / `disk` group; if not, you can
  flip the compose `user: "0:0"` for root access.

The baked-in default inventory is a single in-band
member (id `"1"`), so `docker compose up -d` works out
of the box for a single local host:

```sh
docker compose up -d
curl -s http://localhost:8000/redfish/v1/Systems/1
```

### Remote members

A member with a `host` is reached over the network via
`ipmitool -I <interface> -H <host> -U <user> -E`. The
`bin/ipmi-env` helper reads the member from `bmcs.yaml`
and assembles the auth args; the password is exported
via `IPMI_PASSWORD` so it doesn't appear in the
container's process list.

If your inventory is all-remote, comment out the
`devices:` block in `docker-compose.yaml` -- the
container doesn't need /dev/ipmi0 passed in. The
single-host `SALMON_BMC_*` env vars are gone as of
1.0.0; put those values in `bmcs.yaml`.

## Health checks

- `GET /` (JSON liveness) returns
  `{"service":"salmon","status":"ok","version":"1.0.0"}`.
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
idempotent. Pin `SALMON_TAG=1.0.0` in production;
`:latest` moves whenever a new release is cut.

To roll back, set `SALMON_TAG` to the prior version
and re-run. salmon is stateless -- no migration, no
data loss.

## License

AGPL-3.0. See [LICENSE](./LICENSE).
