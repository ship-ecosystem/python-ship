"""Opt-in concurrent-project isolation check."""

import os
from pathlib import Path
import socket
import subprocess
import tempfile
import time
import unittest
from unittest.mock import patch
from urllib.request import urlopen

from python_ship.cli import main
from python_ship.compose import build_compose
from python_ship.config import ShipConfig


@unittest.skipUnless(os.environ.get("SHIP_DOCKER_TEST") == "1", "set SHIP_DOCKER_TEST=1 for real Docker integration")
class ConcurrentProjectsTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="ship-concurrent-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.projects = [self.root / f"{self.root.name}-first", self.root / f"{self.root.name}-second"]
        self.addCleanup(self._cleanup)
        for project in self.projects:
            project.mkdir()
            (project / "main.py").write_text(
                "async def app(scope, receive, send):\n"
                "    await send({'type': 'http.response.start', 'status': 200, 'headers': []})\n"
                f"    await send({{'type': 'http.response.body', 'body': b'{project.name}'}})\n"
            )
            with socket.socket() as listener:
                listener.bind(("127.0.0.1", 0))
                port = listener.getsockname()[1]
            (project / ".env").write_text(f"SHIP_APP_PORT={port}\n")

    def _ship(self, project, *arguments):
        return main(["--project-directory", str(project), *arguments])

    def _cleanup(self):
        for project in self.projects:
            if (project / "ship.json").is_file():
                self._ship(project, "down", "--volumes")
                name = build_compose(ShipConfig.read(project), project)["name"]
                subprocess.run(["docker", "image", "rm", f"{name}-app:latest"], capture_output=True, check=False)

    def test_two_projects_have_independent_networks_volumes_and_ports(self):
        for project in self.projects:
            with patch("python_ship.cli.require_uv", return_value="/integration/uv"):
                self.assertEqual(0, self._ship(project, "init", "--app", "main:app", "--cache", "redis"))
            self.assertEqual(0, self._ship(project, "up"))
            self.assertEqual(0, self._ship(project, "doctor"))
        first, second = self.projects
        first_model = build_compose(ShipConfig.read(first), first)
        second_model = build_compose(ShipConfig.read(second), second)
        self.assertNotEqual(first_model["name"], second_model["name"])
        for project in self.projects:
            port = int((project / ".env").read_text().split("=", 1)[1])
            deadline = time.monotonic() + 20
            while True:
                try:
                    with urlopen(f"http://127.0.0.1:{port}/", timeout=2) as response:
                        self.assertEqual(project.name.encode(), response.read())
                    break
                except OSError:
                    if time.monotonic() >= deadline:
                        self.fail(f"{project.name} did not serve HTTP on port {port}")
                    time.sleep(0.5)
        self.assertEqual(0, self._ship(first, "exec", "redis", "redis-cli", "SET", "ship:isolated", "first"))
        prefix = ["docker", "compose", "--project-directory", str(second), "-f", str(second / "ship" / "compose.generated.json")]
        result = subprocess.run(prefix + ["exec", "-T", "redis", "redis-cli", "GET", "ship:isolated"], capture_output=True, text=True)
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertEqual("", result.stdout.strip())


if __name__ == "__main__":
    unittest.main()
