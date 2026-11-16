"""Find or install uv before initializing a project."""

import os
from pathlib import Path
import shutil
import subprocess
import sys
from tempfile import TemporaryDirectory
from urllib.error import URLError
from urllib.request import Request, urlopen

from .config import ConfigError

UV_VERSION = "0.13.3"


def locate_uv() -> str | None:
    found = shutil.which("uv")
    if found:
        return found
    # The official standalone installer may not update this process's PATH.
    local = Path.home() / ".local" / "bin" / ("uv.exe" if os.name == "nt" else "uv")
    return str(local) if local.is_file() and (os.name == "nt" or os.access(local, os.X_OK)) else None


def _install_uv() -> None:
    environment = os.environ.copy()
    environment["UV_INSTALL_DIR"] = str(Path.home() / ".local" / "bin")
    if os.name == "nt":
        powershell = shutil.which("powershell") or shutil.which("pwsh")
        if powershell is None:
            raise ConfigError("PowerShell is required to install uv automatically on Windows.")
        # A parent PowerShell 7 session can pass its module path to Windows
        # PowerShell 5.1, making the latter load the wrong Security module.
        environment = {key: value for key, value in environment.items() if key.casefold() != "psmodulepath"}
        command = [
            powershell, "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command",
            f"irm https://astral.sh/uv/{UV_VERSION}/install.ps1 | iex",
        ]
        result = subprocess.run(command, check=False, env=environment)
    else:
        url = f"https://astral.sh/uv/{UV_VERSION}/install.sh"
        try:
            with urlopen(Request(url, headers={"User-Agent": "python-ship/0.1"}), timeout=30) as response:
                script = response.read()
        except (OSError, URLError) as exc:
            raise ConfigError(f"Could not download uv installer from {url}: {exc}") from exc
        with TemporaryDirectory(prefix="python-ship-uv-") as directory:
            installer = Path(directory) / "install.sh"
            installer.write_bytes(script)
            result = subprocess.run(["sh", str(installer)], check=False, env=environment)
    if result.returncode != 0:
        raise ConfigError("The uv installer failed; ship init made no project changes.")


def require_uv() -> str:
    existing = locate_uv()
    if existing:
        return existing
    installer_suffix = "ps1" if os.name == "nt" else "sh"
    installer_url = f"https://astral.sh/uv/{UV_VERSION}/install.{installer_suffix}"
    try:
        answer = input(
            "uv is required. Install it using Astral's official installer "
            f"({installer_url})? [y/N] "
        )
    except EOFError as exc:
        raise ConfigError("uv is required; installation was not confirmed.") from exc
    if answer.strip().lower() not in {"y", "yes"}:
        raise ConfigError("uv is required; ship init cancelled without changing the project.")
    print("Installing uv...", file=sys.stderr)
    _install_uv()
    installed = locate_uv()
    if installed is None:
        raise ConfigError("uv installer finished, but uv was not found; ship init made no project changes.")
    return installed
