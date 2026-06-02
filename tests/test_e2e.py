"""End-to-end tests for salmon.

Assumes the docker-compose-test stack is up and
reachable at http://localhost:8000. Inside the test
image, /usr/bin/ipmitool is replaced by
tests/mock/ipmitool which emits canned output --
salmon's shims parse it back into the same JSON
shapes a real BMC would produce. So the suite runs
in CI on any runner, no /dev/ipmi0 required, no
network egress to a BMC.

CI builds the image fresh, brings the stack up via
docker-compose.test.yaml, and runs this suite.
Locally: ``docker compose -f docker-compose.test.yaml
up -d``.

Coverage:

  / liveness                          service / version
  /redfish                            version doc
  /redfish/v1                         ServiceRoot
  /redfish/v1/odata                   OData service doc
  /redfish/v1/Systems                 ComputerSystemCollection (2 members)
  /redfish/v1/Systems/1               ComputerSystem (PowerState from mock)
  POST .../Actions/ComputerSystem.Reset   each ResetType
  /redfish/v1/Chassis                 ChassisCollection (2 members)
  /redfish/v1/Chassis/1               Chassis
  /redfish/v1/Chassis/1/Power         Voltages + PowerSupplies
  /redfish/v1/Chassis/1/Thermal       Temperatures + Fans
  /redfish/v1/Systems/999             unknown id -> non-2xx

The test image bakes in tests/bmcs.yaml: a two-member
in-band inventory (ids "1" and "2"). So the collections
list two members; per-member paths for id "1" exercise the
mocked ipmitool; an unknown id resolves to a non-2xx.
"""

from __future__ import annotations

import os

import pytest
import requests


SALMON_BASE_URL = os.environ.get(
    "SALMON_BASE_URL", "http://localhost:8000",
)


# ---------------------------------------------------
# Liveness
# ---------------------------------------------------


def test_liveness_reports_salmon() -> None:
    r = requests.get(SALMON_BASE_URL + "/", timeout=5)
    assert r.status_code == 200
    body = r.json()
    assert body["service"] == "salmon"
    assert body["status"] == "ok"
    assert body["version"]


# ---------------------------------------------------
# Version doc + ServiceRoot
# ---------------------------------------------------


def test_redfish_version_document() -> None:
    r = requests.get(SALMON_BASE_URL + "/redfish", timeout=5)
    assert r.status_code == 200
    assert r.json() == {"v1": "/redfish/v1/"}


def test_service_root_shape() -> None:
    # /redfish/v1/ (trailing) 307-redirects to /redfish/v1
    # (no trailing). requests follows redirects by default,
    # so either form lands here. Spot-check both forms
    # for client compatibility.
    for path in ["/redfish/v1", "/redfish/v1/"]:
        r = requests.get(SALMON_BASE_URL + path, timeout=5)
        assert r.status_code == 200
        body = r.json()
        assert body["@odata.id"] == "/redfish/v1/"
        assert body["@odata.type"] == (
            "#ServiceRoot.v1_0_0.ServiceRoot"
        )
        assert body["Systems"] == (
            {"@odata.id": "/redfish/v1/Systems"}
        )
        assert body["Chassis"] == (
            {"@odata.id": "/redfish/v1/Chassis"}
        )


def test_odata_service_document() -> None:
    r = requests.get(
        SALMON_BASE_URL + "/redfish/v1/odata", timeout=5,
    )
    assert r.status_code == 200
    body = r.json()
    assert body["@odata.context"] == "/redfish/v1/$metadata"
    urls = {entry["url"] for entry in body["value"]}
    assert "/redfish/v1/" in urls
    assert "/redfish/v1/Systems" in urls
    assert "/redfish/v1/Chassis" in urls


# ---------------------------------------------------
# Collections
# ---------------------------------------------------


@pytest.mark.parametrize(
    "path",
    [
        "/redfish/v1/Systems",
        "/redfish/v1/Chassis",
    ],
)
def test_collection_lists_both_members(path: str) -> None:
    """The collections are built dynamically from bmcs.yaml.
    The test inventory has two members (ids "1" and "2"), so
    the count is 2 and both member links are present."""
    r = requests.get(SALMON_BASE_URL + path, timeout=5)
    assert r.status_code == 200
    body = r.json()
    assert body["Members@odata.count"] == 2
    member_ids = {m["@odata.id"] for m in body["Members"]}
    assert member_ids == {path + "/1", path + "/2"}


# ---------------------------------------------------
# ComputerSystem (PowerState reads through the mock)
# ---------------------------------------------------


def test_computer_system_shape() -> None:
    r = requests.get(
        SALMON_BASE_URL + "/redfish/v1/Systems/1", timeout=5,
    )
    assert r.status_code == 200
    body = r.json()
    assert body["@odata.id"] == "/redfish/v1/Systems/1"
    assert body["@odata.type"] == (
        "#ComputerSystem.v1_0_0.ComputerSystem"
    )
    assert body["Id"] == "1"
    # The mock-ipmitool always says "Chassis Power is on".
    # ipmi-power-status emits {"PowerState":"On"}, the
    # template lifts it verbatim into the response.
    assert body["PowerState"] == "On"
    actions = body["Actions"]["#ComputerSystem.Reset"]
    assert actions["target"] == (
        "/redfish/v1/Systems/1/Actions/ComputerSystem.Reset"
    )
    allowable = actions["ResetType@Redfish.AllowableValues"]
    assert "ForceRestart" in allowable
    assert "On" in allowable


# ---------------------------------------------------
# Reset action -- exercise the ResetType mapping
# ---------------------------------------------------


