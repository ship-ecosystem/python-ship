"""Built-in web servers. Server choice is independent of the framework adapter,
but has to match the app's interface: ASGI or WSGI."""

from __future__ import annotations

from .contracts import Interface

# Exact pins keep production images reproducible; Renovate proposes upgrades.


class UvicornServer:
    key = "uvicorn"
    label = "Uvicorn (ASGI)"
    requirement = "uvicorn==0.54.0"
    interfaces: tuple[Interface, ...] = ("asgi",)

    def development_command(self, target: str, interface: Interface) -> list[str]:
        return ["python", "-m", "uvicorn", target, "--host", "0.0.0.0", "--port", "8000", "--reload"]

    def production_command(self, target: str, interface: Interface) -> list[str]:
        # Reads WEB_CONCURRENCY (workers) and FORWARDED_ALLOW_IPS (trusted proxies) from the environment.
        return ["python", "-m", "uvicorn", target, "--host", "0.0.0.0", "--port", "8000", "--proxy-headers"]


class HypercornServer:
    key = "hypercorn"
    label = "Hypercorn (ASGI)"
    requirement = "hypercorn==0.18.0"
    interfaces: tuple[Interface, ...] = ("asgi",)

    def development_command(self, target: str, interface: Interface) -> list[str]:
        return ["python", "-m", "hypercorn", target, "--bind", "0.0.0.0:8000", "--reload"]

    def production_command(self, target: str, interface: Interface) -> list[str]:
        return ["python", "-m", "hypercorn", target, "--bind", "0.0.0.0:8000"]


class GunicornServer:
    key = "gunicorn"
    label = "Gunicorn (WSGI)"
    requirement = "gunicorn==27.0.1"
    interfaces: tuple[Interface, ...] = ("wsgi",)

    def development_command(self, target: str, interface: Interface) -> list[str]:
        return ["python", "-m", "gunicorn", "--bind", "0.0.0.0:8000", "--reload", "--access-logfile", "-", target]

    def production_command(self, target: str, interface: Interface) -> list[str]:
        # Reads WEB_CONCURRENCY (workers) and FORWARDED_ALLOW_IPS (trusted proxies) from the environment.
        return ["python", "-m", "gunicorn", "--bind", "0.0.0.0:8000", "--access-logfile", "-", target]


class GranianServer:
    key = "granian"
    label = "Granian (ASGI and WSGI)"
    requirement = "granian[reload]==2.8.8"
    interfaces: tuple[Interface, ...] = ("asgi", "wsgi")

    def development_command(self, target: str, interface: Interface) -> list[str]:
        return ["python", "-m", "granian", "--interface", interface, "--host", "0.0.0.0", "--port", "8000", "--reload", target]

    def production_command(self, target: str, interface: Interface) -> list[str]:
        # Reads GRANIAN_WORKERS and the other GRANIAN_* settings from the environment.
        return ["python", "-m", "granian", "--interface", interface, "--host", "0.0.0.0", "--port", "8000", target]


BUILTIN_SERVERS = {server.key: server for server in (UvicornServer(), HypercornServer(), GunicornServer(), GranianServer())}

# What `ship init` suggests, best first, for each interface.
RECOMMENDED: dict[Interface, tuple[str, ...]] = {
    "asgi": ("uvicorn", "granian", "hypercorn"),
    "wsgi": ("gunicorn", "granian"),
}
