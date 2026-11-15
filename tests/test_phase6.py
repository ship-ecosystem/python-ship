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
        self.assertEqual([{"path": ".env.production", "required": True}], app["env_file"])
        with redirect_stdout(StringIO()):
            self.assertEqual(0, main(["--project-directory", str(self.root), "config:production"]))
        path = self.root / "ship" / "compose.production.json"
        self.assertEqual(production, json.loads(path.read_text()))
        self.assertFalse((self.root / "ship" / "compose.generated.json").exists())

    def test_old_dockerfile_requires_refresh(self):
        (self.root / "ship" / "Dockerfile").write_text("FROM python:3.13-slim\n")
        with redirect_stderr(StringIO()) as errors:
            self.assertEqual(1, main(["--project-directory", str(self.root), "config:production"]))
        self.assertIn("ship init --force", errors.getvalue())
        self.assertFalse((self.root / "ship" / "compose.production.json").exists())

    @unittest.skipUnless(os.environ.get("SHIP_PRODUCTION_DOCKER_TEST") == "1", "set SHIP_PRODUCTION_DOCKER_TEST=1 for live Docker")
    def test_production_image_serves_http_as_nonroot_without_uv(self):
        if not __import__("shutil").which("docker"):
            self.skipTest("Docker is required")
        (self.root / ".env.production").write_text("SHIP_PRODUCTION_CHECK=1\n")
        (self.root / "private.pem").write_text("not-for-image\n")
        self.assertEqual(0, main(["--project-directory", str(self.root), "config:production"]))
        prefix = ["docker", "compose", "--project-directory", str(self.root), "-f",
                  str(self.root / "ship" / "compose.production.json")]
        try:
            subprocess.run(prefix + ["up", "--build", "-d"], check=True, cwd=self.root)
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
