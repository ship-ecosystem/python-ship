"""Every built-in service: fragments, injected variables, dev/production scope and credentials."""

import json
import os
from pathlib import Path
import unittest
from unittest.mock import patch

from python_ship.config import AdditionalService, ConfigError, ShipConfig
from python_ship.contracts import ProvidesDatabaseShell, ServiceContext, ServiceDefinition, ShipEnvironment
from python_ship.services import BUILTIN_SERVICES
from tests.helpers import ProjectTestCase

GROUPS = {
    "mariadb": "database", "mongodb": "database", "sqlserver": "database",
    "valkey": "cache", "memcached": "cache", "elasticsearch": "search", "typesense": "search",
    "centrifugo": "broadcasting", "cypress": "testing", "postgres": "database", "mysql": "database", "redis": "cache", "rabbitmq": "broker", "celery": "tasks",
    "mailpit": "mail", "meilisearch": "search",
    "selenium": "testing", "playwright": "testing", "node": "frontend",
    "garage": "storage", "seaweedfs": "storage", "rustfs": "storage", "silo": "storage",
}
DEVELOPMENT_ONLY = {"mailpit", "selenium", "playwright", "cypress", "node"}


class CatalogTests(ProjectTestCase):
    def context(self, key, *, production=False, instance=None, config=None):
        service = BUILTIN_SERVICES[key]
        name = f"{key}-{instance}" if instance else key
        environment = ShipEnvironment.PRODUCTION if production else ShipEnvironment.DEVELOPMENT
        return service, ServiceContext(environment, config or ShipConfig(app="main:app"), self.root, key, instance, name)

    def test_catalog_contents_and_protocols(self):
        self.assertEqual(GROUPS, {key: service.group for key, service in BUILTIN_SERVICES.items()})
        for key, service in BUILTIN_SERVICES.items():
            with self.subTest(key=key):
                self.assertIsInstance(service, ServiceDefinition)
                self.assertTrue(service.label)
        self.assertEqual({"postgres", "mysql", "mariadb", "mongodb", "sqlserver"}, {key for key, item in BUILTIN_SERVICES.items() if isinstance(item, ProvidesDatabaseShell)})

    def test_shared_service_environment_contract(self):
        contract = json.loads((Path(__file__).resolve().parents[1] / "schema/service-environment.json").read_text())
        for key, variables in contract.items():
            with self.subTest(key=key):
                model = self.model(ShipConfig(app="main:app", services={GROUPS[key]: key}))
                self.assertLessEqual(set(variables), model["services"]["app"]["environment"].keys())

    def test_every_image_is_version_pinned(self):
        for key in BUILTIN_SERVICES:
            with self.subTest(key=key):
                service, context = self.context(key, config=ShipConfig(app="main:app", services={"cache": "redis"}))
                fragment = service.compose_fragment(context)[key]
                image = fragment.get("image")
                if "dockerfile_inline" in fragment.get("build", {}):
                    image = fragment["build"]["dockerfile_inline"].splitlines()[0].removeprefix("FROM ")
                if image is None:
                    self.assertIn(key, ("garage", "celery", "node"))  # Built locally, or the app's own image.
                    continue
                tag = image.rsplit(":", 1)[1]
                self.assertNotIn(tag, {"latest", ""})
                self.assertRegex(tag, r"\d")

    def test_development_only_services_vanish_in_production(self):
        for key in BUILTIN_SERVICES:
            with self.subTest(key=key):
                service, context = self.context(key, production=True, config=ShipConfig(app="main:app", services={"cache": "redis"}))
                self.assertEqual(key in DEVELOPMENT_ONLY, service.compose_fragment(context) == {})

    def test_stateful_services_use_named_volumes_and_healthchecks(self):
        for key in ("postgres", "mysql", "redis", "rabbitmq", "meilisearch", "garage", "seaweedfs", "rustfs", "silo"):
            with self.subTest(key=key):
                service, context = self.context(key)
                model = service.compose_fragment(context)[key]
                self.assertIn("healthcheck", model)
                self.assertTrue(any(volume.startswith(f"{key}_data:") for volume in model["volumes"]))

    def test_secrets_are_required_in_production_and_defaulted_in_development(self):
        secrets = {"postgres": ["DB_PASSWORD"], "mysql": ["DB_PASSWORD", "DB_ROOT_PASSWORD"],
                   "meilisearch": ["MEILISEARCH_KEY"], "rabbitmq": ["RABBITMQ_PASSWORD"], "seaweedfs": ["AWS_SECRET_ACCESS_KEY"],
                   "rustfs": ["AWS_SECRET_ACCESS_KEY"], "silo": ["AWS_SECRET_ACCESS_KEY"],
                   "garage": ["AWS_SECRET_ACCESS_KEY", "GARAGE_RPC_SECRET", "GARAGE_ADMIN_TOKEN"]}
        for key, variables in secrets.items():
            for production in (False, True):
                with self.subTest(key=key, production=production):
                    service, context = self.context(key, production=production)
                    text = str(service.compose_fragment(context)) + str(service.app_environment(context))
                    for variable in variables:
                        if production:
                            self.assertIn(f"${{{variable}:?", text)
                            self.assertNotIn(f"${{{variable}:-", text)
                        else:
                            self.assertIn(f"${{{variable}:-", text)

    def test_named_instances_prefix_every_variable(self):
        for key in ("postgres", "mysql", "redis", "mailpit", "meilisearch", "garage", "seaweedfs", "rustfs", "silo"):
            with self.subTest(key=key):
                service, context = self.context(key, instance="second")
                environment = service.app_environment(context)
                self.assertTrue(environment)
                self.assertTrue(all(name.startswith("SECOND_") for name in environment), environment)
                self.assertIn(f"{key}-second", str(environment))
                fragment = service.compose_fragment(context)
                self.assertEqual([f"{key}-second"], list(fragment))
                self.assertNotIn("${DB_", str(fragment))

    def test_postgres_and_mysql(self):
        service, context = self.context("postgres")
        self.assertEqual({"DB_HOST": "postgres", "DB_PORT": "5432", "DB_DATABASE": "${DB_DATABASE:-app}",
                          "DB_USERNAME": "${DB_USERNAME:-app}", "DB_PASSWORD": "${DB_PASSWORD:-ship}"},
                         service.app_environment(context))
        self.assertIn("psql", service.database_shell(context)[-1])
        service, context = self.context("mysql")
        mysql = service.compose_fragment(context)["mysql"]
        self.assertEqual("${DB_ROOT_PASSWORD:-rootship}", mysql["environment"]["MYSQL_ROOT_PASSWORD"])
        self.assertIn("127.0.0.1", mysql["healthcheck"]["test"][1])
        self.assertEqual("3306", service.app_environment(context)["DB_PORT"])
        self.assertNotIn("DB_ROOT_PASSWORD", service.app_environment(context))
        self.assertIn("mysql", service.database_shell(context)[-1])

    def test_mysql_rejects_the_root_application_user(self):
        config = ShipConfig(app="main:app", services={"database": "mysql"},
                            additional_services=[AdditionalService("database", "mysql", "legacy")])
        self.assertEqual("mysql", self.model(config)["services"]["app"]["environment"]["DB_HOST"])
        (self.root / ".env").write_text("DB_USERNAME=root\n")
        with self.assertRaisesRegex(ConfigError, "DB_USERNAME=root is invalid"):
            self.model(config)
        (self.root / ".env").write_text("DB_USERNAME=app\nLEGACY_DB_USERNAME=ROOT\n")
        with self.assertRaisesRegex(ConfigError, "LEGACY_DB_USERNAME=root is invalid"):
            self.model(config)
        (self.root / ".env").write_text("DB_USERNAME=app\n")
        with patch.dict(os.environ, {"DB_USERNAME": "root"}), self.assertRaisesRegex(ConfigError, "DB_USERNAME=root"):
            self.model(config)

    def test_redis_mailpit_meilisearch(self):
        service, context = self.context("redis")
        redis = service.compose_fragment(context)["redis"]
        self.assertEqual(["redis-server", "--save", "60", "1", "--appendonly", "yes"], redis["command"])
        self.assertEqual({"REDIS_URL": "redis://redis:6379/0", "REDIS_HOST": "redis", "REDIS_PORT": "6379"}, service.app_environment(context))
        service, context = self.context("mailpit")
        self.assertEqual(["127.0.0.1:${MAILPIT_WEB_PORT:-8025}:8025"], service.compose_fragment(context)["mailpit"]["ports"])
        self.assertEqual({"MAIL_HOST": "mailpit", "MAIL_PORT": "1025"}, service.app_environment(context))
        _, named = self.context("mailpit", instance="second")
        self.assertNotIn("ports", service.compose_fragment(named)["mailpit-second"])
        service, context = self.context("meilisearch")
        self.assertEqual("http://meilisearch:7700", service.app_environment(context)["MEILISEARCH_URL"])
        self.assertEqual("development", service.compose_fragment(context)["meilisearch"]["environment"]["MEILI_ENV"])
        _, production = self.context("meilisearch", production=True)
        self.assertEqual("production", service.compose_fragment(production)["meilisearch"]["environment"]["MEILI_ENV"])

    def test_rabbitmq(self):
        service, context = self.context("rabbitmq")
        model = service.compose_fragment(context)["rabbitmq"]
        self.assertTrue(model["image"].startswith("rabbitmq:4."))
        self.assertEqual(["127.0.0.1:${RABBITMQ_MANAGEMENT_PORT:-15672}:15672"], model["ports"])
        self.assertEqual("amqp://${RABBITMQ_USER:-app}:${RABBITMQ_PASSWORD:-ship}@rabbitmq:5672//",
                         service.app_environment(context)["AMQP_URL"])
        _, production = self.context("rabbitmq", production=True)
        self.assertNotIn("ports", service.compose_fragment(production)["rabbitmq"])

    def test_celery_brokers_and_result_backends(self):
        amqp = "amqp://${RABBITMQ_USER:-app}:${RABBITMQ_PASSWORD:-ship}@rabbitmq:5672//"
        cases = (
            ({"cache": "redis"}, "redis://redis:6379/1", "redis://redis:6379/2"),
            ({"broker": "rabbitmq"}, amqp, "rpc://"),
            ({"broker": "rabbitmq", "cache": "redis"}, amqp, "redis://redis:6379/2"),
        )
        for services, broker, backend in cases:
            with self.subTest(services=services):
                config = ShipConfig(app="main:app", services={**services, "tasks": "celery"})
                service, context = self.context("celery", config=config)
                environment = service.app_environment(context)
                self.assertEqual(broker, environment["CELERY_BROKER_URL"])
                self.assertEqual(backend, environment["CELERY_RESULT_BACKEND"])
                self.assertEqual("${CELERY_APP:-main}", environment["CELERY_APP"])
                self.assertEqual([], service.validate(context))
        service, context = self.context("celery", config=ShipConfig(app="main:app", services={"tasks": "celery"}))
        self.assertIn("needs a broker", service.validate(context)[0])
        renamed = ShipConfig(app="main:app", services={"cache": "redis", "tasks": "celery"}, service_names={"redis": "kv"})
        service, context = self.context("celery", config=renamed)
        self.assertEqual("redis://kv:6379/1", service.app_environment(context)["CELERY_BROKER_URL"])

    def test_celery_app_defaults_per_framework(self):
        for framework, target, expected in (("django", "project.asgi:application", "project"),
                                            ("fastapi", "app.main:app", "app.main"), ("generic", "main:app", "main")):
            with self.subTest(framework=framework):
                config = ShipConfig(framework, target, services={"cache": "redis", "tasks": "celery"})
                service, context = self.context("celery", config=config)
                self.assertEqual("${CELERY_APP:-" + expected + "}", service.app_environment(context)["CELERY_APP"])

    def test_celery_runs_worker_and_beat_from_the_app_image(self):
        config = ShipConfig(app="main:app", services={"cache": "redis", "tasks": "celery"}, processes={"other": "true"})
        for production in (False, True):
            with self.subTest(production=production):
                model = self.model(config, production=production)
                services = model["services"]
                for name in ("celery",):
                    self.assertEqual(services["app"]["image"], services[name]["image"])
                    self.assertNotIn("build", services[name])
                    self.assertNotIn("ports", services[name])
                    self.assertEqual("60s", services[name]["stop_grace_period"])
                    self.assertIn("redis", services[name]["depends_on"])
                    self.assertEqual("redis://redis:6379/1", services[name]["environment"]["CELERY_BROKER_URL"])
                self.assertEqual(["app", *(["other"] if production else []), "celery"], model["x-ship"]["app_services"])
                self.assertNotIn("celery", services["app"].get("depends_on", {}))
                worker = services["celery"]["command"][2]
                self.assertIn("-A $${CELERY_APP} worker", worker)
                self.assertEqual(not production, "watchfiles" in worker)
                self.assertNotIn("celery-beat", services)
                self.assertEqual([".:/app"] if not production else None, services["celery"].get("volumes"))
        with self.assertRaisesRegex(ConfigError, "needs a broker"):
            self.model(ShipConfig(app="main:app", services={"tasks": "celery"}))

    def test_postgres_aliases(self):
        (self.root / "ship.json").write_text(
            '{"app": "main:app", "services": {"database": "pgsql"}, "serviceNames": {"pgsql": "db"},'
            ' "additionalServices": [{"group": "database", "service": "pgsql", "name": "reports"}]}')
        config = ShipConfig.read(self.root)
        self.assertEqual({"database": "postgres"}, config.services)
        self.assertEqual("postgres", config.additional_services[0].service)
        self.assertEqual({"postgres": "db"}, config.service_names)
        model = self.model(config)
        self.assertEqual("db", model["services"]["app"]["environment"]["DB_HOST"])
        self.assertIn("postgres-reports", model["services"])
        config.write(self.root)
        self.assertIn('"database": "postgres"', (self.root / "ship.json").read_text())

    def test_browser_services(self):
        service, context = self.context("selenium")
        self.assertEqual({"SELENIUM_REMOTE_URL": "http://selenium:4444/wd/hub", "APP_URL": "http://ship-app.test:8000"},
                         service.app_environment(context))
        self.assertEqual("2gb", service.compose_fragment(context)["selenium"]["shm_size"])
        service, context = self.context("playwright")
        model = service.compose_fragment(context)["playwright"]
        version = BUILTIN_SERVICES["playwright"].version
        self.assertEqual(f"mcr.microsoft.com/playwright:v{version}-noble", model["image"])
        self.assertIn(f"playwright@{version} run-server", model["command"][-1])
        self.assertEqual("ws://playwright:3000/", service.app_environment(context)["PLAYWRIGHT_WS_URL"])

    def test_node_publishes_the_vite_port(self):
        service, context = self.context("node")
        model = service.compose_fragment(context)["node"]
        self.assertEqual(["127.0.0.1:${VITE_PORT:-5173}:${VITE_PORT:-5173}"], model["ports"])
        self.assertEqual([".:/app", "node_modules:/app/node_modules"], model["volumes"])
        self.assertEqual({}, service.app_environment(context))

    def test_storage_choices_share_one_s3_environment_shape(self):
        ports = {"garage": 3900, "seaweedfs": 8333, "rustfs": 9000, "silo": 9000}
        for key, port in ports.items():
            with self.subTest(key=key):
                service, context = self.context(key)
                environment = service.app_environment(context)
                self.assertEqual({"AWS_ENDPOINT", "AWS_USE_PATH_STYLE_ENDPOINT", "AWS_DEFAULT_REGION", "AWS_ACCESS_KEY_ID",
                                  "AWS_SECRET_ACCESS_KEY", "AWS_BUCKET"}, set(environment))
                self.assertEqual(f"http://{key}:{port}", environment["AWS_ENDPOINT"])
                self.assertEqual("${AWS_BUCKET:-local}", environment["AWS_BUCKET"])

    def test_garage_seaweedfs_rustfs_details(self):
        service, context = self.context("garage")
        garage = service.compose_fragment(context)["garage"]
        self.assertEqual({"context": "./ship/garage", "dockerfile": "Dockerfile", "args": {"GARAGE_RPC_PUBLIC_ADDR": "garage:3901"}},
                         garage["build"])
        self.assertIn("garage_meta:/meta", garage["volumes"])
        self.assertEqual(garage["environment"]["GARAGE_DEFAULT_SECRET_KEY"], service.app_environment(context)["AWS_SECRET_ACCESS_KEY"])
        service, context = self.context("seaweedfs")
        seaweed = service.compose_fragment(context)["seaweedfs"]
        self.assertEqual(["/bin/sh", "-c"], seaweed["entrypoint"])
        for marker in ("-s3.config=/etc/seaweedfs/s3_identity.json", "$$AWS_ACCESS_KEY_ID", "$$AWS_SECRET_ACCESS_KEY", "sed"):
            self.assertIn(marker, seaweed["command"][0])
        service, context = self.context("rustfs")
        self.assertNotIn("ports", service.compose_fragment(context)["rustfs"])

    def test_silo_image_variants_and_console_ports(self):
        service, context = self.context("silo")
        model = service.compose_fragment(context)["silo"]
        self.assertFalse(model["image"].endswith("-distroless"))
        self.assertEqual(["127.0.0.1:${SILO_CONSOLE_PORT:-9001}:9001"], model["ports"])
        self.assertEqual(["CMD", "/usr/bin/silo", "healthcheck", "live"], model["healthcheck"]["test"])
        _, distroless = self.context("silo", config=ShipConfig(app="main:app", silo_image="distroless"))
        self.assertTrue(service.compose_fragment(distroless)["silo"]["image"].endswith("-distroless"))
        _, named = self.context("silo", instance="archive")
        self.assertEqual(["127.0.0.1:${ARCHIVE_SILO_CONSOLE_PORT:-0}:9001"], service.compose_fragment(named)["silo-archive"]["ports"])
        _, production = self.context("silo", production=True)
        self.assertNotIn("ports", service.compose_fragment(production)["silo"])


if __name__ == "__main__":
    unittest.main()
