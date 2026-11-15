"""Checks for Phase 1 CLI behavior that does not require a Docker daemon."""

from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from python_ship import __version__
from python_ship.cli import main
from python_ship.config import ShipConfig
from python_ship.runtime import _records, _status, wait_for_stack
from python_ship.config import ConfigError


class PhaseOneTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        (self.root / "main.py").write_text("async def app(scope, receive, send): pass\n")

    def _init(self):
        with patch.object(Path, "cwd", return_value=self.root), patch("python_ship.cli.require_uv", return_value="/usr/bin/uv"):
            self.assertEqual(0, main(["init", "--app", "main:app"]))

    def test_typer_help_errors_and_completion(self):
        with redirect_stdout(StringIO()) as output:
            self.assertEqual(0, main(["--help"]))
        self.assertIn("config:test", output.getvalue())
        with redirect_stderr(StringIO()) as errors:
            self.assertEqual(2, main(["not-a-command"]))
        self.assertIn("No such command", errors.getvalue())
        with redirect_stdout(StringIO()) as output:
            try:
                result = main(["--show-completion", "bash"])
            except SystemExit as exc:
                result = exc.code
        self.assertEqual(0, result)
        self.assertIn("_ship_completion", output.getvalue())

    def test_exec_and_shell_forward_argv_and_exit_code(self):
        self._init()
        calls = []

        def run(argv, **kwargs):
            calls.append((argv, kwargs))
            return subprocess.CompletedProcess(argv, 17)

        with patch.object(Path, "cwd", return_value=self.root), patch("python_ship.cli.shutil.which", return_value="/usr/bin/docker"), patch("python_ship.cli.subprocess.run", side_effect=run):
            self.assertEqual(17, main(["exec", "app", "python", "-m", "pip", "--help", "--", "literal value"]))
            self.assertEqual(17, main(["shell"]))
        self.assertEqual(["exec", "app", "python", "-m", "pip", "--help", "--", "literal value"], calls[0][0][-8:])
        self.assertEqual(["exec", "app", "sh"], calls[1][0][-3:])
        self.assertNotIn("capture_output", calls[0][1])
        self.assertNotIn("stdin", calls[1][1])

    def test_manage_forwards_framework_flags(self):
        (self.root / "manage.py").write_text("")
        (self.root / "project").mkdir()
        (self.root / "project" / "asgi.py").write_text("application = object()\n")
        with patch.object(Path, "cwd", return_value=self.root), patch("python_ship.cli.require_uv", return_value="/usr/bin/uv"):
            self.assertEqual(0, main(["init", "--framework", "django", "--app", "project.asgi:application"]))
        with patch.object(Path, "cwd", return_value=self.root), patch("python_ship.cli.shutil.which", return_value="/usr/bin/docker"), patch("python_ship.cli.subprocess.run", return_value=subprocess.CompletedProcess([], 23)) as run:
            self.assertEqual(23, main(["manage", "migrate", "--database=other", "--noinput"]))
        self.assertEqual(["exec", "app", "python", "manage.py", "migrate", "--database=other", "--noinput"], run.call_args.args[0][-7:])

    def test_reinit_preserves_edits_and_warns_about_old_stub(self):
        self._init()
        config = ShipConfig("generic", "main:app", "3.12", {"cache": "redis"}, ["community"], "custom-name")
        config.write(self.root)
        dockerfile = self.root / "ship" / "Dockerfile"
        dockerfile.write_text("# custom Dockerfile\n")
        (self.root / "ship" / ".ship-version").write_text("0.0.0\n")
        with patch.object(Path, "cwd", return_value=self.root), patch("python_ship.cli.require_uv", return_value="/usr/bin/uv"), patch("python_ship.cli.build_compose", return_value={"services": {"app": {}}}), redirect_stderr(StringIO()) as errors:
            self.assertEqual(0, main(["init"]))
        self.assertIn("version differs", errors.getvalue())
        self.assertEqual("# custom Dockerfile\n", dockerfile.read_text())
        self.assertEqual(config, ShipConfig.read(self.root))
        with patch.object(Path, "cwd", return_value=self.root), patch("python_ship.cli.require_uv", return_value="/usr/bin/uv"), patch("python_ship.cli.build_compose", return_value={"services": {"app": {}}}):
            self.assertEqual(0, main(["init", "--force"]))
        self.assertNotEqual("# custom Dockerfile\n", dockerfile.read_text())
        self.assertEqual(__version__, (self.root / "ship" / ".ship-version").read_text().strip())

    def test_config_test_reports_independent_errors_without_docker(self):
        (self.root / "ship.json").write_text(json.dumps({"framework": "", "app": "", "python": "bad", "services": [], "extensions": 3, "extra": True}))
        with patch.object(Path, "cwd", return_value=self.root), redirect_stderr(StringIO()) as errors:
            self.assertEqual(1, main(["config:test"]))
        report = errors.getvalue()
        for marker in ("Unknown ship.json fields", "framework", "app import target", "python", "services", "extensions", "Dockerfile"):
            self.assertIn(marker, report)

    def test_config_test_reports_adapter_and_service_errors_together(self):
        (self.root / "ship.json").write_text(json.dumps({"framework": "generic", "app": "missing:app", "services": {"database": "missing"}}))
        (self.root / "ship").mkdir()
        (self.root / "ship" / "Dockerfile").write_text("FROM python:3.13-slim\n")
        with patch.object(Path, "cwd", return_value=self.root), redirect_stderr(StringIO()) as errors:
            self.assertEqual(1, main(["config:test"]))
        self.assertIn("does not exist", errors.getvalue())
        self.assertIn("Unknown service", errors.getvalue())

    def test_compose_status_and_failure_diagnostics(self):
        output = '\n'.join(json.dumps(record) for record in [
            {"Service": "app", "State": "running", "Health": "healthy", "Publishers": [{"TargetPort": 8000, "PublishedPort": 8000, "Protocol": "tcp"}]},
            {"Service": "postgres", "State": "exited", "ExitCode": 1},
        ])
        records = _records(output)
        fatal, pending, port = _status(records, {"app", "postgres"})
        self.assertIn("postgres is exited", fatal[0])
        self.assertEqual([], pending)
        self.assertEqual(8000, port)
        self.assertEqual(records, _records("[" + output.replace("\n", ",") + "]"))
        with patch("python_ship.runtime.subprocess.run", return_value=subprocess.CompletedProcess([], 0, output, "")):
            with self.assertRaisesRegex(ConfigError, "Check `ship logs`"):
                wait_for_stack(["docker", "compose"], {"app", "postgres"}, timeout=1)

    def test_stack_waits_for_running_health_and_reachable_port(self):
        starting = json.dumps({"Service": "app", "State": "running", "Health": "starting", "Publishers": []})
        ready = json.dumps({"Service": "app", "State": "running", "Health": "healthy", "Publishers": [{"TargetPort": 8000, "PublishedPort": 8000}]})
        results = [subprocess.CompletedProcess([], 0, starting, ""), subprocess.CompletedProcess([], 0, ready, "")]
        with patch("python_ship.runtime.subprocess.run", side_effect=results) as run, patch("python_ship.runtime.time.sleep"), patch("python_ship.runtime.socket.create_connection") as connect:
            wait_for_stack(["docker", "compose"], {"app"}, timeout=10)
        self.assertEqual(2, run.call_count)
        connect.assert_called_once_with(("127.0.0.1", 8000), timeout=1)

    def test_restarting_container_is_a_startup_failure(self):
        record = {"app": {"Service": "app", "State": "restarting", "ExitCode": 7, "Publishers": []}}
        fatal, _, _ = _status(record, {"app"})
        self.assertIn("app is restarting", fatal[0])


if __name__ == "__main__":
    unittest.main()
