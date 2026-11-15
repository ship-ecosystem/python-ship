"""Phase 4 service selection and configuration checks."""

import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from python_ship.cli import main
from python_ship.compose import build_compose
from python_ship.config import ConfigError, ShipConfig


FIXTURE = Path(__file__).parent / "fixtures" / "plain_asgi" / "main.py"


class ServiceCatalogTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory(prefix="ship-services-")
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        (self.root / "main.py").write_text(FIXTURE.read_text())

    def test_mysql_separate_root_password_and_preflight(self):
        config = ShipConfig("generic", "main:app", services={"database": "mysql"})
        model = build_compose(config, self.root)
        mysql = model["services"]["mysql"]
        self.assertEqual("mysql:9.7.4", mysql["image"])
        self.assertEqual("${DB_PASSWORD:-ship}", mysql["environment"]["MYSQL_PASSWORD"])
        self.assertEqual("${DB_ROOT_PASSWORD:-rootship}", mysql["environment"]["MYSQL_ROOT_PASSWORD"])
        self.assertNotEqual(mysql["environment"]["MYSQL_PASSWORD"], mysql["environment"]["MYSQL_ROOT_PASSWORD"])
        self.assertEqual("mysql", model["services"]["app"]["environment"]["DB_HOST"])
        self.assertIn("mysql_data", model["volumes"])
        self.assertIn("healthcheck", mysql)
        (self.root / ".env").write_text("DB_USER=root\n")
        with self.assertRaisesRegex(ConfigError, "DB_USER=root is invalid"):
            build_compose(config, self.root)
        (self.root / ".env").write_text("DB_USER=app\n")
        with patch.dict(os.environ, {"DB_USER": "root"}):
            with self.assertRaisesRegex(ConfigError, "DB_USER=root is invalid"):
                build_compose(config, self.root)

    def test_mysql_named_instance_and_production_credentials(self):
        config = ShipConfig("generic", "main:app", instances={"reports": {"group": "database", "service": "mysql", "name": "reports-db"}})
        model = build_compose(config, self.root)
        self.assertEqual("reports-db", model["services"]["app"]["environment"]["REPORTS_DB_HOST"])
        self.assertEqual("${REPORTS_DB_ROOT_PASSWORD:-rootship}", model["services"]["reports-db"]["environment"]["MYSQL_ROOT_PASSWORD"])
        production = build_compose(config, self.root, production=True)
        self.assertIn(":?", production["services"]["reports-db"]["environment"]["MYSQL_ROOT_PASSWORD"])
        self.assertIn(":?", production["services"]["reports-db"]["environment"]["MYSQL_PASSWORD"])

    def test_postgres_redis_persistence_health_and_credentials(self):
        config = ShipConfig("generic", "main:app", services={"database": "postgres", "cache": "redis"})
        model = build_compose(config, self.root)
        self.assertEqual("postgres:17.12-alpine3.24", model["services"]["postgres"]["image"])
        self.assertIn("postgres_data", model["volumes"])
        self.assertIn("redis_data", model["volumes"])
        self.assertIn("healthcheck", model["services"]["postgres"])
        self.assertIn("healthcheck", model["services"]["redis"])
        self.assertEqual("redis://redis:6379/0", model["services"]["app"]["environment"]["REDIS_URL"])

    def test_mailpit_meilisearch_browser_and_node_dev_scope(self):
        config = ShipConfig("generic", "main:app", services={"mail": "mailpit", "search": "meilisearch", "testing": "selenium", "frontend": "node"})
        dev = build_compose(config, self.root)
        prod = build_compose(config, self.root, production=True)
        for name in ("mailpit", "selenium", "node"):
            self.assertIn(name, dev["services"])
            self.assertNotIn(name, prod["services"])
        self.assertIn("meilisearch", prod["services"])
        self.assertIn("MEILISEARCH_KEY", dev["services"]["app"]["environment"])
        self.assertNotIn("MAIL_HOST", prod["services"]["app"]["environment"])
        self.assertNotIn("SELENIUM_REMOTE_URL", prod["services"]["app"]["environment"])
        self.assertEqual("http://ship-app.test:8000", dev["services"]["app"]["environment"]["APP_URL"])
        self.assertIn("ship-app.test", dev["services"]["app"]["networks"]["default"]["aliases"])
        self.assertEqual(["127.0.0.1:${SHIP_HMR_PORT:-5173}:5173"], dev["services"]["node"]["ports"])
        self.assertEqual(["127.0.0.1:${MAILPIT_WEB_PORT:-8025}:8025"], dev["services"]["mailpit"]["ports"])
        self.assertIn("node_modules", dev["volumes"])

    def test_npm_proxy_requires_node_and_preserves_flags(self):
        config = ShipConfig("generic", "main:app", services={"frontend": "node"})
        config.write(self.root)
        (self.root / "ship").mkdir()
        (self.root / "ship" / "Dockerfile").write_text("FROM python:3.13-slim\n")
        with patch("python_ship.cli.shutil.which", return_value="/usr/bin/docker"), patch("python_ship.cli.subprocess.run", return_value=subprocess.CompletedProcess([], 17)) as run:
            self.assertEqual(17, main(["--project-directory", str(self.root), "npm", "run", "dev", "--", "--host", "0.0.0.0"]))
        self.assertEqual(["exec", "node", "npm", "run", "dev", "--", "--host", "0.0.0.0"], run.call_args.args[0][-8:])

    def test_playwright_is_optional_and_separate_from_selenium(self):
        plain = build_compose(ShipConfig("generic", "main:app"), self.root)
        self.assertNotIn("playwright", plain["services"])

        config = ShipConfig("generic", "main:app", services={"testing": "playwright"})
        development = build_compose(config, self.root)
        playwright = development["services"]["playwright"]
        self.assertEqual("mcr.microsoft.com/playwright:v1.64.1-noble", playwright["image"])
        self.assertIn("playwright@1.64.1 run-server", playwright["command"][-1])
        self.assertEqual("2gb", playwright["shm_size"])
        self.assertIn("healthcheck", playwright)
        self.assertEqual("ws://playwright:3000/", development["services"]["app"]["environment"]["PLAYWRIGHT_WS_URL"])
        self.assertEqual("http://ship-app.test:8000", development["services"]["app"]["environment"]["APP_URL"])
        self.assertIn("ship-app.test", development["services"]["app"]["networks"]["default"]["aliases"])
        self.assertNotIn("selenium", development["services"])
        self.assertNotIn("playwright", build_compose(config, self.root, production=True)["services"])

        named = ShipConfig("generic", "main:app", instances={"browser": {"group": "testing", "service": "playwright", "name": "browser-pool"}})
        model = build_compose(named, self.root)
        self.assertIn("browser-pool", model["services"])
        self.assertEqual("ws://browser-pool:3000/", model["services"]["app"]["environment"]["BROWSER_PLAYWRIGHT_WS_URL"])

    def test_init_selects_playwright(self):
        with patch("python_ship.cli.require_uv", return_value="/fake/uv"):
            self.assertEqual(0, main(["--project-directory", str(self.root), "init", "--app", "main:app", "--testing", "playwright"]))
        self.assertEqual("playwright", ShipConfig.read(self.root).services["testing"])


if __name__ == "__main__":
    unittest.main()
