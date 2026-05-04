"""Structural tests for salmon's Redfish surface.

Stubs out the Ipmi class so tests don't shell out to a real
ipmitool. Verifies the JSON shape of every endpoint --
@odata.id / @odata.type, link references, ResetType
mapping, sensor breakouts, error paths.
"""

from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient

from salmon.config import Config
from salmon.ipmi import Ipmi, IpmiError
from salmon.main import RESET_TYPE_TO_IPMI, create_app


# Sample ipmitool sensor-list rows (the real format is
# pipe-delimited; tests feed the parsed output via the
# stub directly to avoid duplicating the parser).
SAMPLE_SENSORS = [
    {"name": "CPU Temp",   "value": "42",   "units": "degrees C", "status": "ok",
     "lower_nr": "0", "lower_c": "5", "lower_nc": "10",
     "upper_nc": "85", "upper_c": "90", "upper_nr": "95"},
    {"name": "Fan1",       "value": "3200", "units": "RPM",       "status": "ok",
     "lower_nr": "0", "lower_c": "300", "lower_nc": "500",
     "upper_nc": "0", "upper_c": "0", "upper_nr": "0"},
    {"name": "12V Rail",   "value": "12.05","units": "Volts",     "status": "ok",
     "lower_nr": "10", "lower_c": "11", "lower_nc": "11.5",
     "upper_nc": "12.5", "upper_c": "13", "upper_nr": "14"},
    {"name": "PSU Status", "value": "0x01", "units": "discrete",  "status": "ok",
     "lower_nr": "", "lower_c": "", "lower_nc": "",
     "upper_nc": "", "upper_c": "", "upper_nr": ""},
    {"name": "Bad Sensor", "value": "na",   "units": "degrees C", "status": "ns",
     "lower_nr": "", "lower_c": "", "lower_nc": "",
     "upper_nc": "", "upper_c": "", "upper_nr": ""},
]


class StubIpmi(Ipmi):
    """In-memory stub that bypasses subprocess + cache. Tests
    drop into the route handlers' Ipmi dependency by replacing
    the instance the app holds."""

    def __init__(
        self,
        *,
        power: str = "on",
        sensors: list[dict[str, str]] | None = None,
        action_error: IpmiError | None = None,
    ) -> None:
        # Don't call super; we don't need a real Config.
        self._power = power
        self._sensors = sensors if sensors is not None else SAMPLE_SENSORS
        self._action_error = action_error
        self.actions_called: list[str] = []

    async def power_status(self) -> str:
        return self._power

    async def power_action(self, action: str) -> None:
        self.actions_called.append(action)
        if self._action_error is not None:
            raise self._action_error

    async def sensors(self) -> list[dict[str, str]]:
        return list(self._sensors)


@pytest.fixture
def stub_ipmi() -> StubIpmi:
    return StubIpmi()


@pytest.fixture
def app(stub_ipmi: StubIpmi):
    """A fresh app with a stubbed Ipmi. We rebuild create_app
    per test so its closure picks up our stub instead of a real
    Ipmi initialised from process env."""
    cfg = Config(
        system_id="1",
        system_name="test-system",
        bmc_host=None,
        bmc_user=None,
        bmc_password=None,
        bmc_interface="open",
        cache_ttl_seconds=0,
    )

    # create_app instantiates its own Ipmi; we monkey-patch the
    # Ipmi constructor briefly so the route closures capture our
    # stub instead.
    import salmon.main as main_mod
    original_ipmi = main_mod.Ipmi
    main_mod.Ipmi = lambda _config: stub_ipmi  # type: ignore[assignment]
    try:
        app_instance = create_app(cfg)
    finally:
        main_mod.Ipmi = original_ipmi
    return app_instance


@pytest.fixture
def client(app):
    return TestClient(app)


# ---------------------------------------------------------------------------
# liveness
# ---------------------------------------------------------------------------


def test_liveness_at_root(client: TestClient) -> None:
    r = client.get("/")
    assert r.status_code == 200
    body = r.json()
    assert body["service"] == "salmon"
    assert body["status"] == "ok"
    assert body["version"]


def test_redfish_version_document(client: TestClient) -> None:
    r = client.get("/redfish")
    assert r.status_code == 200
    assert r.json() == {"v1": "/redfish/v1/"}


