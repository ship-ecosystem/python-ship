"""Python adapter capabilities, explicit task scheduling and safe credentials."""
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path

from python_ship.adapters import BUILTIN_ADAPTERS, DjangoAdapter, FlaskAdapter
from python_ship.capabilities import process_suggestions, release_description, required_setting_problems
from python_ship.config import ShipConfig
from tests.helpers import ProjectTestCase


class CapabilityTests(ProjectTestCase):
    def test_exactly_four_builtin_adapters(self):
        self.assertEqual({"generic", "django", "fastapi", "flask"}, set(BUILTIN_ADAPTERS))

    def test_django_required_secret_is_discovered_without_importing_code(self):
        (self.root / "settings.py").write_text('raise RuntimeError("never import")\nimport os\nSECRET_KEY = os.environ["DJANGO_SECRET_KEY"]\n')
        config = ShipConfig(framework="django", app="project.asgi:application")
        description = release_description(DjangoAdapter(), config, self.root)
        self.assertEqual(["DJANGO_SECRET_KEY"], description["requiredEnv"])
        self.assertTrue(required_setting_problems(description, self.root))
        (self.root / ".env.production").write_text("DJANGO_SECRET_KEY=private-secret-value\n")
        self.assertEqual([], required_setting_problems(description, self.root))

    def test_celery_beat_is_an_explicit_production_process_suggestion(self):
        (self.root / "requirements.txt").write_text("celery[redis]==5.7.0\n")
        config = ShipConfig(app="main:app", services={"tasks": "celery", "cache": "redis"})
        suggestions = process_suggestions(BUILTIN_ADAPTERS["generic"], config, self.root)
        self.assertIn("celery-beat", suggestions)
        self.assertNotIn("celery-beat", self.model(config, production=True)["services"])

    def test_flask_factory_is_detected_and_served_without_host_execution(self):
        (self.root / "main.py").write_text("from flask import Flask\ndef create_app():\n    app = Flask(__name__)\n    return app\n")
        target = FlaskAdapter().default_app(self.root)
        self.assertEqual("main:create_app()", target)
        config = ShipConfig(framework="flask", app=target)
        self.assertEqual([], FlaskAdapter().validate(config, self.root))
        self.assertIn(target, self.model(config, production=True)["services"]["app"]["command"])

    def test_rabbitmq_credentials_are_encoded_without_touching_external_urls(self):
        path = Path(__file__).resolve().parents[1] / "src/python_ship/templates/app-entrypoint.py"
        spec = spec_from_file_location("ship_test_amqp", path)
        module = module_from_spec(spec)
        spec.loader.exec_module(module)
        environment = module.application_environment({"RABBITMQ_HOST": "rabbitmq", "RABBITMQ_USER": "app",
            "RABBITMQ_PASSWORD": "p@:/", "CELERY_BROKER_URL": "amqp://app:p@:/@rabbitmq:5672//"})
        self.assertEqual("amqp://app:p%40%3A%2F@rabbitmq:5672/%2F", environment["AMQP_URL"])
        self.assertEqual(environment["AMQP_URL"], environment["CELERY_BROKER_URL"])
        self.assertEqual({"AMQP_URL": "amqps://external"}, module.application_environment({"AMQP_URL": "amqps://external"}))

    def test_mongodb_uri_encodes_credentials_and_removes_admin_settings(self):
        path = Path(__file__).resolve().parents[1] / "src/python_ship/templates/app-entrypoint.py"
        spec = spec_from_file_location("ship_test_entrypoint", path)
        module = module_from_spec(spec)
        spec.loader.exec_module(module)
        values = module.application_environment({"MONGODB_HOST": "mongodb", "MONGODB_DATABASE": "app",
            "MONGODB_USERNAME": "a@b", "MONGODB_PASSWORD": "p:/?#", "MONGODB_ROOT_PASSWORD": "admin", "DB_ROOT_PASSWORD": "root"})
        self.assertIn("a%40b:p%3A%2F%3F%23", values["MONGODB_URI"])
        self.assertNotIn("MONGODB_ROOT_PASSWORD", values)
        self.assertNotIn("DB_ROOT_PASSWORD", values)