@pytest.mark.parametrize(
    "reset_type,expected_ipmi_verb",
    [
        ("On",               "on"),
        ("ForceOn",          "on"),
        ("ForceOff",         "off"),
        ("GracefulShutdown", "soft"),
        ("GracefulRestart",  "soft"),
        ("ForceRestart",     "reset"),
        ("PowerCycle",       "cycle"),
        ("Nmi",              "diag"),
    ],
)
def test_reset_action_maps_correctly(
    reset_type: str, expected_ipmi_verb: str,
) -> None:
    """Each Redfish ResetType lands on the right
    `ipmitool chassis power` subcommand. The shim
    echoes both values in its JSON output so the
    response template can surface them in the
    ExtendedInfo message; we assert the verb is
    there as a proxy for the mapping being right."""
    r = requests.post(
        SALMON_BASE_URL + "/redfish/v1/Systems/1"
                          "/Actions/ComputerSystem.Reset",
        json={"ResetType": reset_type},
        timeout=10,
    )
    assert r.status_code == 200, r.text
    body = r.json()
    message = body["@Message.ExtendedInfo"][0]["Message"]
    assert reset_type in message
    assert f"ipmitool {expected_ipmi_verb}" in message


def test_reset_action_rejects_unknown_type() -> None:
    """ResetType is an enum at the url2code validation
    layer -- an unsupported value should 400 before
    we ever shell out to ipmitool."""
    r = requests.post(
        SALMON_BASE_URL + "/redfish/v1/Systems/1"
                          "/Actions/ComputerSystem.Reset",
        json={"ResetType": "BogusValue"},
        timeout=5,
    )
    assert r.status_code == 400


# ---------------------------------------------------
# Unknown member id
# ---------------------------------------------------


def test_unknown_member_id_is_not_2xx() -> None:
    """An id that isn't in bmcs.yaml has no BMC to resolve.
    ipmi-env exits non-zero, so url2code surfaces a non-2xx
    (502) rather than a half-built ComputerSystem."""
    r = requests.get(
        SALMON_BASE_URL + "/redfish/v1/Systems/999", timeout=10,
    )
    assert not (200 <= r.status_code < 300), (
        f"unknown id should not be 2xx, got {r.status_code}"
    )


# ---------------------------------------------------
# Chassis (static)
# ---------------------------------------------------


def test_chassis_shape() -> None:
    r = requests.get(
        SALMON_BASE_URL + "/redfish/v1/Chassis/1", timeout=5,
    )
    assert r.status_code == 200
    body = r.json()
    assert body["@odata.id"] == "/redfish/v1/Chassis/1"
    assert body["@odata.type"] == "#Chassis.v1_0_0.Chassis"
    assert body["Id"] == "1"
    assert body["Power"] == (
        {"@odata.id": "/redfish/v1/Chassis/1/Power"}
    )
    assert body["Thermal"] == (
        {"@odata.id": "/redfish/v1/Chassis/1/Thermal"}
    )


# ---------------------------------------------------
# Thermal (Temperatures + Fans through ipmitool mock)
# ---------------------------------------------------


def test_thermal_includes_cpu_temp_and_fan() -> None:
    r = requests.get(
        SALMON_BASE_URL + "/redfish/v1/Chassis/1/Thermal",
        timeout=10,
    )
    assert r.status_code == 200
    body = r.json()
    assert body["@odata.type"] == "#Thermal.v1_0_0.Thermal"
    temp_names = {t["Name"] for t in body["Temperatures"]}
    fan_names  = {f["Name"] for f in body["Fans"]}
    # CPU1 is "ok" in the mock so it should land in
    # Temperatures; CPU2 is "ns" (not specified) AND
    # its reading is "na" so the shim drops it.
    assert "CPU1 Temp" in temp_names
    assert "CPU2 Temp" not in temp_names
    assert "Fan1" in fan_names
    assert "Fan2" in fan_names

    cpu1 = next(t for t in body["Temperatures"]
                if t["Name"] == "CPU1 Temp")
    assert cpu1["ReadingCelsius"] == 35.0
    assert cpu1["Status"]["State"] == "Enabled"

    fan1 = next(f for f in body["Fans"]
                if f["Name"] == "Fan1")
    assert fan1["Reading"] == 4200.0
    assert fan1["ReadingUnits"] == "RPM"


# ---------------------------------------------------
# Power (Voltages + PowerSupplies through ipmitool mock)
# ---------------------------------------------------


def test_power_includes_voltages_and_psus() -> None:
    r = requests.get(
        SALMON_BASE_URL + "/redfish/v1/Chassis/1/Power",
        timeout=10,
    )
    assert r.status_code == 200
    body = r.json()
    assert body["@odata.type"] == "#Power.v1_0_0.Power"

    voltage_names = {v["Name"] for v in body["Voltages"]}
    assert "+12V" in voltage_names
    assert "+5V"  in voltage_names

    v12 = next(v for v in body["Voltages"]
               if v["Name"] == "+12V")
    assert v12["ReadingVolts"] == 12.18

    psu_names = {p["Name"] for p in body["PowerSupplies"]}
    # The PSU-detection rule is "Watts" unit AND name
    # contains PSU or starts with PS. Pin the rule.
    assert "PSU1 Input Power" in psu_names
    psu1 = next(p for p in body["PowerSupplies"]
                if p["Name"] == "PSU1 Input Power")
    assert psu1["PowerInputWatts"] == 240.0


# ---------------------------------------------------
# Response headers
# ---------------------------------------------------


def test_redfish_responses_are_application_json() -> None:
    """The Content-Type lives in
    template_content_type. All current endpoints set
    application/json (we could plausibly use
    application/redfish+json later; pin the current
    choice so a drift is loud)."""
    r = requests.get(
        SALMON_BASE_URL + "/redfish/v1/Systems/1", timeout=5,
    )
    assert r.status_code == 200
    assert "application/json" in r.headers.get(
        "content-type", "",
    )
