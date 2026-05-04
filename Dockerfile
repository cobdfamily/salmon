# Two-stage build: uv builds the venv from the lockfile;
# runtime image is python:3.12-slim with the venv copied in
# plus ipmitool installed via apt.

FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim AS builder

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/app/.venv

WORKDIR /app

COPY pyproject.toml uv.lock README.md ./
RUN uv sync --frozen --no-install-project --no-default-groups

COPY src ./src
RUN uv sync --frozen --no-default-groups


FROM python:3.12-slim AS runtime

# ipmitool is the CLI salmon shells out to. The apt package
# pulls in the userland tool only -- no kernel modules
# (those are on the host). For in-band usage the host needs
# ipmi_devintf loaded; for remote usage neither host nor
# container need the kernel side.
RUN apt-get update -y \
 && apt-get install -y --no-install-recommends \
        ipmitool \
        ca-certificates \
 && rm -rf /var/lib/apt/lists/*

RUN groupadd --system --gid 1000 salmon \
 && useradd --system --uid 1000 --gid 1000 \
        --home /app --shell /sbin/nologin salmon

WORKDIR /app
COPY --from=builder --chown=salmon:salmon /app /app

ENV PATH="/app/.venv/bin:${PATH}" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

USER salmon
EXPOSE 8000

CMD ["uvicorn", "salmon.main:app", "--host", "0.0.0.0", "--port", "8000"]
