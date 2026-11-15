"""Opt-in production startup checks for all built-in framework adapters."""

import json
from dataclasses import replace
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import time
import unittest
from unittest.mock import patch

from python_ship.cli import main
from python_ship.compose import build_compose
from python_ship.config import ShipConfig


FIXTURES = Path(__file__).parent / "fixtures"


@unittest.skipUnless(os.environ.get("SHIP_PRODUCTION_MATRIX_TEST") == "1", "set SHIP_PRODUCTION_MATRIX_TEST=1 for live production tests")
class ProductionDockerTests(unittest.TestCase):
    def test_generic_django_fastapi_http_database_and_restart(self):
        if shutil.which("docker") is None:
            self.skipTest("Docker is required")
        scenarios = (
            ("generic", "plain_asgi", "main:app", b"hello from plain ASGI"),
            ("django", "django_app", "project.asgi:application", b'"framework": "django"'),
            ("fastapi", "fastapi_app", "main:app", b'"framework":"fastapi initial"'),
        )
        for framework, fixture, target, response in scenarios:
            if os.environ.get("SHIP_PRODUCTION_FRAMEWORK") and os.environ["SHIP_PRODUCTION_FRAMEWORK"] != framework:
                continue
            with self.subTest(framework=framework), tempfile.TemporaryDirectory(prefix=f"ship-prod-{framework}-") as directory:
                root = Path(directory)
                shutil.copytree(FIXTURES / fixture, root, dirs_exist_ok=True)
                if framework == "generic":
                    (root / "requirements.txt").write_text("psycopg[binary]>=3,<4\n")
                if framework == "django":
                    assets = root / "assets"
                    assets.mkdir()
                    (assets / "site.txt").write_text("collected")
                    with (root / "project" / "settings.py").open("a") as settings:
                        settings.write(
                            "\nfrom pathlib import Path\n"
                            "INSTALLED_APPS = ['django.contrib.staticfiles']\n"
                            "STATIC_URL = '/static/'\n"
                            "STATICFILES_DIRS = [Path(__file__).resolve().parent.parent / 'assets']\n"
                            "STATIC_ROOT = Path(__file__).resolve().parent.parent / 'staticfiles'\n"
                        )
                (root / ".env.production").write_text("DB_PASSWORD=fixture-production-password\n")
                with patch("python_ship.cli.require_uv", return_value="uv"):
                    self.assertEqual(0, main(["--project-directory", str(root), "init", "--framework", framework,
                                               "--app", target, "--database", "postgres"]))
                if framework == "generic":
                    replace(ShipConfig.read(root), processes={
                        "worker": ["python", "-c", "import time; time.sleep(600)"],
                    }).write(root)
                if framework == "django":
                    replace(ShipConfig.read(root), django_collectstatic=True).write(root)
                self.assertEqual(0, main(["--project-directory", str(root), "config:test"]))
                model = build_compose(ShipConfig.read(root), root, production=True)
                self.assertNotIn("ports", model["services"]["app"])
                prefix = ["docker", "compose", "--project-directory", str(root), "--env-file",
                          str(root / ".env.production"), "-f", str(root / "ship" / "compose.production.json")]
                try:
                    self.assertEqual(0, main(["--project-directory", str(root), "build"]))
                    subprocess.run(prefix + ["up", "-d"], cwd=root, check=True)
                    probe = (
                        "import os,urllib.request,psycopg; "
                        f"assert {response!r} in urllib.request.urlopen('http://127.0.0.1:8000',timeout=3).read(); "
                        "assert psycopg.connect(host=os.environ['DB_HOST'],dbname=os.environ['DB_NAME'],"
                        "user=os.environ['DB_USER'],password=os.environ['DB_PASSWORD']).execute('SELECT 1').fetchone()[0]==1"
                    )
                    self._wait_probe(prefix, root, probe)
                    if framework == "django":
                        collected = subprocess.run(prefix + ["exec", "-T", "app", "sh", "-c",
                                                            "test -f /app/staticfiles/site.txt"], cwd=root)
                        self.assertEqual(0, collected.returncode)
                    if framework == "generic":
                        worker = subprocess.run(prefix + ["exec", "-T", "worker", "python", "-c",
                                                           "import os,psycopg; assert os.getuid()==10001; "
                                                           "assert psycopg.connect(host=os.environ['DB_HOST'],dbname=os.environ['DB_NAME'],"
                                                           "user=os.environ['DB_USER'],password=os.environ['DB_PASSWORD']).execute('SELECT 1').fetchone()[0]==1"],
                                                cwd=root, capture_output=True, text=True)
                        self.assertEqual(0, worker.returncode, worker.stderr)
                    subprocess.run(prefix + ["restart", "app"], cwd=root, check=True)
                    self._wait_probe(prefix, root, probe)
                finally:
                    subprocess.run(prefix + ["down", "--volumes"], cwd=root, check=False)

    def _wait_probe(self, prefix, root, probe):
        deadline = time.monotonic() + 60
        while True:
            result = subprocess.run(prefix + ["exec", "-T", "app", "python", "-c", probe],
                                    cwd=root, capture_output=True, text=True)
            if result.returncode == 0:
                return
            if time.monotonic() >= deadline:
                self.fail(result.stderr or result.stdout)
            time.sleep(1)


if __name__ == "__main__":
    unittest.main()
