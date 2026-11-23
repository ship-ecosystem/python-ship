"""Prepare structured service credentials without interpolating unescaped URI components."""
import os
import re
import sys
from urllib.parse import quote


def application_environment(values):
    environment = dict(values)
    for key in list(environment):
        if re.fullmatch(r"(?:[A-Z][A-Z0-9_]*_)?MONGODB_HOST", key):
            prefix = key.removesuffix("MONGODB_HOST")
            def get(name, prefix=prefix):
                return environment.get(prefix + "MONGODB_" + name, "")
            if not all(get(name) for name in ("HOST", "DATABASE", "USERNAME", "PASSWORD")):
                raise ValueError("MongoDB connection variables are incomplete")
            environment[prefix + "MONGODB_URI"] = (
                "mongodb://" + quote(get("USERNAME"), safe="") + ":" + quote(get("PASSWORD"), safe="") +
                "@" + get("HOST") + ":27017/" + quote(get("DATABASE"), safe="") +
                "?authSource=" + quote(get("DATABASE"), safe=""))
        if key.endswith(("DB_ROOT_PASSWORD", "MONGODB_ROOT_PASSWORD")):
            environment.pop(key, None)
    return environment


if __name__ == "__main__":
    if len(sys.argv) < 2:
        raise SystemExit("ship: application command is required")
    os.execvpe(sys.argv[1], sys.argv[1:], application_environment(os.environ))
