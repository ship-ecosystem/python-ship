"""The `ship` command line: argument forwarding, exit codes and Docker invocation."""

import json
import subprocess
import sys
import unittest
from unittest.mock import patch

from python_ship import __version__
from python_ship.cli import main
from python_ship.config import AdditionalService
from tests.helpers import FIXTURES, ProjectTestCase, captured, fake_docker


class GlobalOptionTests(ProjectTestCase):
    def test_version(self):
        for option in ("--version", "-V"):
            with self.subTest(option=option), captured() as output:
                self.assertEqual(0, main([option]))
            self.assertEqual(f"ship (python-ship) {__version__}\n", output.stdout)

    def test_help_lists_php_ship_commands(self):
        with captured() as output:
            self.assertEqual(0, main(["--help"]))
        for command in ("init", "up", "down", "exec", "shell", "logs", "db", "config:test", "build", "release",
                        "npm", "uv", "doctor"):
            self.assertIn(command, output.stdout)
        with captured() as output:
            self.assertEqual(0, main([]))
        self.assertIn("Usage", output.stdout + output.stderr)

    def test_unknown_command_and_bad_option_exit_2(self):
        with captured() as output:
            self.assertEqual(2, main(["not-a-command"]))
        self.assertIn("No such command", output.stderr)
        with captured():
            self.assertEqual(2, main(["down", "--nope"]))

    def test_shell_completion(self):
        with captured() as output:
            try:
                result = main(["--show-completion", "bash"])
            except SystemExit as exc:
                result = exc.code
        self.assertEqual(0, result)
        self.assertIn("_ship_completion", output.stdout)

    def test_interrupts_exit_130(self):
        with patch("python_ship.cli.run_init", side_effect=KeyboardInterrupt), captured() as output:
            self.assertEqual(130, self.ship("init", "-n", "--app", "main:app"))
        self.assertNotIn("Traceback", output.stderr)

    def test_missing_config_is_a_clean_error(self):
        with fake_docker(), captured() as output:
            self.assertEqual(1, self.ship("up"))
        self.assertIn("run `ship init` first", output.stderr)


class DevelopmentCommandTests(ProjectTestCase):
    def setUp(self):
        super().setUp()
        self.assertEqual(0, self.init("--app", "main:app", "--database", "postgres"))

    def compose_args(self, command):
        return command[command.index("compose") + 1:]

    def test_exec_forwards_arguments_unchanged_and_returns_the_exit_code(self):
        with fake_docker(returncode=17) as docker:
            self.assertEqual(17, self.ship("exec", "app", "python", "-m", "pip", "--help", "--", "literal value", "--project-directory"))
        self.assertEqual(["exec", "app", "python", "-m", "pip", "--help", "--", "literal value", "--project-directory"], docker.last[-9:])
        self.assertNotIn("capture_output", docker.calls[0][1])
        self.assertEqual(self.root.resolve(), docker.calls[0][1]["cwd"])

    def test_exec_without_a_terminal_disables_tty_allocation(self):
        with fake_docker(tty=False) as docker:
            self.ship("exec", "app", "true")
            self.ship("db")
        self.assertEqual(["exec", "-T", "app", "true"], docker.calls[0][0][-4:])
        self.assertIn("-T", docker.calls[1][0])

    def test_exec_requires_a_command(self):
        with fake_docker() as docker, captured() as output:
            self.assertEqual(1, self.ship("exec", "app"))
        self.assertEqual([], docker.calls)
        self.assertIn("requires a command", output.stderr)

    def test_shell_logs_down_and_up(self):
        with fake_docker() as docker, captured():
            self.assertEqual(0, self.ship("shell"))
            self.assertEqual(0, self.ship("shell", "postgres"))
            self.assertEqual(0, self.ship("logs", "app", "-f"))
            self.assertEqual(0, self.ship("logs"))
            self.assertEqual(0, self.ship("down", "--volumes"))
            self.assertEqual(0, self.ship("up"))
        commands = [call[0] for call in docker.calls]
        self.assertEqual(["exec", "app", "sh"], commands[0][-3:])
        self.assertEqual(["exec", "postgres", "sh"], commands[1][-3:])
        self.assertEqual(["logs", "--follow", "app"], commands[2][-3:])
        self.assertEqual("logs", commands[3][-1])
        self.assertEqual(["down", "--volumes"], commands[4][-2:])
        self.assertEqual(["up", "--build", "--detach"], commands[5][-3:])
        compose = self.root / "ship" / "docker-compose.generated.yml"
        for command in commands:
            self.assertEqual(["docker", "compose", "--project-directory", str(self.root.resolve()), "-f", str(compose.resolve())],
                             [part if not part.startswith("/") else str(type(self.root)(part).resolve()) for part in command[:6]])

    def test_override_file_applies_to_every_development_command(self):
        for name in ("docker-compose.override.yml", "compose.override.yaml"):
            with self.subTest(name=name):
                override = self.root / name
                override.write_text("services:\n  app:\n    environment:\n      X: y\n")
                with fake_docker() as docker, captured():
                    for arguments in (["up"], ["logs"], ["exec", "app", "true"], ["db"], ["shell"], ["down"]):
                        self.assertEqual(0, self.ship(*arguments))
                for command, _ in docker.calls:
                    self.assertIn(str(override), command)
                override.unlink()

    def test_project_directory_works_from_anywhere(self):
        with fake_docker() as docker, patch("python_ship.cli.Path.cwd", return_value=FIXTURES):
            self.assertEqual(0, main(["--project-directory", str(self.root), "exec", "app", "true"]))
            self.assertEqual(0, main([f"--project-directory={self.root}", "exec", "app", "true"]))
        for _, kwargs in docker.calls:
            self.assertEqual(self.root.resolve(), kwargs["cwd"])

    def test_docker_missing_is_reported(self):
        with patch("python_ship.cli.shutil.which", return_value=None), captured() as output:
            self.assertEqual(1, self.ship("up"))
        self.assertIn("Docker is required", output.stderr)

    def test_up_refuses_broken_stubs_and_warns_about_stale_ones(self):
        (self.root / "ship" / ".ship-version").write_text("0.0.1\n")
        with fake_docker(), captured() as output:
            self.assertEqual(0, self.ship("up"))
        self.assertIn("published by python-ship 0.0.1", output.stderr)
        self.assertIn("http://127.0.0.1:8000", output.stdout)
        (self.root / "ship" / "check-python-security-floor.py").unlink()
        with fake_docker() as docker, captured() as output:
            self.assertEqual(1, self.ship("up"))
        self.assertEqual([], docker.calls)
        self.assertIn("check-python-security-floor.py is missing", output.stderr)

    def test_failed_compose_up_is_returned(self):
        with fake_docker(returncode=3), captured():
            self.assertEqual(3, self.ship("up"))


