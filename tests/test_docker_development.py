"""Real Docker development stacks for a framework-free app, Django and FastAPI.

Opt in with SHIP_DOCKER_TEST=1. Each test builds images, starts containers and
removes everything (containers, volumes, images) afterwards.
"""

import json
import os
from pathlib import Path
import subprocess
import sys
import unittest
import uuid

from python_ship.config import AdditionalService, ShipConfig
from tests.helpers import DockerProjectTestCase, captured, flag, free_port, wait_http


def ship_process(root, *arguments, stdin=""):
    """Run the CLI in a child process, for commands that read standard input."""
    return subprocess.run([sys.executable, "-m", "python_ship", "--project-directory", str(root), *arguments],
                          input=stdin, capture_output=True, text=True,
                          env={**os.environ, "PYTHONPATH": str(Path(__file__).parents[1] / "src")})


@unittest.skipUnless(flag("SHIP_DOCKER_TEST"), "set SHIP_DOCKER_TEST=1 for real Docker integration")
class FrameworkDevelopmentTests(DockerProjectTestCase):
    def up(self, *init_arguments):
        self.port = free_port()
        (self.root / ".env").write_text(f"APP_PORT={self.port}\n")
        # Init can create a production settings stub with blank required values. Validate
        # production here only when the fixture supplied its own settings before init.
        has_production_settings = (self.root / ".env.production").is_file()
        self.assertEqual(0, self.init(*init_arguments), self.init_output.stderr)
        with captured() as output:
            if has_production_settings:
                self.assertEqual(0, self.ship("config:test"), output.stderr + output.stdout)
            code = self.ship("up")
        self.assertEqual(0, code, output.stderr + output.stdout[-3000:])
        self.assertIn(f"http://127.0.0.1:{self.port}", output.stdout)

    def assert_reloads(self, path, old, new, url="/"):
        path.write_text(path.read_text().replace(old, new))
        wait_http(self.port, url, new.encode(), timeout=60)

    def test_plain_asgi_app_without_framework_or_manifest(self):
        self.assertFalse((self.root / "requirements.txt").exists())
        self.up()
        self.assertEqual("generic", ShipConfig.read(self.root).framework)
        self.assertEqual(b"hello from plain ASGI", wait_http(self.port))
        self.assert_reloads(self.root / "main.py", "hello from plain ASGI", "hello after reload")
        with captured() as output:
            self.assertEqual(0, self.ship("exec", "app", "python", "-c", "import uvicorn, sys; print(sys.version_info[:2])"))
            self.assertEqual(0, self.ship("doctor"))
            self.assertEqual(7, self.ship("exec", "app", "python", "-c", "raise SystemExit(7)"))
        self.assertIn("Every service is running", output.stdout)
        if sys.platform.startswith("linux") and hasattr(os, "getuid"):
            self.assertEqual(0, self.ship("exec", "app", "python", "-c", "open('/app/created.txt', 'w').write('x')"))
            self.assertEqual(os.getuid(), (self.root / "created.txt").stat().st_uid)

    def test_django_http_database_redis_manage_and_db(self):
        self.copy_fixture("django_app")
        (self.root / ".env.production").write_text("DB_PASSWORD=production-only-secret\n")
        self.up("--database", "postgres", "--cache", "redis")
        config = ShipConfig.read(self.root)
        self.assertEqual(("django", "project.asgi:application"), (config.framework, config.app))
        self.assertEqual({"framework": "django"}, json.loads(wait_http(self.port, "/")))
        self.assertEqual({"database": 1, "redis": True}, json.loads(wait_http(self.port, "/dependencies/")))
        with captured():
            self.assertEqual(0, self.ship("manage", "check"))
            self.assertEqual(0, self.ship("manage", "migrate", "--noinput"))
        result = ship_process(self.root, "db", stdin="SELECT 'ship-db-ok' AS result;\n")
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertIn("ship-db-ok", result.stdout)
        self.assert_reloads(self.root / "project" / "views.py", '"framework": "django"', '"framework": "django reloaded"')

    def test_fastapi_http_database_redis_reload_and_mysql_db(self):
        self.copy_fixture("fastapi_app")
        self.up("--database", "postgres", "--cache", "redis")
        config = ShipConfig.read(self.root)
        self.assertEqual(("fastapi", "main:app"), (config.framework, config.app))
        self.assertEqual({"framework": "fastapi initial"}, json.loads(wait_http(self.port, "/")))
        self.assertEqual({"database": 1, "redis": True}, json.loads(wait_http(self.port, "/dependencies")))
        self.assert_reloads(self.root / "main.py", "fastapi initial", "fastapi reloaded")
        result = ship_process(self.root, "db", stdin="SELECT 40 + 2;\n")
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertIn("42", result.stdout)

    def test_hypercorn_serves_and_reloads(self):
        self.up("--server", "hypercorn")
        self.assertEqual(b"hello from plain ASGI", wait_http(self.port))
        self.assert_reloads(self.root / "main.py", "hello from plain ASGI", "hello from hypercorn")
        self.assertEqual(0, self.ship("exec", "app", "python", "-c", "import hypercorn"))

    def test_flask_on_gunicorn_http_database_redis_reload_and_cli(self):
        self.copy_fixture("flask_app")
        self.up("--database", "postgres", "--cache", "redis")
        config = ShipConfig.read(self.root)
        self.assertEqual(("flask", "app:app", "gunicorn"), (config.framework, config.app, config.server))
        self.assertEqual({"framework": "flask initial"}, json.loads(wait_http(self.port, "/")))
        self.assertEqual({"database": 1, "redis": True}, json.loads(wait_http(self.port, "/dependencies")))
        self.assert_reloads(self.root / "app.py", "flask initial", "flask reloaded")
        result = ship_process(self.root, "flask", "hello")
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertIn("hello from flask", result.stdout)

    def test_flask_on_granian_wsgi(self):
        self.copy_fixture("flask_app")
        self.up("--database", "postgres", "--cache", "redis", "--server", "granian")
        self.assertEqual({"database": 1, "redis": True}, json.loads(wait_http(self.port, "/dependencies")))
        self.assert_reloads(self.root / "app.py", "flask initial", "flask on granian")

    def test_fastapi_on_granian_asgi(self):
        self.copy_fixture("fastapi_app")
        self.up("--database", "postgres", "--cache", "redis", "--server", "granian")
        self.assertEqual({"database": 1, "redis": True}, json.loads(wait_http(self.port, "/dependencies")))
        self.assert_reloads(self.root / "main.py", "fastapi initial", "fastapi on granian")

    def test_plain_wsgi_app_gets_gunicorn(self):
        (self.root / "main.py").write_text(
            "def app(environ, start_response):\n"
            "    start_response('200 OK', [('Content-Type', 'text/plain')])\n"
            "    return [b'hello from plain WSGI']\n")
        self.up()
        self.assertEqual("gunicorn", ShipConfig.read(self.root).server)
        self.assertEqual(b"hello from plain WSGI", wait_http(self.port))

    def test_broken_app_fails_up_with_guidance(self):
        (self.root / "main.py").write_text("import missing_module\napp = None\n")
        self.port = free_port()
        (self.root / ".env").write_text(f"APP_PORT={self.port}\n")
        self.assertEqual(0, self.init("--app", "main:app"))
        with captured() as output:
            self.assertEqual(1, self.ship("up"))
        self.assertIn("ship logs", output.stderr)


