# syntax=docker/dockerfile:1.7
# moreradicale - CalDAV/CardDAV server (extended fork of Radicale)

# ---- Builder stage ----
FROM ghcr.io/astral-sh/uv:0.9-python3.14-bookworm-slim AS builder

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    UV_PROJECT_ENVIRONMENT=/app/.venv

WORKDIR /app

# Copy project files needed for install
COPY pyproject.toml README.md ./
COPY moreradicale/ ./moreradicale/

# Build venv with bcrypt extra for password hashing
RUN --mount=type=cache,target=/root/.cache/uv \
    uv venv /app/.venv && \
    uv pip install --no-cache .[bcrypt]

# ---- Runtime stage ----
FROM python:3.14-slim-bookworm

# curl for healthcheck; tini for proper signal handling
RUN apt-get update && apt-get install -y --no-install-recommends \
        curl \
        tini \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --system --gid 1000 moreradicale \
    && useradd --system --uid 1000 --gid 1000 \
        --home-dir /var/lib/moreradicale \
        --shell /bin/false \
        moreradicale \
    && mkdir -p /var/lib/moreradicale/collections /etc/moreradicale \
    && chown -R moreradicale:moreradicale /var/lib/moreradicale /etc/moreradicale

# Copy venv from builder
COPY --from=builder --chown=moreradicale:moreradicale /app/.venv /app/.venv

ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

USER moreradicale
WORKDIR /var/lib/moreradicale

EXPOSE 5232

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD curl -fsS http://127.0.0.1:5232/.web/ -o /dev/null || exit 1

VOLUME ["/var/lib/moreradicale/collections"]

ENTRYPOINT ["/usr/bin/tini", "--", "moreradicale"]
CMD ["--config", "/etc/moreradicale/config"]
