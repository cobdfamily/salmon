"""salmon -- Redfish-flavoured HTTP facade for legacy IPMI BMCs via ipmitool.

The HTTP surface lives at /redfish/v1/. Liveness `/` and FastAPI's
`/docs` / `/redoc` stay at the root.

Single-host scope for v0.1.0: salmon represents one BMC (either the
local /dev/ipmi0 or a remote host configured via env vars). Multi-
host orchestration is deferred.
"""

__version__ = "0.1.0"
