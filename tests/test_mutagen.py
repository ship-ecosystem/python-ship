"""Optional Mutagen sync: session lifecycle, and a live opt-in check."""

from dataclasses import replace
import os
import subprocess
import time
import unittest
from unittest.mock import patch

from python_ship.compose import compose_path
from python_ship.config import ConfigError, ShipConfig
from python_ship.mutagen import IGNORES, _label, enabled, start, stop
from tests import vite
from tests.helpers import DockerProjectTestCase, ProjectTestCase, captured, flag, free_port, wait_http


class SessionTests(ProjectTestCase):
    def fake_run(self, listing="Watching|true|true\n", failing=None):
        calls = []

        def run(command, **kwargs):
            calls.append(command)
            if command[-3:-1] == ["ps", "-q"]:
                return subprocess.CompletedProcess(command, 0, command[-1] + "-container\n", "")
            if failing and failing in command:
                return subprocess.CompletedProcess(command, 1, "", f"{failing} broke")
            if "list" in command:
                return subprocess.CompletedProcess(command, 0, listing, "")
            return subprocess.CompletedProcess(command, 0, "", "")
        return calls, run

    def test_enabled_values(self):
        for value, expected in (("1", True), ("true", True), ("TRUE", True), ("0", False), ("", False), ("yes", False)):
            with self.subTest(value=value), patch.dict(os.environ, {"SHIP_MUTAGEN": value}):
                self.assertEqual(expected, enabled())

    def test_sessions_are_labelled_scoped_and_ignore_host_environments(self):
        calls, run = self.fake_run()
        with patch("python_ship.mutagen.shutil.which", return_value="mutagen"), patch("python_ship.mutagen.subprocess.run", side_effect=run):
            start(["docker", "compose"], self.root)
            stop(self.root)
        create = next(command for command in calls if "create" in command)
        self.assertIn("docker://app-container/app", create)
        self.assertIn("two-way-resolved", create)
        self.assertIn(_label(self.root), create)
        for pattern in IGNORES:
            self.assertIn(pattern, create)
        for pattern in (".venv", ".env", "node_modules", "ship/docker-compose.*.yml"):
            self.assertIn(pattern, IGNORES)
        self.assertEqual(["mutagen", "sync", "terminate", "--label-selector", _label(self.root)], calls[-1])

    def test_each_process_gets_its_own_session(self):
        calls, run = self.fake_run("Watching|true|true\nWatching|true|true\n")
        with patch("python_ship.mutagen.shutil.which", return_value="mutagen"), patch("python_ship.mutagen.subprocess.run", side_effect=run):
            start(["docker", "compose"], self.root, ("web", "worker"))
        created = [command for command in calls if "create" in command]
        self.assertEqual(2, len(created))
        self.assertTrue(any("docker://worker-container/app" in command for command in created))

    def test_failures_terminate_the_session(self):
        calls, run = self.fake_run(failing="create")
        with patch("python_ship.mutagen.shutil.which", return_value="mutagen"), \
                patch("python_ship.mutagen.subprocess.run", side_effect=run), self.assertRaisesRegex(ConfigError, "create broke"):
            start(["docker", "compose"], self.root)
        self.assertIn("terminate", calls[-1])

    def test_missing_binary_fails_before_compose_up(self):
        self.assertEqual(0, self.init("--app", "main:app"))
        with patch.dict(os.environ, {"SHIP_MUTAGEN": "1"}), \
                patch("shutil.which", side_effect=lambda name: "/usr/bin/docker" if name == "docker" else None), \
                patch("python_ship.cli.subprocess.run") as compose, captured() as output:
            self.assertEqual(1, self.ship("up"))
        compose.assert_not_called()
        self.assertIn("Mutagen CLI", output.stderr)

    def test_label_is_stable_per_project(self):
        self.assertEqual(_label(self.root), _label(self.root / "."))
        self.assertNotEqual(_label(self.root), _label(self.root.parent))


