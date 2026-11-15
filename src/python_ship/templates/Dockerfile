ARG PYTHON_VERSION=3.13
FROM python:${PYTHON_VERSION}-slim AS builder
ARG SHIP_SERVER_REQUIREMENT="uvicorn>=0.53,<1"
COPY --from=ghcr.io/astral-sh/uv:@UV_VERSION@ /uv /uvx /bin/

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_NO_CACHE=1 \
    UV_PROJECT_ENVIRONMENT=/opt/ship-venv \
    VIRTUAL_ENV=/opt/ship-venv \
    PATH="/opt/ship-venv/bin:$PATH"

WORKDIR /app
COPY . /app

# Keep dependencies outside /app so the development bind mount cannot hide them.
RUN uv venv /opt/ship-venv

# A uv project keeps its lockfile authoritative. Other projects use uv's pip interface.
RUN if [ -f uv.lock ] && [ -f pyproject.toml ]; then \
      uv sync --locked --no-dev; \
    elif [ -f requirements.txt ]; then \
      uv pip install -r requirements.txt; \
    elif [ -f pyproject.toml ]; then \
      uv sync --no-dev; \
    fi

# Ship supplies the selected ASGI server even when the app has no dependencies.
RUN uv pip install "$SHIP_SERVER_REQUIREMENT"

EXPOSE 8000

FROM python:${PYTHON_VERSION}-slim AS production
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    VIRTUAL_ENV=/opt/ship-venv \
    PATH="/opt/ship-venv/bin:$PATH"
WORKDIR /app
COPY --from=builder /opt/ship-venv /opt/ship-venv
COPY --from=builder /app /app
RUN /usr/local/bin/python -m pip uninstall --yes pip setuptools wheel
RUN chown -R 10001:10001 /app /opt/ship-venv
USER 10001:10001
EXPOSE 8000

# Keep the default build target compatible with existing development commands.
FROM builder AS development
