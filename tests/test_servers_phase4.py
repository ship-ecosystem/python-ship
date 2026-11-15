"""Framework-independent ASGI server selection and extension contracts."""

import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from python_ship.cli import main
from python_ship.compose import build_compose
from python_ship.config import ConfigError, ShipConfig


FIXTURE = Path(__file__).parent / "fixtures" / "plain_asgi" / "main.py"


class ServerSelectionTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory(prefix="ship-server-")
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        (self.root / "main.py").write_text(FIXTURE.read_text())

    def test_builtin_servers_are_independent_of_framework(self):
        for framework, target in (("generic", "main:app"), ("fastapi", "main:app"), ("django", "project.asgi:application")):
            with self.subTest(framework=framework):
                if framework == "django":
                    (self.root / "manage.py").write_text("\n")
                    (self.root / "project").mkdir(exist_ok=True)
                    (self.root / "project" / "asgi.py").write_text("application = object()\n")
                config = ShipConfig(framework, target, server="hypercorn")
                dev = build_compose(config, self.root)["services"]["app"]
                prod = build_compose(config, self.root, production=True)["services"]["app"]
                self.assertEqual(["python", "-m", "hypercorn", target, "--bind", "0.0.0.0:8000", "--reload"], dev["command"])
                self.assertEqual(["python", "-m", "hypercorn", target, "--bind", "0.0.0.0:8000"], prod["command"])
                self.assertEqual("hypercorn==0.18.0", dev["build"]["args"]["SHIP_SERVER_REQUIREMENT"])
                self.assertNotIn("volumes", prod)

    def test_init_selects_hypercorn_and_requires_fresh_dockerfile(self):
        with patch("python_ship.cli.require_uv", return_value="/fake/uv"):
            self.assertEqual(0, main(["--project-directory", str(self.root), "init", "--app", "main:app", "--server", "hypercorn"]))
        self.assertEqual("hypercorn", ShipConfig.read(self.root).server)
        self.assertIn('uv pip install "$SHIP_SERVER_REQUIREMENT"', (self.root / "ship" / "Dockerfile").read_text())
        self.assertEqual(0, main(["--project-directory", str(self.root), "config:test"]))
        with patch("python_ship.cli.require_uv", return_value="/fake/uv"):
            self.assertEqual(0, main(["--project-directory", str(self.root), "init"]))
        self.assertEqual("hypercorn", ShipConfig.read(self.root).server)
        (self.root / "ship" / "Dockerfile").write_text("FROM python:3.13-slim\nRUN uv pip install uvicorn\n")
        self.assertEqual(1, main(["--project-directory", str(self.root), "config:test"]))
        with patch("python_ship.cli.require_uv", return_value="/fake/uv"):
            self.assertEqual(1, main(["--project-directory", str(self.root), "init"]))
            self.assertEqual(0, main(["--project-directory", str(self.root), "init", "--force"]))
        self.assertEqual(0, main(["--project-directory", str(self.root), "config:test"]))

    def test_unknown_and_invalid_server_are_configuration_errors(self):
        with self.assertRaisesRegex(ConfigError, "Unknown ASGI server"):
            build_compose(ShipConfig("generic", "main:app", server="missing"), self.root)
        (self.root / "ship.json").write_text(json.dumps({"app": "main:app", "server": 4}))
        with self.assertRaisesRegex(ConfigError, "server must"):
            ShipConfig.read(self.root)

    def test_selected_community_server_entry_point(self):
        class CommunityServer:
            key = "community"
            requirement = "community-asgi==2.0"
            def development_command(self, target): return ["python", "-m", "community_asgi", target, "--reload"]
            def production_command(self, target): return ["python", "-m", "community_asgi", target]

        class EntryPoint:
            name = "community-server"
            def load(self): return CommunityServer

        def entries(*, group):
            return [EntryPoint()] if group == "python_ship.servers" else []

        config = ShipConfig("generic", "main:app", extensions=["community-server"], server="community")
        with patch("python_ship.plugins.entry_points", side_effect=entries):
            model = build_compose(config, self.root)
        self.assertEqual(["python", "-m", "community_asgi", "main:app", "--reload"], model["services"]["app"]["command"])
        self.assertEqual("community-asgi==2.0", model["services"]["app"]["build"]["args"]["SHIP_SERVER_REQUIREMENT"])

    def test_invalid_server_extension_and_incompatible_adapter_are_reported(self):
        class BrokenServer:
            key = "broken"
            requirement = ""

        class EntryPoint:
            name = "broken-server"
            def load(self): return BrokenServer

        with patch("python_ship.plugins.entry_points", side_effect=lambda *, group: [EntryPoint()] if group == "python_ship.servers" else []):
            with self.assertRaisesRegex(ConfigError, "server contract"):
                build_compose(ShipConfig("generic", "main:app", extensions=["broken-server"], server="broken"), self.root)

        class CustomAdapter:
            key = "custom"
            def validate(self, config, root): return []
            def development_command(self, config): return ["python", "serve.py"]
            def production_command(self, config): return ["python", "serve.py"]

        from python_ship.services import BUILTIN_SERVICES
        with patch("python_ship.compose.load_extensions", return_value=({"custom": CustomAdapter()}, BUILTIN_SERVICES)):
            with self.assertRaisesRegex(ConfigError, "does not support selectable ASGI servers"):
                build_compose(ShipConfig("custom", "main:app", server="hypercorn"), self.root)


if __name__ == "__main__":
    unittest.main()
