"""Every built-in service: fragments, injected variables, dev/production scope and credentials."""

import os
import unittest
from unittest.mock import patch

from python_ship.config import AdditionalService, ConfigError, ShipConfig
from python_ship.contracts import ProvidesDatabaseShell, ServiceContext, ServiceDefinition, ShipEnvironment
from python_ship.services import BUILTIN_SERVICES
from tests.helpers import ProjectTestCase

GROUPS = {
    "postgres": "database", "mysql": "database", "redis": "cache", "mailpit": "mail", "meilisearch": "search",
    "selenium": "testing", "playwright": "testing", "node": "frontend",
    "garage": "storage", "seaweedfs": "storage", "rustfs": "storage", "silo": "storage",
}
DEVELOPMENT_ONLY = {"mailpit", "selenium", "playwright", "node"}


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
        self.assertEqual({"postgres", "mysql"}, {key for key, item in BUILTIN_SERVICES.items() if isinstance(item, ProvidesDatabaseShell)})

    def test_every_image_is_version_pinned(self):
        for key in BUILTIN_SERVICES:
            with self.subTest(key=key):
                service, context = self.context(key)
                image = service.compose_fragment(context)[key].get("image")
                if image is None:
                    self.assertEqual("garage", key)
                    continue
                tag = image.rsplit(":", 1)[1]
                self.assertNotIn(tag, {"latest", ""})
                self.assertRegex(tag, r"\d")

    def test_development_only_services_vanish_in_production(self):
        for key in BUILTIN_SERVICES:
            with self.subTest(key=key):
                service, context = self.context(key, production=True)
                self.assertEqual(key in DEVELOPMENT_ONLY, service.compose_fragment(context) == {})

    def test_stateful_services_use_named_volumes_and_healthchecks(self):
        for key in ("postgres", "mysql", "redis", "meilisearch", "garage", "seaweedfs", "rustfs", "silo"):
            with self.subTest(key=key):
                service, context = self.context(key)
                model = service.compose_fragment(context)[key]
                self.assertIn("healthcheck", model)
                self.assertTrue(any(volume.startswith(f"{key}_data:") for volume in model["volumes"]))

    def test_secrets_are_required_in_production_and_defaulted_in_development(self):
        secrets = {"postgres": ["DB_PASSWORD"], "mysql": ["DB_PASSWORD", "DB_ROOT_PASSWORD"],
                   "meilisearch": ["MEILISEARCH_KEY"], "seaweedfs": ["AWS_SECRET_ACCESS_KEY"],
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
        self.assertEqual({"DB_HOST": "postgres", "DB_PORT": "5432", "DB_NAME": "${DB_NAME:-app}",
                          "DB_USER": "${DB_USER:-app}", "DB_PASSWORD": "${DB_PASSWORD:-ship}"},
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
        (self.root / ".env").write_text("DB_USER=root\n")
        with self.assertRaisesRegex(ConfigError, "DB_USER=root is invalid"):
            self.model(config)
        (self.root / ".env").write_text("DB_USER=app\nLEGACY_DB_USER=ROOT\n")
        with self.assertRaisesRegex(ConfigError, "LEGACY_DB_USER=root is invalid"):
            self.model(config)
        (self.root / ".env").write_text("DB_USER=app\n")
        with patch.dict(os.environ, {"DB_USER": "root"}), self.assertRaisesRegex(ConfigError, "DB_USER=root"):
            self.model(config)

    def test_redis_mailpit_meilisearch(self):
        service, context = self.context("redis")
        redis = service.compose_fragment(context)["redis"]
        self.assertEqual(["redis-server", "--save", "60", "1", "--appendonly", "yes"], redis["command"])
        self.assertEqual({"REDIS_URL": "redis://redis:6379/0"}, service.app_environment(context))
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
