"""Real readiness and protocol checks for the newly ported provider catalog."""
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

from python_ship.compose import build_compose, render
from python_ship.config import ShipConfig
from python_ship.services import BUILTIN_SERVICES
from tests.helpers import flag


@unittest.skipUnless(flag("SHIP_PORTABLE_DOCKER_TEST"), "set SHIP_PORTABLE_DOCKER_TEST=1 for portable provider integration")
class PortableProviderDockerTests(unittest.TestCase):
    def check_provider(self, key):
        with tempfile.TemporaryDirectory(prefix="ship-portable-") as directory:
            root = Path(directory)
            (root / "main.py").write_text("async def app(scope, receive, send): pass\n")
            service = BUILTIN_SERVICES[key]
            config = ShipConfig(name=root.name, app="main:app", services={service.group: key}, service_names={key: "provider"})
            model = build_compose(config, root)
            model["services"].pop("app")
            model.pop("x-ship", None)
            provider = model["services"]["provider"]
            # This host is above Elasticsearch's default disk allocation watermark.
            if key == "elasticsearch" and os.environ.get("SHIP_ELASTIC_LOW_DISK") == "1":
                provider["environment"]["cluster.routing.allocation.disk.threshold_enabled"] = "false"
            if key in ("elasticsearch", "typesense", "centrifugo"):
                port = {"elasticsearch": 9200, "typesense": 8108, "centrifugo": 8000}[key]
                provider["ports"] = [f"127.0.0.1::{port}"]
            path = root / "compose.json"
            path.write_text(render(model))
            prefix = ["docker", "compose", "--project-directory", str(root), "-f", str(path)]
            def run(*args, check=True):
                result = subprocess.run([*prefix, *args], capture_output=True, text=True)
                if check:
                    self.assertEqual(0, result.returncode, result.stderr + result.stdout)
                return result
            try:
                run("up", "-d", "--wait", "--wait-timeout", "180", "provider")
                if key == "mariadb":
                    self.assertIn("42", run("exec", "-T", "provider", "mariadb", "-uapp", "-psecret", "app", "-Nse", "SELECT 42").stdout)
                elif key == "mongodb":
                    self.assertIn("42", run("exec", "-T", "provider", "mongosh", "-u", "app", "-p", "secret", "--authenticationDatabase", "app", "app", "--quiet", "--eval", "db.probe.insertOne({v:42}); print(db.probe.findOne().v)").stdout)
                elif key == "sqlserver":
                    self.assertIn("42", run("exec", "-T", "provider", "/opt/mssql-tools18/bin/sqlcmd", "-C", "-S", "localhost", "-U", "app", "-P", "ShipSecret123", "-d", "app", "-Q", "SELECT 42").stdout)
                elif key == "valkey":
                    run("exec", "-T", "provider", "valkey-cli", "SET", "ship:probe", "42")
                    self.assertEqual("42", run("exec", "-T", "provider", "valkey-cli", "GET", "ship:probe").stdout.strip())
                elif key == "memcached":
                    network = model["name"] + "_default"
                    script = "import socket; s=socket.create_connection(('provider',11211)); s.sendall(b'version\\r\\n'); assert s.recv(100).startswith(b'VERSION')"
                    result = subprocess.run(["docker", "run", "--rm", "--network", network, "python:3.13-slim", "python", "-c", script], capture_output=True, text=True)
                    self.assertEqual(0, result.returncode, result.stderr)
                elif key in ("elasticsearch", "typesense", "centrifugo"):
                    from urllib.request import Request, urlopen
                    port = {"elasticsearch": 9200, "typesense": 8108, "centrifugo": 8000}[key]
                    mapped = run("port", "provider", str(port)).stdout.strip()
                    endpoint = {"elasticsearch": "/_cluster/health", "typesense": "/health", "centrifugo": "/health"}[key]
                    request = Request("http://" + mapped + endpoint)
                    if key == "elasticsearch":
                        import base64
                        request.add_header("Authorization", "Basic " + base64.b64encode(b"elastic:secret").decode())
                    response = urlopen(request, timeout=10)
                    self.assertEqual(200, response.status)
                    if key != "centrifugo":
                        json.loads(response.read())
                elif key == "cypress":
                    result = run("run", "--rm", "--no-deps", "--entrypoint", "cypress", "provider", "version")
                    self.assertIn("16.3.0", result.stdout)
            finally:
                result = run("down", "--volumes", check=False)
                self.assertEqual(0, result.returncode, result.stderr)

    def test_mariadb(self): self.check_provider("mariadb")
    def test_mongodb(self): self.check_provider("mongodb")
    def test_sqlserver(self): self.check_provider("sqlserver")
    def test_valkey(self): self.check_provider("valkey")
    def test_memcached(self): self.check_provider("memcached")
    def test_elasticsearch(self): self.check_provider("elasticsearch")
    def test_typesense(self): self.check_provider("typesense")
    def test_centrifugo(self): self.check_provider("centrifugo")
    def test_cypress(self): self.check_provider("cypress")
