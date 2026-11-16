"""Production images for a framework-free app, Django and FastAPI, built and run for real.

Opt in with SHIP_PRODUCTION_DOCKER_TEST=1.
"""

import time
import unittest
from unittest.mock import patch

from python_ship.config import ShipConfig
from tests.helpers import DockerProjectTestCase, captured, flag, free_port, wait_http

DB_PROBE = ("import os,psycopg; c=psycopg.connect(host=os.environ['DB_HOST'],dbname=os.environ['DB_NAME'],"
            "user=os.environ['DB_USER'],password=os.environ['DB_PASSWORD']); assert c.execute('SELECT 1').fetchone()[0]==1")
HARDENING = ("import os,shutil; assert os.getuid()==10001, os.getuid(); "
             "assert shutil.which('uv') is None and shutil.which('pip') is None; "
             "assert not os.path.exists('/app/.env.production') and not os.path.exists('/app/.env'); "
             "assert not os.path.exists('/app/private.pem') and not os.path.exists('/app/dist/ship')")


@unittest.skipUnless(flag("SHIP_PRODUCTION_DOCKER_TEST"), "set SHIP_PRODUCTION_DOCKER_TEST=1 for live production builds")
class ProductionDockerTests(DockerProjectTestCase):
    def prepare(self, fixture, *init_arguments, **fields):
        if fixture:
            self.copy_fixture(fixture)
        self.port = free_port()
        (self.root / ".env").write_text("DEVELOPMENT_ONLY=1\n")
        (self.root / "private.pem").write_text("must not reach the image\n")
        (self.root / "dist" / "ship" / "old").mkdir(parents=True)
        (self.root / ".env.production").write_text(f"DB_PASSWORD=production-secret-value\nAPP_PORT={self.port}\n")
        self.assertEqual(0, self.init("--database", "postgres", *init_arguments), self.init_output.stderr)
        config = ShipConfig.read(self.root)
        ShipConfig(**{**config.__dict__, "publish_ports": True, **fields}).write(self.root)
        with captured() as output:
            self.assertEqual(0, self.ship("config:test"), output.stderr)
            code = self.ship("build")
        self.assertEqual(0, code, output.stderr + output.stdout[-3000:])
        started = self.compose("up", "-d", "--wait", production=True)
        self.assertEqual(0, started.returncode, started.stderr + self.compose("logs", production=True).stdout[-3000:])

    def exec(self, service, script):
        deadline = time.monotonic() + 60
        while True:
            result = self.compose("exec", "-T", service, "python", "-c", script, production=True)
            if result.returncode == 0 or time.monotonic() > deadline:
                break
            time.sleep(1)
        self.assertEqual(0, result.returncode, result.stderr + self.compose("logs", service, production=True).stdout[-2000:])

    def restart_and_check(self, path, expected):
        self.assertEqual(0, self.compose("restart", "app", production=True).returncode)
        wait_http(self.port, path, expected)

    def test_framework_free_app_with_database_and_worker(self):
        (self.root / "requirements.txt").write_text("psycopg[binary]>=3.2,<4\n")
        self.prepare(None, "--app", "main:app", processes={"worker": "python -c 'import time; time.sleep(600)'"})
        wait_http(self.port, "/", b"hello from plain ASGI")
        self.exec("app", DB_PROBE)
        self.exec("app", HARDENING)
        self.exec("worker", DB_PROBE + "; import os; assert os.getuid()==10001")
        self.restart_and_check("/", b"hello from plain ASGI")

    def test_django_release_commands_collectstatic_and_dependencies(self):
        settings = self.root / "project"
        self.copy_fixture("django_app")
        (self.root / "assets").mkdir()
        (self.root / "assets" / "site.txt").write_text("collected")
        with (settings / "settings.py").open("a") as stream:
            stream.write("\nfrom pathlib import Path\nINSTALLED_APPS = ['django.contrib.staticfiles']\nSTATIC_URL = '/static/'\n"
                         "STATICFILES_DIRS = [Path(__file__).resolve().parent.parent / 'assets']\n"
                         "STATIC_ROOT = Path(__file__).resolve().parent.parent / 'staticfiles'\n")
        self.prepare(None, "--cache", "redis", django_collectstatic=True)
        wait_http(self.port, "/", b'"framework": "django"')
        wait_http(self.port, "/dependencies/", b'"redis": true')
        self.exec("app", "import os; assert os.path.isfile('/app/staticfiles/site.txt')")
        self.exec("app", "import os; assert not os.access('/opt/ship-venv/bin', os.W_OK)")
        self.exec("app", HARDENING)
        logs = self.compose("logs", "app", production=True).stdout
        self.assertIn("System check identified", logs)
        self.restart_and_check("/dependencies/", b'"database": 1')

    def test_fastapi_with_hypercorn(self):
        self.prepare("fastapi_app", "--cache", "redis", "--server", "hypercorn")
        wait_http(self.port, "/", b"fastapi initial")
        wait_http(self.port, "/dependencies", b'"redis":true')
        self.exec("app", HARDENING + "; import hypercorn")
        self.restart_and_check("/dependencies", b'"database":1')

    def test_flask_on_gunicorn_with_a_celery_worker(self):
        self.copy_fixture("flask_app")
        with (self.root / "requirements.txt").open("a") as stream:
            stream.write("celery[redis]==5.7.0\n")
        (self.root / "tasks.py").write_text(
            "import os\nfrom celery import Celery\n"
            "celery = Celery('tasks', broker=os.environ['CELERY_BROKER_URL'], backend=os.environ['CELERY_RESULT_BACKEND'])\n"
            "@celery.task\ndef add(x, y):\n    return x + y\n")
        self.prepare(None, "--cache", "redis", "--tasks", "celery")
        with (self.root / ".env.production").open("a") as stream:
            stream.write("CELERY_APP=tasks\n")
        self.assertEqual(0, self.compose("up", "-d", "--force-recreate", production=True).returncode)
        wait_http(self.port, "/", b"flask initial")
        wait_http(self.port, "/dependencies", b'"redis":true')
        self.exec("app", HARDENING + "; import gunicorn")
        self.exec("app", "from tasks import add; assert add.delay(2, 3).get(timeout=60) == 5")
        self.exec("celery", "import os; assert os.getuid() == 10001")
        self.restart_and_check("/dependencies", b'"database":1')

    def test_node_assets_are_built_without_node_in_the_runtime(self):
        (self.root / "package.json").write_text('{"name":"assets","version":"1.0.0","scripts":{"build":"mkdir -p dist && echo built > dist/asset.txt"}}')
        (self.root / "package-lock.json").write_text('{"name":"assets","version":"1.0.0","lockfileVersion":3,"packages":{"":{"name":"assets","version":"1.0.0"}}}')
        self.port = free_port()
        (self.root / ".env.production").write_text(f"APP_PORT={self.port}\n")
        self.assertEqual(0, self.init("--app", "main:app", "--node-assets"))
        with captured() as output:
            self.assertEqual(0, self.ship("build"), output.stderr)
        image = self.model(production=True)["services"]["app"]["image"]
        import subprocess
        result = subprocess.run(["docker", "run", "--rm", image, "sh", "-c", "test -f /app/dist/asset.txt && ! command -v node"],
                                capture_output=True, text=True)
        self.assertEqual(0, result.returncode, result.stderr)

    def test_security_floor_rejects_a_stale_base_image(self):
        self.assertEqual(0, self.init("--app", "main:app"))
        with patch.dict("python_ship.security.MINIMUM_PATCHED", {"3.13": "3.13.999"}), captured() as output:
            self.assertEqual(1, self.ship("build"))
        self.assertIn("Building the production images failed", output.stderr)


if __name__ == "__main__":
    unittest.main()
