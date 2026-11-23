"""Canonical release conformance and publication failure boundaries."""
import json
from pathlib import Path
import subprocess
from unittest.mock import patch

import jsonschema

from python_ship.config import ConfigError, ShipConfig
from python_ship.release import assemble_release, plan_images
from tests.helpers import ProjectTestCase
from tests.test_release import fake_release_docker


class ReleaseContractTests(ProjectTestCase):
    def setUp(self):
        super().setUp()
        self.init("--app", "main:app")

    def test_complete_folder_passes_shared_checker_and_no_env_retains_names(self):
        config = self.write_config(name="fixture", app="main:app", services={"database": "postgres"},
            external_network=["proxy", "metrics"], deploy_commands=[["python", "migrate.py", ""]],
            processes={"socket": {"command": ["python", "socket.py"],
                "http": {"port": 8081, "role": "websocket", "health": "/ready", "urlSetting": "SOCKET_URL"}}})
        (self.root / ".env.production").write_text("DB_PASSWORD=actual-password\nSOCKET_URL=https://socket.example\n")
        model, _ = plan_images(self.model(config, production=True), "v1")
        with patch("python_ship.release.subprocess.run", side_effect=fake_release_docker):
            path = assemble_release(self.root, config, model, "v1", no_env=True)
        manifest = json.loads((path / "release.json").read_text())
        schema = json.loads((Path(__file__).resolve().parents[1] / "schema/ship-release.schema.json").read_text())
        jsonschema.Draft202012Validator(schema).validate(manifest)
        self.assertIsNone(manifest["env"]["file"])
        self.assertFalse((path / ".env").exists())
        self.assertEqual(["DB_PASSWORD", "SOCKET_URL"], manifest["env"]["keys"])
        self.assertEqual("process", manifest["services"]["socket"]["role"])
        self.assertEqual(["python", "migrate.py", ""], manifest["deploy"]["commands"][0])
        self.assertEqual(1, len(manifest["images"]))
        self.assertEqual(1, len(manifest["pulledImages"]))
        checker = Path(__file__).resolve().parents[2] / "com.ship.spec/tools/check-release.mjs"
        if checker.is_file():
            result = subprocess.run(["node", str(checker), str(path)], capture_output=True, text=True)
            self.assertEqual(0, result.returncode, result.stderr + result.stdout)

    def test_no_settings_is_valid_without_required_credentials(self):
        config = ShipConfig.read(self.root)
        model, _ = plan_images(self.model(config, production=True), "v1")
        with patch("python_ship.release.subprocess.run", side_effect=fake_release_docker):
            path = assemble_release(self.root, config, model, "v1")
            with self.assertRaisesRegex(ConfigError, "already exists"):
                assemble_release(self.root, config, model, "v1")
        self.assertIsNone(json.loads((path / "release.json").read_text())["env"]["file"])

    def test_mixed_platforms_and_missing_registry_digests_fail_without_publication(self):
        for defect in ("platform", "digest"):
            config = self.write_config(name="fixture", app="main:app", services={"storage": "garage", "cache": "redis"})
            (self.root / ".env.production").write_text("AWS_SECRET_ACCESS_KEY=actual-secret\nGARAGE_RPC_SECRET=" + "a" * 64 + "\n")
            model, _ = plan_images(self.model(config, production=True), "v1")
            def runner(command, defect=defect, **kwargs):
                result = fake_release_docker(command, **kwargs)
                if command[:3] == ["docker", "image", "inspect"]:
                    info = json.loads(result.stdout)
                    if defect == "platform" and "garage" in command[-1]:
                        info[0]["Architecture"] = "arm64"
                    if defect == "digest" and "redis" in command[-1]:
                        info[0]["RepoDigests"] = []
                    result.stdout = json.dumps(info)
                return result
            with patch("python_ship.release.subprocess.run", side_effect=runner), self.assertRaises(ConfigError):
                assemble_release(self.root, config, model, "v1")
            self.assertFalse((self.root / "dist/ship/v1").exists())
