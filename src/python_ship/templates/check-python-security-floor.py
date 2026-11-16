"""Fail the image build on a Python release with known security vulnerabilities.

Published by `ship init` and run by ship/Dockerfile's base stage. ship.json's
"python" is normally a branch ("3.13"), and Docker keeps building on whatever
python:3.13-slim is cached locally, so this checks the Python actually in the
image. SHIP_PYTHON_FLOOR is the first patched release per branch, oldest first
("3.11.18 3.12.16 ..."); empty skips the check. An optional argument stands in
for the running version, for tests.
"""

import os
import platform
import sys


def parse(version):
    return tuple(int(part) for part in version.split(".")[:3])


def main():
    version = sys.argv[1] if len(sys.argv) > 1 else platform.python_version()
    floor = [parse(item) for item in os.environ.get("SHIP_PYTHON_FLOOR", "").split()]
    if not floor:
        return 0
    image = os.environ.get("SHIP_BASE_IMAGE") or "the Python base image"
    try:
        current = parse(version)
    except ValueError:
        return 0  # A pre-release such as 3.16.0a1 has no floor yet.
    if current[:2] < floor[0][:2]:
        oldest = ".".join(map(str, floor[0][:2]))
        sys.stderr.write(
            f"ship: Python {version} is past end of life and gets no security fixes. "
            f"Set \"python\" in ship.json to {oldest} or newer, then build again.\n"
        )
        return 1
    for minimum in floor:
        if minimum[:2] == current[:2] and current < minimum:
            sys.stderr.write(
                f"ship: Python {version} has known security vulnerabilities, fixed in "
                f"{'.'.join(map(str, minimum))}. The locally cached base image is stale: "
                f"run `docker pull {image}` and build again.\n"
            )
            return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