@unittest.skipUnless(flag("SHIP_DOCKER_TEST"), "set SHIP_DOCKER_TEST=1 for real Docker integration")
class StackTopologyTests(DockerProjectTestCase):
    def setUp(self):
        # Registered first so it runs last, after the stack that uses the network is down.
        self.network = f"ship-test-shared-{uuid.uuid4().hex[:8]}"
        self.addCleanup(subprocess.run, ["docker", "network", "rm", self.network], capture_output=True, check=False)
        super().setUp()

    def test_named_instances_service_names_external_network_and_override(self):
        port = free_port()
        (self.root / ".env").write_text(f"APP_PORT={port}\n")
        ShipConfig(
            app="main:app", services={"database": "postgres", "cache": "redis"},
            additional_services=[AdditionalService("database", "postgres", "analytics"),
                                 AdditionalService("cache", "redis", "queue")],
            service_names={"app": "web", "postgres": "main-db"}, external_network=self.network,
        ).write(self.root)
        (self.root / "docker-compose.override.yml").write_text("services:\n  web:\n    environment:\n      OVERRIDE_TEST: present\n")
        created = subprocess.run(["docker", "network", "create", self.network], capture_output=True, text=True)
        self.assertEqual(0, created.returncode, created.stderr)
        self.assertEqual(0, self.init())
        with captured() as output:
            self.assertEqual(0, self.ship("up"), output.stderr)
        probe = (
            "import os,socket; e=os.environ; "
            "assert e['DB_HOST']=='main-db' and e['ANALYTICS_DB_HOST']=='postgres-analytics'; "
            "assert e['QUEUE_REDIS_URL']=='redis://redis-queue:6379/0' and e['OVERRIDE_TEST']=='present'; "
            "[socket.create_connection((h,p),3).close() for h,p in "
            "[('main-db',5432),('postgres-analytics',5432),('redis',6379),('redis-queue',6379)]]"
        )
        self.assertEqual(0, self.ship("exec", "web", "python", "-c", probe))
        result = ship_process(self.root, "db", "analytics", stdin="SELECT current_database();\n")
        self.assertEqual(0, result.returncode, result.stderr)
        inspected = json.loads(subprocess.run(["docker", "network", "inspect", self.network], capture_output=True, text=True).stdout)
        names = {container["Name"] for container in inspected[0]["Containers"].values()}
        self.assertTrue(any("-web-" in name for name in names), names)
        volumes = subprocess.run(["docker", "volume", "ls", "--format", "{{.Name}}"], capture_output=True, text=True).stdout
        self.assertIn("postgres-analytics_data", volumes)

    def test_two_projects_run_side_by_side(self):
        projects = []
        for label in ("first", "second"):
            project = self.root / f"{self.root.name}-{label}"
            project.mkdir()
            (project / "main.py").write_text(
                "async def app(scope, receive, send):\n"
                "    await send({'type': 'http.response.start', 'status': 200, 'headers': []})\n"
                f"    await send({{'type': 'http.response.body', 'body': b'{label}'}})\n")
            port = free_port()
            (project / ".env").write_text(f"APP_PORT={port}\n")
            projects.append((project, port, label))
            self.addCleanup(lambda project=project: subprocess.run(
                [sys.executable, "-m", "python_ship", "--project-directory", str(project), "down", "--volumes"],
                capture_output=True, env={**os.environ, "PYTHONPATH": str(Path(__file__).parents[1] / "src")}))
        from python_ship.cli import main
        from tests.helpers import no_uv_prompt
        for project, _, _ in projects:
            with no_uv_prompt(), captured():
                self.assertEqual(0, main(["--project-directory", str(project), "init", "-n", "--cache", "redis"]))
                self.assertEqual(0, main(["--project-directory", str(project), "up"]))
        for _, port, label in projects:
            self.assertEqual(label.encode(), wait_http(port))
        first, second = projects[0][0], projects[1][0]
        self.assertEqual(0, main(["--project-directory", str(first), "exec", "redis", "redis-cli", "SET", "ship:isolated", "first"]))
        other = ship_process(second, "exec", "redis", "redis-cli", "GET", "ship:isolated")
        self.assertEqual(0, other.returncode, other.stderr)
        self.assertEqual("", other.stdout.strip())


if __name__ == "__main__":
    unittest.main()
