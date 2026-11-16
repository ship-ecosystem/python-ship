"""Run lint, type checks, unit tests and the packaging check in an isolated environment."""

import os
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory

ROOT = Path(__file__).resolve().parents[1]
RUFF = "ruff==0.15.22"
MYPY = "mypy==1.19.1"


def run(*args: str) -> None:
    print("+", " ".join(args), flush=True)
    subprocess.run(args, cwd=ROOT, check=True)


def main() -> None:
    typer = os.environ.get("SHIP_TYPER_REQUIREMENT")
    with TemporaryDirectory(prefix="ship-local-check-") as directory:
        environment = Path(directory) / "venv"
        run("uv", "venv", "--python", sys.executable, str(environment))
        python = environment / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
        run("uv", "pip", "install", "--python", str(python), "-e", ".", RUFF, MYPY, *([typer] if typer else []))
        run(str(python), "-m", "ruff", "check", "src", "scripts", "tests", "examples")
        run(str(python), "-m", "mypy")
        run(str(python), "-m", "unittest", "discover", "-s", "tests", "-t", ".")
        run(str(python), "scripts/check_package.py")


if __name__ == "__main__":
    main()
