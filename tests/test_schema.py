"""The shared ship.json schema (schema/ship.schema.json, identical in PHP Ship)."""

import json
from pathlib import Path
import unittest

from python_ship.config import FIELDS, SCHEMA_URL, AdditionalService, ShipConfig
from tests.helpers import ProjectTestCase

SCHEMA_PATH = Path(__file__).parents[1] / "schema" / "ship.schema.json"
SCHEMA = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))

try:
    import jsonschema
except ImportError:  # Installed by scripts/check_local.py; optional elsewhere.
    jsonschema = None


class SchemaTests(ProjectTestCase):
    def test_schema_declares_every_field_python_ship_reads(self):
        self.assertEqual(set(FIELDS), set(SCHEMA["properties"]) & set(FIELDS))
        self.assertEqual(SCHEMA_URL, SCHEMA["$id"])
        for php_field in ("php", "node", "phpExtensions"):
            self.assertIn(php_field, SCHEMA["properties"])

    @unittest.skipIf(jsonschema is None, "jsonschema is not installed")
    def test_written_configs_validate(self):
        jsonschema.Draft202012Validator.check_schema(SCHEMA)
        validator = jsonschema.Draft202012Validator(SCHEMA)
        configs = (
            ShipConfig(app="main:app"),
            ShipConfig(framework="django", app="project.asgi:application", python="3.12", server="granian",
                       services={"database": "postgres", "cache": "redis", "tasks": "celery"},
                       additional_services=[AdditionalService("database", "mysql", "analytics")],
                       service_names={"app": "web"}, external_network="shared", extensions=["x"], name="site",
                       silo_image="distroless", publish_ports=True, host_user=False,
                       processes={"worker": "python w.py", "beat": ["python", "b.py"]},
                       deploy_commands=["python manage.py migrate", ["python", "seed.py"]],
                       node_assets=True, django_collectstatic=True),
        )
        for config in configs:
            with self.subTest(config=config.framework):
                config.write(self.root)
                document = json.loads((self.root / "ship.json").read_text())
                self.assertEqual([], [error.message for error in validator.iter_errors(document)])

    @unittest.skipIf(jsonschema is None, "jsonschema is not installed")
    def test_php_ship_configs_validate_and_mistakes_do_not(self):
        validator = jsonschema.Draft202012Validator(SCHEMA)
        php = {"php": "8.4", "node": "24", "services": {"database": "pgsql", "cache": "redis"},
               "additionalServices": [{"group": "database", "service": "mysql", "name": "analytics"}],
               "phpExtensions": ["gd"], "deployCommands": ["php artisan migrate --force"],
               "processes": {"horizon": "php artisan horizon"}, "hostUser": True, "extensions": []}
        self.assertEqual([], list(validator.iter_errors(php)))
        for wrong in ({"unknown": 1}, {"publishPorts": "yes"}, {"additionalServices": [{"group": "testing", "service": "x", "name": "BAD NAME"}]},
                      {"siloImage": "tiny"}, {"python": "latest"}):
            with self.subTest(wrong=wrong):
                self.assertTrue(list(validator.iter_errors(wrong)))


if __name__ == "__main__":
    unittest.main()
