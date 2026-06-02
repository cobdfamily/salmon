# salmon -- url2code base image + ipmitool + the
# bin/ shims + the YAML that declares the Redfish
# surface. No Python source in this repo; the HTTP
# surface is entirely defined in config/tools.yaml
# which url2code reads on startup.
#
# Same shape as the other url2code-derived images
# (brl, needle, outofoffice, pandoc).

# Pinned to 2.1.0 (current engine GA). salmon's multi-host surface
# needs route path-parameter support (the `{id}` segment exposed to
# commands as `{id}` and to templates as `{request.id}`), which
# landed in url2code 1.7.0; 2.1.0 is a superset of that plus the
# response-templating salmon's Redfish surface has always relied on.
# 2.0.0 is a breaking engine release (subprocess timeout/cancellation
# edge-cases may shift) — certify against the e2e suite before tagging.
ARG URL2CODE_TAG=2.1.0
FROM kibble.apps.blindhub.ca/cobdfamily/url2code:${URL2CODE_TAG}

# ipmitool is the CLI every bin/ shim shells out to.
# Switch to root for the apt install, switch back to
# the unprivileged runtime user url2code already
# created for us.
USER root
RUN apt-get update -y \
 && apt-get install -y --no-install-recommends \
        ipmitool \
        ca-certificates \
 && rm -rf /var/lib/apt/lists/*

# Drop in the YAML + the shims. /app is the url2code
# working dir; tools.yaml at /app/config/tools.yaml
# is what URL2CODE_CONFIG defaults to (matches brl
# / needle / etc).
COPY config /app/config
COPY bin    /app/bin
RUN chmod +x /app/bin/*

# Hand back to the url2code base image's runtime
# user. The base image set its USER to url2code in
# the runtime stage; the apt step above flipped to
# root, so we flip back so the FastAPI process
# inherits the unprivileged identity. The bin/
# shims work as that user because /dev/ipmi0 (in-
# band mode) is opened via the host's device
# permissions, not the container user's.
USER url2code

# Sanity check the YAML at build time -- a malformed
# YAML would crash on first request; this fails the
# build instead. url2code's load_config() does full
# pydantic validation.
RUN python -c "from url2code.config import load_config; load_config('/app/config/tools.yaml')"
