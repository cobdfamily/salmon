# Changelog

All notable changes to salmon. Format roughly follows
[Keep a Changelog](https://keepachangelog.com); dates
are ISO 8601 in UTC.

## [Unreleased]

## [1.0.2] -- 2026-06-01

### Changed

- Base image `1.7.0 -> 2.1.0`, bringing salmon onto the current
  engine GA. 1.7.0's route path-parameter support (the multi-host
  `{id}` routing salmon depends on) is preserved; the bump also picks
  up the async executor (2.0.0), streamed I/O (2.1.0), rate limiting +
  size caps (1.3.0), the readiness probe + graceful drain (1.4.0),
  `/metrics` (1.5.0), and OTel tracing (1.6.0). 2.0.0 is a breaking
  engine release (subprocess timeout/cancellation edge-cases may
  shift) — certified against the e2e suite (CI).
- `api.version` `1.0.1 -> 1.0.2`.

## [1.0.1] -- 2026-06-01

### Fixed

- **`/redfish/v1/Chassis/{id}/Power` and `/Thermal` returned 502.**
  The `ipmi-sensors-power` / `ipmi-sensors-thermal` shims passed the
  BMC id to their python parser with a `SALMON_BMC_ID="$1" ipmitool
  ... | python3` prefix — but a `VAR=x cmd1 | cmd2` assignment scopes
  `VAR` to `cmd1` (ipmitool) only, so the parser on the right of the
  pipe hit `KeyError` on `os.environ["SALMON_BMC_ID"]` and exited
  non-zero (→ 502). Now `export SALMON_BMC_ID="$1"` before the
  pipeline so it reaches the parser. (PowerState / Reset were
  unaffected — their parsers don't read the id.) Surfaced by the
  e2e suite against the 1.0.0 image.

### Changed

- `api.version` `1.0.0 -> 1.0.1`.

## [1.0.0] -- 2026-06-01

### Changed (multi-host)

- **salmon now represents N BMCs, not one.** The single
  hardcoded member (id `"1"`) is replaced by an inventory in
  `config/bmcs.yaml`: a YAML list of members, each keyed by its
  Redfish `id`. That id is the `{id}` path segment in
  `/redfish/v1/Systems/{id}` and `/redfish/v1/Chassis/{id}` (and
  the sub-resources). REQUIRES url2code **>= 1.7.0** for route
  path-parameter support.
- **Path-param routes.** The five per-member endpoints
  (`computer-system`, `computer-system-reset`, `chassis`,
  `chassis-power`, `chassis-thermal`) moved from the literal
  `/1` to `/{id}`. The captured id is validated as `text`,
  echoed into the response templates as `{request.id}`, and
  passed to the ipmitool shims as their first command arg.
- **Dynamic collections.** `/redfish/v1/Systems` and
  `/redfish/v1/Chassis` now list every member in `bmcs.yaml`
  instead of a single static member. A new shim
  `bin/ipmi-collection` reads the inventory and emits the
  `Members` array + `count`, which the collection templates lift
  whole (`native_json` mode).
- **Per-BMC credentials from a mounted file.** `bin/ipmi-env`
  resolves the id against `bmcs.yaml` (via url2code's runtime
  PyYAML, the brl `bin/brl-translate` pattern) and assembles the
  ipmitool connection args: remote (`-I <interface> -H <host>
  -U <user>` + `IPMI_PASSWORD`/`-E`) when the member has a
  `host`, in-band (`-I open`) when it doesn't. The password
  still travels via the env, never argv. The single-host
  `SALMON_BMC_*` env-var model is gone -- creds live in the
  mounted file, sourced from the operator's secret store.
- **Base image** pinned `1.6.0 -> 1.7.0` (path-param support).
- `api.version` `0.3.1 -> 1.0.0`.

### Migration

The image bakes in a single in-band example member (id `"1"`),
so an in-band single-host deployment keeps working out of the
box. For multiple or remote BMCs, write a `bmcs.yaml` (see the
documented format in the baked-in `config/bmcs.yaml`) and mount
it read-only over `/app/config/bmcs.yaml`. Operators that set
`SALMON_BMC_HOST` / `_USER` / `_PASSWORD` must move those values
into the file -- the env vars are no longer read.

## [0.3.1] -- 2026-06-01

### Fixed

- **Release SBOM now actually attaches.** `release.yml` (and the
  removed standalone `sbom.yml`) used the uv / `cyclonedx-py`
  environment path, which scans a Python `.venv` — but salmon is
  a YAML-only repo with no `pyproject` / venv, so no usable SBOM
  was ever produced or attached to the image. Switched to scanning
  the built image with **syft** (`anchore/sbom-action`), matching
  the rest of the fleet's YAML-only consumers (brl, needle,
  outofoffice, pandoc). The standalone `sbom.yml` workflow (which
  failed on every push for the same reason) is removed; the SBOM
  is attached at release time and scanned daily by the shared
  `cve-scan` workflow.

### Changed

