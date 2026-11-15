"""Portable development commands and project dependency modes."""

from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from python_ship import __version__
from python_ship.cli import main
from python_ship.compose import build_compose
from python_ship.config import ShipConfig
from python_ship.uv import require_uv


class PhaseFiveTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="ship-phase5-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        (self.root / "main.py").write_text("async def app(scope, receive, send): pass\n")
        with patch("python_ship.cli.require_uv", return_value="/fake/uv"):
            self.assertEqual(0, main(["--project-directory", str(self.root), "init", "--app", "main:app"]))

    def test_ship_uv_runs_in_host_project_and_forwards_arguments(self):
        with patch("python_ship.cli.locate_uv", return_value="/path/to/uv"), patch(
            "python_ship.cli.subprocess.run", return_value=subprocess.CompletedProcess([], 23)
        ) as run:
            self.assertEqual(23, main(["--project-directory", str(self.root), "uv", "add", "httpx", "--dev"]))
        run.assert_called_once_with(["/path/to/uv", "add", "httpx", "--dev"], cwd=self.root, check=False)

    def test_ship_uv_explains_missing_binary_and_arguments(self):
        with patch("python_ship.cli.locate_uv", return_value=None), redirect_stderr(StringIO()) as errors:
            self.assertEqual(1, main(["--project-directory", str(self.root), "uv", "lock"]))
        self.assertIn("uv is required", errors.getvalue())
        with patch("python_ship.cli.locate_uv", return_value="/path/to/uv"), redirect_stderr(StringIO()) as errors:
            self.assertEqual(1, main(["--project-directory", str(self.root), "uv"]))
        self.assertIn("requires uv arguments", errors.getvalue())

    def test_linux_development_user_matches_host(self):
        model = build_compose(ShipConfig("generic", "main:app"), self.root)
        app = model["services"]["app"]
        if sys.platform.startswith("linux") and hasattr(os, "getuid"):
            self.assertEqual(f"{os.getuid()}:{os.getgid()}", app["user"])
        else:
            self.assertNotIn("user", app)
        production = build_compose(ShipConfig("generic", "main:app"), self.root, production=True)
        self.assertNotIn("user", production["services"]["app"])

    def test_uv_install_confirmation_paths(self):
        with patch("python_ship.uv.locate_uv", return_value="/existing/uv"), patch("builtins.input") as prompt:
            self.assertEqual("/existing/uv", require_uv())
        prompt.assert_not_called()
        with patch("python_ship.uv.locate_uv", return_value=None), patch("builtins.input", return_value="no"), patch("python_ship.uv._install_uv") as install:
            with self.assertRaisesRegex(ValueError, "cancelled"):
                require_uv()
        install.assert_not_called()
        with patch("python_ship.uv.locate_uv", side_effect=[None, "/installed/uv"]), patch("builtins.input", return_value="yes"), patch("python_ship.uv._install_uv") as install:
            self.assertEqual("/installed/uv", require_uv())
        install.assert_called_once_with()
        with patch("python_ship.uv.locate_uv", side_effect=[None, None]), patch("builtins.input", return_value="yes"), patch("python_ship.uv._install_uv"):
            with self.assertRaisesRegex(ValueError, "was not found"):
                require_uv()

    @unittest.skipUnless(os.environ.get("SHIP_UV_INSTALL_TEST") == "1" and os.name != "nt", "set SHIP_UV_INSTALL_TEST=1 for the real Unix installer")
    def test_official_uv_installer_in_isolated_home(self):
        with tempfile.TemporaryDirectory(prefix="ship-uv-installer-") as home:
            with patch.dict(os.environ, {"HOME": home, "UV_NO_MODIFY_PATH": "1"}), patch(
                "python_ship.uv.shutil.which", return_value=None
            ), patch("builtins.input", return_value="yes"):
                executable = require_uv()
            self.assertEqual(str(Path(home) / ".local" / "bin" / "uv"), executable)
            result = subprocess.run([executable, "--version"], capture_output=True, text=True)
            self.assertEqual(0, result.returncode, result.stderr)
            self.assertIn("uv ", result.stdout)

    def test_doctor_reports_versions_and_crashed_containers(self):
        crashed = json.dumps({"Service": "app", "State": "exited", "ExitCode": 3, "Publishers": []})
        with patch("python_ship.cli.shutil.which", return_value="/usr/bin/docker"), patch(
            "python_ship.cli.subprocess.run", return_value=subprocess.CompletedProcess([], 0, crashed, "")
        ), redirect_stderr(StringIO()) as errors:
            self.assertEqual(1, main(["--project-directory", str(self.root), "doctor"]))
        self.assertIn("app is exited", errors.getvalue())
        self.assertIn("ship logs SERVICE", errors.getvalue())

        healthy = json.dumps({"Service": "app", "State": "running", "Health": "healthy", "Publishers": [
            {"TargetPort": 8000, "PublishedPort": 8000, "Protocol": "tcp"}
        ]})
        with patch("python_ship.cli.shutil.which", return_value="/usr/bin/docker"), patch(
            "python_ship.cli.subprocess.run", return_value=subprocess.CompletedProcess([], 0, healthy, "")
        ), redirect_stdout(StringIO()) as output:
            self.assertEqual(0, main(["--project-directory", str(self.root), "doctor"]))
        self.assertIn(f"Published Dockerfile: {__version__}", output.getvalue())


if __name__ == "__main__":
    unittest.main()
