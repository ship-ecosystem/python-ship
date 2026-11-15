"""Production environment checks shared by config:test and build."""

import json
from pathlib import Path
import re


REQUIRED = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*):\?")
UNSAFE_VALUES = {"ship", "rootship", "shipsecret", "shipsecretplaceholder", "changeme", "0" * 64}


def read_env(path: Path) -> dict[str, str]:
    """Parse the portable KEY=VALUE subset accepted by Compose env files."""
    if not path.is_file():
        return {}
    values = {}
    for raw in path.read_text(encoding="utf-8-sig").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip().removeprefix("export ").strip()
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", key):
            continue
        value = value.strip()
        if value.startswith('"'):
            match = re.match(r'^"((?:\\.|[^"\\])*)"', value)
            if match:
                value = re.sub(r'\\(["\\])', r'\1', match.group(1))
        elif value.startswith("'"):
            end = value.find("'", 1)
            if end >= 0:
                value = value[1:end]
        else:
            value = value.split(" #", 1)[0].rstrip()
        values[key] = value
    return values


def credential_problems(model: dict, root: Path) -> list[str]:
    required = sorted(set(REQUIRED.findall(json.dumps(model))))
    if not required:
        return []
    values = read_env(root / ".env.production")
    missing = [key for key in required if not values.get(key)]
    unsafe = [key for key in required if values.get(key, "").lower() in UNSAFE_VALUES]
    problems = []
    if missing:
        problems.append(f"Production credentials missing from .env.production: {', '.join(missing)}.")
    if unsafe:
        problems.append(f"Production credentials use known development defaults: {', '.join(unsafe)}.")
    return problems
