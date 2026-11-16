"""The installable examples in examples/, loaded through their real entry points.

SHIP_EXAMPLE_PLUGINS_TEST=1 needs both example distributions installed next to
python-ship. SHIP_EXAMPLE_DOCKER_TEST=1 also runs them in Docker, through
init, up, build and a release booted from its archives.
"""

import json
import subprocess
import unittest

from python_ship.compose import build_compose
from python_ship.config import ShipConfig
from tests.helpers import DockerProjectTestCase, ProjectTestCase, captured, fake_docker, flag, free_port, wait_http

SERVICE_APP = (
    "import os, urllib.request\n"
    "async def app(scope, receive, send):\n"
    "    if scope['type'] != 'http': return\n"
    "    with urllib.request.urlopen(os.environ['EXAMPLE_HTTP_URL'], timeout=3) as response:\n"
    "        assert response.status == 200\n"
    "    await send({'type': 'http.response.start', 'status': 200, 'headers': []})\n"
    "    await send({'type': 'http.response.body', 'body': b'community-service-ok'})\n"
)
STARLETTE_APP = (
    "from starlette.applications import Starlette\n"
    "from starlette.responses import PlainTextResponse\n"
    "from starlette.routing import Route\n"
    "async def homepage(request): return PlainTextResponse('community-adapter-ok')\n"
    "app = Starlette(routes=[Route('/', homepage)])\n"
)


@unittest.skipUnless(flag("SHIP_EXAMPLE_PLUGINS_TEST"), "install both examples and set SHIP_EXAMPLE_PLUGINS_TEST=1")
class InstalledExampleTests(ProjectTestCase):
    def test_service_entry_point(self):
        ShipConfig(app="main:app", services={"example": "example-http"}, extensions=["example-http"]).write(self.root)
        self.assertEqual(0, self.init())
        for production in (False, True):
            model = build_compose(ShipConfig.read(self.root), self.root, production=production)
            self.assertEqual("http://example-http:8081/", model["services"]["app"]["environment"]["EXAMPLE_HTTP_URL"])
        ShipConfig(app="main:app", services={"example": "example-http"}, service_names={"example-http": "helper"},
                   extensions=["example-http"]).write(self.root)
        self.assertEqual("http://helper:8081/", self.model()["services"]["app"]["environment"]["EXAMPLE_HTTP_URL"])

    def test_adapter_entry_point_detection_commands_and_release_hook(self):
        (self.root / "main.py").write_text(STARLETTE_APP)
        (self.root / "requirements.txt").write_text("starlette\n")
        ShipConfig(app="main:app", extensions=["example-starlette"]).write(self.root)
        self.assertEqual(0, self.init("--framework", "example-starlette"))
        config = ShipConfig.read(self.root)
        self.assertEqual("example-starlette", config.framework)
        production = build_compose(config, self.root, production=True)["services"]["app"]["command"]
        self.assertEqual(["sh", "-c"], production[:2])
        self.assertIn("python -c 'import main' && exec python -m uvicorn main:app", production[2])
        with captured() as output:
            self.assertEqual(0, self.ship("--help"))
        self.assertIn("routes", output.stdout)
        with fake_docker() as docker:
            self.assertEqual(0, self.ship("routes"))
        self.assertEqual(["exec", "app", "python", "-c"], docker.last[-5:-1])


@unittest.skipUnless(flag("SHIP_EXAMPLE_DOCKER_TEST"), "install both examples and set SHIP_EXAMPLE_DOCKER_TEST=1")
class InstalledExampleDockerTests(DockerProjectTestCase):
    def run_example(self, config, expected):
        port = free_port()
        (self.root / ".env").write_text(f"APP_PORT={port}\n")
        (self.root / ".env.production").write_text(f"APP_PORT={port}\n")
        config.write(self.root)
        self.assertEqual(0, self.init(), self.init_output.stderr)
        with captured() as output:
            self.assertEqual(0, self.ship("config:test"), output.stderr)
            self.assertEqual(0, self.ship("up"), output.stderr)
        wait_http(port, expected=expected)
        with captured() as output:
            self.assertEqual(0, self.ship("down", "--volumes"))
            self.assertEqual(0, self.ship("release", "--tag", "v1"), output.stderr)
        artifact = self.root / "dist" / "ship" / "v1"
        compose = ["docker", "compose", "--project-directory", str(artifact)]
        self.addCleanup(subprocess.run, compose + ["down", "--volumes"], capture_output=True)
        release = json.loads((artifact / "docker-compose.yml").read_text().split("\n", 1)[1])
        self.assertTrue(all("build" not in service for service in release["services"].values()))
        self.assertEqual(0, subprocess.run(compose + ["up", "-d"], capture_output=True).returncode)
        wait_http(port, expected=expected)
        return release

    def test_service(self):
        (self.root / "main.py").write_text(SERVICE_APP)
        release = self.run_example(ShipConfig(app="main:app", services={"example": "example-http"}, extensions=["example-http"],
                                              publish_ports=True), b"community-service-ok")
        self.assertIn("example-http", release["services"])

    def test_adapter(self):
        (self.root / "main.py").write_text(STARLETTE_APP)
        (self.root / "requirements.txt").write_text("starlette\n")
        release = self.run_example(ShipConfig("example-starlette", "main:app", extensions=["example-starlette"], publish_ports=True),
                                   b"community-adapter-ok")
        self.assertIn("import main", release["services"]["app"]["command"][2])
        result = subprocess.run(["docker", "compose", "--project-directory", str(self.root / "dist" / "ship" / "v1"),
                                 "exec", "-T", "app", "python", "-c", "import main; print(len(main.app.routes))"],
                                capture_output=True, text=True)
        self.assertEqual("1", result.stdout.strip(), result.stderr)


if __name__ == "__main__":
    unittest.main()
