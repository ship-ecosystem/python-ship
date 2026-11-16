"""Verify that `docker compose up` produced a usable development stack."""

from __future__ import annotations

import http.client
import json
import subprocess
import time
from typing import Any

from .config import ConfigError


def records(output: str) -> dict[str, dict[str, Any]]:
    """`docker compose ps --format json` output keyed by service; Compose versions differ in shape."""
    try:
        parsed = [json.loads(line) for line in output.splitlines() if line.strip()]
    except json.JSONDecodeError as exc:
        raise ConfigError(f"Could not read Docker Compose container status: {exc}") from exc
    if len(parsed) == 1 and isinstance(parsed[0], list):
        parsed = parsed[0]
    return {record["Service"]: record for record in parsed if isinstance(record, dict) and "Service" in record}


def status(found: dict[str, dict[str, Any]], expected: set[str], app: str = "app") -> tuple[list[str], list[str], int | None]:
    """Fatal problems, still-pending services, and the app's published host port."""
    fatal = []
    pending = []
    for name in sorted(expected):
        record = found.get(name)
        if record is None:
            pending.append(f"{name} has no container")
            continue
        state = record.get("State", "unknown")
        health = record.get("Health", "")
        if state in {"exited", "dead", "restarting"} or health == "unhealthy":
            fatal.append(f"{name} is {state} (health: {health or 'none'}, exit code: {record.get('ExitCode', '?')})")
        elif state != "running" or health == "starting":
            pending.append(f"{name} is {state} (health: {health or 'none'})")
    port = None
    for publisher in (found.get(app) or {}).get("Publishers") or []:
        if str(publisher.get("TargetPort")) == "8000" and publisher.get("Protocol", "tcp") == "tcp" and publisher.get("PublishedPort"):
            port = int(publisher["PublishedPort"])
            break
    if app in expected and port is None:
        pending.append(f"{app} port 8000 is not published")
    return fatal, pending, port


def wait_for_stack(prefix: list[str], expected: set[str], app: str = "app", timeout: float = 180.0) -> int | None:
    """Wait for every container, healthcheck and the app port; raise with the reason on failure."""
    deadline = time.monotonic() + timeout
    last_pending: list[str] = []
    while time.monotonic() < deadline:
        result = subprocess.run(prefix + ["ps", "--all", "--format", "json"], capture_output=True, text=True, check=False)
        if result.returncode != 0:
            raise ConfigError(f"Could not inspect the stack: {result.stderr.strip()}")
        fatal, pending, port = status(records(result.stdout), expected, app)
        if fatal:
            raise ConfigError("; ".join(fatal) + ". Run `ship logs SERVICE` to see why.")
        if not pending and port is not None:
            if responds(port):
                return port
            pending.append(f"{app} is not answering HTTP on port {port} yet")
        last_pending = pending
        time.sleep(1)
    raise ConfigError("The stack did not become ready: " + "; ".join(last_pending) + ". Run `ship logs` to see why.")


def responds(port: int) -> bool:
    """Whether the app answers HTTP at all; any status counts, even an error page.

    A bare TCP connect isn't enough: Docker's port proxy accepts connections
    before the server in the container is listening.
    """
    connection = http.client.HTTPConnection("127.0.0.1", port, timeout=3)
    try:
        connection.request("GET", "/", headers={"User-Agent": "python-ship"})
        return connection.getresponse().status > 0
    except (OSError, http.client.HTTPException):
        return False
    finally:
        connection.close()
