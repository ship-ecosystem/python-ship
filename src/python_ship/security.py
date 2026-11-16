"""The oldest CPython release per branch that Ship builds an image on.

The generated Compose models pass this to ``ship/Dockerfile`` as the
``SHIP_PYTHON_FLOOR`` build argument, and the Dockerfile fails the build when
the image's actual Python is older. ship.json's ``python`` is normally a bare
branch (``"3.13"``), so a stale, locally cached ``python:3.13-slim`` would
otherwise keep building on a vulnerable patch release indefinitely. A branch
older than every entry here is past end of life and is refused too.

Raise these whenever python.org ships a security release; see
https://www.python.org/downloads/ and https://blog.python.org/.
"""

import re

# Branch -> first patched release.
#
# Current floor: the expedited security releases of 2026-10-21 and 2026-10-26,
# which fixed the bundled pip certificate-chain problem, tarfile link handling
# (CVE-2026-114583), and the bundled Expat vulnerabilities (CVE-2026-77214,
# CVE-2026-102633).
MINIMUM_PATCHED = {
    "3.11": "3.11.18",
    "3.12": "3.12.16",
    "3.13": "3.13.18",
    "3.14": "3.14.10",
    "3.15": "3.15.2",
}

DEFAULT_PYTHON = "3.13"

_VERSION = re.compile(r"(\d+)\.(\d+)(?:\.(\d+))?")


def _parts(version: str) -> tuple[int, ...]:
    return tuple(int(part) for part in version.split("."))


def build_arg() -> str:
    """Space-separated, oldest branch first, for the Dockerfile build argument."""
    return " ".join(MINIMUM_PATCHED.values())


def problem_with(version: str) -> str | None:
    """Why ``version`` is unsafe to build on, or ``None`` when it is acceptable.

    A bare branch (``"3.13"``) is acceptable while the branch is supported; the
    Dockerfile checks the resolved patch release. A pinned release
    (``"3.13.4"``) is compared against the floor here.
    """
    match = _VERSION.fullmatch(version)
    if match is None:
        return f"Python version {version!r} must be a branch such as \"3.13\" or a release such as \"3.13.18\"."
    branch = f"{match.group(1)}.{match.group(2)}"
    oldest = next(iter(MINIMUM_PATCHED))
    if _parts(branch) < _parts(oldest):
        return (
            f"Python {branch} is past end of life and gets no security fixes. "
            f"Use Python {oldest} or newer."
        )
    minimum = MINIMUM_PATCHED.get(branch)
    if minimum is None or match.group(3) is None:
        return None
    if _parts(version) < _parts(minimum):
        return (
            f"Python {version} has known security vulnerabilities. Use {minimum} or newer, "
            f"or just \"{branch}\" to always build on its latest patch release."
        )
    return None
