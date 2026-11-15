"""Mutagen Compose integration and session lifecycle."""

from contextlib import redirect_stderr
from io import StringIO
import os
from pathlib import Path
import subprocess
import socket
import tempfile
import time
import unittest
from unittest.mock import patch
from urllib.request import urlopen

from python_ship.cli import main
from python_ship.compose import build_compose
from python_ship.config import ShipConfig
from python_ship.mutagen import IGNORES, _label, start, stop


class MutagenTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="ship-mutagen-test-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        (self.root / "main.py").write_text("async def app(scope, receive, send): pass\n")

    def test_compose_mode_excludes_bind_mount_and_keeps_production_independent(self):
        config = ShipConfig("generic", "main:app")
        with patch.dict(os.environ, {"SHIP_MUTAGEN": "1"}):
            app = build_compose(config, self.root)["services"]["app"]
            self.assertNotIn("volumes", app)
            self.assertNotIn("user", app)
            production = build_compose(config, self.root, production=True)["services"]["app"]
            self.assertNotIn("volumes", production)
        self.assertEqual([".:/app"], build_compose(config, self.root)["services"]["app"]["volumes"])

    def test_session_is_scoped_and_ignores_host_environments(self):
        calls = []
        def run(command, **kwargs):
            calls.append(command)
            if command[-3:] == ["ps", "-q", "app"]:
                return subprocess.CompletedProcess(command, 0, "container123\n", "")
            if "list" in command:
                return subprocess.CompletedProcess(command, 0, "Watching|true|true\n", "")
            return subprocess.CompletedProcess(command, 0, "", "")
        with patch("python_ship.mutagen.shutil.which", return_value="mutagen"), patch(
            "python_ship.mutagen.subprocess.run", side_effect=run
        ):
            start(["docker", "compose"], self.root)
            stop(self.root)
        create = next(command for command in calls if "create" in command)
        self.assertIn("docker://container123/app", create)
        self.assertIn("--mode", create)
        for pattern in IGNORES:
            self.assertIn(pattern, create)
        terminate = calls[-1]
        self.assertEqual(["mutagen", "sync", "terminate", "--label-selector", _label(self.root)], terminate)

    def test_missing_binary_fails_before_compose_up(self):
        with patch("python_ship.cli.require_uv", return_value="uv"):
            self.assertEqual(0, main(["--project-directory", str(self.root), "init", "--app", "main:app"]))
        with patch.dict(os.environ, {"SHIP_MUTAGEN": "1"}), patch(
            "python_ship.mutagen.shutil.which", side_effect=lambda name: "docker" if name == "docker" else None
        ), patch(
            "python_ship.cli.subprocess.run"
        ) as compose, redirect_stderr(StringIO()) as errors:
            self.assertEqual(1, main(["--project-directory", str(self.root), "up"]))
        compose.assert_not_called()
        self.assertIn("Mutagen CLI", errors.getvalue())

    @unittest.skipUnless(os.environ.get("SHIP_MUTAGEN_TEST") == "1", "set SHIP_MUTAGEN_TEST=1 for live Docker/Mutagen test")
    def test_live_sync_reload_and_cleanup(self):
        if not __import__("shutil").which("docker") or not __import__("shutil").which("mutagen"):
            self.skipTest("Docker and Mutagen are required")
        with socket.socket() as listener:
            listener.bind(("127.0.0.1", 0))
            port = listener.getsockname()[1]
        def source(body):
            return ("async def app(scope, receive, send):\n"
                    "    await send({'type': 'http.response.start', 'status': 200, 'headers': []})\n"
                    f"    await send({{'type': 'http.response.body', 'body': b'{body}'}})\n")
        (self.root / "main.py").write_text(source("before"))
        (self.root / ".venv").mkdir()
        (self.root / ".venv" / "host-only").write_text("ignore me")
        (self.root / ".env").write_text("SHIP_TEST_SECRET=ignore-me\n")
        with patch.dict(os.environ, {"SHIP_MUTAGEN": "1", "SHIP_APP_PORT": str(port)}), patch(
            "python_ship.cli.require_uv", return_value="uv"
        ):
            self.assertEqual(0, main(["--project-directory", str(self.root), "init", "--app", "main:app"]))
            try:
                self.assertEqual(0, main(["--project-directory", str(self.root), "up"]))
                model = build_compose(ShipConfig.read(self.root), self.root)
                self.assertNotIn("volumes", model["services"]["app"])
                compose_path = self.root / "ship" / "compose.generated.json"
                inspect = subprocess.run(["docker", "compose", "-f", str(compose_path), "exec", "-T", "app", "sh", "-c",
                                          "test ! -e /app/.venv && test ! -e /app/.env"], cwd=self.root)
                self.assertEqual(0, inspect.returncode)
                deadline = time.monotonic() + 30
                while time.monotonic() < deadline:
                    try:
                        if urlopen(f"http://127.0.0.1:{port}", timeout=2).read() == b"before":
                            break
                    except OSError:
                        pass
                    time.sleep(1)
                else:
                    self.fail("The initial app response did not become available")
                (self.root / "main.py").write_text(source("after"))
                deadline = time.monotonic() + 60
                while time.monotonic() < deadline:
                    try:
                        if urlopen(f"http://127.0.0.1:{port}", timeout=2).read() == b"after":
                            break
                    except OSError:
                        pass
                    time.sleep(1)
                else:
                    self.fail("Mutagen edit did not reload the app")
                self.assertTrue((self.root / ".venv" / "host-only").is_file())
            finally:
                self.assertEqual(0, main(["--project-directory", str(self.root), "down"]))
            sessions = subprocess.run(["mutagen", "sync", "list", "--label-selector", _label(self.root),
                                       "--template", '{{range .}}{{.Name}}{{end}}'], capture_output=True, text=True)
            self.assertEqual("", sessions.stdout.strip())


if __name__ == "__main__":
    unittest.main()
