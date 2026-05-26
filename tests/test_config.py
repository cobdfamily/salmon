"""Static checks on config/tools.yaml.

salmon has no Python source of its own -- the HTTP
surface is declared in config/tools.yaml. These tests
pin its shape so a careless edit can't ship a
malformed config that only surfaces at container
start, or quietly drop a Redfish endpoint a consumer
depends on.

What we DON'T check here: the actual response bodies.
Those land in test_e2e.py because they need ipmitool
(mocked) + url2code's templating engine to materialize.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml


REPO_ROOT = Path(__file__).resolve().parent.parent
TOOLS_YAML = REPO_ROOT / "config" / "tools.yaml"


@pytest.fixture(scope="module")
def cfg() -> dict:
    return yaml.safe_load(TOOLS_YAML.read_text())


@pytest.fixture(scope="module")
def endpoints(cfg) -> list[dict]:
    return cfg["endpoints"]


@pytest.fixture(scope="module")
def by_name(endpoints) -> dict[str, dict]:
    """Endpoint lookup keyed by `name`. Tests reference
    endpoints by name rather than list-index so a YAML
    reorder doesn't break the assertions."""
    return {e["name"]: e for e in endpoints}


# ---------------------------------------------------
# Top-level shape
# ---------------------------------------------------


def test_api_title_is_salmon(cfg) -> None:
    """The `service` field on the liveness response
    reads from api.title (url2code 1.0.6+). Operators
    grep for `"service":"salmon"` in dashboards, so
    pin it."""
    assert cfg["api"]["title"] == "salmon"


def test_api_version_matches_changelog(cfg) -> None:
    """api.version is what GET / reports. Pin it to
    the SemVer string the CHANGELOG calls out; keeps
    a release from shipping with a stale version."""
    assert cfg["api"]["version"] == "0.3.0"


# ---------------------------------------------------
# Endpoint inventory
# ---------------------------------------------------


EXPECTED_ENDPOINTS = {
    "redfish-version":          ("GET",  "/redfish"),
    "service-root":             ("GET",  "/redfish/v1/"),
    "odata-root":               ("GET",  "/redfish/v1/odata"),
    "systems-collection":       ("GET",  "/redfish/v1/Systems"),
    "computer-system":          ("GET",  "/redfish/v1/Systems/1"),
    "computer-system-reset":    ("POST", "/redfish/v1/Systems/1/Actions/ComputerSystem.Reset"),
    "chassis-collection":       ("GET",  "/redfish/v1/Chassis"),
    "chassis":                  ("GET",  "/redfish/v1/Chassis/1"),
    "chassis-power":            ("GET",  "/redfish/v1/Chassis/1/Power"),
    "chassis-thermal":          ("GET",  "/redfish/v1/Chassis/1/Thermal"),
}


def test_every_expected_endpoint_present(by_name) -> None:
    missing = set(EXPECTED_ENDPOINTS) - set(by_name)
    assert not missing, f"endpoints missing from YAML: {missing}"


def test_no_extra_endpoints(by_name) -> None:
    """Don't quietly ship a new endpoint. If you're
    adding one, update EXPECTED_ENDPOINTS in this
    test alongside the YAML edit so the inventory
    stays the single source of truth."""
    extra = set(by_name) - set(EXPECTED_ENDPOINTS)
    assert not extra, (
        f"endpoints in YAML but not in test inventory: "
        f"{extra} -- if intentional, add them to "
        f"EXPECTED_ENDPOINTS in tests/test_config.py"
    )


@pytest.mark.parametrize(
    "name,method,route",
    [(n, m, r) for n, (m, r) in EXPECTED_ENDPOINTS.items()],
)
def test_endpoint_route_and_method(
    by_name, name: str, method: str, route: str,
) -> None:
    endpoint = by_name[name]
    assert endpoint["method"] == method
    assert endpoint["route"] == route


# ---------------------------------------------------
# Reset action enum
# ---------------------------------------------------


EXPECTED_RESET_TYPES = {
    "On", "ForceOff", "GracefulShutdown",
    "GracefulRestart", "ForceRestart", "ForceOn",
    "PowerCycle", "Nmi",
}


def test_reset_enum_choices(by_name) -> None:
    """The Reset action validates ResetType against
    the Redfish-spec allowable values. Drop one and a
    legit caller request would 400; add an unsupported
    one and we'd shell out to ipmitool with garbage."""
    reset = by_name["computer-system-reset"]
    choices = (
        reset["request"]["validations"]
              ["ResetType"]["choices"]
    )
    assert set(choices) == EXPECTED_RESET_TYPES


