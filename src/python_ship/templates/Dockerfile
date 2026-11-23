# Published by `ship init`; edit freely. `ship init --force` replaces it with the version
# matching the installed python-ship.
ARG PYTHON_VERSION=3.13
@ASSET_STAGE@
FROM python:${PYTHON_VERSION}-slim AS base
ARG PYTHON_VERSION
# The first patched release per Python branch, from the generated Compose file. A stale, locally
# cached base image fails here instead of building on a release with known vulnerabilities.
ARG SHIP_PYTHON_FLOOR=""
COPY ship/check-python-security-floor.py /usr/local/share/ship/check-python-security-floor.py
RUN SHIP_BASE_IMAGE="python:${PYTHON_VERSION}-slim" SHIP_PYTHON_FLOOR="${SHIP_PYTHON_FLOOR}" \
    python /usr/local/share/ship/check-python-security-floor.py
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app/src \
    VIRTUAL_ENV=/opt/ship-venv \
    PATH="/opt/ship-venv/bin:$PATH"
COPY ship/app-entrypoint.py /usr/local/bin/ship-app-entrypoint.py
ENTRYPOINT ["python", "/usr/local/bin/ship-app-entrypoint.py"]
WORKDIR /app

FROM base AS builder
ARG SHIP_SERVER_REQUIREMENT="uvicorn==0.54.0"
COPY --from=ghcr.io/astral-sh/uv:@UV_VERSION@ /uv /uvx /bin/
ENV UV_NO_CACHE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    UV_PROJECT_ENVIRONMENT=/opt/ship-venv
COPY . /app
@ASSET_COPY@
# Dependencies live outside /app, so the development bind mount cannot hide them.
RUN uv venv /opt/ship-venv
# A locked uv project keeps its lockfile authoritative; other projects use uv's pip interface.
RUN if [ -f uv.lock ] && [ -f pyproject.toml ]; then \
      uv sync --locked; \
    elif [ -f requirements.txt ]; then \
      uv pip install -r requirements.txt; \
    elif [ -f pyproject.toml ]; then \
      uv sync; \
    fi
# Ship supplies the selected ASGI server even when the app declares no dependencies.
RUN uv pip install "$SHIP_SERVER_REQUIREMENT"

# Prune development groups for the production runtime after the full development install.
FROM builder AS production-dependencies
RUN if [ -f uv.lock ] && [ -f pyproject.toml ]; then uv sync --locked --no-dev; \
    elif [ -f pyproject.toml ]; then uv sync --no-dev; fi
RUN uv pip install "$SHIP_SERVER_REQUIREMENT"

# Production: the installed app and server only. No uv, no pip, no source bind mount, not root.
FROM base AS production
COPY --from=production-dependencies /opt/ship-venv /opt/ship-venv
COPY --from=builder --chown=10001:10001 /app /app
# The app may write under /app at boot (collectstatic, caches); the venv stays read-only.
RUN chown 10001:10001 /app \
    && /usr/local/bin/python -m pip uninstall --yes --quiet pip setuptools wheel
USER 10001:10001
EXPOSE 8000

FROM builder AS development
EXPOSE 8000
