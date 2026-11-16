"""Built-in ASGI servers. Server choice is independent of the framework adapter."""


class UvicornServer:
    key = "uvicorn"
    label = "Uvicorn"
    # Exact pins keep production images reproducible; Renovate proposes upgrades.
    requirement = "uvicorn==0.54.0"

    def development_command(self, target: str) -> list[str]:
        return ["python", "-m", "uvicorn", target, "--host", "0.0.0.0", "--port", "8000", "--reload"]

    def production_command(self, target: str) -> list[str]:
        # Uvicorn also reads WEB_CONCURRENCY (worker count) and FORWARDED_ALLOW_IPS (trusted
        # proxies) from the environment, so both can be set in .env.production.
        return ["python", "-m", "uvicorn", target, "--host", "0.0.0.0", "--port", "8000", "--proxy-headers"]


class HypercornServer:
    key = "hypercorn"
    label = "Hypercorn"
    requirement = "hypercorn==0.18.0"

    def development_command(self, target: str) -> list[str]:
        return ["python", "-m", "hypercorn", target, "--bind", "0.0.0.0:8000", "--reload"]

    def production_command(self, target: str) -> list[str]:
        return ["python", "-m", "hypercorn", target, "--bind", "0.0.0.0:8000"]


BUILTIN_SERVERS = {server.key: server for server in (UvicornServer(), HypercornServer())}
