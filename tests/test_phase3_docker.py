"""Opt-in Docker check for named instances and an external network."""

import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from python_ship.cli import main
from python_ship.compose import build_compose
from python_ship.config import ShipConfig


FIXTURE = Path(__file__).parent / "fixtures" / "plain_asgi"


@unittest.skipUnless(os.environ.get("SHIP_DOCKER_TEST") == "1", "set SHIP_DOCKER_TEST=1 for real Docker integration")
class PhaseThreeDockerTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory(prefix="ship-phase3-")
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.network = self.root.name + "-external"
        self.addCleanup(self._cleanup_docker)

    def _ship(self, *arguments):
        return main(["--project-directory", str(self.root), *arguments])

    def _cleanup_docker(self):
        if (self.root / "ship.json").is_file():
            self._ship("down", "--volumes")
            name = build_compose(ShipConfig.read(self.root), self.root)["name"]
            subprocess.run(["docker", "image", "rm", f"{name}-app:latest"], capture_output=True, check=False)
        subprocess.run(["docker", "network", "rm", self.network], capture_output=True, check=False)

    def test_named_instances_and_external_network(self):
        shutil.copy(FIXTURE / "main.py", self.root / "main.py")
        config = ShipConfig(
            "generic", "main:app", services={"database": "postgres", "cache": "redis"},
            instances={
                "analytics": {"group": "database", "service": "postgres", "name": "analytics-db"},
                "queue": {"group": "cache", "service": "redis", "name": "queue-redis", "network": "shared"},
            }, networks={"shared": self.network},
        )
        config.write(self.root)
        (self.root / ".env.production").write_text(
            "DB_PASSWORD=fixture-production-password\n"
            "ANALYTICS_DB_PASSWORD=fixture-analytics-password\n"
        )
        (self.root / "compose.override.yaml").write_text("services:\n  app:\n    environment:\n      OVERRIDE_TEST: present\n")
        created = subprocess.run(["docker", "network", "create", self.network], capture_output=True, text=True)
        self.assertEqual(0, created.returncode, created.stderr)
        with patch("python_ship.cli.require_uv", return_value="/integration/uv"):
            self.assertEqual(0, self._ship("init"))
        self.assertEqual(0, self._ship("config:test"))
        self.assertEqual(0, self._ship("up"))
        probe = (
            "import os,socket; "
            "assert os.environ['ANALYTICS_DB_HOST']=='analytics-db'; "
            "assert os.environ['QUEUE_REDIS_URL']=='redis://queue-redis:6379/0'; "
            "assert os.environ['OVERRIDE_TEST']=='present'; "
            "[(socket.create_connection((host,port),3)).close() for host,port in "
            "[('postgres',5432),('analytics-db',5432),('redis',6379),('queue-redis',6379)]]"
        )
        self.assertEqual(0, self._ship("exec", "app", "python", "-c", probe))
        inspected = subprocess.run(["docker", "network", "inspect", self.network], capture_output=True, text=True)
        self.assertEqual(0, inspected.returncode, inspected.stderr)
        attached = json.loads(inspected.stdout)[0]["Containers"].values()
        names = {item["Name"] for item in attached}
        self.assertTrue(any("-app-" in name for name in names))
        self.assertTrue(any("-queue-redis-" in name for name in names))


if __name__ == "__main__":
    unittest.main()