class DatabaseCommandTests(ProjectTestCase):
    def test_default_and_named_database_shells(self):
        self.write_config(app="main:app", services={"database": "postgres"},
                          additional_services=[AdditionalService("database", "mysql", "legacy")])
        self.assertEqual(0, self.init())
        with fake_docker() as docker, captured():
            self.assertEqual(0, self.ship("db"))
            self.assertEqual(0, self.ship("db", "legacy"))
        self.assertEqual(["exec", "postgres", "sh", "-c", 'exec psql -U "$POSTGRES_USER" -d "$POSTGRES_DB"'], docker.calls[0][0][-5:])
        self.assertEqual(["exec", "mysql-legacy", "sh", "-c"], docker.calls[1][0][-5:-1])
        with fake_docker() as docker, captured() as output:
            self.assertEqual(1, self.ship("db", "missing"))
        self.assertIn("additional database instances: legacy", output.stderr)
        self.assertEqual([], docker.calls)

    def test_without_a_database(self):
        self.assertEqual(0, self.init("--app", "main:app"))
        with fake_docker() as docker, captured() as output:
            self.assertEqual(1, self.ship("db"))
        self.assertIn("No database is selected", output.stderr)
        self.assertEqual([], docker.calls)

    def test_only_named_databases(self):
        self.write_config(app="main:app", additional_services=[AdditionalService("database", "postgres", "reports")])
        self.assertEqual(0, self.init())
        with fake_docker(), captured() as output:
            self.assertEqual(1, self.ship("db"))
        self.assertIn("name one of the additional instances: reports", output.stderr)


class ProxyCommandTests(ProjectTestCase):
    def test_manage_is_registered_for_django_and_forwards_flags(self):
        self.copy_fixture("django_app")
        self.assertEqual(0, self.init())
        with fake_docker(returncode=23) as docker:
            self.assertEqual(23, self.ship("manage", "migrate", "--database=other", "--noinput", "--help"))
        self.assertEqual(["exec", "app", "python", "manage.py", "migrate", "--database=other", "--noinput", "--help"], docker.last[-8:])

    def test_manage_is_absent_for_other_frameworks(self):
        self.assertEqual(0, self.init("--app", "main:app"))
        with captured() as output:
            self.assertEqual(2, self.ship("manage", "check"))
        self.assertIn("No such command 'manage'", output.stderr)

    def test_renamed_app_is_followed_by_shell_and_proxies(self):
        self.copy_fixture("django_app")
        self.write_config(framework="django", app="project.asgi:application", service_names={"app": "web"})
        self.assertEqual(0, self.init())
        with fake_docker() as docker:
            self.ship("manage", "check")
            self.ship("shell")
        self.assertEqual(["exec", "web", "python", "manage.py", "check"], docker.calls[0][0][-5:])
        self.assertEqual(["exec", "web", "sh"], docker.calls[1][0][-3:])

    def test_npm_runs_in_the_node_service(self):
        self.assertEqual(0, self.init("--app", "main:app", "--frontend", "node"))
        with fake_docker(returncode=5) as docker:
            self.assertEqual(5, self.ship("npm", "run", "dev", "--", "--host", "0.0.0.0"))
        self.assertEqual(["exec", "node", "npm", "run", "dev", "--", "--host", "0.0.0.0"], docker.last[-8:])

    def test_npm_without_node(self):
        self.assertEqual(0, self.init("--app", "main:app"))
        with fake_docker() as docker, captured() as output:
            self.assertEqual(1, self.ship("npm", "install"))
        self.assertEqual([], docker.calls)
        self.assertIn("--frontend node", output.stderr)


