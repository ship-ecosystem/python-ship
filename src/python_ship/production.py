"""Production environment checks shared by config:test, build and release."""

from __future__ import annotations

import json
from pathlib import Path
import re

REQUIRED = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*):\?")
# Every development default a built-in service uses for a secret. Shipping one to production
# would give every project that forgot the same, publicly known credential.
DEVELOPMENT_DEFAULTS = frozenset({
    "ship", "rootship", "shipsecret", "shipsecretplaceholder", "shipsearchkey", "changeme", "0" * 64,
})
_KEY = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")


def read_env(path: Path) -> dict[str, str]:
    """Parse the KEY=VALUE subset that Compose env files accept.

    Handles a BOM, ``export`` prefixes, single and double quotes (with escapes in
    double quotes) and trailing ``#`` comments on unquoted values.
    """
    if not path.is_file():
        return {}
    values: dict[str, str] = {}
    for raw in path.read_text(encoding="utf-8-sig").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        if key.startswith("export "):
            key = key[len("export "):].strip()
        if not _KEY.fullmatch(key):
            continue
        value = value.strip()
        if value.startswith('"'):
            match = re.match(r'^"((?:\\.|[^"\\])*)"', value)
            if match:
                value = re.sub(r'\\(["\\$])', r"\1", match.group(1)).replace("\\n", "\n")
        elif value.startswith("'"):
            end = value.find("'", 1)
            if end >= 0:
                value = value[1:end]
        else:
            value = re.split(r"\s+#", value, maxsplit=1)[0].rstrip()
        values[key] = value
    return values


def required_variables(model: dict) -> list[str]:
    return sorted(set(REQUIRED.findall(json.dumps(model))))


def credential_problems(model: dict, root: Path) -> list[str]:
    """Required production variables missing from .env.production, or set to a development default."""
    required = required_variables(model)
    if not required:
        return []
    values = read_env(root / ".env.production")
    missing = [key for key in required if not values.get(key)]
    unsafe = [key for key in required if values.get(key, "").lower() in DEVELOPMENT_DEFAULTS]
    problems = []
    if missing:
        problems.append(f"Production credentials missing from .env.production: {', '.join(missing)}.")
    if unsafe:
        problems.append(f"Production credentials still use a development default: {', '.join(unsafe)}.")
    return problems
