"""Verify that the release tag matches the package version before publishing."""

import ast
import os
from pathlib import Path
import re
import tomllib

ROOT = Path(__file__).resolve().parents[1]


def package_version() -> str:
    tree = ast.parse((ROOT / "src" / "python_ship" / "__init__.py").read_text(encoding="utf-8"))
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == "__version__" for t in node.targets):
            return ast.literal_eval(node.value)
    raise SystemExit("src/python_ship/__init__.py does not define __version__.")


def main() -> None:
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]
    version = package_version()
    tag = os.environ.get("RELEASE_TAG", "")
    problems = []
    if project["name"] != "python-ship":
        problems.append(f"project name is {project['name']!r}, not 'python-ship'")
    if "version" in project or "version" not in project.get("dynamic", []):
        problems.append("pyproject.toml must take its version dynamically from python_ship.__version__")
    if tag != f"v{version}":
        problems.append(f"tag {tag!r} does not match package version {version!r} (expected v{version})")
    version_match = re.fullmatch(r"(\d+)\.\d+\.\d+((?:a|b|rc)\d+)?", version)
    if not version_match:
        problems.append(f"{version!r} is not a final or pre-release version; publish no .dev or local versions")
    elif int(version_match[1]) >= 1 and version_match[2] is None:
        changelog = ROOT / "CHANGELOG.md"
        if not changelog.is_file() or f"## [{version}]" not in changelog.read_text(encoding="utf-8"):
            problems.append(f"CHANGELOG.md has no '## [{version}]' section")
    if problems:
        raise SystemExit("Release check failed:\n- " + "\n- ".join(problems))
    print(f"Release tag {tag} matches python-ship {version}.")


if __name__ == "__main__":
    main()
