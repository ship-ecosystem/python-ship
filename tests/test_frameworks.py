"""Framework adapter checks; Docker tests opt in with SHIP_DOCKER_TEST=1."""

import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import time
import unittest
from unittest.mock import patch
from urllib.request import urlopen

from python_ship.adapters import DjangoAdapter, FastAPIAdapter, GenericAdapter
from python_ship.cli import main
from python_ship.compose import build_compose
from python_ship.config import ConfigError, ShipConfig


FIXTURES = Path(__file__).parent / "fixtures"


class FrameworkContractTests(unittest.TestCase):
    def test_bad_framework_targets_report_useful_errors(self):
        for adapter, fixture, target, marker in (
            (DjangoAdapter(), "django_app", "project.missing:application", "Django app"),
            (FastAPIAdapter(), "fastapi_app", "missing:app", "FastAPI app"),
            (FastAPIAdapter(), "fastapi_app", "main:missing", "callable 'missing' is not defined"),
        ):
            with self.subTest(adapter=adapter.key):
                root = FIXTURES / fixture
                problems = adapter.validate(ShipConfig(adapter.key, target), root)
                self.assertIn(marker, problems[0])
                with self.assertRaisesRegex(ConfigError, marker):
                    build_compose(ShipConfig(adapter.key, target), root)

    def test_release_actions_are_declarative_and_not_boot_commands(self):
        for adapter, fixture, target in (
            (GenericAdapter(), "plain_asgi", "main:app"),
            (DjangoAdapter(), "django_app", "project.asgi:application"),
            (FastAPIAdapter(), "fastapi_app", "main:app"),
        ):
            with self.subTest(adapter=adapter.key):
                config = ShipConfig(adapter.key, target)
                compose = build_compose(config, FIXTURES / fixture)
                self.assertNotIn("migrate", compose["services"]["app"]["command"])
                self.assertEqual(adapter.development_command(config), compose["services"]["app"]["command"])
                self.assertTrue(adapter.production_command(config))
                self.assertEqual([], adapter.validate(config, FIXTURES / fixture))
        self.assertEqual([["python", "manage.py", "migrate", "--noinput"]], DjangoAdapter().release_commands(ShipConfig("django", "project.asgi:application")))
        self.assertEqual([], FastAPIAdapter().release_commands(ShipConfig("fastapi", "main:app")))
        self.assertEqual([], GenericAdapter().release_commands(ShipConfig("generic", "main:app")))

    def test_plain_asgi_compose_has_no_framework_dependency(self):
        root = FIXTURES / "plain_asgi"
        self.assertFalse((root / "requirements.txt").exists())
        self.assertFalse((root / "pyproject.toml").exists())
        self.assertEqual("generic", ShipConfig("generic", "main:app").framework)
        self.assertIn("app", build_compose(ShipConfig("generic", "main:app"), root)["services"])

    def test_generated_project_name_is_safe_for_docker_image(self):
        root = FIXTURES / "plain_asgi"
        with tempfile.TemporaryDirectory(prefix="ship-_framework-") as directory:
            destination = Path(directory)
            shutil.copy(root / "main.py", destination / "main.py")
            name = build_compose(ShipConfig("generic", "main:app"), destination)["name"]
        self.assertNotIn("-_", name)
        self.assertNotIn("_-", name)


@unittest.skipUnless(os.environ.get("SHIP_DOCKER_TEST") == "1", "set SHIP_DOCKER_TEST=1 for real Docker integration")
class FrameworkDockerTests(unittest.TestCase):
    def setUp(self):
        if shutil.which("docker") is None:
            self.skipTest("Docker CLI is unavailable")
        directory = tempfile.TemporaryDirectory(prefix="ship-framework-")
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.addCleanup(self._down)

    def _run(self, argv):
        with patch.object(Path, "cwd", return_value=self.root):
            return main(argv)

    def _down(self):
        if (self.root / "ship.json").exists():
            self._run(["down", "--volumes"])
            name = build_compose(ShipConfig.read(self.root), self.root)["name"]
            subprocess.run(["docker", "image", "rm", f"{name}-app:latest"], capture_output=True, check=False)

    def _init_and_up(self, fixture, framework, target):
        shutil.copytree(FIXTURES / fixture, self.root, dirs_exist_ok=True)
        with patch("python_ship.cli.require_uv", return_value="/integration/uv"):
            self.assertEqual(0, self._run(["init", "--framework", framework, "--app", target, "--database", "postgres", "--cache", "redis"]))
        self.assertEqual(0, self._run(["config:test"]))
        self.assertEqual(0, self._run(["up"]))

    def _get(self, path):
        deadline = time.monotonic() + 20
        while True:
            try:
                with urlopen(f"http://127.0.0.1:8000{path}", timeout=3) as response:
                    self.assertEqual(200, response.status)
                    return json.load(response)
            except OSError:
                if time.monotonic() >= deadline:
                    self._run(["logs", "app"])
                    raise
                time.sleep(0.5)

    def test_django_http_manage_database_and_redis(self):
        self._init_and_up("django_app", "django", "project.asgi:application")
        self.assertEqual({"framework": "django"}, self._get("/"))
        self.assertEqual({"database": 1, "redis": True}, self._get("/dependencies/"))
        self.assertEqual(0, self._run(["manage", "check"]))
        self.assertEqual(0, self._run(["manage", "migrate", "--noinput"]))

    def test_fastapi_http_reload_database_and_redis(self):
        self._init_and_up("fastapi_app", "fastapi", "main:app")
        self.assertEqual({"framework": "fastapi initial"}, self._get("/"))
        self.assertEqual({"database": 1, "redis": True}, self._get("/dependencies"))
        path = self.root / "main.py"
        path.write_text(path.read_text().replace("fastapi initial", "fastapi reloaded"))
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            try:
                if self._get("/") == {"framework": "fastapi reloaded"}:
                    break
            except OSError:
                pass
            time.sleep(0.5)
        else:
            self.fail("FastAPI did not reload the changed bind mounted file")


if __name__ == "__main__":
    unittest.main()
