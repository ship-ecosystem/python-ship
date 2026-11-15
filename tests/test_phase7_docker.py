"""Opt-in clean-directory release boot, deploy, crash, and recovery."""

import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import tempfile
import time
import unittest
from unittest.mock import patch
from urllib.request import urlopen

from python_ship.cli import main
from python_ship.config import ShipConfig


@unittest.skipUnless(os.environ.get("SHIP_RELEASE_DOCKER_TEST") == "1", "set SHIP_RELEASE_DOCKER_TEST=1 for live Docker release")
class ReleaseDockerTests(unittest.TestCase):
    def test_release_boots_without_source_and_recovers_from_crash(self):
        if os.name == "nt" or shutil.which("docker") is None:
            self.skipTest("This release shell test needs Docker on a Unix host")
        with tempfile.TemporaryDirectory(prefix="ship-release-test-") as directory:
            base = Path(directory)
            project = base / "project"
            project.mkdir()
            clean = base / "clean-machine"
            with socket.socket() as listener:
                listener.bind(("127.0.0.1", 0))
                port = listener.getsockname()[1]
            (project / "main.py").write_text(
                "import os, psycopg\n"
                "async def app(scope, receive, send):\n"
                "    if scope['type'] != 'http': return\n"
                "    with psycopg.connect(host=os.environ['DB_HOST'], dbname=os.environ['DB_NAME'], "
                "user=os.environ['DB_USER'], password=os.environ['DB_PASSWORD']) as db:\n"
                "        value = db.execute('SELECT value FROM ship_release_probe').fetchone()[0].encode()\n"
                "    await send({'type': 'http.response.start', 'status': 200, 'headers': []})\n"
                "    await send({'type': 'http.response.body', 'body': value})\n"
            )
            (project / "requirements.txt").write_text("psycopg[binary]>=3,<4\n")
            (project / ".env.production").write_text(
                f"DB_PASSWORD=release-integration-secret\nSHIP_PRODUCTION_PORT={port}\n"
            )
            migration = (
                "import os,psycopg; "
                "db=psycopg.connect(host=os.environ['DB_HOST'],dbname=os.environ['DB_NAME'],"
                "user=os.environ['DB_USER'],password=os.environ['DB_PASSWORD']); "
                "db.execute('CREATE TABLE IF NOT EXISTS ship_release_probe(value text)'); "
                "db.execute(\"INSERT INTO ship_release_probe VALUES ('deployed')\"); db.commit(); db.close()"
            )
            with patch("python_ship.cli.require_uv", return_value="uv"):
                self.assertEqual(0, main(["--project-directory", str(project), "init", "--app", "main:app", "--database", "postgres"]))
            config = ShipConfig.read(project)
            from dataclasses import replace
            replace(config, name=base.name, publish_ports=True, deploy_commands=[["python", "-c", migration]]).write(project)
            self.assertEqual(0, main(["--project-directory", str(project), "release", "--tag", "test1"]))
            artifact = project / "dist" / "ship" / "test1"
            shutil.copytree(artifact, clean)
            manifest = json.loads((clean / "release.json").read_text())
            app_image = next(entry["image"] for entry in manifest["images"] if "app" in entry["services"])
            shutil.rmtree(project)
            subprocess.run(["docker", "image", "rm", app_image], capture_output=True, check=True)
            for entry in manifest["images"]:
                subprocess.run(["docker", "load", "-i", str(clean / entry["file"])], check=True, cwd=clean)
            prefix = ["docker", "compose", "-f", str(clean / "docker-compose.yml")]
            try:
                subprocess.run(["sh", "deploy-commands.sh"], check=True, cwd=clean)
                subprocess.run(prefix + ["up", "-d"], check=True, cwd=clean)
                self._wait_http(port)
                subprocess.run(prefix + ["restart", "app"], check=True, cwd=clean)
                self._wait_http(port)
                path = clean / "docker-compose.yml"
                original = path.read_text()
                crashed = json.loads(original)
                crashed["services"]["app"]["command"] = ["python", "-c", "raise SystemExit(17)"]
                crashed["services"]["app"]["restart"] = "no"
                path.write_text(json.dumps(crashed, indent=2) + "\n")
                subprocess.run(prefix + ["up", "-d", "--force-recreate", "app"], check=True, cwd=clean)
                self._wait_exit(prefix, clean, 17)
                path.write_text(original)
                subprocess.run(prefix + ["up", "-d", "--force-recreate", "app"], check=True, cwd=clean)
                self._wait_http(port)
            finally:
                subprocess.run(prefix + ["down", "--volumes"], check=False, cwd=clean)

    def _wait_http(self, port):
        deadline = time.monotonic() + 45
        while True:
            try:
                self.assertEqual(b"deployed", urlopen(f"http://127.0.0.1:{port}", timeout=3).read())
                return
            except OSError:
                if time.monotonic() >= deadline:
                    raise
                time.sleep(1)

    def _wait_exit(self, prefix, root, code):
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            container = subprocess.run(prefix + ["ps", "-a", "-q", "app"], cwd=root, capture_output=True, text=True)
            if container.stdout.strip():
                result = subprocess.run(["docker", "inspect", "--format", "{{.State.ExitCode}}", container.stdout.strip()],
                                        capture_output=True, text=True)
                if result.stdout.strip() == str(code):
                    return
            time.sleep(1)
        self.fail(f"App did not exit with {code}")


if __name__ == "__main__":
    unittest.main()
