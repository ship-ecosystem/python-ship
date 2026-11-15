"""Community extension installation and failure contracts."""

from contextlib import redirect_stderr
from io import StringIO
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from python_ship.cli import main
from python_ship.compose import build_compose
from python_ship.config import ConfigError, ShipConfig
from python_ship.plugins import load_extensions


class FakeEntryPoint:
    def __init__(self, name, factory):
        self.name = name
        self.factory = factory

    def load(self):
        return self.factory


class PluginFailures(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="ship-phase8-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        (self.root / "main.py").write_text("async def app(scope, receive, send): pass\n")
        (self.root / ".env.production").write_text("EXAMPLE=1\n")
        ship = self.root / "ship"
        ship.mkdir()
        (ship / "Dockerfile").write_text("FROM python:3.13-slim AS production\n")

    def test_missing_extension_fails_init_build_and_release_before_docker(self):
        ShipConfig("generic", "main:app", extensions=["missing-package"]).write(self.root)
        with patch("python_ship.plugins.entry_points", return_value=[]), patch(
            "python_ship.cli.subprocess.run"
        ) as docker:
            for command in (["init"], ["build"], ["release", "--tag", "v1"]):
                with self.subTest(command=command), redirect_stderr(StringIO()) as errors:
                    self.assertEqual(1, main(["--project-directory", str(self.root), *command]))
                self.assertIn("missing-package", errors.getvalue())
                self.assertIn("Ship CLI environment", errors.getvalue())
        docker.assert_not_called()

    def test_duplicate_name_broken_constructor_and_invalid_implementation(self):
        class Broken:
            def __init__(self):
                raise RuntimeError("constructor exploded")
        class InvalidService:
            key, group, label = "invalid", "example", "Invalid"
            compose_fragment = "not callable"
            def app_environment(self): return {}
        class InvalidAdapter:
            key = "invalid-adapter"
            def detect(self, root): return True
            validate = None
            def development_command(self, config): return []
            def production_command(self, config): return []
            def console_commands(self): return {}
            def release_commands(self, config): return []
        cases = (
            ({"python_ship.services": [FakeEntryPoint("duplicate", InvalidService)],
              "python_ship.adapters": [FakeEntryPoint("duplicate", InvalidAdapter)]}, "duplicate", "multiple"),
            ({"python_ship.services": [FakeEntryPoint("broken", Broken)]}, "broken", "constructor exploded"),
            ({"python_ship.services": [FakeEntryPoint("invalid", InvalidService)]}, "invalid", "service contract"),
            ({"python_ship.adapters": [FakeEntryPoint("invalid-adapter", InvalidAdapter)]}, "invalid-adapter", "adapter contract"),
        )
        for groups, selected, message in cases:
            with self.subTest(selected=selected):
                with patch("python_ship.plugins.entry_points", side_effect=lambda *, group: groups.get(group, [])):
                    with self.assertRaisesRegex(ConfigError, message):
                        load_extensions(ShipConfig("generic", "main:app", extensions=[selected]))

    def test_duplicate_builtin_key_and_bad_optional_hook(self):
        class Duplicate:
            key, group, label = "redis", "cache", "Duplicate"
            def compose_fragment(self): return {}
            def app_environment(self): return {}
        class BadHook:
            key, group, label = "bad-hook", "example", "Bad hook"
            def compose_fragment(self): return {}
            def app_environment(self): return {}
            compose_fragment_for = "not callable"
        for selected, factory, message in (("duplicate-key", Duplicate, "duplicate key"),
                                           ("bad-hook", BadHook, "optional service hook")):
            with patch("python_ship.plugins.entry_points", side_effect=lambda *, group: [FakeEntryPoint(selected, factory)] if group == "python_ship.services" else []):
                with self.assertRaisesRegex(ConfigError, message):
                    load_extensions(ShipConfig("generic", "main:app", extensions=[selected]))


@unittest.skipUnless(__import__("os").environ.get("SHIP_EXAMPLE_PLUGINS_TEST") == "1", "install both example wheels and set SHIP_EXAMPLE_PLUGINS_TEST=1")
class InstalledExamplePlugins(unittest.TestCase):
    def test_real_entry_points_build_both_compose_models(self):
        with tempfile.TemporaryDirectory(prefix="ship-example-service-") as directory:
            root = Path(directory)
            (root / "main.py").write_text("async def app(scope, receive, send): pass\n")
            config = ShipConfig("generic", "main:app", services={"example": "example-http"}, extensions=["example-http"])
            config.write(root)
            with patch("python_ship.cli.require_uv", return_value="uv"):
                self.assertEqual(0, main(["--project-directory", str(root), "init"]))
            for production in (False, True):
                model = build_compose(config, root, production=production)
                self.assertIn("example-http", model["services"])
                self.assertEqual("http://example-http:8081/", model["services"]["app"]["environment"]["EXAMPLE_HTTP_URL"])
        with tempfile.TemporaryDirectory(prefix="ship-example-adapter-") as directory:
            root = Path(directory)
            (root / "main.py").write_text("app = object()\n")
            (root / "requirements.txt").write_text("starlette\n")
            config = ShipConfig("example-starlette", "main:app", extensions=["example-starlette"])
            config.write(root)
            with patch("python_ship.cli.require_uv", return_value="uv"):
                self.assertEqual(0, main(["--project-directory", str(root), "init"]))
            development = build_compose(config, root)
            production = build_compose(config, root, production=True)
            self.assertIn("--reload", development["services"]["app"]["command"])
            self.assertIn("--lifespan on", production["services"]["app"]["command"][2])


if __name__ == "__main__":
    unittest.main()
