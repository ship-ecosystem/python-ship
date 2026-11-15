import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from python_ship.adapters import DjangoAdapter
from python_ship.cli import main
from python_ship.compose import build_compose
from python_ship.config import ConfigError, ShipConfig


class ScaffoldTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        (self.root / "requirements.txt").write_text("fastapi\nuvicorn\n", encoding="utf-8")
        (self.root / "app").mkdir()
        (self.root / "app" / "main.py").write_text("app = object()\n", encoding="utf-8")

    def test_fastapi_init_and_compose(self):
        with patch.object(Path, "cwd", return_value=self.root), patch("python_ship.cli.require_uv", return_value="/usr/bin/uv"):
            self.assertEqual(0, main(["init", "--framework", "fastapi", "--app", "app.main:app", "--database", "postgres", "--cache", "redis"]))
            self.assertEqual(0, main(["config:test"]))
        config = ShipConfig.read(self.root)
        compose = build_compose(config, self.root)
        self.assertEqual(["python", "-m", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--reload"], compose["services"]["app"]["command"])
        self.assertEqual("postgres", compose["services"]["app"]["environment"]["DB_HOST"])
        self.assertEqual("redis://redis:6379/0", compose["services"]["app"]["environment"]["REDIS_URL"])
        self.assertIn("postgres_data", compose["volumes"])
        self.assertIn(".env", (self.root / ".dockerignore").read_text(encoding="utf-8"))
        self.assertIn("uv pip install -r requirements.txt", (self.root / "ship" / "Dockerfile").read_text(encoding="utf-8"))
        self.assertIn("ghcr.io/astral-sh/uv:0.13.3", (self.root / "ship" / "Dockerfile").read_text(encoding="utf-8"))

    def test_plain_asgi_app_needs_no_framework_or_dependency_manifest(self):
        (self.root / "requirements.txt").unlink()
        (self.root / "main.py").write_text(
            "async def app(scope, receive, send): pass\n", encoding="utf-8"
        )
        with patch.object(Path, "cwd", return_value=self.root), patch("python_ship.cli.require_uv", return_value="/usr/bin/uv"):
            self.assertEqual(0, main(["init", "--app", "main:app"]))
            self.assertEqual(0, main(["config:test"]))
        config = ShipConfig.read(self.root)
        self.assertEqual("generic", config.framework)
        compose = build_compose(config, self.root)
        self.assertEqual(["python", "-m", "uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000", "--reload"], compose["services"]["app"]["command"])
        self.assertIn('uv pip install "$SHIP_SERVER_REQUIREMENT"', (self.root / "ship" / "Dockerfile").read_text(encoding="utf-8"))
        self.assertIn("uv sync --locked --no-dev", (self.root / "ship" / "Dockerfile").read_text(encoding="utf-8"))

    def test_declining_uv_aborts_init_without_writing_files(self):
        (self.root / "main.py").write_text("async def app(scope, receive, send): pass\n", encoding="utf-8")
        with patch.object(Path, "cwd", return_value=self.root), patch("python_ship.uv.locate_uv", return_value=None), patch("builtins.input", return_value="no"):
            self.assertEqual(1, main(["init", "--app", "main:app"]))
        self.assertFalse((self.root / "ship.json").exists())
        self.assertFalse((self.root / "ship").exists())
        self.assertFalse((self.root / ".dockerignore").exists())

    def test_accepting_uv_installs_before_init(self):
        (self.root / "main.py").write_text("async def app(scope, receive, send): pass\n", encoding="utf-8")
        with patch.object(Path, "cwd", return_value=self.root), patch("python_ship.uv.locate_uv", side_effect=[None, "/home/test/.local/bin/uv"]), patch("builtins.input", return_value="yes"), patch("python_ship.uv._install_uv") as installer:
            self.assertEqual(0, main(["init", "--app", "main:app"]))
        installer.assert_called_once_with()
        self.assertTrue((self.root / "ship.json").exists())

    def test_failed_uv_install_aborts_init_without_writing_files(self):
        (self.root / "main.py").write_text("async def app(scope, receive, send): pass\n", encoding="utf-8")
        with patch.object(Path, "cwd", return_value=self.root), patch("python_ship.uv.locate_uv", return_value=None), patch("builtins.input", return_value="yes"), patch("python_ship.uv._install_uv", side_effect=ConfigError("install failed")):
            self.assertEqual(1, main(["init", "--app", "main:app"]))
        self.assertFalse((self.root / "ship.json").exists())
        self.assertFalse((self.root / "ship").exists())

    def test_django_adapter(self):
        (self.root / "manage.py").write_text("", encoding="utf-8")
        (self.root / "project").mkdir()
        (self.root / "project" / "asgi.py").write_text("application = object()\n", encoding="utf-8")
        config = ShipConfig("django", "project.asgi:application")
        compose = build_compose(config, self.root)
        self.assertEqual(["python", "-m", "uvicorn", "project.asgi:application", "--host", "0.0.0.0", "--port", "8000", "--reload"], compose["services"]["app"]["command"])
        self.assertEqual(["python", "-m", "uvicorn", "project.asgi:application", "--host", "0.0.0.0", "--port", "8000"], DjangoAdapter().production_command(config))
        self.assertEqual([["python", "manage.py", "migrate", "--noinput"]], DjangoAdapter().release_commands(config))

    def test_invalid_service_fails(self):
        config = ShipConfig("fastapi", "app.main:app", services={"database": "unknown"})
        with self.assertRaisesRegex(ConfigError, "Unknown service"):
            build_compose(config, self.root)

    def test_config_validation(self):
        (self.root / "ship.json").write_text(json.dumps({"framework": "fastapi", "app": "app.main:app", "extensions": "bad"}), encoding="utf-8")
        with self.assertRaisesRegex(ConfigError, "extensions must be a list"):
            ShipConfig.read(self.root)

    def test_explicit_plugin_registration(self):
        class CustomAdapter:
            key = "custom"
            def detect(self, root): return True
            def validate(self, config, root): return []
            def development_command(self, config): return ["python", "serve.py"]
            def production_command(self, config): return ["python", "serve.py"]
            def console_commands(self): return {}
            def release_commands(self, config): return []

        class EntryPoint:
            name = "custom"
            def load(self): return CustomAdapter

        def fake_entry_points(*, group):
            return [EntryPoint()] if group == "python_ship.adapters" else []

        from python_ship import plugins
        with patch.object(plugins, "entry_points", side_effect=fake_entry_points):
            adapters, _ = plugins.load_extensions(ShipConfig("custom", "app.main:app", extensions=["custom"]))
        self.assertIn("custom", adapters)


if __name__ == "__main__":
    unittest.main()