# ---------------------------------------------------------------------------
# ServiceRoot
# ---------------------------------------------------------------------------


def test_service_root_shape(client: TestClient) -> None:
    r = client.get("/redfish/v1/")
    assert r.status_code == 200
    body = r.json()
    assert body["@odata.id"] == "/redfish/v1/"
    assert body["@odata.type"].startswith("#ServiceRoot.")
    assert body["RedfishVersion"]
    assert body["Systems"] == {"@odata.id": "/redfish/v1/Systems"}
    assert body["Chassis"] == {"@odata.id": "/redfish/v1/Chassis"}


def test_service_root_without_trailing_slash(client: TestClient) -> None:
    r = client.get("/redfish/v1")
    assert r.status_code == 200
    assert r.json()["@odata.id"] == "/redfish/v1/"


# ---------------------------------------------------------------------------
# Systems
# ---------------------------------------------------------------------------


def test_systems_collection(client: TestClient) -> None:
    r = client.get("/redfish/v1/Systems")
    assert r.status_code == 200
    body = r.json()
    assert body["@odata.id"] == "/redfish/v1/Systems"
    assert body["Members@odata.count"] == 1
    assert body["Members"] == [{"@odata.id": "/redfish/v1/Systems/1"}]


def test_system_member_powered_on(client: TestClient) -> None:
    r = client.get("/redfish/v1/Systems/1")
    assert r.status_code == 200
    body = r.json()
    assert body["Id"] == "1"
    assert body["PowerState"] == "On"
    assert body["@odata.type"].startswith("#ComputerSystem.")
    # Reset action declared with the full set of allowable values.
    reset_action = body["Actions"]["#ComputerSystem.Reset"]
    assert reset_action["target"].endswith("/Actions/ComputerSystem.Reset")
    assert set(reset_action["ResetType@Redfish.AllowableValues"]) == set(RESET_TYPE_TO_IPMI)


def test_system_member_powered_off(stub_ipmi: StubIpmi, client: TestClient) -> None:
    stub_ipmi._power = "off"
    r = client.get("/redfish/v1/Systems/1")
    assert r.status_code == 200
    assert r.json()["PowerState"] == "Off"


def test_system_member_404_on_unknown_id(client: TestClient) -> None:
    r = client.get("/redfish/v1/Systems/nope")
    assert r.status_code == 404


# ---------------------------------------------------------------------------
# Reset action
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "reset_type, expected",
    sorted(RESET_TYPE_TO_IPMI.items()),
)
def test_reset_maps_each_resettype_to_ipmitool(
    stub_ipmi: StubIpmi, client: TestClient,
    reset_type: str, expected: str,
) -> None:
    """Every documented ResetType drives the matching ipmitool
    subcommand. Drift in this mapping silently sends the wrong
    action -- catch it here."""
    stub_ipmi.actions_called.clear()
    r = client.post(
        "/redfish/v1/Systems/1/Actions/ComputerSystem.Reset",
        json={"ResetType": reset_type},
    )
    assert r.status_code == 204
    assert stub_ipmi.actions_called == [expected]


def test_reset_rejects_unknown_resettype(client: TestClient) -> None:
    r = client.post(
        "/redfish/v1/Systems/1/Actions/ComputerSystem.Reset",
        json={"ResetType": "NopeNotARealValue"},
    )
    assert r.status_code == 400


def test_reset_404_on_unknown_id(client: TestClient) -> None:
    r = client.post(
        "/redfish/v1/Systems/nope/Actions/ComputerSystem.Reset",
        json={"ResetType": "On"},
    )
    assert r.status_code == 404


def test_reset_502_on_ipmi_error(stub_ipmi: StubIpmi, client: TestClient) -> None:
    stub_ipmi._action_error = IpmiError("BMC offline", 1, "no route to host")
    r = client.post(
        "/redfish/v1/Systems/1/Actions/ComputerSystem.Reset",
        json={"ResetType": "On"},
    )
    assert r.status_code == 502
    # Caller sees ipmitool's stderr in the body for diagnosis.
    assert "no route to host" in r.json()["detail"]


# ---------------------------------------------------------------------------
# Chassis
# ---------------------------------------------------------------------------


