"""Opt-in Docker proof for separately installed community packages."""

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
from python_ship.compose import build_compose
from python_ship.config import ShipConfig


@unittest.skipUnless(os.environ.get("SHIP_EXAMPLE_DOCKER_TEST") == "1", "install example wheels and set SHIP_EXAMPLE_DOCKER_TEST=1")
class InstalledExamplesDockerTests(unittest.TestCase):
    def test_service_and_adapter_through_init_build_and_release(self):
        if shutil.which("docker") is None:
            self.skipTest("Docker is required")
        for kind in ("service", "adapter"):
            with self.subTest(kind=kind), tempfile.TemporaryDirectory(prefix=f"ship-community-{kind}-") as directory:
                root = Path(directory)
                with socket.socket() as listener:
                    listener.bind(("127.0.0.1", 0))
                    port = listener.getsockname()[1]
                (root / ".env").write_text(f"SHIP_APP_PORT={port}\n")
                (root / ".env.production").write_text(f"SHIP_PRODUCTION_PORT={port}\n")
                if kind == "service":
                    (root / "main.py").write_text(
                        "import os, urllib.request\n"
                        "async def app(scope, receive, send):\n"
                        "    if scope['type'] != 'http': return\n"
                        "    with urllib.request.urlopen(os.environ['EXAMPLE_HTTP_URL'], timeout=3) as response:\n"
                        "        assert response.status == 200\n"
                        "    await send({'type': 'http.response.start', 'status': 200, 'headers': []})\n"
                        "    await send({'type': 'http.response.body', 'body': b'community-service-ok'})\n"
                    )
                    config = ShipConfig("generic", "main:app", services={"example": "example-http"},
                                        extensions=["example-http"], name=root.name, publish_ports=True)
                    expected = b"community-service-ok"
                else:
                    (root / "requirements.txt").write_text("starlette\n")
                    (root / "main.py").write_text(
                        "from starlette.applications import Starlette\n"
                        "from starlette.responses import PlainTextResponse\n"
                        "from starlette.routing import Route\n"
                        "async def homepage(request): return PlainTextResponse('community-adapter-ok')\n"
                        "app = Starlette(routes=[Route('/', homepage)])\n"
                    )
                    config = ShipConfig("example-starlette", "main:app", extensions=["example-starlette"],
                                        name=root.name, publish_ports=True)
                    expected = b"community-adapter-ok"
                config.write(root)
                with patch("python_ship.cli.require_uv", return_value="uv"):
                    self.assertEqual(0, main(["--project-directory", str(root), "init"]))
                self.assertEqual(0, main(["--project-directory", str(root), "config:test"]))
                artifact = root / "dist" / "ship" / "v1"
                release_prefix = ["docker", "compose", "-f", str(artifact / "docker-compose.yml")]
                try:
                    self.assertEqual(0, main(["--project-directory", str(root), "up"]))
                    self._wait_http(port, expected)
                    self.assertEqual(0, main(["--project-directory", str(root), "down", "--volumes"]))
                    self.assertEqual(0, main(["--project-directory", str(root), "build"]))
                    self.assertEqual(0, main(["--project-directory", str(root), "release", "--tag", "v1"]))
                    release = json.loads((artifact / "docker-compose.yml").read_text())
                    self.assertTrue(all("build" not in service for service in release["services"].values()))
                    if kind == "service":
                        self.assertIn("example-http", release["services"])
                        self.assertIn("EXAMPLE_HTTP_URL", release["services"]["app"]["environment"])
                    else:
                        self.assertIn("--lifespan on", release["services"]["app"]["command"][2])
                    subprocess.run(release_prefix + ["up", "-d"], cwd=artifact, check=True)
                    self._wait_http(port, expected)
                finally:
                    if artifact.is_dir():
                        subprocess.run(release_prefix + ["down", "--volumes"], cwd=artifact, check=False)
                    if (root / "ship.json").is_file():
                        main(["--project-directory", str(root), "down", "--volumes"])
                        name = build_compose(ShipConfig.read(root), root, production=True)["name"]
                        for tag in ("local", "v1"):
                            subprocess.run(["docker", "image", "rm", f"{name}-app:{tag}"], capture_output=True, check=False)

    def _wait_http(self, port, expected):
        deadline = time.monotonic() + 60
        while True:
            try:
                with urlopen(f"http://127.0.0.1:{port}/", timeout=3) as response:
                    self.assertEqual(expected, response.read())
                return
            except OSError:
                if time.monotonic() >= deadline:
                    raise
                time.sleep(1)


if __name__ == "__main__":
    unittest.main()
