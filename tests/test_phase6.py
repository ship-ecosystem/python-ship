"""First production image and Compose contract."""

from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
import json
import os
from pathlib import Path
import subprocess
import tempfile
import time
import unittest
from unittest.mock import patch

from python_ship.cli import main
from python_ship.compose import build_compose
from python_ship.config import ShipConfig
from python_ship.production import read_env


FIXTURES = Path(__file__).parent / "fixtures"


class ProductionModelTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="ship-phase6-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        (self.root / "main.py").write_text(
            "async def app(scope, receive, send):\n"
            "    await send({'type': 'http.response.start', 'status': 200, 'headers': []})\n"
            "    await send({'type': 'http.response.body', 'body': b'production'})\n"
        )
        with patch("python_ship.cli.require_uv", return_value="uv"):
            self.assertEqual(0, main(["--project-directory", str(self.root), "init", "--app", "main:app"]))

    def test_distinct_models_and_production_target(self):
        config = ShipConfig.read(self.root)
        development = build_compose(config, self.root)
        production = build_compose(config, self.root, production=True)
        self.assertNotEqual(development["name"], production["name"])
        self.assertEqual([".:/app"], development["services"]["app"]["volumes"])
        app = production["services"]["app"]
        self.assertNotIn("volumes", app)
        self.assertNotIn("ports", app)
        self.assertEqual("production", app["build"]["target"])
        self.assertEqual([{"path": ".env.production", "required": False}], app["env_file"])
        with patch("python_ship.cli.shutil.which", return_value="docker"), patch(
            "python_ship.cli.subprocess.run", return_value=subprocess.CompletedProcess([], 0)
        ) as docker, redirect_stdout(StringIO()):
            self.assertEqual(0, main(["--project-directory", str(self.root), "build"]))
        self.assertIn("build", docker.call_args.args[0])
        path = self.root / "ship" / "compose.production.json"
        self.assertEqual(production, json.loads(path.read_text()))
        self.assertFalse((self.root / "ship" / "compose.generated.json").exists())
        self.assertNotIn("@ASSET_", (self.root / "ship" / "Dockerfile").read_text())

    def test_old_dockerfile_requires_refresh(self):
        (self.root / "ship" / "Dockerfile").write_text("FROM python:3.13-slim\n")
        with redirect_stderr(StringIO()) as errors:
            self.assertEqual(1, main(["--project-directory", str(self.root), "build"]))
        self.assertIn("ship init --force", errors.getvalue())
        self.assertFalse((self.root / "ship" / "compose.production.json").exists())

    def test_production_credentials_are_checked_before_docker(self):
        config = ShipConfig("generic", "main:app", services={"database": "postgres", "storage": "silo"})
        config.write(self.root)
        with redirect_stderr(StringIO()) as errors:
            self.assertEqual(1, main(["--project-directory", str(self.root), "config:test"]))
        self.assertIn("DB_PASSWORD", errors.getvalue())
        self.assertIn("AWS_SECRET_ACCESS_KEY", errors.getvalue())
        (self.root / ".env.production").write_text("DB_PASSWORD=ship\n")
        with patch("python_ship.cli.subprocess.run") as docker, redirect_stderr(StringIO()) as errors:
            self.assertEqual(1, main(["--project-directory", str(self.root), "build"]))
        docker.assert_not_called()
        self.assertIn("known development defaults: DB_PASSWORD", errors.getvalue())
        self.assertIn("AWS_SECRET_ACCESS_KEY", errors.getvalue())
        (self.root / ".env.production").write_text(
            "export DB_PASSWORD='safe production value'\n"
            'AWS_SECRET_ACCESS_KEY="safe-secret" # comment\n'
        )
        self.assertEqual("safe production value", read_env(self.root / ".env.production")["DB_PASSWORD"])
        self.assertEqual(0, main(["--project-directory", str(self.root), "config:test"]))

    def test_production_port_is_explicit(self):
        config = ShipConfig("generic", "main:app", publish_ports=True)
        config.write(self.root)
        loaded = ShipConfig.read(self.root)
        self.assertTrue(loaded.publish_ports)
        production = build_compose(loaded, self.root, production=True)
        self.assertEqual(["${SHIP_PRODUCTION_PORT:-8000}:8000"], production["services"]["app"]["ports"])

    def test_config_test_reports_conflict_missing_and_unsafe_credentials_together(self):
        config = ShipConfig("generic", "main:app", services={"database": "postgres", "storage": "silo"},
                            processes={"postgres": ["python", "worker.py"]})
        config.write(self.root)
        (self.root / ".env.production").write_text("DB_PASSWORD=ship\n")
        with redirect_stderr(StringIO()) as errors:
            self.assertEqual(1, main(["--project-directory", str(self.root), "config:test"]))
        report = errors.getvalue()
        self.assertIn("conflicts with another Compose service", report)
        self.assertIn("AWS_SECRET_ACCESS_KEY", report)
        self.assertIn("known development defaults: DB_PASSWORD", report)

    def test_named_processes_share_application_image_without_ports(self):
        config = ShipConfig("generic", "main:app", processes={
            "worker": ["python", "worker.py"],
            "scheduler": ["python", "scheduler.py"],
        })
        config.write(self.root)
        loaded = ShipConfig.read(self.root)
        self.assertEqual(config.processes, loaded.processes)
        for production in (False, True):
            services = build_compose(loaded, self.root, production=production)["services"]
            for name in ("worker", "scheduler"):
                self.assertEqual(services["app"]["image"], services[name]["image"])
                self.assertNotIn("build", services[name])
                self.assertNotIn("ports", services[name])
            self.assertEqual(["python", "worker.py"], services["worker"]["command"])
            if production:
                self.assertNotIn("volumes", services["worker"])
            else:
                self.assertEqual([".:/app"], services["worker"]["volumes"])
        with patch("python_ship.cli.require_uv", return_value="uv"):
            self.assertEqual(0, main(["--project-directory", str(self.root), "init"]))
        self.assertEqual(config.processes, ShipConfig.read(self.root).processes)

    def test_django_startup_checks_and_optional_static_collection(self):
        django_root = FIXTURES / "django_app"
        config = ShipConfig("django", "project.asgi:application", django_collectstatic=True)
        command = build_compose(config, django_root, production=True)["services"]["app"]["command"]
        self.assertEqual(["sh", "-c"], command[:2])
        self.assertIn("python manage.py check --deploy", command[2])
        self.assertIn("python manage.py collectstatic --noinput", command[2])
        self.assertIn("exec python -m uvicorn project.asgi:application", command[2])
        generic = build_compose(ShipConfig("generic", "main:app"), self.root, production=True)
        self.assertEqual("python", generic["services"]["app"]["command"][0])

    def test_node_assets_are_opt_in_and_require_a_lockfile(self):
        (self.root / "package.json").write_text(json.dumps({
            "name": "ship-assets-fixture", "version": "1.0.0",
            "scripts": {"build": "mkdir -p dist && echo built > dist/asset.txt"},
        }))
        with patch("python_ship.cli.require_uv", return_value="uv"):
            self.assertEqual(0, main(["--project-directory", str(self.root), "init", "--node-assets", "--force"]))
        dockerfile = (self.root / "ship" / "Dockerfile").read_text()
        self.assertIn("AS assets", dockerfile)
        self.assertIn("COPY --from=assets", dockerfile)
        with redirect_stderr(StringIO()) as errors:
            self.assertEqual(1, main(["--project-directory", str(self.root), "config:test"]))
        self.assertIn("package-lock.json", errors.getvalue())
        (self.root / "package-lock.json").write_text(json.dumps({
            "name": "ship-assets-fixture", "version": "1.0.0", "lockfileVersion": 3,
            "packages": {"": {"name": "ship-assets-fixture", "version": "1.0.0"}},
        }))
        self.assertEqual(0, main(["--project-directory", str(self.root), "config:test"]))

    @unittest.skipUnless(os.environ.get("SHIP_NODE_ASSETS_TEST") == "1", "set SHIP_NODE_ASSETS_TEST=1 for the live Node build")
    def test_node_asset_build_is_copied_without_node_runtime(self):
        if not __import__("shutil").which("docker"):
            self.skipTest("Docker is required")
        (self.root / "package.json").write_text(json.dumps({
            "name": "ship-assets-fixture", "version": "1.0.0",
            "scripts": {"build": "mkdir -p dist && echo built > dist/asset.txt"},
        }))
        (self.root / "package-lock.json").write_text(json.dumps({
            "name": "ship-assets-fixture", "version": "1.0.0", "lockfileVersion": 3,
            "packages": {"": {"name": "ship-assets-fixture", "version": "1.0.0"}},
        }))
        with patch("python_ship.cli.require_uv", return_value="uv"):
            self.assertEqual(0, main(["--project-directory", str(self.root), "init", "--node-assets", "--force"]))
        image = build_compose(ShipConfig.read(self.root), self.root, production=True)["services"]["app"]["image"]
        try:
            self.assertEqual(0, main(["--project-directory", str(self.root), "build"]))
            result = subprocess.run(["docker", "run", "--rm", image, "sh", "-c",
                                     "test -f /app/dist/asset.txt && ! command -v node"], capture_output=True, text=True)
            self.assertEqual(0, result.returncode, result.stderr)
        finally:
            subprocess.run(["docker", "image", "rm", image], capture_output=True, check=False)

    @unittest.skipUnless(os.environ.get("SHIP_PRODUCTION_DOCKER_TEST") == "1", "set SHIP_PRODUCTION_DOCKER_TEST=1 for live Docker")
    def test_production_image_serves_http_as_nonroot_without_uv(self):
        if not __import__("shutil").which("docker"):
            self.skipTest("Docker is required")
        (self.root / ".env.production").write_text("SHIP_PRODUCTION_CHECK=1\n")
        (self.root / "private.pem").write_text("not-for-image\n")
        self.assertEqual(0, main(["--project-directory", str(self.root), "build"]))
        prefix = ["docker", "compose", "--project-directory", str(self.root), "-f",
                  str(self.root / "ship" / "compose.production.json")]
        try:
            subprocess.run(prefix + ["up", "-d"], check=True, cwd=self.root)
            command = ["python", "-c", "import os,urllib.request; assert os.getuid()==10001; "
                       "assert urllib.request.urlopen('http://127.0.0.1:8000',timeout=5).read()==b'production' "]
            deadline = time.monotonic() + 30
            while True:
                checked = subprocess.run(prefix + ["exec", "-T", "app", *command], cwd=self.root, capture_output=True, text=True)
                if checked.returncode == 0:
                    break
                if time.monotonic() >= deadline:
                    self.fail(checked.stderr)
                time.sleep(1)
            subprocess.run(prefix + ["exec", "-T", "app", "sh", "-c",
                                     "! command -v uv && ! command -v pip && test ! -e /app/.env.production && test ! -e /app/private.pem"], check=True, cwd=self.root)
        finally:
            subprocess.run(prefix + ["down", "--volumes"], cwd=self.root, check=False)


if __name__ == "__main__":
    unittest.main()
