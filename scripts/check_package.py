"""Build and smoke-test wheel and sdist outside the source checkout."""

import os
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory

ROOT = Path(__file__).resolve().parents[1]


def run(*args: str, cwd: Path = ROOT) -> None:
    subprocess.run(args, cwd=cwd, check=True)


def check_artifact(artifact: Path, base: Path) -> None:
    environment = base / artifact.name.replace(".", "-")
    run("uv", "venv", "--python", sys.executable, str(environment))
    python = environment / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    ship = environment / ("Scripts/ship.exe" if os.name == "nt" else "bin/ship")
    run("uv", "pip", "install", "--python", str(python), str(artifact))
    project = environment / "project"
    project.mkdir()
    (project / "main.py").write_text(
        "async def app(scope, receive, send):\n"
        "    if scope['type'] == 'http':\n"
        "        await send({'type': 'http.response.start', 'status': 200, 'headers': []})\n"
        "        await send({'type': 'http.response.body', 'body': b'ok'})\n",
        encoding="utf-8",
    )
    (project / ".env.production").write_text("APP_PORT=18080\n", encoding="utf-8")
    run(
        str(python), "-c",
        "from importlib.resources import files; p=files('python_ship'); "
        "assert all(p.joinpath(n).is_file() for n in "
        "('py.typed', 'templates/Dockerfile', 'templates/check-python-security-floor.py', "
        "'templates/garage/Dockerfile', 'templates/garage/garage.toml'))",
        cwd=project,
    )
    run(str(ship), "--help", cwd=project)
    version = subprocess.run([str(ship), "--version"], cwd=project, capture_output=True, text=True, check=True).stdout
    if artifact.name.split("-")[1].removesuffix(".tar.gz") not in version:
        raise RuntimeError(f"{artifact.name} reports the wrong version: {version!r}")
    run(str(ship), "init", "--no-interaction", "--app", "main:app", cwd=project)
    run(str(ship), "config:test", cwd=project)
    for relative in ("ship/Dockerfile", "ship/check-python-security-floor.py", "ship.json", ".gitignore"):
        if not (project / relative).is_file():
            raise RuntimeError(f"{artifact.name} did not create {relative}")
    print(f"PASS: installed {artifact.name} and initialized a project", flush=True)


def main() -> None:
    with TemporaryDirectory(prefix="ship-package-check-") as directory:
        base = Path(directory)
        artifacts = base / "dist"
        run("uv", "build", "--out-dir", str(artifacts), ".")
        wheel = sorted(artifacts.glob("python_ship-*.whl"))
        sdist = sorted(artifacts.glob("python_ship-*.tar.gz"))
        if len(wheel) != 1 or len(sdist) != 1:
            raise RuntimeError("Expected exactly one wheel and one source distribution")
        for artifact in (wheel[0], sdist[0]):
            check_artifact(artifact, base)


if __name__ == "__main__":
    main()
