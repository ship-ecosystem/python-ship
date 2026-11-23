"""Append missing application settings without rewriting user values or comments."""
from pathlib import Path
import re

from .capabilities import release_description
from .compose import build_compose
from .config import ShipConfig
from .plugins import load_extensions
from .production import read_env, required_variables


def development_settings(config: ShipConfig, root: Path) -> dict[str, str]:
    model = build_compose(config, root)
    values = dict(model["services"][config.app_service]["environment"])
    # Include administrative credentials needed to provision the servers, without passing them to the app.
    for service in model["services"].values():
        for variable, default in re.findall(r"\$\{([A-Za-z_][A-Za-z0-9_]*):-([^}]*)\}", str(service.get("environment", {}))):
            values.setdefault(variable, default)
    for key, value in list(values.items()):
        if not isinstance(value, str):
            values.pop(key)
            continue
        match = re.fullmatch(r"\$\{([A-Za-z_][A-Za-z0-9_]*):-([^}]*)\}", value)
        if match:
            values[key] = match[2]
        else:
            # Preserve references in composite URLs, rather than resolving a password into them.
            values[key] = re.sub(r"\$\{([A-Za-z_][A-Za-z0-9_]*):-[^}]*\}", r"${\1}", value)
    return values


def append_missing(path: Path, values: dict[str, str], *, private: bool = False) -> list[str]:
    present = read_env(path)
    missing = sorted(set(values) - set(present))
    if not missing:
        return []
    current = path.read_text(encoding="utf-8") if path.is_file() else ""
    def quote(value: str) -> str:
        return '"' + value.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n") + '"'
    # Private creation from the first byte, including on POSIX systems with permissive umasks.
    import os
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600 if private else 0o644)
    with os.fdopen(descriptor, "a", encoding="utf-8") as stream:
        stream.write(("\n" if current and not current.endswith("\n") else "") + "\n# Added by ship init\n")
        stream.write("".join(f"{key}={quote(values[key]) if values[key] else ''}\n" for key in missing))
    return missing


def ensure_environment(config: ShipConfig, root: Path) -> None:
    development = development_settings(config, root)
    (root / ".env.example").touch(exist_ok=True)
    append_missing(root / ".env.example", development)
    env = root / ".env"
    if not env.exists():
        import shutil
        shutil.copyfile(root / ".env.example", env)
        env.chmod(0o600)
    append_missing(env, development, private=True)
    registry = load_extensions(config)
    production = build_compose(config, root, production=True, registry=registry)
    required = set(required_variables(production)) | set(release_description(registry.adapter(config), config, root).get("requiredEnv", []))
    append_missing(root / ".env.production", dict.fromkeys(required, ""), private=True)


def environment_warnings(config: ShipConfig, root: Path) -> list[str]:
    desired = development_settings(config, root)
    warnings = []
    for filename in (".env", ".env.example"):
        values = read_env(root / filename)
        for key, value in desired.items():
            if key not in values:
                warnings.append(f"{filename} is missing {key}; run ship init to append it.")
            elif "${" not in value and values[key] != value:
                # Defaults can be overridden, but fixed protocol/host settings are controlled by Compose.
                if key.endswith(("_HOST", "_PORT", "_URL")):
                    warnings.append(f"{filename}'s {key} differs from Ship's managed connection; the container setting takes precedence.")
    return warnings
