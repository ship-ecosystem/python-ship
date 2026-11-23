"""Portable releases booted with Docker alone, from a directory with no source and no Ship.

Opt in with SHIP_RELEASE_DOCKER_TEST=1.
"""

import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import time
import unittest
import uuid

from python_ship.config import ShipConfig
from tests.helpers import DockerProjectTestCase, captured, flag, free_port, wait_http

PLAIN_DB_APP = (
    "import os, psycopg\n"
    "async def app(scope, receive, send):\n"
    "    if scope['type'] != 'http': return\n"
    "    with psycopg.connect(host=os.environ['DB_HOST'], dbname=os.environ['DB_DATABASE'], user=os.environ['DB_USERNAME'],"
    " password=os.environ['DB_PASSWORD']) as db:\n"
    "        value = db.execute('SELECT value FROM ship_release_probe').fetchone()[0].encode()\n"
    "    await send({'type': 'http.response.start', 'status': 200, 'headers': []})\n"
    "    await send({'type': 'http.response.body', 'body': value})\n"
)
PLAIN_MIGRATION = (
    "python -c \"import os,psycopg; db=psycopg.connect(host=os.environ['DB_HOST'],dbname=os.environ['DB_DATABASE'],"
    "user=os.environ['DB_USERNAME'],password=os.environ['DB_PASSWORD']); "
    "db.execute('CREATE TABLE IF NOT EXISTS ship_release_probe(value text)'); "
    "db.execute(\\\"INSERT INTO ship_release_probe VALUES ('deployed')\\\"); db.commit()\""
)


@unittest.skipUnless(flag("SHIP_RELEASE_DOCKER_TEST") and os.name != "nt", "set SHIP_RELEASE_DOCKER_TEST=1 on a Unix host with Docker")
class ReleaseDockerTests(DockerProjectTestCase):
    def release_and_boot(self, *, fixture, init_arguments, deploy_commands, checks):
        if fixture:
            self.copy_fixture(fixture)
        port = free_port()
        (self.root / ".env.production").write_text(f"DB_PASSWORD=release-secret-value\nAPP_PORT={port}\n")
        self.assertEqual(0, self.init("--database", "postgres", *init_arguments), self.init_output.stderr)
        config = ShipConfig.read(self.root)
        ShipConfig(**{**config.__dict__, "name": f"rel-{uuid.uuid4().hex[:10]}", "publish_ports": True,
                      "deploy_commands": deploy_commands}).write(self.root)
        with captured() as output:
            code = self.ship("release", "--tag", "1.0.0")
        self.assertEqual(0, code, output.stderr + output.stdout[-3000:])

        clean = Path(tempfile.mkdtemp(prefix="ship-clean-machine-"))
        self.addCleanup(shutil.rmtree, clean, True)
        release = clean / "release"
        shutil.copytree(self.root / "dist" / "ship" / "1.0.0", release)
        manifest = json.loads((release / "release.json").read_text())
        compose = ["docker", "compose", "--project-directory", str(release)]
        for entry in manifest["images"]:
            if entry["ref"].startswith("rel-"):
                self.addCleanup(subprocess.run, ["docker", "image", "rm", "-f", entry["ref"]], capture_output=True)
        # Registered after the image removals so it runs before them.
        self.addCleanup(subprocess.run, compose + ["down", "--volumes"], capture_output=True)
        for entry in manifest["images"]:
            if any(service == "app" for service in entry["services"]):
                subprocess.run(["docker", "image", "rm", entry["ref"]], capture_output=True)
            self.assertEqual(0, subprocess.run(["docker", "load", "-i", entry["file"]], cwd=release, capture_output=True).returncode)
        self.assertFalse(any(release.glob("*.py")))
        deployed = subprocess.run(["sh", "deploy-commands.sh"], cwd=release, capture_output=True, text=True)
        self.assertEqual(0, deployed.returncode, deployed.stderr)
        started = subprocess.run(compose + ["up", "-d"], cwd=release, capture_output=True, text=True)
        self.assertEqual(0, started.returncode, started.stderr)
        for path, expected in checks:
            wait_http(port, path, expected)
        self.assertEqual(0, subprocess.run(compose + ["restart", "app"], cwd=release, capture_output=True).returncode)
        wait_http(port, checks[-1][0], checks[-1][1])
        return release, compose, port

    def test_framework_free_release_deploys_and_recovers_from_a_crash(self):
        (self.root / "main.py").write_text(PLAIN_DB_APP)
        (self.root / "requirements.txt").write_text("psycopg[binary]>=3.2,<4\n")
        release, compose, port = self.release_and_boot(
            fixture=None, init_arguments=("--app", "main:app"), deploy_commands=[PLAIN_MIGRATION],
            checks=[("/", b"deployed")])
        path = release / "docker-compose.yml"
        original = path.read_text()
        crashed = json.loads(original.split("\n", 1)[1])
        crashed["services"]["app"]["command"] = ["python", "-c", "raise SystemExit(17)"]
        crashed["services"]["app"]["restart"] = "no"
        path.write_text(json.dumps(crashed))
        subprocess.run(compose + ["up", "-d", "--force-recreate", "app"], cwd=release, capture_output=True, check=True)
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            container = subprocess.run(compose + ["ps", "-a", "-q", "app"], cwd=release, capture_output=True, text=True).stdout.strip()
            code = subprocess.run(["docker", "inspect", "--format", "{{.State.ExitCode}}", container], capture_output=True, text=True).stdout.strip()
            if code == "17":
                break
            time.sleep(1)
        else:
            self.fail("The crashed app container did not exit with 17")
        path.write_text(original)
        subprocess.run(compose + ["up", "-d", "--force-recreate", "app"], cwd=release, capture_output=True, check=True)
        wait_http(port, "/", b"deployed")

    def test_django_release(self):
        self.release_and_boot(fixture="django_app", init_arguments=("--cache", "redis"),
                              deploy_commands=["python manage.py migrate --noinput"],
                              checks=[("/", b"django"), ("/dependencies/", b'"database": 1')])

    def test_fastapi_release(self):
        self.release_and_boot(fixture="fastapi_app", init_arguments=("--cache", "redis"),
                              deploy_commands=[["python", "-c", "import fastapi"]],
                              checks=[("/", b"fastapi initial"), ("/dependencies", b'"redis":true')])

    def test_flask_release_on_granian(self):
        self.release_and_boot(fixture="flask_app", init_arguments=("--cache", "redis", "--server", "granian"),
                              deploy_commands=["flask routes"],
                              checks=[("/", b"flask initial"), ("/dependencies", b'"redis":true')])


if __name__ == "__main__":
    unittest.main()
