"""Container-based frontend package managers with exact versions and lockfile authority."""
from collections.abc import Callable
from dataclasses import dataclass
import json
from pathlib import Path
import re
import shlex
import uuid

from .config import ConfigError

VERSIONS = {"npm": "12.2.2", "pnpm": "12.21.0", "yarn": "4.18.1"}
LOCKFILES = {"npm": "package-lock.json", "pnpm": "pnpm-lock.yaml", "yarn": "yarn.lock"}
NODE_IMAGES = {
    "22": "node:22.23.5-bookworm-slim@sha256:e9f565d32859f0cbfb52125eafe8be39390f24dac302dc3cdf566e8ba3ab21ee",
    "24": "node:24.22.3-bookworm-slim@sha256:dc26ae92c9fb15cf6b1d63e20f0944b2efe0f115b20e00be48fb23fceb061c54",
    "26": "node:26.13.1-bookworm-slim@sha256:5df79989ed685e09f0a98019db4cebfb22fb9df1a53c0161664a011f7ff6b0f1",
}


@dataclass(frozen=True)
class PackageManager:
    name: str
    version: str

    @property
    def package(self) -> str:
        return "@yarnpkg/cli-dist" if self.name == "yarn" and not self.version.startswith("1.") else self.name

    @property
    def installer(self) -> list[str]:
        # Only the pinned tool's installation script is permitted, never application scripts.
        return ["npm", "install", "--global", "--force", f"--allow-scripts={self.package}@{self.version}", f"{self.package}@{self.version}"]

    @property
    def frozen_install(self) -> list[str]:
        if self.name == "npm":
            return ["npm", "ci", "--no-audit", "--no-fund"]
        if self.name == "pnpm":
            return ["pnpm", "install", "--frozen-lockfile"]
        return ["yarn", "install", "--frozen-lockfile" if self.version.startswith("1.") else "--immutable"]


def parse_manager(value: str) -> PackageManager:
    match = re.fullmatch(r"(npm|pnpm|yarn)@(\d+\.\d+\.\d+)(?:\+sha(?:224|256|384|512)\.[a-fA-F0-9]+)?", value)
    if not match:
        raise ConfigError("packageManager must pin npm, pnpm or yarn to an exact version (e.g. pnpm@12.21.0).")
    return PackageManager(match[1], match[2])


def inspect_manager(root: Path) -> PackageManager:
    path = root / "package.json"
    try:
        manifest = json.loads(path.read_text()) if path.is_file() else {}
    except ValueError as exc:
        raise ConfigError("package.json is not valid JSON.") from exc
    if not isinstance(manifest, dict):
        raise ConfigError("package.json must contain an object.")
    locks = [tool for tool, lock in LOCKFILES.items() if (root / lock).exists()]
    if len(locks) > 1:
        raise ConfigError("Conflicting frontend lockfiles; select one package manager.")
    if any((root / name).exists() for name in ("bun.lock", "bun.lockb", "npm-shrinkwrap.json")):
        raise ConfigError("Unsupported frontend lockfile; use npm, pnpm or Yarn with one authoritative lockfile.")
    declared = manifest.get("packageManager")
    manager = parse_manager(declared) if isinstance(declared, str) else None
    if declared is not None and manager is None:
        raise ConfigError("package.json packageManager must be an exact tool@version string.")
    if manager and locks and manager.name != locks[0]:
        raise ConfigError("package.json packageManager conflicts with the committed lockfile.")
    if not manager and locks and locks[0] != "npm":
        raise ConfigError("Pin the frontend tool in package.json packageManager.")
    manager = manager or PackageManager("npm", VERSIONS["npm"])
    if manager.name == "yarn" and not manager.version.startswith("1."):
        config = root / ".yarnrc.yml"
        if not config.is_file() or not re.search(r"^nodeLinker:\s*['\"]?node-modules['\"]?\s*(?:#.*)?$", config.read_text(), re.M):
            raise ConfigError("Modern Yarn requires .yarnrc.yml with nodeLinker: node-modules.")
    return manager


def node_dockerfile(root: Path, node: str) -> str:
    manager = inspect_manager(root)
    return (f"FROM {NODE_IMAGES[node]}\n" + "RUN " + shlex.join(manager.installer) + "\nWORKDIR /app\n"
            + 'CMD ["sleep", "infinity"]\n')


def asset_stage(root: Path, node: str) -> str:
    manager = inspect_manager(root)
    lock = LOCKFILES[manager.name]
    if not (root / lock).is_file():
        raise ConfigError(f"nodeAssets requires {lock} for reproducible builds.")
    return (node_dockerfile(root, node).split("\nWORKDIR", 1)[0].replace("\nRUN", " AS assets\nRUN", 1)
            + "\nWORKDIR /assets\nCOPY . .\nRUN " + shlex.join(manager.frozen_install)
            + " && " + shlex.join([manager.name, "run", "build"])
            + " && test -d dist && mkdir /ship-assets && cp -a dist/. /ship-assets/\n")


def switch_manager(root: Path, value: str, run: Callable[[list[str]], int]) -> None:
    """Restore tracked configuration and locks after failure; retain the old dependency volume."""
    if value in VERSIONS:
        value += "@" + VERSIONS[value]
    manager = parse_manager(value)
    files = ["package.json", *LOCKFILES.values(), ".yarnrc.yml"]
    before = {name: (root / name).read_bytes() if (root / name).is_file() else None for name in files}
    try:
        manifest = json.loads((root / "package.json").read_text())
        manifest["packageManager"] = f"{manager.name}@{manager.version}"
        (root / "package.json").write_text(json.dumps(manifest, indent=2) + "\n")
        for tool, lock in LOCKFILES.items():
            if tool != manager.name:
                (root / lock).unlink(missing_ok=True)
        if manager.name == "yarn" and not manager.version.startswith("1."):
            path = root / ".yarnrc.yml"
            current = path.read_text() if path.is_file() else ""
            current = re.sub(r"^nodeLinker:.*\n?", "", current, flags=re.M)
            path.write_text(current.rstrip() + "\nnodeLinker: node-modules\n")
        if run(manager.installer) != 0:
            raise ConfigError("Package-manager installation failed.")
        backup = "/tmp/ship-manager-" + uuid.uuid4().hex
        save = f"mkdir -p {backup}; if [ -d /app/node_modules ]; then cp -a /app/node_modules/. {backup}/; fi"
        if run(["sh", "-c", save]) != 0:
            raise ConfigError("Could not back up frontend dependencies; installation was not attempted.")
        if run([manager.name, "install"]) != 0:
            restore = f"find /app/node_modules -mindepth 1 -maxdepth 1 -exec rm -rf -- {{}} +; cp -a {backup}/. /app/node_modules/"
            if run(["sh", "-c", restore]) != 0:
                raise ConfigError(f"Dependency restore failed; backup remains in frontend container at {backup}.")
            run(["rm", "-rf", backup])
            raise ConfigError("Dependency installation failed; project configuration, locks and dependencies were restored.")
        run(["rm", "-rf", backup])
    except Exception:
        for name, content in before.items():
            if content is None:
                (root / name).unlink(missing_ok=True)
            else:
                (root / name).write_bytes(content)
        raise
