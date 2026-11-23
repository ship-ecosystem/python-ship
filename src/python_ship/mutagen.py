"""Optional direct Mutagen synchronization for Docker Desktop development."""

import hashlib
import os
from pathlib import Path
import shutil
import subprocess
import time

from .config import ConfigError

IGNORES = (
    ".venv", "venv", "node_modules", "__pycache__", ".pytest_cache",
    ".mypy_cache", ".ruff_cache", ".tox", "dist", "build", ".ship-secrets", ".npmrc", "auth.json",
    "*.egg-info", ".env", ".env.*", "ship/docker-compose.*.yml", "dist/ship",
)


def enabled() -> bool:
    return os.environ.get("SHIP_MUTAGEN", "").lower() in {"1", "true"}


def _label(root: Path) -> str:
    digest = hashlib.sha256(str(root.resolve()).encode()).hexdigest()[:16]
    return f"ship-project={digest}"


def _binary(*, required: bool = False) -> str | None:
    binary = shutil.which("mutagen")
    if required and binary is None:
        raise ConfigError("SHIP_MUTAGEN requires the Mutagen CLI on PATH. Install Mutagen, then run `ship up` again.")
    return binary


def _run(command: list[str], root: Path) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(command, cwd=root, capture_output=True, text=True, check=False, timeout=45)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ConfigError(f"Mutagen command failed: {exc}") from exc


def stop(root: Path, *, required: bool = False) -> None:
    binary = _binary(required=required)
    if binary is None:
        return
    result = _run([binary, "sync", "terminate", "--label-selector", _label(root)], root)
    if result.returncode != 0:
        raise ConfigError(f"Could not stop Mutagen sync: {result.stderr.strip() or result.stdout.strip()}")


def start(prefix: list[str], root: Path, services: tuple[str, ...] = ("app",)) -> None:
    binary = _binary(required=True)
    assert binary is not None
    label = _label(root)
    try:
        for service in services:
            container = subprocess.run(prefix + ["ps", "-q", service], cwd=root, capture_output=True, text=True, check=False)
            container_id = container.stdout.strip()
            if container.returncode != 0 or not container_id:
                raise ConfigError(f"Could not find the running {service} container for Mutagen sync.")
            name = "ship-" + label.partition("=")[2] + "-" + service
            command = [binary, "sync", "create", "--no-global-configuration", "--name", name, "--label", label,
                       "--mode", "two-way-resolved", "--ignore-vcs"]
            for pattern in IGNORES:
                command += ["--ignore", pattern]
            command += [str(root.resolve()), f"docker://{container_id}/app"]
            created = _run(command, root)
            if created.returncode != 0:
                raise ConfigError(f"Could not create Mutagen sync for {service}: {created.stderr.strip() or created.stdout.strip()}")
        deadline = time.monotonic() + 120
        while time.monotonic() < deadline:
            status = _run([binary, "sync", "list", "--label-selector", label,
                           "--template", '{{range .}}{{.Status}}|{{.Alpha.Connected}}|{{.Beta.Connected}}{{"\\n"}}{{end}}'], root)
            if status.returncode != 0:
                raise ConfigError(f"Could not inspect Mutagen sync: {status.stderr.strip()}")
            ready = [line for line in status.stdout.splitlines() if line.strip().lower() == "watching|true|true"]
            if len(ready) == len(services):
                flushed = _run([binary, "sync", "flush", "--label-selector", label], root)
                if flushed.returncode != 0:
                    raise ConfigError(f"Could not flush Mutagen sync: {flushed.stderr.strip()}")
                return
            time.sleep(1)
        raise ConfigError("Mutagen did not finish its initial sync within 120 seconds. Run `ship up` again.")
    except (ConfigError, OSError):
        try:
            stop(root, required=True)
        except ConfigError:
            pass
        raise
