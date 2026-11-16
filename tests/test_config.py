"""ship.json parsing, validation and round-tripping."""

import json
import unittest

from python_ship.config import AdditionalService, ConfigError, ShipConfig, command_argv, command_text
from tests.helpers import ProjectTestCase


class ShipConfigTests(ProjectTestCase):
    def write_json(self, data) -> None:
        (self.root / "ship.json").write_text(json.dumps(data), encoding="utf-8")

    def test_minimal_file_uses_defaults(self):
        self.write_json({"app": "main:app"})
        config = ShipConfig.read(self.root)
        self.assertEqual("generic", config.framework)
        self.assertEqual("3.13", config.python)
        self.assertEqual("uvicorn", config.server)
        self.assertEqual("standard", config.silo_image)
        self.assertTrue(config.host_user)
        self.assertFalse(config.publish_ports)
        self.assertEqual("app", config.app_service)

    def test_every_field_round_trips_with_php_ship_names(self):
        config = ShipConfig(
            framework="django", app="project.asgi:application", python="3.12", server="hypercorn",
            services={"database": "postgres", "cache": "redis"},
            additional_services=[AdditionalService("database", "mysql", "analytics")],
            service_names={"app": "client-app", "postgres": "client-db"}, external_network="shared_infra",
            extensions=["community"], name="client", silo_image="distroless", publish_ports=True, host_user=False,
            processes={"worker": "python worker.py", "beat": ["python", "beat.py"]},
            deploy_commands=["python manage.py migrate --noinput", ["python", "seed.py"]],
            node_assets=True, django_collectstatic=True,
        )
        config.write(self.root)
        data = json.loads((self.root / "ship.json").read_text())
        for key in ("additionalServices", "serviceNames", "externalNetwork", "siloImage", "publishPorts",
                    "hostUser", "processes", "deployCommands", "nodeAssets", "djangoCollectstatic"):
            self.assertIn(key, data)
        self.assertEqual([{"group": "database", "service": "mysql", "name": "analytics"}], data["additionalServices"])
        self.assertEqual(config, ShipConfig.read(self.root))
        self.assertEqual("client-app", config.app_service)

    def test_defaults_are_omitted_from_the_written_file(self):
        ShipConfig(app="main:app").write(self.root)
        self.assertEqual({"framework", "app", "python", "services", "extensions"},
                         set(json.loads((self.root / "ship.json").read_text())))

    def test_reports_every_independent_problem_in_one_pass(self):
        self.write_json({
            "framework": "", "app": "", "python": "2.7", "server": "Bad Server", "services": [],
            "additionalServices": [{"group": "testing", "service": "playwright", "name": "two"},
                                   {"group": "database", "service": "postgres", "name": "Bad-Name"}],
            "serviceNames": {"app": "Bad Name"}, "externalNetwork": "", "extensions": 3, "name": "Bad",
            "siloImage": "other", "publishPorts": "yes", "hostUser": 1, "processes": {"app": ""},
            "deployCommands": [""], "nodeAssets": "no", "djangoCollectstatic": True, "extra": 1,
        })
        config, problems = ShipConfig.inspect(self.root)
        self.assertIsNone(config)
        report = "\n".join(problems)
        for marker in ('"framework"', '"app"', "end of life", '"server"', '"services"', "doesn't support additional",
                       "Bad-Name", '"serviceNames"', '"externalNetwork"', '"extensions"', '"name"', '"siloImage"',
                       '"publishPorts"', '"hostUser"', "is reserved", "process 'app'", '"deployCommands"',
                       '"nodeAssets"', '"djangoCollectstatic" requires', "unknown fields: extra"):
            self.assertIn(marker, report)

    def test_missing_invalid_and_duplicate_keys(self):
        with self.assertRaisesRegex(ConfigError, "No ship.json found"):
            ShipConfig.read(self.root)
        (self.root / "ship.json").write_text("{not json")
        with self.assertRaisesRegex(ConfigError, "not valid JSON"):
            ShipConfig.read(self.root)
        (self.root / "ship.json").write_text("[]")
        with self.assertRaisesRegex(ConfigError, "JSON object"):
            ShipConfig.read(self.root)
        (self.root / "ship.json").write_text('{"app": "main:app", "app": "other:app"}')
        with self.assertRaisesRegex(ConfigError, "duplicate key 'app'"):
            ShipConfig.read(self.root)

    def test_utf8_bom_is_accepted(self):
        (self.root / "ship.json").write_bytes(b"\xef\xbb\xbf" + json.dumps({"app": "main:app"}).encode())
        self.assertEqual("main:app", ShipConfig.read(self.root).app)

    def test_additional_services_rules(self):
        self.write_json({"app": "main:app", "additionalServices": [
            {"group": "database", "service": "postgres", "name": "analytics"},
            {"group": "database", "service": "mysql", "name": "analytics"},
            {"group": "cache", "service": "redis", "name": "queue", "network": "x"},
            {"group": "cache"},
            "nope",
        ]})
        _, problems = ShipConfig.inspect(self.root)
        report = "\n".join(problems)
        self.assertIn("uses the name 'analytics' twice", report)
        self.assertIn("unknown fields: network", report)
        self.assertIn("requires a group and a service", report)
        self.assertIn("must be an object", report)

    def test_python_versions_follow_the_security_floor(self):
        for version, valid in (("3.13", True), ("3.11", True), ("3.13.18", True), ("3.13.4", False),
                               ("3.10", False), ("3", False), ("latest", False)):
            with self.subTest(version=version):
                self.write_json({"app": "main:app", "python": version})
                _, problems = ShipConfig.inspect(self.root)
                self.assertEqual(valid, not problems, problems)

    def test_service_names_must_be_unique(self):
        self.write_json({"app": "main:app", "serviceNames": {"app": "same", "redis": "same"}})
        _, problems = ShipConfig.inspect(self.root)
        self.assertIn("same name", problems[0])

    def test_lenient_read_keeps_valid_fields_next_to_invalid_ones(self):
        self.write_json({"app": "main:app", "framework": "django", "name": "BAD NAME", "extensions": ["x"],
                         "processes": {"worker": "python worker.py"}, "publishPorts": True,
                         "djangoCollectstatic": True})
        config = ShipConfig.lenient(self.root)
        self.assertIsNotNone(config)
        assert config is not None
        self.assertIsNone(config.name)
        self.assertEqual(["x"], config.extensions)
        self.assertEqual({"worker": "python worker.py"}, config.processes)
        self.assertTrue(config.publish_ports)
        self.assertTrue(config.django_collectstatic)
        self.assertEqual("django", config.framework)
        (self.root / "ship.json").write_text("{broken")
        self.assertIsNone(ShipConfig.lenient(self.root))

    def test_command_helpers(self):
        self.assertEqual(["sh", "-c", "python worker.py --queue $QUEUE"], command_argv("python worker.py --queue $QUEUE"))
        self.assertEqual(["python", "worker.py"], command_argv(["python", "worker.py"]))
        self.assertEqual("python 'a b.py'", command_text(["python", "a b.py"]))
        self.assertEqual("echo hi", command_text("echo hi"))


if __name__ == "__main__":
    unittest.main()
