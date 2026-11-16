"""Shared helpers for the unit and Docker-backed tests."""

from __future__ import annotations

from contextlib import ExitStack, contextmanager, redirect_stderr, redirect_stdout
from io import StringIO
import os
from pathlib import Path
import shutil
import socket
import subprocess
import tempfile
import time
from typing import Any
import unittest
from unittest.mock import patch
from urllib.request import urlopen

from python_ship.cli import main
from python_ship.compose import build_compose, compose_path
from python_ship.config import ShipConfig

FIXTURES = Path(__file__).parent / "fixtures"
PLAIN_APP = (FIXTURES / "plain_asgi" / "main.py").read_text(encoding="utf-8")
DOCKER = shutil.which("docker") is not None


def flag(name: str) -> bool:
    return os.environ.get(name) == "1"


def free_port() -> int:
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        return int(listener.getsockname()[1])


@contextmanager
def no_uv_prompt():
    """ship init requires uv on the host; tests pretend it is installed."""
    with patch("python_ship.init.require_uv", return_value="/usr/bin/uv"):
        yield


class Output:
    def __init__(self) -> None:
        self.out = StringIO()
        self.err = StringIO()

    @property
    def stdout(self) -> str:
        return self.out.getvalue()

    @property
    def stderr(self) -> str:
        return self.err.getvalue()


@contextmanager
def captured():
    output = Output()
    with redirect_stdout(output.out), redirect_stderr(output.err):
        yield output


class FakeEntryPoint:
    def __init__(self, name: str, factory: Any):
        self.name = name
        self.factory = factory

    def load(self) -> Any:
        return self.factory


def entry_points_for(groups: dict[str, list[FakeEntryPoint]]):
    """A stand-in for importlib.metadata.entry_points(group=...)."""
    return patch("python_ship.plugins.entry_points", side_effect=lambda *, group: groups.get(group, []))


class DockerCalls:
    """Records subprocess.run calls made by the CLI instead of running Docker."""

    def __init__(self, returncode: int = 0, stdout: str = "") -> None:
        self.calls: list[tuple[list[str], dict[str, Any]]] = []
        self.returncode = returncode
        self.stdout = stdout

    def __call__(self, command: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        self.calls.append((list(command), kwargs))
        return subprocess.CompletedProcess(command, self.returncode, self.stdout, "")

    @property
    def last(self) -> list[str]:
        return self.calls[-1][0]


@contextmanager
def fake_docker(returncode: int = 0, stdout: str = "", tty: bool = True):
    calls = DockerCalls(returncode, stdout)
    with ExitStack() as stack:
        stack.enter_context(patch("python_ship.cli.shutil.which",
                                  side_effect=lambda name: f"/usr/bin/{name}" if name == "docker" else None))
        stack.enter_context(patch("python_ship.cli.subprocess.run", side_effect=calls))
        stack.enter_context(patch("python_ship.cli.sys.stdin.isatty", return_value=tty))
        stack.enter_context(patch("python_ship.cli.wait_for_stack", return_value=8000))
        yield calls


class ProjectTestCase(unittest.TestCase):
    """A temporary project directory with a framework-free ASGI app."""

    app_source = PLAIN_APP

    def setUp(self) -> None:
        directory = tempfile.TemporaryDirectory(prefix="ship-test-")
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        (self.root / "main.py").write_text(self.app_source, encoding="utf-8")

    def ship(self, *arguments: str) -> int:
        return main(["--project-directory", str(self.root), *arguments])

    def init(self, *arguments: str) -> int:
        """Non-interactive `ship init`; its output is kept in ``self.init_output``."""
        with no_uv_prompt(), captured() as output:
            code = self.ship("init", "--no-interaction", *arguments)
        self.init_output = output
        return code

    def write_config(self, **fields: Any) -> ShipConfig:
        config = ShipConfig(**fields)
        config.write(self.root)
        return config

    def copy_fixture(self, name: str) -> None:
        shutil.copytree(FIXTURES / name, self.root, dirs_exist_ok=True,
                        ignore=shutil.ignore_patterns("__pycache__"))

    def model(self, config: ShipConfig | None = None, *, production: bool = False) -> dict[str, Any]:
        return build_compose(config or ShipConfig.read(self.root), self.root, production=production)


class DockerProjectTestCase(ProjectTestCase):
    """A project that is torn down, volumes and images included, after the test."""

    def setUp(self) -> None:
        if not DOCKER:
            self.skipTest("Docker is required")
        super().setUp()
        self.addCleanup(self.tear_down_stack)

    def tear_down_stack(self) -> None:
        if not (self.root / "ship.json").is_file():
            return
        with captured():
            self.ship("down", "--volumes")
        production = compose_path(self.root, production=True)
        if production.is_file():
            # The env file is needed even to stop the stack: Compose refuses required ${VAR:?} values without it.
            env_file = self.root / ".env.production"
            subprocess.run(["docker", "compose", "--project-directory", str(self.root),
                            *(["--env-file", str(env_file)] if env_file.is_file() else []), "-f", str(production),
                            "down", "--volumes", "--rmi", "local"], capture_output=True, check=False)
        try:
            name = build_compose(ShipConfig.read(self.root), self.root)["name"]
        except Exception:
            return
        images = subprocess.run(["docker", "image", "ls", "--format", "{{.Repository}}:{{.Tag}}",
                                 "--filter", f"reference={name}*"], capture_output=True, text=True, check=False)
        for image in images.stdout.split():
            subprocess.run(["docker", "image", "rm", "-f", image], capture_output=True, check=False)

    def compose(self, *arguments: str, production: bool = False, check: bool = False) -> subprocess.CompletedProcess[str]:
        command = ["docker", "compose", "--project-directory", str(self.root)]
        if production and (self.root / ".env.production").is_file():
            command += ["--env-file", str(self.root / ".env.production")]
        command += ["-f", str(compose_path(self.root, production=production)), *arguments]
        return subprocess.run(command, cwd=self.root, capture_output=True, text=True, check=check)


def wait_http(port: int, path: str = "/", expected: bytes | None = None, timeout: float = 60) -> bytes:
    """GET until it succeeds (and, when given, contains ``expected``)."""
    deadline = time.monotonic() + timeout
    last: Exception | bytes | None = None
    while time.monotonic() < deadline:
        try:
            with urlopen(f"http://127.0.0.1:{port}{path}", timeout=5) as response:
                body = response.read()
            if expected is None or expected in body:
                return body
            last = body
        except OSError as exc:
            last = exc
        time.sleep(0.5)
    raise AssertionError(f"http://127.0.0.1:{port}{path} did not return {expected!r}; last result: {last!r}")
