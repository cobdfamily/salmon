"""End-to-end tests for salmon.

Boots the salmon docker image (no real BMC, no /dev/ipmi0
passthrough) and exercises the Redfish surface over HTTP.
The container's ipmitool calls all fail (no BMC reachable),
which is exactly the path we want to verify -- salmon should
degrade gracefully on read paths (PowerState: Unknown, empty
sensor arrays) and return 502 on action paths.

Run locally:
    docker run -d --name salmon-e2e -p 8000:8000 \\
      kibble.apps.blindhub.ca/cobdfamily/salmon:latest
    SALMON_BASE_URL=http://localhost:8000 pytest tests/test_e2e.py

CI builds the image fresh, boots it, and runs this suite.
See .github/workflows/test.yml.
"""

from __future__ import annotations

import os
from typing import Any

import pytest
import requests

SALMON_BASE_URL = os.environ.get(
    "SALMON_BASE_URL", "http://localhost:8000",
)


# ---------------------------------------------------------------------------
# liveness
# ---------------------------------------------------------------------------


def test_liveness_returns_salmon_service() -> None:
    r = requests.get(SALMON_BASE_URL + "/", timeout=5)
    assert r.status_code == 200
    body = r.json()
    assert body["service"] == "salmon"
    assert body["status"] == "ok"
    assert body["version"]


def test_redfish_version_document() -> None:
    r = requests.get(SALMON_BASE_URL + "/redfish", timeout=5)
    assert r.status_code == 200
    assert r.json() == {"v1": "/redfish/v1/"}


# ---------------------------------------------------------------------------
# ServiceRoot
# ---------------------------------------------------------------------------


def test_service_root_shape() -> None:
    r = requests.get(SALMON_BASE_URL + "/redfish/v1/", timeout=5)
    assert r.status_code == 200
    body = r.json()
    assert body["@odata.id"] == "/redfish/v1/"
    assert body["@odata.type"].startswith("#ServiceRoot.")
    assert body["RedfishVersion"]
    assert body["Systems"] == {"@odata.id": "/redfish/v1/Systems"}
    assert body["Chassis"] == {"@odata.id": "/redfish/v1/Chassis"}


def test_service_root_works_without_trailing_slash() -> None:
    r = requests.get(SALMON_BASE_URL + "/redfish/v1", timeout=5)
    assert r.status_code == 200


# ---------------------------------------------------------------------------
# Systems
# ---------------------------------------------------------------------------


def test_systems_collection() -> None:
    r = requests.get(SALMON_BASE_URL + "/redfish/v1/Systems", timeout=5)
    assert r.status_code == 200
    body = r.json()
    assert body["Members@odata.count"] == 1
    assert body["Members"] == [{"@odata.id": "/redfish/v1/Systems/1"}]


def test_system_member_degrades_to_unknown_without_bmc() -> None:
    """No /dev/ipmi0 passthrough in this test environment ->
    ipmitool fails -> salmon degrades to PowerState:Unknown
    rather than 5xx-ing the read path. This is the resilience
    contract for read endpoints."""
    r = requests.get(SALMON_BASE_URL + "/redfish/v1/Systems/1", timeout=10)
    assert r.status_code == 200
    body = r.json()
    assert body["Id"] == "1"
    assert body["PowerState"] == "Unknown"


def test_system_member_404_on_unknown_id() -> None:
    r = requests.get(SALMON_BASE_URL + "/redfish/v1/Systems/nope", timeout=5)
    assert r.status_code == 404


# ---------------------------------------------------------------------------
# Reset action -- 502 on ipmi failure (no BMC)
# ---------------------------------------------------------------------------


def test_reset_502_on_ipmi_unreachable() -> None:
    """Action endpoints don't degrade gracefully -- if the
    caller asked for a power-state change and we can't make
    it, the right move is a hard 502 with the ipmitool
    stderr surfacing the cause, not a fake success."""
    r = requests.post(
        SALMON_BASE_URL + "/redfish/v1/Systems/1/Actions/ComputerSystem.Reset",
        json={"ResetType": "On"},
        timeout=10,
    )
    assert r.status_code == 502
    body = r.json()
    assert "detail" in body
    # The detail field carries the captured ipmitool stderr
    # so callers can diagnose connection-level issues.
    assert isinstance(body["detail"], str) and body["detail"]


def test_reset_400_on_unknown_resettype() -> None:
    """Unknown ResetType is rejected at the request-validation
    layer before ipmitool ever runs -- 400, not 502."""
    r = requests.post(
        SALMON_BASE_URL + "/redfish/v1/Systems/1/Actions/ComputerSystem.Reset",
        json={"ResetType": "Teleport"},
        timeout=5,
    )
    assert r.status_code == 400


# ---------------------------------------------------------------------------
# Chassis
# ---------------------------------------------------------------------------


def test_chassis_collection() -> None:
    r = requests.get(SALMON_BASE_URL + "/redfish/v1/Chassis", timeout=5)
    assert r.status_code == 200
    body = r.json()
    assert body["Members@odata.count"] == 1


def test_chassis_member() -> None:
    r = requests.get(SALMON_BASE_URL + "/redfish/v1/Chassis/1", timeout=5)
    assert r.status_code == 200
    body = r.json()
    assert body["Id"] == "1"
    assert body["Power"] == {"@odata.id": "/redfish/v1/Chassis/1/Power"}
    assert body["Thermal"] == {"@odata.id": "/redfish/v1/Chassis/1/Thermal"}


def test_power_resource_empty_when_ipmi_unreachable() -> None:
    """Read-path resilience: ipmitool sensor list fails ->
    Voltages array is empty, NOT a 5xx."""
    r = requests.get(SALMON_BASE_URL + "/redfish/v1/Chassis/1/Power", timeout=10)
    assert r.status_code == 200
    body = r.json()
    assert body["Voltages"] == []
    assert body["PowerSupplies"] == []


def test_thermal_resource_empty_when_ipmi_unreachable() -> None:
    r = requests.get(SALMON_BASE_URL + "/redfish/v1/Chassis/1/Thermal", timeout=10)
    assert r.status_code == 200
    body = r.json()
    assert body["Temperatures"] == []
    assert body["Fans"] == []


# ---------------------------------------------------------------------------
# OpenAPI / docs -- FastAPI provides these by default; verify
# they're reachable so consumers can introspect.
# ---------------------------------------------------------------------------


def test_openapi_json_reachable() -> None:
    r = requests.get(SALMON_BASE_URL + "/openapi.json", timeout=5)
    assert r.status_code == 200
    body = r.json()
    assert body["info"]["title"] == "salmon"


def test_swagger_docs_reachable() -> None:
    r = requests.get(SALMON_BASE_URL + "/docs", timeout=5)
    assert r.status_code == 200
