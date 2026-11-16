"""Compose and extension behavior introduced in Phase 3."""

import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from python_ship.adapters import BUILTIN_ADAPTERS
from python_ship.cli import main
from python_ship.compose import build_compose, write_compose
from python_ship.config import ConfigError, ShipConfig
from python_ship.plugins import load_extensions
from python_ship.services import BUILTIN_SERVICES


FIXTURE = Path(__file__).parent / "fixtures" / "plain_asgi"


class PhaseThreeTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory(prefix="ship_phase3_")
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        (self.root / "main.py").write_text((FIXTURE / "main.py").read_text())

    def test_named_instances_have_separate_volumes_dns_and_variables(self):
        config = ShipConfig(
            "generic", "main:app", services={"database": "postgres", "cache": "redis"},
            instances={
                "analytics": {"group": "database", "service": "postgres", "name": "analytics-db"},
                "queue": {"group": "cache", "service": "redis", "name": "queue-redis", "network": "shared"},
            }, networks={"shared": "ship-external-test"},
        )
        compose = build_compose(config, self.root)
        self.assertEqual({"app", "postgres", "redis", "analytics-db", "queue-redis"}, set(compose["services"]))
        self.assertEqual({"postgres_data", "redis_data", "analytics-db_data", "queue-redis_data"}, set(compose["volumes"]))
        app = compose["services"]["app"]
        self.assertEqual("analytics-db", app["environment"]["ANALYTICS_DB_HOST"])
        self.assertEqual("${ANALYTICS_DB_NAME:-app}", app["environment"]["ANALYTICS_DB_NAME"])
        self.assertEqual("redis://queue-redis:6379/0", app["environment"]["QUEUE_REDIS_URL"])
        self.assertEqual("analytics-db", next(name for name in app["depends_on"] if name == "analytics-db"))
        self.assertEqual(["default", "shared"], app["networks"])
        self.assertEqual(["default", "shared"], compose["services"]["queue-redis"]["networks"])
        self.assertEqual({"external": True, "name": "ship-external-test"}, compose["networks"]["shared"])
        self.assertEqual(["127.0.0.1:${SHIP_APP_PORT:-8000}:8000"], app["ports"])
        if shutil.which("docker"):
            path = write_compose(config, self.root)
            result = subprocess.run(["docker", "compose", "--project-directory", str(self.root), "-f", str(path), "config", "--quiet"], capture_output=True, text=True)
            self.assertEqual(0, result.returncode, result.stderr)

    def test_config_roundtrip_and_reinit_preserve_instances(self):
        config = ShipConfig("generic", "main:app", instances={"extra": {"group": "database", "service": "postgres", "name": "report-db", "prefix": "REPORT"}}, networks={"shared": "my-network"})
        config.write(self.root)
        self.assertEqual(config, ShipConfig.read(self.root))
        with patch.object(Path, "cwd", return_value=self.root), patch("python_ship.cli.require_uv", return_value="/bin/uv"):
            self.assertEqual(0, main(["init"]))
        self.assertEqual(config, ShipConfig.read(self.root))

    def test_invalid_config_and_duplicate_keys(self):
        (self.root / "ship.json").write_text('{"app":"main:app","app":"other:app"}')
        with self.assertRaisesRegex(ConfigError, "Duplicate ship.json key"):
            ShipConfig.read(self.root)
        (self.root / "ship.json").write_text(json.dumps({"app": "main:app", "instances": {"bad": {"group": 5, "service": "postgres", "prefix": "bad"}}, "networks": {"default": "bad"}, "extensions": ["same", "same"]}))
        _, problems = ShipConfig.inspect(self.root)
        self.assertGreaterEqual(len(problems), 4)
        self.assertTrue(any("duplicate" in problem for problem in problems))
        self.assertTrue(any("network" in problem for problem in problems))

    def test_conflicting_names_groups_and_prefixes(self):
        cases = [
            (ShipConfig("generic", "main:app", services={"cache": "postgres"}), "Unknown service"),
            (ShipConfig("generic", "main:app", instances={"app": {"group": "database", "service": "postgres"}}), "Duplicate Compose service"),
            (ShipConfig("generic", "main:app", instances={"one": {"group": "database", "service": "postgres", "prefix": "SAME"}, "two": {"group": "cache", "service": "redis", "prefix": "SAME"}}), "Duplicate environment prefix"),
            (ShipConfig("generic", "main:app", instances={"one": {"group": "cache", "service": "redis", "network": "missing"}}), "Unknown external network"),
            (ShipConfig("generic", "main:app", name="bad--name"), "Invalid Compose project name"),
        ]
        for config, message in cases:
            with self.subTest(message=message):
                with self.assertRaisesRegex(ConfigError, message):
                    build_compose(config, self.root)

    def test_duplicate_fragment_names_and_malformed_commands(self):
        class Colliding:
            def __init__(self, key): self.key, self.group, self.label = key, key, key
            def compose_fragment(self): return {self.key: {"image": "example:1"}, "helper": {"image": "example:1"}}
            def app_environment(self): return {}
        definitions = {**BUILTIN_SERVICES, "first": Colliding("first"), "second": Colliding("second")}
        with patch("python_ship.compose.load_extensions", return_value=(BUILTIN_ADAPTERS, definitions)):
            with self.assertRaisesRegex(ConfigError, "Duplicate Compose service 'helper'"):
                build_compose(ShipConfig("generic", "main:app", services={"first": "first", "second": "second"}), self.root)

        class InvalidCommand:
            def validate(self, config, root): return []
            def development_command(self, config): return ["python", 7]
        with patch("python_ship.compose.load_extensions", return_value=({"broken": InvalidCommand()}, BUILTIN_SERVICES)):
            with self.assertRaisesRegex(ConfigError, "invalid command"):
                build_compose(ShipConfig("broken", "main:app"), self.root)

    def test_app_fragment_merges_without_erasing_base_settings(self):
        class Extra:
            key, group, label = "extra", "search", "extra"
            def compose_fragment(self):
                return {"extra": {"image": "example:1", "environment": {"VALUE": "one"}, "volumes": ["extra_data:/data"],
                                  "ports": ["9001:9001", {"target": 9002, "published": 9002}], "networks": ["default"],
                                  "depends_on": {"helper": {"condition": "service_started"}}},
                        "helper": {"image": "example-helper:1"},
                        "app": {"build": {"args": {"EXTRA": "yes"}}, "environment": {"EXTRA_URL": "http://extra"},
                                "volumes": ["./extra:/extra"], "ports": ["127.0.0.1:9000:9000"],
                                "depends_on": {"extra": {"condition": "service_started"}}}}
            def app_environment(self): return {"EXTRA_FLAG": "yes"}
        service = Extra()
        with patch("python_ship.compose.load_extensions", return_value=(BUILTIN_ADAPTERS, {**BUILTIN_SERVICES, "extra": service})):
            compose = build_compose(ShipConfig("generic", "main:app", services={"search": "extra"}), self.root)
        app = compose["services"]["app"]
        self.assertEqual("ship/Dockerfile", app["build"]["dockerfile"])
        self.assertEqual({"PYTHON_VERSION": "3.13", "SHIP_SERVER_REQUIREMENT": "uvicorn>=0.53,<1", "EXTRA": "yes"}, app["build"]["args"])
        self.assertIn(".:/app", app["volumes"])
        self.assertIn("./extra:/extra", app["volumes"])
        self.assertIn("127.0.0.1:${SHIP_APP_PORT:-8000}:8000", app["ports"])
        self.assertIn("127.0.0.1:9000:9000", app["ports"])
        self.assertEqual("yes", app["environment"]["EXTRA_FLAG"])
        self.assertEqual({"condition": "service_started"}, app["depends_on"]["extra"])
        self.assertIn("extra_data", compose["volumes"])
        self.assertEqual(["127.0.0.1:9001:9001", {"host_ip": "127.0.0.1", "target": 9002, "published": 9002}], compose["services"]["extra"]["ports"])
        self.assertEqual(["default"], compose["services"]["extra"]["networks"])
        self.assertEqual({"helper": {"condition": "service_started"}}, compose["services"]["extra"]["depends_on"])
        if shutil.which("docker"):
            (self.root / "ship").mkdir(exist_ok=True)
            path = self.root / "ship" / "compose.generated.json"
            path.write_text(json.dumps(compose))
            result = subprocess.run(["docker", "compose", "--project-directory", str(self.root), "-f", str(path), "config", "--quiet"], capture_output=True, text=True)
            self.assertEqual(0, result.returncode, result.stderr)

    def test_config_aware_hook_and_development_only_service(self):
        class Mail:
            key, group, label, development_only = "mail", "mail", "mail", True
            def compose_fragment(self): return {"mail": {"image": "example:1"}}
            def app_environment(self): return {"MAIL_HOST": "mail"}
            def compose_fragment_for(self, config, instance): return {instance["name"]: {"image": "example:1"}}
            def app_environment_for(self, config, instance): return {"MAIL_HOST": instance["name"]}
        config = ShipConfig("generic", "main:app", instances={"mailbox": {"group": "mail", "service": "mail", "name": "custom-mail"}})
        with patch("python_ship.compose.load_extensions", return_value=(BUILTIN_ADAPTERS, {**BUILTIN_SERVICES, "mail": Mail()})):
            development = build_compose(config, self.root)
            production = build_compose(config, self.root, production=True)
        self.assertEqual("custom-mail", development["services"]["app"]["environment"]["MAILBOX_MAIL_HOST"])
        self.assertIn("custom-mail", development["services"])
        self.assertNotIn("custom-mail", production["services"])
        self.assertNotIn("MAILBOX_MAIL_HOST", production["services"]["app"]["environment"])
        self.assertNotIn("volumes", production["services"]["app"])

    def test_plugin_load_failures_are_configuration_errors(self):
        class EntryPoint:
            name = "broken"
            def load(self):
                raise RuntimeError("plugin import failed")
        with patch("python_ship.plugins.entry_points", side_effect=lambda *, group: [EntryPoint()] if group == "python_ship.services" else []):
            with self.assertRaisesRegex(ConfigError, "Could not load extension 'broken': plugin import failed"):
                load_extensions(ShipConfig("generic", "main:app", extensions=["broken"]))

        class BrokenService:
            key, group, label = "broken", "search", "broken"
            def compose_fragment(self): raise RuntimeError("fragment failed")
            def app_environment(self): return {}
        with patch("python_ship.compose.load_extensions", return_value=(BUILTIN_ADAPTERS, {**BUILTIN_SERVICES, "broken": BrokenService()})):
            with self.assertRaisesRegex(ConfigError, "fragment failed"):
                build_compose(ShipConfig("generic", "main:app", services={"search": "broken"}), self.root)

    def test_additional_instance_groups_accept_community_services(self):
        class CommunityService:
            def __init__(self, key, group):
                self.key, self.group, self.label = key, group, key
            def compose_fragment(self):
                return {self.key: {"image": "example:1", "volumes": [f"{self.key}_data:/data"]}}
            def app_environment(self):
                return {"SERVICE_HOST": self.key}
        additions = {group: CommunityService(group, group) for group in ("storage", "search", "mail")}
        instances = {f"second_{group}": {"group": group, "service": group} for group in additions}
        with patch("python_ship.compose.load_extensions", return_value=(BUILTIN_ADAPTERS, {**BUILTIN_SERVICES, **additions})):
            compose = build_compose(ShipConfig("generic", "main:app", instances=instances), self.root)
        for group in additions:
            self.assertIn(f"second_{group}", compose["services"])
            self.assertIn(f"second_{group}_data", compose["volumes"])
            self.assertEqual(f"second_{group}", compose["services"]["app"]["environment"][f"SECOND_{group.upper()}_SERVICE_HOST"])

    def test_project_directory_and_override_apply_to_development_commands(self):
        config = ShipConfig("generic", "main:app", services={"database": "postgres"})
        config.write(self.root)
        (self.root / "ship").mkdir()
        (self.root / "ship" / "Dockerfile").write_text("FROM python:3.13-slim\n")
        override = self.root / "compose.override.yaml"
        override.write_text("services:\n  app:\n    environment:\n      OVERRIDE_USED: yes\n")
        calls = []
        def run(command, **kwargs):
            calls.append((command, kwargs))
            return subprocess.CompletedProcess(command, 0)
        with patch("python_ship.cli.shutil.which", side_effect=lambda name: "/usr/bin/docker" if name == "docker" else None), patch("python_ship.cli.subprocess.run", side_effect=run), patch("python_ship.cli.wait_for_stack"):
            for arguments in (["up"], ["logs"], ["exec", "app", "python", "--version"], ["db"], ["shell"], ["down"]):
                self.assertEqual(0, main(["--project-directory", str(self.root), *arguments]))
        for command, kwargs in calls:
            self.assertIn(str(override.resolve()), command)
            self.assertEqual(self.root.resolve(), kwargs["cwd"])
        self.assertEqual(["exec", "app", "python", "--version"], calls[2][0][-4:])

        django = Path(__file__).parent / "fixtures" / "django_app"
        (self.root / "manage.py").write_text((django / "manage.py").read_text())
        (self.root / "project").mkdir()
        (self.root / "project" / "asgi.py").write_text((django / "project" / "asgi.py").read_text())
        ShipConfig("django", "project.asgi:application").write(self.root)
        with patch("python_ship.cli.shutil.which", return_value="/usr/bin/docker"), patch("python_ship.cli.subprocess.run", return_value=subprocess.CompletedProcess([], 0)) as manage:
            self.assertEqual(0, main(["--project-directory", str(self.root), "manage", "check", "--deploy"]))
        self.assertIn(str(override.resolve()), manage.call_args.args[0])
        self.assertEqual(["exec", "app", "python", "manage.py", "check", "--deploy"], manage.call_args.args[0][-6:])

    def test_database_shell_selects_named_instance(self):
        config = ShipConfig("generic", "main:app", instances={"analytics": {"group": "database", "service": "postgres", "name": "analytics-db"}})
        config.write(self.root)
        (self.root / "ship").mkdir()
        (self.root / "ship" / "Dockerfile").write_text("FROM python:3.13-slim\n")
        with patch("python_ship.cli.shutil.which", return_value="/usr/bin/docker"), patch("python_ship.cli.subprocess.run", return_value=subprocess.CompletedProcess([], 0)) as run:
            self.assertEqual(0, main(["--project-directory", str(self.root), "db", "--service", "analytics"]))
        self.assertEqual(["exec", "analytics-db", "sh", "-c", 'exec psql -U "$POSTGRES_USER" -d "$POSTGRES_DB"'], run.call_args.args[0][-5:])


if __name__ == "__main__":
    unittest.main()
