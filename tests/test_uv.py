"""`ship uv` and the host uv requirement of `ship init`."""

import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from python_ship.config import ConfigError
from python_ship.uv import UV_VERSION, locate_uv, require_uv
from tests.helpers import ProjectTestCase, captured, fake_docker, flag


class ShipUvTests(ProjectTestCase):
    def setUp(self):
        super().setUp()
        self.assertEqual(0, self.init("--app", "main:app", "--python", "3.12"))

    def test_runs_in_an_isolated_container_and_forwards_arguments(self):
        with fake_docker(returncode=23) as docker:
            self.assertEqual(23, self.ship("uv", "add", "httpx", "--dev", "--project-directory"))
        command, kwargs = docker.calls[-1]
        self.assertEqual(["docker", "run", "--rm", "--interactive"], command[:4])
        self.assertEqual(["uv", "add", "httpx", "--dev", "--project-directory"], command[-5:])
        self.assertIn(f"{self.root.resolve()}:/app", command)
        self.assertIn(f"ghcr.io/astral-sh/uv:{UV_VERSION}-python3.12-trixie-slim", command)
        for setting in ("UV_NO_SYNC=1", "UV_PROJECT_ENVIRONMENT=/tmp/ship-uv-venv", "UV_PYTHON=python3.12"):
            self.assertIn(setting, command)
        if sys.platform.startswith("linux"):
            self.assertIn(f"{os.getuid()}:{os.getgid()}", command)
        self.assertEqual(self.root.resolve(), kwargs["cwd"])

    def test_pinned_python_release_uses_its_branch_image(self):
        self.write_config(app="main:app", python="3.13.18")
        with fake_docker() as docker:
            self.ship("uv", "lock")
        self.assertIn(f"ghcr.io/astral-sh/uv:{UV_VERSION}-python3.13-trixie-slim", docker.last)

    def test_refuses_commands_whose_effects_would_vanish(self):
        for subcommand in ("sync", "run", "pip", "python", "tool", "venv"):
            with self.subTest(subcommand=subcommand), fake_docker() as docker, captured() as output:
                self.assertEqual(1, self.ship("uv", subcommand))
            self.assertEqual([], docker.calls)
            self.assertIn("ship up", output.stderr)
        with fake_docker(), captured() as output:
            self.assertEqual(1, self.ship("uv"))
        self.assertIn("requires uv arguments", output.stderr)

    def test_missing_docker(self):
        with patch("python_ship.cli.shutil.which", return_value=None), captured() as output:
            self.assertEqual(1, self.ship("uv", "lock"))
        self.assertIn("Docker is required", output.stderr)


class RequireUvTests(unittest.TestCase):
    def test_existing_uv_needs_no_prompt(self):
        with patch("python_ship.uv.locate_uv", return_value="/existing/uv"), patch("builtins.input") as prompt:
            self.assertEqual("/existing/uv", require_uv())
        prompt.assert_not_called()

    def test_decline_install_failure_and_missing_binary(self):
        with patch("python_ship.uv.locate_uv", return_value=None), patch("builtins.input", return_value="no"), \
                patch("python_ship.uv._install_uv") as install, self.assertRaisesRegex(ConfigError, "cancelled"):
            require_uv()
        install.assert_not_called()
        with patch("python_ship.uv.locate_uv", return_value=None), patch("builtins.input", side_effect=EOFError), \
                self.assertRaisesRegex(ConfigError, "not confirmed"):
            require_uv()
        with patch("python_ship.uv.locate_uv", side_effect=[None, "/installed/uv"]), patch("builtins.input", return_value="yes"), \
                patch("python_ship.uv._install_uv") as install:
            self.assertEqual("/installed/uv", require_uv())
        install.assert_called_once_with()
        with patch("python_ship.uv.locate_uv", side_effect=[None, None]), patch("builtins.input", return_value="y"), \
                patch("python_ship.uv._install_uv"), self.assertRaisesRegex(ConfigError, "was not found"):
            require_uv()

    def test_locate_falls_back_to_the_installer_directory(self):
        with tempfile.TemporaryDirectory() as home:
            binary = Path(home) / ".local" / "bin" / ("uv.exe" if os.name == "nt" else "uv")
            with patch("python_ship.uv.shutil.which", return_value=None), patch("python_ship.uv.Path.home", return_value=Path(home)):
                self.assertIsNone(locate_uv())
                binary.parent.mkdir(parents=True)
                binary.write_text("")
                binary.chmod(0o755)
                self.assertEqual(str(binary), locate_uv())

    @unittest.skipUnless(flag("SHIP_UV_INSTALL_TEST"), "set SHIP_UV_INSTALL_TEST=1 to run the official installer")
    def test_official_uv_installer_in_isolated_home(self):
        with tempfile.TemporaryDirectory(prefix="ship-uv-installer-") as home:
            original_which = shutil.which
            installer_env = {"UV_NO_MODIFY_PATH": "1"}
            if os.name != "nt":
                installer_env["HOME"] = home
            with patch.dict(os.environ, installer_env), patch("python_ship.uv.Path.home", return_value=Path(home)), \
                    patch("python_ship.uv.shutil.which", side_effect=lambda name: None if name == "uv" else original_which(name)), \
                    patch("builtins.input", return_value="yes"):
                executable = require_uv()
            self.assertEqual(str(Path(home) / ".local" / "bin" / ("uv.exe" if os.name == "nt" else "uv")), executable)
            result = subprocess.run([executable, "--version"], capture_output=True, text=True)
            self.assertEqual(0, result.returncode, result.stderr)
            self.assertIn(UV_VERSION, result.stdout)

    @unittest.skipUnless(flag("SHIP_CLI_BOOTSTRAP_TEST"), "set SHIP_CLI_BOOTSTRAP_TEST=1 to install the CLI with uv-managed Python")
    def test_cli_installs_with_uv_managed_python_only(self):
        uv = shutil.which("uv")
        self.assertIsNotNone(uv)
        with tempfile.TemporaryDirectory(prefix="ship-cli-bootstrap-") as home:
            base = Path(home)
            environment = {**os.environ, "HOME": home, "UV_PYTHON_PREFERENCE": "only-managed",
                           "UV_PYTHON_INSTALL_DIR": str(base / "python"), "UV_TOOL_DIR": str(base / "tools"),
                           "UV_TOOL_BIN_DIR": str(base / "bin"), "UV_CACHE_DIR": str(base / "cache"), "UV_NO_PROGRESS": "1"}
            repository = Path(__file__).parents[1]
            installed = subprocess.run([uv, "tool", "install", "--python", "3.13", "."], cwd=repository, env=environment,
                                       capture_output=True, text=True)
            self.assertEqual(0, installed.returncode, installed.stderr)
            executable = base / "bin" / ("ship.exe" if os.name == "nt" else "ship")
            result = subprocess.run([str(executable), "--version"], env=environment, capture_output=True, text=True)
            self.assertEqual(0, result.returncode, result.stderr)
            self.assertIn("python-ship", result.stdout)


if __name__ == "__main__":
    unittest.main()
