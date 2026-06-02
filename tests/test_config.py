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
BMCS_YAML = REPO_ROOT / "config" / "bmcs.yaml"


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
    assert cfg["api"]["version"] == "1.0.0"


# ---------------------------------------------------
# Endpoint inventory
# ---------------------------------------------------


EXPECTED_ENDPOINTS = {
    "redfish-version":          ("GET",  "/redfish"),
    "service-root":             ("GET",  "/redfish/v1/"),
    "odata-root":               ("GET",  "/redfish/v1/odata"),
    "systems-collection":       ("GET",  "/redfish/v1/Systems"),
    "computer-system":          ("GET",  "/redfish/v1/Systems/{id}"),
    "computer-system-reset":    ("POST", "/redfish/v1/Systems/{id}/Actions/ComputerSystem.Reset"),
    "chassis-collection":       ("GET",  "/redfish/v1/Chassis"),
    "chassis":                  ("GET",  "/redfish/v1/Chassis/{id}"),
    "chassis-power":            ("GET",  "/redfish/v1/Chassis/{id}/Power"),
    "chassis-thermal":          ("GET",  "/redfish/v1/Chassis/{id}/Thermal"),
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
# Multi-host: path-param routes + bmcs.yaml inventory
# ---------------------------------------------------


# The per-member endpoints address a BMC by its Redfish id
# captured from the `{id}` route segment (url2code 1.7.0
# path-params). The id is threaded into the response template
# as `{request.id}` and passed to the shim as the FIRST arg.
PER_MEMBER_ENDPOINTS = {
    "computer-system",
    "computer-system-reset",
    "chassis",
    "chassis-power",
    "chassis-thermal",
}


@pytest.mark.parametrize("name", sorted(PER_MEMBER_ENDPOINTS))
def test_per_member_route_uses_id_path_param(
    by_name, name: str,
) -> None:
    """Every per-member endpoint addresses the BMC via the
    `{id}` path segment -- no literal `/1` left anywhere.
    A hardcoded id would pin salmon back to single-host."""
    route = by_name[name]["route"]
    assert "{id}" in route
    assert "/Systems/1" not in route
    assert "/Chassis/1" not in route


@pytest.mark.parametrize("name", sorted(PER_MEMBER_ENDPOINTS))
def test_per_member_validates_id_as_text(
    by_name, name: str,
) -> None:
    """Each per-member endpoint declares an `id` text
    validation so the captured path segment is coerced /
    echoed consistently into `{request.id}`."""
    validations = (
        by_name[name].get("request", {}).get("validations", {})
    )
    assert "id" in validations, (
        f"{name} is missing the `id` path-param validation"
    )
    assert validations["id"]["type"] == "text"


@pytest.mark.parametrize("name", sorted(PER_MEMBER_ENDPOINTS))
def test_per_member_template_uses_request_id(
    by_name, name: str,
) -> None:
    """Per-member templates render the id from
    `{request.id}` and never reference the old
    `{static.id}` / template_static.id."""
    output = by_name[name]["output"]
    assert "template_static" not in output, (
        f"{name} still carries template_static (should be gone)"
    )
    blob = yaml.safe_dump(output["template"])
    assert "{static.id}" not in blob
    assert "{static.name}" not in blob


def test_no_endpoint_has_literal_one_route(by_name) -> None:
    """Belt-and-braces: no endpoint anywhere still pins a
    literal `/Systems/1` or `/Chassis/1` route."""
    for name, endpoint in by_name.items():
        route = endpoint["route"]
        assert not route.endswith("/Systems/1")
        assert not route.endswith("/Chassis/1")
        assert "/Systems/1/" not in route
        assert "/Chassis/1/" not in route


# ---- bmcs.yaml inventory ----------------------------------


def test_bmcs_yaml_parses_and_is_a_list() -> None:
    """The BMC inventory parses and is a list of members --
    bin/ipmi-env and bin/ipmi-collection both iterate it."""
    members = yaml.safe_load(BMCS_YAML.read_text())
    assert isinstance(members, list)
    assert members, "bmcs.yaml has no members"


def test_bmcs_entries_have_required_fields() -> None:
    """Every member needs an `id` (it's the Redfish resource
    id / route segment). Remote members also need a `user`;
    host / password / interface are optional (omitting host
    means in-band, which needs no auth)."""
    members = yaml.safe_load(BMCS_YAML.read_text())
    for m in members:
        assert "id" in m and str(m["id"]) != "", (
            f"member without an id: {m}"
        )
        if m.get("host"):
            assert m.get("user"), (
                f"remote member {m['id']} missing user"
            )


# ---------------------------------------------------
# Command surface
# ---------------------------------------------------


# The four ipmitool-touching endpoints pass the BMC id as the
# FIRST command arg, ahead of any existing args.
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


@pytest.mark.parametrize("name", sorted(IPMITOOL_BACKED))
def test_ipmitool_backed_endpoints_pass_id_first(
    by_name, name: str,
) -> None:
    """Each ipmitool-backed endpoint passes `{id}` as the
    FIRST command arg, so the shim can resolve the BMC from
    bmcs.yaml. ipmi-reset also takes `{ResetType}` second."""
    args = by_name[name]["command"].get("args", [])
    assert args and args[0] == "{id}", (
        f"{name} must pass {{id}} as the first command arg, "
        f"got {args}"
    )


def test_reset_passes_resettype_after_id(by_name) -> None:
    """ipmi-reset's args are [id, ResetType] in that order."""
    args = by_name["computer-system-reset"]["command"]["args"]
    assert args == ["{id}", "{ResetType}"]


# The collection envelopes are now dynamic: built from
# bmcs.yaml by bin/ipmi-collection, not /bin/true static.
COLLECTION_ENDPOINTS = {
    "systems-collection": "/redfish/v1/Systems",
    "chassis-collection": "/redfish/v1/Chassis",
}


@pytest.mark.parametrize(
    "name,base_path", list(COLLECTION_ENDPOINTS.items()),
)
def test_collections_use_ipmi_collection(
    by_name, name: str, base_path: str,
) -> None:
    """Both collections run bin/ipmi-collection with their
    base path as the sole arg, in native_json mode, and lift
    the whole Members list + count out of parsed_output."""
    endpoint = by_name[name]
    cmd = endpoint["command"]
    assert cmd["executable"] == "/app/bin/ipmi-collection"
    assert cmd["args"] == [base_path]
    output = endpoint["output"]
    assert output["mode"] == "native_json"
    assert output["template"]["Members"] == "{parsed_output.Members}"
    assert (
        output["template"]["Members@odata.count"]
        == "{parsed_output.count}"
    )


STATIC_ONLY = {
    "redfish-version",
    "service-root",
    "odata-root",
    "chassis",
}


@pytest.mark.parametrize("name", sorted(STATIC_ONLY))
def test_static_only_endpoints_run_bin_true(
    by_name, name: str,
) -> None:
    """Static-only endpoints (ServiceRoot, the OData
    document, the per-member Chassis envelope) ship
    `/bin/true` as the executable -- the template is the
    body and no ipmitool runs. If any of these drift to a
    real shim, we'd be paying a round-trip for a response
    that doesn't need one."""
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
