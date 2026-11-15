"""Portable development commands and project dependency modes."""

from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from python_ship import __version__
from python_ship.cli import main
from python_ship.compose import build_compose
from python_ship.config import ShipConfig
from python_ship.config import ConfigError
from python_ship.uv import UV_VERSION, require_uv


class PhaseFiveTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="ship-phase5-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        (self.root / "main.py").write_text("async def app(scope, receive, send): pass\n")
        with patch("python_ship.cli.require_uv", return_value="/fake/uv"):
            self.assertEqual(0, main(["--project-directory", str(self.root), "init", "--app", "main:app"]))

    def test_ship_uv_uses_isolated_container_and_forwards_arguments(self):
        with patch("python_ship.cli.shutil.which", return_value="/usr/bin/docker"), patch(
            "python_ship.cli.subprocess.run", return_value=subprocess.CompletedProcess([], 23)
        ) as run:
            self.assertEqual(23, main(["--project-directory", str(self.root), "uv", "add", "httpx", "--dev"]))
        command = run.call_args.args[0]
        self.assertEqual(["docker", "run", "--rm", "--interactive"], command[:4])
        self.assertEqual(["uv", "add", "httpx", "--dev"], command[-4:])
        self.assertIn(f"{self.root.resolve()}:/app", command)
        self.assertIn(f"ghcr.io/astral-sh/uv:{UV_VERSION}-python3.13-trixie-slim", command)
        self.assertIn("UV_NO_SYNC=1", command)
        self.assertIn("UV_PROJECT_ENVIRONMENT=/tmp/ship-uv-venv", command)
        self.assertEqual(self.root.resolve(), run.call_args.kwargs["cwd"])

    def test_ship_uv_explains_missing_docker_and_unsafe_commands(self):
        with patch("python_ship.cli.shutil.which", return_value=None), redirect_stderr(StringIO()) as errors:
            self.assertEqual(1, main(["--project-directory", str(self.root), "uv", "lock"]))
        self.assertIn("Docker is required", errors.getvalue())
        with redirect_stderr(StringIO()) as errors:
            self.assertEqual(1, main(["--project-directory", str(self.root), "uv"]))
        self.assertIn("requires uv arguments", errors.getvalue())
        with redirect_stderr(StringIO()) as errors:
            self.assertEqual(1, main(["--project-directory", str(self.root), "uv", "sync"]))
        self.assertIn("ship up", errors.getvalue())

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

    @unittest.skipUnless(os.environ.get("SHIP_UV_INSTALL_TEST") == "1", "set SHIP_UV_INSTALL_TEST=1 for the real platform installer")
    def test_official_uv_installer_in_isolated_home(self):
        with tempfile.TemporaryDirectory(prefix="ship-uv-installer-") as home:
            original_which = shutil.which
            # UV_INSTALL_DIR isolates the install without changing the system
            # profile used by PowerShell on Windows.
            installer_env = {"UV_NO_MODIFY_PATH": "1"}
            if os.name != "nt":
                installer_env["HOME"] = home
            with patch.dict(os.environ, installer_env), patch(
                "python_ship.uv.Path.home", return_value=Path(home)
            ), patch("python_ship.uv.shutil.which", side_effect=lambda name: None if name == "uv" else original_which(name)), patch("builtins.input", return_value="yes"):
                executable = require_uv()
            self.assertEqual(str(Path(home) / ".local" / "bin" / ("uv.exe" if os.name == "nt" else "uv")), executable)
            result = subprocess.run([executable, "--version"], capture_output=True, text=True)
            self.assertEqual(0, result.returncode, result.stderr)
            self.assertIn("uv ", result.stdout)

    def test_init_uv_decline_and_installer_failure_leave_project_untouched(self):
        for outcome in ("decline", "failure"):
            with self.subTest(outcome=outcome), tempfile.TemporaryDirectory(prefix="ship-uv-block-") as directory:
                root = Path(directory)
                (root / "main.py").write_text("async def app(scope, receive, send): pass\n")
                with patch("python_ship.uv.locate_uv", return_value=None), patch(
                    "builtins.input", return_value="no" if outcome == "decline" else "yes"
                ), patch("python_ship.uv._install_uv", side_effect=ConfigError("installer failed")) as installer, redirect_stderr(StringIO()):
                    self.assertEqual(1, main(["--project-directory", str(root), "init", "--app", "main:app"]))
                self.assertEqual(outcome == "failure", installer.called)
                self.assertEqual(["main.py"], sorted(path.name for path in root.iterdir()))

    @unittest.skipUnless(os.environ.get("SHIP_CLI_BOOTSTRAP_TEST") == "1", "set SHIP_CLI_BOOTSTRAP_TEST=1 for isolated CLI installation")
    def test_cli_installs_with_uv_managed_python_only(self):
        uv = shutil.which("uv")
        self.assertIsNotNone(uv)
        with tempfile.TemporaryDirectory(prefix="ship-cli-bootstrap-") as home:
            base = Path(home)
            environment = {
                **os.environ,
                "HOME": home,
                "UV_PYTHON_PREFERENCE": "only-managed",
                "UV_PYTHON_INSTALL_DIR": str(base / "python"),
                "UV_TOOL_DIR": str(base / "tools"),
                "UV_TOOL_BIN_DIR": str(base / "bin"),
                "UV_CACHE_DIR": str(base / "cache"),
                "UV_NO_PROGRESS": "1",
            }
            repository = Path(__file__).parents[1]
            installed = subprocess.run([uv, "tool", "install", "--python", "3.13", "."], cwd=repository, env=environment, capture_output=True, text=True)
            self.assertEqual(0, installed.returncode, installed.stderr)
            self.assertTrue(any((base / "python").iterdir()))
            executable = base / "bin" / ("ship.exe" if os.name == "nt" else "ship")
            result = subprocess.run([str(executable), "--help"], env=environment, capture_output=True, text=True)
            self.assertEqual(0, result.returncode, result.stderr)
            self.assertIn("config:test", result.stdout)

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
