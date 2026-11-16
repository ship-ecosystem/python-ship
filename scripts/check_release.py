"""Verify that the release tag and the two package version declarations agree."""

import os
from pathlib import Path
import sys
import tomllib


ROOT = Path(__file__).resolve().parents[1]


def main() -> None:
    metadata = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]
    sys.path.insert(0, str(ROOT / "src"))
    from python_ship import __version__

    version = metadata["version"]
    tag = os.environ.get("RELEASE_TAG", "")
    if metadata["name"] != "python-ship" or version != __version__ or tag != f"v{version}":
        raise SystemExit(
            f"Release mismatch: project={metadata['name']!r}, metadata={version!r}, "
            f"module={__version__!r}, tag={tag!r}; expected tag v{version}."
        )
    print(f"Release tag {tag} matches python-ship {version}.")


if __name__ == "__main__":
    main()
