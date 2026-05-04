"""salmon FastAPI app.

The HTTP surface is a Redfish-flavoured subset:

  /                                                  liveness (non-Redfish)
  /redfish                                           Redfish version document
  /redfish/v1/                                       ServiceRoot
  /redfish/v1/odata                                  OData service document
  /redfish/v1/Systems                                ComputerSystemCollection
  /redfish/v1/Systems/<id>                           ComputerSystem
  /redfish/v1/Systems/<id>/Actions/
                  ComputerSystem.Reset               POST: power actions
  /redfish/v1/Chassis                                ChassisCollection
  /redfish/v1/Chassis/<id>                           Chassis
  /redfish/v1/Chassis/<id>/Power                     Power (Voltages,
                                                     PowerSupplies)
  /redfish/v1/Chassis/<id>/Thermal                   Thermal (Temperatures,
                                                     Fans)

The id used for both Systems/<id> and Chassis/<id> is configured
via SALMON_SYSTEM_ID (default "1"). salmon represents one BMC; the
Systems and Chassis collections always have exactly one member in
v0.1.0.

Liveness `/` reports {service: salmon, status: ok, version: <ver>}
and intentionally lives at the root, NOT under /redfish/v1/. Keeps
load-balancer / monitoring probes off the Redfish path.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse

from . import __version__
from .config import Config, load
from .ipmi import Ipmi, IpmiError

logger = logging.getLogger("salmon")


# Map Redfish ResetType action values -> ipmitool subcommand.
# Spec values: On, ForceOff, GracefulShutdown, GracefulRestart,
# ForceRestart, Nmi, ForceOn, PushPowerButton, PowerCycle.
# We support the ones that map cleanly onto ipmitool.
RESET_TYPE_TO_IPMI = {
    "On":                "on",
    "ForceOn":           "on",
    "ForceOff":          "off",
    "GracefulShutdown":  "soft",
    "GracefulRestart":   "soft",  # ipmitool has no graceful-reset;
                                  # closest is soft (ACPI) which
                                  # most BMCs interpret as restart
                                  # if power was on.
    "ForceRestart":      "reset",
    "PowerCycle":        "cycle",
    "Nmi":               "diag",
}


def create_app(config: Config | None = None) -> FastAPI:
    config = config or load()
    ipmi = Ipmi(config)

    app = FastAPI(
        title="salmon",
        description=(
            "Redfish-flavoured HTTP facade for legacy IPMI BMCs via "
            "ipmitool. Single-host scope: salmon represents one BMC."
        ),
        version=__version__,
        docs_url="/docs",
        redoc_url="/redoc",
    )

    # ----- liveness --------------------------------------------------

    @app.get("/", tags=["Health"])
    async def liveness() -> dict[str, str]:
        return {
            "service": "salmon",
            "status": "ok",
            "version": __version__,
        }

    # ----- Redfish version document ----------------------------------

    @app.get("/redfish", tags=["Redfish"])
    async def redfish_versions() -> dict[str, str]:
        """Per Redfish spec the bare /redfish reports the protocol
        versions the service implements. v1 is the only one we do."""
        return {"v1": "/redfish/v1/"}

    # ----- ServiceRoot -----------------------------------------------

    @app.get("/redfish/v1", tags=["Redfish"])
    @app.get("/redfish/v1/", tags=["Redfish"])
    async def service_root() -> dict[str, Any]:
        return {
            "@odata.id": "/redfish/v1/",
            "@odata.type": "#ServiceRoot.v1_15_0.ServiceRoot",
            "Id": "RootService",
            "Name": "salmon ServiceRoot",
            "RedfishVersion": "1.15.0",
            "UUID": "00000000-0000-0000-0000-000000000001",
            "Systems": {"@odata.id": "/redfish/v1/Systems"},
            "Chassis": {"@odata.id": "/redfish/v1/Chassis"},
            "Links": {},
            "Oem": {
                "Salmon": {
                    "ServiceVersion": __version__,
                    "Implementation": "ipmitool-backed",
                },
            },
        }

    @app.get("/redfish/v1/odata", tags=["Redfish"])
    async def odata_service_doc() -> dict[str, Any]:
        return {
            "@odata.context": "/redfish/v1/$metadata",
            "value": [
                {"name": "Service", "kind": "Singleton",
                 "url": "/redfish/v1/"},
                {"name": "Systems", "kind": "Singleton",
                 "url": "/redfish/v1/Systems"},
                {"name": "Chassis", "kind": "Singleton",
                 "url": "/redfish/v1/Chassis"},
            ],
        }

    # ----- Systems collection + member -------------------------------

    @app.get("/redfish/v1/Systems", tags=["Redfish"])
    async def systems_collection() -> dict[str, Any]:
        return {
            "@odata.id": "/redfish/v1/Systems",
            "@odata.type": "#ComputerSystemCollection.ComputerSystemCollection",
            "Name": "Computer System Collection",
            "Members@odata.count": 1,
            "Members": [
                {"@odata.id": f"/redfish/v1/Systems/{config.system_id}"},
            ],
        }

    async def _power_state() -> str:
        """Map ipmitool's "on"/"off" to Redfish's "On"/"Off".
        Redfish allows additional values (Paused, ...) we don't
        emit because IPMI doesn't surface them."""
        try:
            state = await ipmi.power_status()
        except IpmiError as exc:
            logger.warning("power_status failed: %s", exc)
            return "Unknown"
        return "On" if state == "on" else "Off"

    @app.get("/redfish/v1/Systems/{sid}", tags=["Redfish"])
    async def system(sid: str) -> dict[str, Any]:
        if sid != config.system_id:
            raise HTTPException(status_code=404, detail="No such system.")
        return {
            "@odata.id": f"/redfish/v1/Systems/{config.system_id}",
            "@odata.type": "#ComputerSystem.v1_20_0.ComputerSystem",
            "Id": config.system_id,
            "Name": config.system_name,
            "SystemType": "Physical",
            "PowerState": await _power_state(),
            "Status": {"State": "Enabled", "Health": "OK"},
            "Links": {
                "Chassis": [
                    {"@odata.id": f"/redfish/v1/Chassis/{config.system_id}"},
                ],
            },
            "Actions": {
                "#ComputerSystem.Reset": {
                    "target": (
                        f"/redfish/v1/Systems/{config.system_id}"
                        "/Actions/ComputerSystem.Reset"
                    ),
                    "ResetType@Redfish.AllowableValues": sorted(RESET_TYPE_TO_IPMI),
                },
            },
        }

    @app.post(
        "/redfish/v1/Systems/{sid}/Actions/ComputerSystem.Reset",
        tags=["Redfish"],
        status_code=204,
    )
    async def reset(sid: str, body: dict[str, Any]) -> JSONResponse:
        if sid != config.system_id:
            raise HTTPException(status_code=404, detail="No such system.")
        reset_type = body.get("ResetType")
        if reset_type not in RESET_TYPE_TO_IPMI:
            raise HTTPException(
                status_code=400,
                detail=(
                    f"Unsupported ResetType {reset_type!r}; "
                    f"allowed: {sorted(RESET_TYPE_TO_IPMI)}"
                ),
            )
        action = RESET_TYPE_TO_IPMI[reset_type]
        try:
            await ipmi.power_action(action)
        except IpmiError as exc:
            raise HTTPException(
                status_code=502,
                detail=f"ipmitool error: {exc.stderr or str(exc)}",
            ) from exc
        return JSONResponse(status_code=204, content=None)

    # ----- Chassis collection + member -------------------------------

    @app.get("/redfish/v1/Chassis", tags=["Redfish"])
    async def chassis_collection() -> dict[str, Any]:
        return {
            "@odata.id": "/redfish/v1/Chassis",
            "@odata.type": "#ChassisCollection.ChassisCollection",
            "Name": "Chassis Collection",
            "Members@odata.count": 1,
            "Members": [
                {"@odata.id": f"/redfish/v1/Chassis/{config.system_id}"},
            ],
        }

    @app.get("/redfish/v1/Chassis/{cid}", tags=["Redfish"])
    async def chassis(cid: str) -> dict[str, Any]:
        if cid != config.system_id:
            raise HTTPException(status_code=404, detail="No such chassis.")
        return {
            "@odata.id": f"/redfish/v1/Chassis/{config.system_id}",
            "@odata.type": "#Chassis.v1_25_0.Chassis",
            "Id": config.system_id,
            "Name": config.system_name,
            "ChassisType": "RackMount",
            "Status": {"State": "Enabled", "Health": "OK"},
            "Power": {"@odata.id": f"/redfish/v1/Chassis/{config.system_id}/Power"},
            "Thermal": {"@odata.id": f"/redfish/v1/Chassis/{config.system_id}/Thermal"},
            "Links": {
                "ComputerSystems": [
                    {"@odata.id": f"/redfish/v1/Systems/{config.system_id}"},
                ],
            },
        }

    # ----- Sensors: Power (voltages, supplies) + Thermal (temps,fans)

    def _row_to_redfish(row: dict[str, str], member_id: int, kind: str) -> dict[str, Any]:
        """Map a parsed ipmitool sensor row into a Redfish reading.
        kind drives which Reading* / status fields apply."""
        name = row.get("name", "").strip() or f"Sensor{member_id}"
        raw_value = row.get("value", "").strip()
        try:
            reading: float | None = float(raw_value)
        except ValueError:
            reading = None
        status_text = (row.get("status", "") or "").strip().lower()
        # ipmitool status: "ok" | "ns" (no reading) | "nc" / "cr" /
        # "nr" (warning / critical / non-recoverable). Map to
        # Redfish Health values.
        if status_text == "ok":
            health = "OK"
        elif status_text in {"nc", "lnc", "unc"}:
            health = "Warning"
        elif status_text in {"cr", "lcr", "ucr", "nr", "lnr", "unr"}:
            health = "Critical"
        else:
            health = "OK" if reading is not None else "Unknown"
        out: dict[str, Any] = {
            "MemberId": str(member_id),
            "Name": name,
            "Status": {
                "State": "Enabled" if reading is not None else "Absent",
                "Health": health,
            },
        }
        if kind == "voltage":
            out["ReadingVolts"] = reading
        elif kind == "temperature":
            out["ReadingCelsius"] = reading
        elif kind == "fan":
            out["Reading"] = reading
            out["ReadingUnits"] = "RPM"
        return out

    @app.get("/redfish/v1/Chassis/{cid}/Power", tags=["Redfish"])
    async def power(cid: str) -> dict[str, Any]:
        if cid != config.system_id:
            raise HTTPException(status_code=404, detail="No such chassis.")
        try:
            rows = await ipmi.sensors()
        except IpmiError as exc:
            logger.warning("sensors failed: %s", exc)
            rows = []
        voltages: list[dict[str, Any]] = []
        for i, row in enumerate(rows):
            units = (row.get("units", "") or "").lower()
            if "volt" in units:
                voltages.append(_row_to_redfish(row, len(voltages), "voltage"))
        return {
            "@odata.id": f"/redfish/v1/Chassis/{config.system_id}/Power",
            "@odata.type": "#Power.v1_7_1.Power",
            "Id": "Power",
            "Name": "Power",
            "Voltages": voltages,
            "PowerSupplies": [],   # IPMI doesn't surface PSU
                                   # composition reliably; leave
                                   # empty until SDR FRU parsing
                                   # lands.
        }

    @app.get("/redfish/v1/Chassis/{cid}/Thermal", tags=["Redfish"])
    async def thermal(cid: str) -> dict[str, Any]:
        if cid != config.system_id:
            raise HTTPException(status_code=404, detail="No such chassis.")
        try:
            rows = await ipmi.sensors()
        except IpmiError as exc:
            logger.warning("sensors failed: %s", exc)
            rows = []
        temperatures: list[dict[str, Any]] = []
        fans: list[dict[str, Any]] = []
        for row in rows:
            units = (row.get("units", "") or "").lower()
            if "degrees" in units or "celsius" in units or units == "degrees c":
                temperatures.append(_row_to_redfish(row, len(temperatures), "temperature"))
            elif "rpm" in units:
                fans.append(_row_to_redfish(row, len(fans), "fan"))
        return {
            "@odata.id": f"/redfish/v1/Chassis/{config.system_id}/Thermal",
            "@odata.type": "#Thermal.v1_7_0.Thermal",
            "Id": "Thermal",
            "Name": "Thermal",
            "Temperatures": temperatures,
            "Fans": fans,
        }

    return app


# Module-level app for `uvicorn salmon.main:app`.
app = create_app()


def run() -> None:
    """Entry point for `salmon` console script."""
    import uvicorn
    uvicorn.run(
        "salmon.main:app",
        host="0.0.0.0",
        port=8000,
        log_level="info",
    )
