# Changelog

All notable changes to salmon. Format roughly follows
[Keep a Changelog](https://keepachangelog.com); dates
are ISO 8601 in UTC.

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
