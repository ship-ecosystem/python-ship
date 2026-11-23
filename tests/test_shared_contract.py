"""Shared configuration semantics: process endpoints, argv and external networks."""
import json
from pathlib import Path
import unittest

from python_ship.config import ShipConfig
from tests.helpers import ProjectTestCase


class SharedConfigurationTests(ProjectTestCase):
    def test_process_http_health_and_networks_are_production_only(self):
        config, problems = ShipConfig.from_data({"app": "main:app", "externalNetwork": ["proxy", "metrics"],
            "processes": {"socket": {"command": ["python", "socket.py", ""],
                "http": {"port": 8081, "role": "websocket", "health": "/ready", "urlSetting": "SOCKET_URL"}}}})
        self.assertEqual([], problems)
        self.assertNotIn("socket", self.model(config)["services"])
        model = self.model(config, production=True)
        socket = model["services"]["socket"]
        self.assertEqual(["python", "socket.py", ""], socket["command"])
        self.assertIn("urllib.request", socket["healthcheck"]["test"][3])
        self.assertEqual(["8081"], socket["expose"])
        self.assertEqual(["default", "external_0", "external_1"], socket["networks"])
        config.write(self.root)
        self.assertEqual(config, ShipConfig.read(self.root))

    def test_malformed_processes_and_networks_fail_early(self):
        for value in [{"command": []}, {"command": "x", "unknown": True},
                      {"command": "x", "http": {"port": True, "role": "site"}},
                      {"command": "x", "http": {"port": 80, "role": "site", "urlSetting": "secret=value"}}]:
            self.assertTrue(ShipConfig.from_data({"app": "main:app", "processes": {"worker": value}})[1])
        for value in [[], ["bad network"], 3]:
            self.assertTrue(ShipConfig.from_data({"app": "main:app", "externalNetwork": value})[1])

    def test_schema_copies_match_local_spec_when_available(self):
        root = Path(__file__).resolve().parents[1]
        spec = root.parent / "com.ship.spec" / "schemas"
        if not spec.is_dir():
            self.skipTest("local shared spec checkout unavailable")
        for name in ["ship.schema.json", "ship-release.schema.json"]:
            self.assertEqual((spec / name).read_bytes(), (root / "schema" / name).read_bytes())
            json.loads((root / "schema" / name).read_text())


if __name__ == "__main__":
    unittest.main()
