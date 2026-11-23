"""Explicit public build values and temporary BuildKit credentials."""
from pathlib import Path
import re
import shlex
from typing import Any

from .config import ShipConfig

NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
PUBLIC_PREFIXES = ("VITE_", "NEXT_PUBLIC_", "NUXT_PUBLIC_", "PUBLIC_", "GATSBY_", "REACT_APP_")


def validate_build(value: Any) -> list[str]:
    if not isinstance(value, dict) or set(value) - {"publicEnv", "secrets", "npmrc"}:
        return ["build must contain publicEnv, secrets and optional npmrc only."]
    problems = []
    public, secrets = value.get("publicEnv", {}), value.get("secrets", [])
    if not isinstance(public, dict) or any(not isinstance(key, str) or not NAME.fullmatch(key) or
            key in {"PATH", "HOME", "PYTHONPATH", "VIRTUAL_ENV", "UV_PROJECT_ENVIRONMENT"} or
            not isinstance(item, str) or "\0" in item for key, item in public.items()):
        problems.append("build.publicEnv must map safe environment names to public strings.")
    if not isinstance(secrets, list) or any(not isinstance(key, str) or not NAME.fullmatch(key) or
            key in {"PATH", "HOME", "PYTHONPATH", "VIRTUAL_ENV", "UV_PROJECT_ENVIRONMENT"} or key.startswith(PUBLIC_PREFIXES) for key in secrets):
        problems.append("build.secrets must list private environment names; public frontend names cannot be secrets.")
    elif len(set(secrets)) != len(secrets) or (isinstance(public, dict) and set(public) & set(secrets)):
        problems.append("Build variables must have one unambiguous visibility.")
    if not isinstance(value.get("npmrc", False), bool):
        problems.append("build.npmrc must be a boolean.")
    return problems


def build_problems(config: ShipConfig, root: Path) -> list[str]:
    problems = []
    for name in [*config.build.get("secrets", []), *(["npmrc"] if config.build.get("npmrc") else [])]:
        path = root / ".ship-secrets" / name
        if not path.is_file() or path.is_symlink():
            problems.append(f"Required build secret file .ship-secrets/{name} is missing or is a symlink.")
    return problems


def protect_runs(text: str, config: ShipConfig) -> str:
    """Mount credentials only for dependency installation/build RUNs; never ENV or COPY them."""
    public = config.build.get("publicEnv", {})
    secrets = config.build.get("secrets", [])
    mounts = [f"--mount=type=secret,id=ship_build_{key},required=true" for key in secrets]
    env = [f'{key}="$(cat /run/secrets/ship_build_{key})"' for key in secrets]
    if config.build.get("npmrc"):
        mounts.append("--mount=type=secret,id=ship_npmrc,target=/run/secrets/ship_npmrc,required=true")
        env.append("NPM_CONFIG_USERCONFIG=/run/secrets/ship_npmrc")
    # Join Dockerfile continuation lines before wrapping shell commands.
    text = text.replace("\\\n", " ")
    result = []
    for line in text.splitlines():
        result.append(line)
        if line.startswith("FROM ") and (" AS builder" in line or " AS assets" in line or " AS production-dependencies" in line):
            result.extend("ARG " + key for key in public)
        if line.startswith("RUN ") and ("uv " in line or "npm " in line or "pnpm " in line or "yarn " in line):
            script = line[4:]
            result[-1] = "RUN " + " ".join(mounts) + " " + ("env " + " ".join(env) + " " if env else "") + "sh -c " + shlex.quote(script)
    return "\n".join(result) + "\n"
