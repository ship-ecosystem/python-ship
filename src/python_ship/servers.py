"""ASGI server choices, independent of framework adapters."""


class UvicornServer:
    key = "uvicorn"
    requirement = "uvicorn>=0.53,<1"

    def development_command(self, target: str) -> list[str]:
        return ["python", "-m", "uvicorn", target, "--host", "0.0.0.0", "--port", "8000", "--reload"]

    def production_command(self, target: str) -> list[str]:
        return ["python", "-m", "uvicorn", target, "--host", "0.0.0.0", "--port", "8000"]


class HypercornServer:
    key = "hypercorn"
    requirement = "hypercorn==0.18.0"

    def development_command(self, target: str) -> list[str]:
        return ["python", "-m", "hypercorn", target, "--bind", "0.0.0.0:8000", "--reload"]

    def production_command(self, target: str) -> list[str]:
        return ["python", "-m", "hypercorn", target, "--bind", "0.0.0.0:8000"]


BUILTIN_SERVERS = {server.key: server for server in (UvicornServer(), HypercornServer())}