- `api.version` `0.3.0 -> 0.3.1`.
- Pinned the url2code base image `1.1.0 -> 1.6.0`. The old 1.1.0
  base was published arm64-only in kibble, which broke salmon's
  multi-arch release build and its amd64 CI build; 1.6.0 is proper
  multi-arch and a superset of the response-templating salmon
  relies on.

## [0.3.0] -- 2026-05-26

### Added

- **CI test suite.** Two layers:
  - `tests/test_config.py` -- structural checks on
    `config/tools.yaml` (34 tests). Verifies every
    expected endpoint is present, the
    `ComputerSystem.Reset` enum matches the
    `AllowableValues` advertised in the
    ComputerSystem JSON, every `template_static.id`
    agrees, ipmitool-backed endpoints invoke the
    right shim, and `@odata.type` strings match the
    Redfish spec. Runs without docker.
  - `tests/test_e2e.py` -- end-to-end checks against
    a running container (20 tests). Walks the whole
    Redfish surface: liveness, version doc,
    ServiceRoot, OData doc, both collections,
    ComputerSystem with `PowerState` reading through
    the mock, every `ResetType` -> ipmitool verb
    mapping, Chassis, Thermal (CPU temp + fans),
    Power (voltages + PSU watts).

- **Mocked ipmitool for CI.** `tests/mock/ipmitool`
  is a small POSIX sh script that emits canned
  `chassis power status` / `sensor list` /
  `chassis power <verb>` output. `Dockerfile.test`
  copies it over `/usr/bin/ipmitool` in a derived
  test image so the e2e suite runs on any CI runner
  without `/dev/ipmi0` or a network-reachable BMC.

- **GitHub Actions workflow** at
  `.github/workflows/test.yml`. Two jobs:
  yaml (fast, runs config tests directly) ->
  e2e (builds salmon + test image via buildx +
  gha cache, brings up the stack via
  `docker-compose.test.yaml`, runs pytest). Nightly
  cron catches `url2code:latest` base-image
  regressions.

### Fixed

- `bin/ipmi-reset` no longer leaks ipmitool's
  status-line text ("Chassis Power Control: ...")
  into stdout. The line is redirected to stderr so
  url2code's `native_json` parser sees only the
  JSON the shim itself emits. Without this fix, the
  Reset action returned 502 for every ResetType.

## [0.2.0] -- 2026-05-26

### Changed (reshape, no wire-protocol break)

- **salmon is now a url2code project.** The Redfish
  surface is declared in `config/tools.yaml` and the
  IPMI shell-out lives in four POSIX `sh` shims under
  `bin/`. The HTTP shape and JSON envelopes are
  unchanged -- Redfish clients (Ironic, Foreman,
  Tinkerbell) see byte-equivalent responses to the
  0.1.x hand-coded FastAPI app.

- **Implementation collapsed.** ~300 lines of Python
  -> one YAML file + ~150 lines of shell. The
  Redfish translation (`"PowerState": "On"`) lives
  in the shims; the wrapper JSON (`@odata.id`,
  `@odata.type`, action targets) lives in
  `output.template` declarations.

- **New image base.** `FROM
  kibble.apps.blindhub.ca/cobdfamily/url2code:1.1.0`
  (the url2code release that ships the response-
  shape templating feature this port depends on),
  plus `apt-get install ipmitool ca-certificates`.

### Removed

- `src/salmon/` -- the hand-written FastAPI app.
- `src/salmon/ipmi.py` -- replaced by the shell
  shims at `bin/ipmi-*`.
- `pyproject.toml` + `uv.lock` -- no Python source in
  this repo any more.
- `tests/` -- the FastAPI-specific test suite. A
  url2code-shaped CI suite is planned for v0.3.0.

### Known differences

- **`/redfish/v1/` (trailing slash)** now returns a
  307 redirect to `/redfish/v1` (no trailing). All
  Redfish clients COBD operates against follow 307
  redirects, so this is behaviourally transparent.
  url2code's `normalize_route` is the source of the
  difference; a future url2code release may add
  opt-in trailing-slash retention.

### Migration

Consumers pinned to `salmon:0.1.x` should update
their pin to `salmon:0.2.0` (or `:latest`) and
re-deploy. No client-side changes required.

## [0.1.0] -- 2026-05-22

Initial release. Hand-written FastAPI Redfish facade
over `ipmitool`. See git history for the complete
0.1.x feature set; it lives at tag `v0.1.0` on the
repo.

[1.0.2]: https://github.com/cobdfamily/salmon/compare/v1.0.1...v1.0.2
[1.0.1]: https://github.com/cobdfamily/salmon/compare/v1.0.0...v1.0.1
[1.0.0]: https://github.com/cobdfamily/salmon/compare/v0.3.1...v1.0.0
[0.3.1]: https://github.com/cobdfamily/salmon/compare/v0.3.0...v0.3.1
[0.3.0]: https://github.com/cobdfamily/salmon/compare/v0.2.0...v0.3.0
[0.2.0]: https://github.com/cobdfamily/salmon/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/cobdfamily/salmon/releases/tag/v0.1.0
