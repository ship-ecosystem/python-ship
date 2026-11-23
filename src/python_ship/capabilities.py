"""Optional adapter capabilities, shared by initialization, validation and releases."""
from pathlib import Path
from typing import Any

from .config import ConfigError, ShipConfig, http_problem, process_problem
from .production import DEVELOPMENT_DEFAULTS, read_env


def release_description(adapter: Any, config: ShipConfig, root: Path) -> dict[str, Any]:
    hook = getattr(adapter, "describe_release", None)
    if hook is None:
        return {}
    result = hook(config, root)
    if not isinstance(result, dict) or set(result) - {"health", "urlSetting", "requiredEnv"}:
        raise ConfigError(f"Adapter {adapter.key}: invalid release description.")
    problem = http_problem({"port": 8000, "role": "site", "health": result.get("health"), "urlSetting": result.get("urlSetting")})
    if problem:
        raise ConfigError(problem)
    import re
    if not isinstance(result.get("requiredEnv", []), list) or any(
            not isinstance(key, str) or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", key) for key in result.get("requiredEnv", [])):
        raise ConfigError("Adapter requiredEnv must list setting names.")
    return result


def required_setting_problems(description: dict[str, Any], root: Path) -> list[str]:
    values = read_env(root / ".env.production")
    return [f"{key} is required in .env.production with a real value."
            for key in description.get("requiredEnv", []) if not values.get(key) or values[key].lower() in DEVELOPMENT_DEFAULTS]


def process_suggestions(adapter: Any, config: ShipConfig, root: Path) -> dict[str, Any]:
    hook = getattr(adapter, "suggest_processes", None)
    if hook is None:
        return {}
    result = hook(config, root)
    if not isinstance(result, dict):
        raise ConfigError("Adapter process suggestions must map names to process definitions.")
    for name, value in result.items():
        _, issues = ShipConfig.from_data({"app": config.app, "processes": {name: value}})
        if process_problem(value) or issues:
            raise ConfigError(f"Invalid adapter process suggestion {name!r}.")
    return result
