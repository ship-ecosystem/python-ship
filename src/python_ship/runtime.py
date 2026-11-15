"""Verify that `docker compose up` produced a usable development stack."""

import json
import socket
import subprocess
import time

from .config import ConfigError


def _records(output: str) -> dict[str, dict]:
    try:
        parsed = [json.loads(line) for line in output.splitlines() if line.strip()]
    except json.JSONDecodeError as exc:
        raise ConfigError(f"Could not read Docker Compose container status: {exc}") from exc
    if len(parsed) == 1 and isinstance(parsed[0], list):
        parsed = parsed[0]
    return {record["Service"]: record for record in parsed if isinstance(record, dict) and "Service" in record}


def _status(records: dict[str, dict], expected: set[str]) -> tuple[list[str], list[str], int | None]:
    fatal = []
    pending = []
    for name in sorted(expected):
        record = records.get(name)
        if record is None:
            pending.append(f"{name} has no container")
            continue
        state = record.get("State", "unknown")
        health = record.get("Health", "")
        if state in {"exited", "dead", "restarting"} or health == "unhealthy":
            fatal.append(f"{name} is {state} (health: {health or 'none'}, exit: {record.get('ExitCode', '?')})")
        elif state != "running" or health == "starting":
            pending.append(f"{name} is {state} (health: {health or 'none'})")
    port = None
    app = records.get("app")
    if app:
        for publisher in app.get("Publishers") or []:
            if str(publisher.get("TargetPort")) == "8000" and publisher.get("Protocol", "tcp") == "tcp":
                port = publisher.get("PublishedPort")
                break
    if port is None:
        pending.append("app port 8000 is not published")
    return fatal, pending, port


def wait_for_stack(prefix: list[str], expected: set[str], timeout: float = 120.0) -> None:
    deadline = time.monotonic() + timeout
    last_pending = []
    while time.monotonic() < deadline:
        result = subprocess.run(prefix + ["ps", "--all", "--format", "json"], capture_output=True, text=True, check=False)
        if result.returncode != 0:
            raise ConfigError(f"Could not inspect the stack: {result.stderr.strip()}")
        fatal, pending, port = _status(_records(result.stdout), expected)
        if fatal:
            raise ConfigError("; ".join(fatal) + ". Check `ship logs` for details.")
        if not pending and port is not None:
            try:
                with socket.create_connection(("127.0.0.1", int(port)), timeout=1):
                    return
            except OSError:
                pending.append(f"app port {port} is not accepting connections")
        last_pending = pending
        time.sleep(1)
    raise ConfigError("Stack did not become ready: " + "; ".join(last_pending) + ". Check `ship logs` for details.")