class ConfigTestCommandTests(ProjectTestCase):
    def test_valid_project(self):
        self.assertEqual(0, self.init("--app", "main:app"))
        with captured() as output:
            self.assertEqual(0, self.ship("config:test"))
        self.assertIn("both Compose models are valid", output.stdout)

    def test_reports_schema_stub_adapter_service_and_credential_problems_together(self):
        (self.root / "ship.json").write_text(json.dumps({"framework": "", "app": "", "python": "bad", "services": [], "extra": True}))
        with captured() as output:
            self.assertEqual(1, self.ship("config:test"))
        for marker in ("unknown fields", '"framework"', '"app"', '"python"', '"services"', "ship/Dockerfile is missing", "problems found"):
            self.assertIn(marker, output.stderr)

        self.assertEqual(0, self.init("--app", "main:app"))
        self.write_config(app="missing:app", services={"database": "nope", "storage": "silo"}, processes={"silo": "x"})
        with captured() as output:
            self.assertEqual(1, self.ship("config:test"))
        self.assertIn("does not exist", output.stderr)
        self.assertIn("Unknown service 'nope'", output.stderr)
        self.assertIn("Process 'silo' conflicts", output.stderr)

        self.write_config(app="main:app", services={"database": "postgres", "storage": "silo"})
        (self.root / ".env.production").write_text("DB_PASSWORD=ship\n")
        with captured() as output:
            self.assertEqual(1, self.ship("config:test"))
        self.assertIn("missing from .env.production: AWS_SECRET_ACCESS_KEY", output.stderr)
        self.assertIn("development default: DB_PASSWORD", output.stderr)

    def test_never_invokes_docker(self):
        self.assertEqual(0, self.init("--app", "main:app"))
        with patch("python_ship.cli.subprocess.run") as run, captured():
            self.ship("config:test")
        run.assert_not_called()


class DoctorTests(ProjectTestCase):
    def setUp(self):
        super().setUp()
        self.assertEqual(0, self.init("--app", "main:app", "--database", "postgres"))

    def test_reports_crashed_and_healthy_stacks(self):
        crashed = "\n".join(json.dumps(record) for record in (
            {"Service": "app", "State": "exited", "ExitCode": 3, "Publishers": []},
            {"Service": "postgres", "State": "running", "Health": "healthy"},
        ))
        with fake_docker(stdout=crashed), captured() as output:
            self.assertEqual(1, self.ship("doctor"))
        self.assertIn("app is exited", output.stderr)
        self.assertIn("ship logs SERVICE", output.stderr)
        self.assertIn(f"python-ship {__version__}", output.stdout)
        healthy = "\n".join(json.dumps(record) for record in (
            {"Service": "app", "State": "running", "Publishers": [{"TargetPort": 8000, "PublishedPort": 18000, "Protocol": "tcp"}]},
            {"Service": "postgres", "State": "running", "Health": "healthy"},
        ))
        with fake_docker(stdout=healthy), captured() as output:
            self.assertEqual(0, self.ship("doctor"))
        self.assertIn("postgres: postgres:", output.stdout)
        self.assertIn("http://127.0.0.1:18000", output.stdout)

    def test_does_not_require_production_credentials(self):
        healthy = json.dumps([{"Service": "app", "State": "running", "Publishers": [{"TargetPort": 8000, "PublishedPort": 8000}]},
                              {"Service": "postgres", "State": "running", "Health": "healthy"}])
        with fake_docker(stdout=healthy), captured():
            self.assertEqual(0, self.ship("doctor"))

    def test_compose_failure(self):
        with fake_docker(returncode=1), captured() as output:
            self.assertEqual(1, self.ship("doctor"))
        self.assertIn("Could not inspect Docker Compose", output.stderr)


class EntryPointTests(unittest.TestCase):
    def test_python_dash_m(self):
        result = subprocess.run([sys.executable, "-m", "python_ship", "--version"], capture_output=True, text=True)
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertIn(__version__, result.stdout)


if __name__ == "__main__":
    unittest.main()