def test_reset_allowable_values_match_choices(by_name) -> None:
    """The AllowableValues array advertised in the
    ComputerSystem JSON has to match the enum the
    action endpoint actually accepts. A drift here
    would let a caller send ResetType=X that the
    Redfish-spec discoverable surface advertised but
    the action endpoint then 400s on."""
    cs = by_name["computer-system"]
    allowable = (
        cs["output"]["template"]["Actions"]
          ["#ComputerSystem.Reset"]
          ["ResetType@Redfish.AllowableValues"]
    )
    assert set(allowable) == EXPECTED_RESET_TYPES


# ---------------------------------------------------
# OData id consistency
# ---------------------------------------------------


def test_static_id_consistent_across_endpoints(by_name) -> None:
    """Every endpoint that declares template_static.id
    uses the SAME id. Salmon v0.2.x is single-host;
    if these drift, the Members links in the
    collections would point at a system that GET on
    that id wouldn't find."""
    ids = set()
    for name, endpoint in by_name.items():
        static = (
            endpoint.get("output", {})
                    .get("template_static", {})
        )
        if "id" in static:
            ids.add(static["id"])
    assert ids == {"1"}, (
        f"expected every template_static.id to be '1', "
        f"got {ids}"
    )


# ---------------------------------------------------
# Command surface
# ---------------------------------------------------


IPMITOOL_BACKED = {
    "computer-system":       "/app/bin/ipmi-power-status",
    "computer-system-reset": "/app/bin/ipmi-reset",
    "chassis-power":         "/app/bin/ipmi-sensors-power",
    "chassis-thermal":       "/app/bin/ipmi-sensors-thermal",
}


@pytest.mark.parametrize(
    "name,expected_exec",
    list(IPMITOOL_BACKED.items()),
)
def test_ipmitool_backed_endpoints_invoke_shim(
    by_name, name: str, expected_exec: str,
) -> None:
    """Pin the shim path each ipmitool-backed endpoint
    runs. The shim's contract (emit JSON to stdout) is
    what the response template depends on; pointing
    an endpoint at the wrong shim wouldn't trip a
    YAML validator but would emit the wrong shape."""
    cmd = by_name[name]["command"]
    assert cmd["executable"] == expected_exec


STATIC_ONLY = {
    "redfish-version",
    "service-root",
    "odata-root",
    "systems-collection",
    "chassis-collection",
    "chassis",
}


@pytest.mark.parametrize("name", sorted(STATIC_ONLY))
def test_static_only_endpoints_run_bin_true(
    by_name, name: str,
) -> None:
    """Static-only endpoints (ServiceRoot, collection
    envelopes, etc.) ship `/bin/true` as the
    executable -- the template is the body and no
    ipmitool runs. If any of these drift to a real
    shim, we'd be paying an ipmitool round-trip for
    a response that doesn't need one."""
    cmd = by_name[name]["command"]
    assert cmd["executable"] == "/bin/true"


# ---------------------------------------------------
# OData type strings
# ---------------------------------------------------


EXPECTED_ODATA_TYPES = {
    "service-root":         "#ServiceRoot.v1_0_0.ServiceRoot",
    "systems-collection":   "#ComputerSystemCollection.ComputerSystemCollection",
    "computer-system":      "#ComputerSystem.v1_0_0.ComputerSystem",
    "chassis-collection":   "#ChassisCollection.ChassisCollection",
    "chassis":              "#Chassis.v1_0_0.Chassis",
    "chassis-power":        "#Power.v1_0_0.Power",
    "chassis-thermal":      "#Thermal.v1_0_0.Thermal",
}


@pytest.mark.parametrize(
    "name,expected_type",
    list(EXPECTED_ODATA_TYPES.items()),
)
def test_odata_type_matches_redfish_spec(
    by_name, name: str, expected_type: str,
) -> None:
    """Redfish clients use @odata.type to look up the
    schema. A typo'd version (e.g.
    `#ComputerSystem.v1_0_1.ComputerSystem` when the
    JSON only carries v1.0.0 fields) would cause
    strict validators to reject the response. These
    strings are the contract."""
    template = by_name[name]["output"]["template"]
    assert template["@odata.type"] == expected_type