def test_chassis_collection(client: TestClient) -> None:
    r = client.get("/redfish/v1/Chassis")
    assert r.status_code == 200
    body = r.json()
    assert body["Members@odata.count"] == 1
    assert body["Members"] == [{"@odata.id": "/redfish/v1/Chassis/1"}]


def test_chassis_member(client: TestClient) -> None:
    r = client.get("/redfish/v1/Chassis/1")
    assert r.status_code == 200
    body = r.json()
    assert body["Id"] == "1"
    assert body["Power"] == {"@odata.id": "/redfish/v1/Chassis/1/Power"}
    assert body["Thermal"] == {"@odata.id": "/redfish/v1/Chassis/1/Thermal"}
    # Linked-Systems back-reference.
    assert body["Links"]["ComputerSystems"] == [
        {"@odata.id": "/redfish/v1/Systems/1"},
    ]


# ---------------------------------------------------------------------------
# Power resource (Voltages from sensor list)
# ---------------------------------------------------------------------------


def test_power_resource_voltages(client: TestClient) -> None:
    r = client.get("/redfish/v1/Chassis/1/Power")
    assert r.status_code == 200
    body = r.json()
    # Only the Volt-units row from SAMPLE_SENSORS appears.
    assert len(body["Voltages"]) == 1
    v = body["Voltages"][0]
    assert v["Name"] == "12V Rail"
    assert v["ReadingVolts"] == pytest.approx(12.05)
    assert v["Status"]["Health"] == "OK"


# ---------------------------------------------------------------------------
# Thermal resource (Temperatures + Fans)
# ---------------------------------------------------------------------------


def test_thermal_resource_temperatures_and_fans(client: TestClient) -> None:
    r = client.get("/redfish/v1/Chassis/1/Thermal")
    assert r.status_code == 200
    body = r.json()
    # CPU Temp + Bad Sensor (also "degrees C") -> 2 temperatures.
    # Bad Sensor has unparseable value, so its reading is None.
    assert len(body["Temperatures"]) == 2
    cpu = next(t for t in body["Temperatures"] if t["Name"] == "CPU Temp")
    assert cpu["ReadingCelsius"] == pytest.approx(42.0)
    assert cpu["Status"]["Health"] == "OK"

    bad = next(t for t in body["Temperatures"] if t["Name"] == "Bad Sensor")
    assert bad["ReadingCelsius"] is None
    assert bad["Status"]["State"] == "Absent"

    # Fan1 -> one fan in RPM units.
    assert len(body["Fans"]) == 1
    fan = body["Fans"][0]
    assert fan["Name"] == "Fan1"
    assert fan["Reading"] == pytest.approx(3200.0)
    assert fan["ReadingUnits"] == "RPM"


def test_power_and_thermal_404_on_unknown_chassis(client: TestClient) -> None:
    assert client.get("/redfish/v1/Chassis/nope/Power").status_code == 404
    assert client.get("/redfish/v1/Chassis/nope/Thermal").status_code == 404


# ---------------------------------------------------------------------------
# Resilience: ipmitool failures degrade gracefully on read paths
# ---------------------------------------------------------------------------


def test_power_state_unknown_when_ipmi_fails(
    stub_ipmi: StubIpmi, client: TestClient,
) -> None:
    """If ipmitool errors on power_status, the System's
    PowerState becomes "Unknown" rather than 502 -- read-only
    Redfish endpoints should be resilient to BMC blips."""

    async def failing(self_: Any) -> str:
        raise IpmiError("BMC unreachable", 1, "timeout")

    stub_ipmi.power_status = failing.__get__(stub_ipmi)  # type: ignore[method-assign]
    r = client.get("/redfish/v1/Systems/1")
    assert r.status_code == 200
    assert r.json()["PowerState"] == "Unknown"


def test_thermal_resource_empty_when_ipmi_fails(
    stub_ipmi: StubIpmi, client: TestClient,
) -> None:
    async def failing(self_: Any) -> list[dict[str, str]]:
        raise IpmiError("BMC unreachable", 1, "timeout")

    stub_ipmi.sensors = failing.__get__(stub_ipmi)  # type: ignore[method-assign]
    r = client.get("/redfish/v1/Chassis/1/Thermal")
    assert r.status_code == 200
    body = r.json()
    assert body["Temperatures"] == []
    assert body["Fans"] == []