@unittest.skipUnless(flag("SHIP_MUTAGEN_TEST"), "set SHIP_MUTAGEN_TEST=1 (needs Docker and Mutagen)")
class LiveMutagenTests(DockerProjectTestCase):
    def source(self, body):
        return ("async def app(scope, receive, send):\n"
                "    await send({'type': 'http.response.start', 'status': 200, 'headers': []})\n"
                f"    await send({{'type': 'http.response.body', 'body': b'{body}'}})\n")

    def test_live_sync_reload_and_cleanup(self):
        import shutil
        if shutil.which("mutagen") is None:
            self.skipTest("Mutagen is required")
        port = free_port()
        (self.root / "main.py").write_text(self.source("before"))
        (self.root / ".venv").mkdir()
        (self.root / ".venv" / "host-only").write_text("ignore me")
        (self.root / ".env").write_text(f"APP_PORT={port}\n")
        with patch.dict(os.environ, {"SHIP_MUTAGEN": "1"}):
            self.assertEqual(0, self.init("--app", "main:app"))
            replace(ShipConfig.read(self.root), processes={"worker": ["python", "-c", "import time; time.sleep(600)"]}).write(self.root)
            try:
                with captured() as output:
                    result = self.ship("up")
                self.assertEqual(0, result, output.stderr)
                path = compose_path(self.root)
                inspect = subprocess.run(["docker", "compose", "-f", str(path), "exec", "-T", "app", "sh", "-c",
                                          "test ! -e /app/.venv && test ! -e /app/.env"], cwd=self.root)
                self.assertEqual(0, inspect.returncode)
                wait_http(port, expected=b"before")
                (self.root / "main.py").write_text(self.source("after"))
                wait_http(port, expected=b"after", timeout=90)
                deadline = time.monotonic() + 30
                while True:
                    worker = subprocess.run(["docker", "compose", "-f", str(path), "exec", "-T", "worker", "python", "-c",
                                             "assert 'after' in open('/app/main.py').read()"], cwd=self.root, capture_output=True, text=True)
                    if worker.returncode == 0 or time.monotonic() > deadline:
                        break
                    time.sleep(1)
                self.assertEqual(0, worker.returncode, worker.stderr)
            finally:
                with captured():
                    self.assertEqual(0, self.ship("down"))
        sessions = subprocess.run(["mutagen", "sync", "list", "--label-selector", _label(self.root), "--template", "{{range .}}{{.Name}}{{end}}"],
                                  capture_output=True, text=True)
        self.assertEqual("", sessions.stdout.strip())

    def test_vite_hmr_in_the_synced_node_container(self):
        import shutil
        if shutil.which("mutagen") is None:
            self.skipTest("Mutagen is required")
        ports = {"APP_PORT": free_port(), "VITE_PORT": free_port()}
        (self.root / ".env").write_text("".join(f"{name}={port}\n" for name, port in ports.items()))
        vite.write_project(self.root)
        with patch.dict(os.environ, {"SHIP_MUTAGEN": "1"}):
            self.assertEqual(0, self.init("--app", "main:app", "--frontend", "node"))
            try:
                with captured() as output:
                    self.assertEqual(0, self.ship("up"), output.stderr)
                inspect = subprocess.run(["docker", "compose", "-f", str(compose_path(self.root)), "exec", "-T", "node", "sh", "-c",
                                          "test -f /app/vite.config.js && ! grep -q ' /app ' /proc/self/mounts"],
                                         cwd=self.root, capture_output=True, text=True)
                self.assertEqual(0, inspect.returncode, "node should see a Mutagen copy, not a bind mount")
                vite.assert_hmr(self, self.root, ports["VITE_PORT"], ports["APP_PORT"], self.ship)
            finally:
                with captured():
                    self.assertEqual(0, self.ship("down"))


if __name__ == "__main__":
    unittest.main()
