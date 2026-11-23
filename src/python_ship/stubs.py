"""The ship/ directory `ship init` publishes, and checks that it is current."""

from __future__ import annotations

from importlib.resources import files
import json
from pathlib import Path

from . import __version__
from .config import ShipConfig
from .package_managers import LOCKFILES, asset_stage, inspect_manager, node_dockerfile
from .uv import UV_VERSION

FLOOR_SCRIPT = "check-python-security-floor.py"
ASSET_STAGE = """FROM node:24.18.0-alpine3.24 AS assets
WORKDIR /assets
COPY package.json package-lock.json ./
RUN npm ci
COPY . .
RUN npm run build && test -d dist && mkdir /ship-assets && cp -a dist/. /ship-assets/
"""
ASSET_COPY = "COPY --from=assets /ship-assets/ /app/dist/"


def _template(*parts: str) -> str:
    return files("python_ship").joinpath("templates", *parts).read_text(encoding="utf-8")


def render_dockerfile(config: ShipConfig, root: Path | None = None) -> str:
    return (
        _template("Dockerfile")
        .replace("@UV_VERSION@", UV_VERSION)
        .replace("@ASSET_STAGE@", asset_stage(root or Path.cwd(), config.node) if config.node_assets else "")
        .replace("@ASSET_COPY@", ASSET_COPY if config.node_assets else "")
    )


def uses_garage(config: ShipConfig) -> bool:
    return config.services.get("storage") == "garage" or any(
        entry.service == "garage" for entry in config.additional_services
    )


def publish_stubs(root: Path, config: ShipConfig, *, force: bool) -> bool:
    """Publish ship/. Returns whether the Dockerfile was (re)published.

    The Dockerfile is the project's to edit, so it is only replaced with --force.
    The security floor check is Ship's own and always kept current.
    """
    ship = root / "ship"
    ship.mkdir(exist_ok=True)
    dockerfile = ship / "Dockerfile"
    published = force or not dockerfile.exists()
    if published:
        dockerfile.write_text(render_dockerfile(config, root), encoding="utf-8", newline="\n")
        (ship / ".ship-version").write_text(__version__ + "\n", encoding="utf-8")
    if config.services.get("frontend") == "node":
        node = ship / "node"
        node.mkdir(exist_ok=True)
        path = node / "Dockerfile"
        if force or not path.exists():
            path.write_text(node_dockerfile(root, config.node), encoding="utf-8", newline="\n")
    (ship / FLOOR_SCRIPT).write_text(_template(FLOOR_SCRIPT), encoding="utf-8", newline="\n")
    (ship / "app-entrypoint.py").write_text(_template("app-entrypoint.py"), encoding="utf-8", newline="\n")
    if uses_garage(config):
        garage = ship / "garage"
        garage.mkdir(exist_ok=True)
        for name in ("Dockerfile", "garage.toml"):
            target = garage / name
            if force or not target.exists():
                target.write_text(_template("garage", name), encoding="utf-8", newline="\n")
    return published


def published_version(root: Path) -> str | None:
    path = root / "ship" / ".ship-version"
    return path.read_text(encoding="utf-8").strip() if path.is_file() else None


def stub_version_warning(root: Path) -> str | None:
    version = published_version(root)
    if version != __version__:
        return (
            f"ship/Dockerfile was published by python-ship {version or 'an unknown version'}, "
            f"but {__version__} is installed. Run `ship init --force` to republish it "
            "(commit or back up any edits first)."
        )
    return None


def _dockerfile(root: Path) -> str | None:
    path = root / "ship" / "Dockerfile"
    return path.read_text(encoding="utf-8") if path.is_file() else None


def server_stub_problem(root: Path, config: ShipConfig) -> str | None:
    text = _dockerfile(root)
    if text is not None and (config.server or "uvicorn") != "uvicorn" and "SHIP_SERVER_REQUIREMENT" not in text:
        return "ship/Dockerfile cannot install the selected web server; run `ship init --force` to refresh it."
    return None


def asset_stub_problem(root: Path, config: ShipConfig) -> str | None:
    text = _dockerfile(root)
    if config.node_assets and text is not None and "AS assets" not in text:
        return "nodeAssets requires a refreshed ship/Dockerfile; run `ship init --force`."
    return None


def stub_problems(root: Path, config: ShipConfig | None) -> list[str]:
    """Problems with the published ship/ directory that would break a build."""
    text = _dockerfile(root)
    if text is None:
        return ["ship/Dockerfile is missing; run `ship init`."]
    problems = []
    if FLOOR_SCRIPT in text and not (root / "ship" / FLOOR_SCRIPT).is_file():
        problems.append(f"ship/{FLOOR_SCRIPT} is missing; run `ship init` to publish it.")
    if config is None:
        return problems
    for problem in (server_stub_problem(root, config), asset_stub_problem(root, config)):
        if problem:
            problems.append(problem)
    if uses_garage(config):
        problems.extend(
            f"ship/garage/{name} is missing; run `ship init` to publish Garage's build files."
            for name in ("Dockerfile", "garage.toml")
            if not (root / "ship" / "garage" / name).is_file()
        )
    return problems


def production_stub_problem(root: Path) -> str | None:
    text = _dockerfile(root)
    if text is None or "AS production" not in text:
        return "ship/Dockerfile has no production stage; run `ship init --force` to refresh it."
    return None


def asset_problems(root: Path, config: ShipConfig) -> list[str]:
    """What a nodeAssets production build needs from the project itself."""
    if not config.node_assets:
        return []
    problems = []
    package = root / "package.json"
    if not package.is_file():
        problems.append("nodeAssets requires package.json with a build script.")
    else:
        try:
            data = json.loads(package.read_text(encoding="utf-8"))
            scripts = data.get("scripts") if isinstance(data, dict) else None
            if not isinstance(scripts, dict) or not isinstance(scripts.get("build"), str):
                problems.append("nodeAssets requires a \"build\" script in package.json.")
        except (OSError, ValueError):
            problems.append("nodeAssets requires a valid package.json.")
    try:
        manager = inspect_manager(root)
        lock = LOCKFILES[manager.name]
        if not (root / lock).is_file():
            problems.append(f"nodeAssets requires {lock} for a reproducible build.")
    except ValueError as exc:
        problems.append(str(exc))
    return problems
