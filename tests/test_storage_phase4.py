"""S3 service selection, credentials, and generated Compose checks."""

import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from python_ship.cli import main
from python_ship.compose import build_compose
from python_ship.config import ConfigError, ShipConfig


FIXTURE = Path(__file__).parent / "fixtures" / "plain_asgi" / "main.py"


class StorageCatalogTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory(prefix="ship-storage-")
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        (self.root / "main.py").write_text(FIXTURE.read_text())

    def test_all_storage_choices_have_matching_app_credentials_and_persistence(self):
        ports = {"garage": 3900, "seaweedfs": 8333, "rustfs": 9000, "silo": 9000}
        for choice, port in ports.items():
            with self.subTest(choice=choice):
                config = ShipConfig("generic", "main:app", services={"storage": choice})
                model = build_compose(config, self.root)
                service = model["services"][choice]
                app = model["services"]["app"]
                self.assertEqual(f"http://{choice}:{port}", app["environment"]["AWS_ENDPOINT"])
                self.assertEqual("true", app["environment"]["AWS_USE_PATH_STYLE_ENDPOINT"])
                self.assertIn("AWS_SECRET_ACCESS_KEY", app["environment"])
                self.assertIn("healthcheck", service)
                self.assertTrue(any(volume.startswith(choice + "_") for volume in model["volumes"]))
                production = build_compose(config, self.root, production=True)
                self.assertIn(":?", production["services"]["app"]["environment"]["AWS_SECRET_ACCESS_KEY"])
                self.assertNotIn("ports", production["services"][choice])

    def test_garage_bootstrap_and_published_templates(self):
        with patch("python_ship.cli.require_uv", return_value="/fake/uv"):
            self.assertEqual(0, main(["--project-directory", str(self.root), "init", "--app", "main:app", "--storage", "garage"]))
        self.assertTrue((self.root / "ship" / "garage" / "Dockerfile").is_file())
        self.assertIn("__GARAGE_RPC_PUBLIC_ADDR__", (self.root / "ship" / "garage" / "garage.toml").read_text())
        model = build_compose(ShipConfig.read(self.root), self.root)
        garage = model["services"]["garage"]
        self.assertEqual("garage:3901", garage["build"]["args"]["GARAGE_RPC_PUBLIC_ADDR"])
        self.assertEqual("${AWS_ACCESS_KEY_ID:-shipaccesskey}", garage["environment"]["GARAGE_DEFAULT_ACCESS_KEY"])
        self.assertEqual(garage["environment"]["GARAGE_DEFAULT_SECRET_KEY"], model["services"]["app"]["environment"]["AWS_SECRET_ACCESS_KEY"])
        self.assertEqual("${AWS_BUCKET:-local}", garage["environment"]["GARAGE_DEFAULT_BUCKET"])
        self.assertIn("garage_meta", model["volumes"])
        self.assertEqual(0, main(["--project-directory", str(self.root), "config:test"]))
        (self.root / "ship" / "garage" / "garage.toml").unlink()
        self.assertEqual(1, main(["--project-directory", str(self.root), "config:test"]))
        self.assertEqual(1, main(["--project-directory", str(self.root), "up"]))
        with patch("python_ship.cli.require_uv", return_value="/fake/uv"):
            self.assertEqual(0, main(["--project-directory", str(self.root), "init"]))
        self.assertTrue((self.root / "ship" / "garage" / "garage.toml").is_file())

    def test_seaweedfs_identity_file_is_configured(self):
        model = build_compose(ShipConfig("generic", "main:app", services={"storage": "seaweedfs"}), self.root)
        service = model["services"]["seaweedfs"]
        command = service["command"][0]
        self.assertEqual(["/bin/sh", "-c"], service["entrypoint"])
        self.assertIn("-s3.config=/etc/seaweedfs/s3_identity.json", command)
        self.assertIn("$$AWS_ACCESS_KEY_ID", command)
        self.assertIn("$$AWS_SECRET_ACCESS_KEY", command)
        self.assertIn("sed", command)

    def test_named_storage_uses_distinct_host_volume_and_credentials(self):
        for choice in ("garage", "seaweedfs", "rustfs", "silo"):
            with self.subTest(choice=choice):
                config = ShipConfig("generic", "main:app", instances={"archive": {"group": "storage", "service": choice, "name": "archive-store"}})
                model = build_compose(config, self.root)
                app = model["services"]["app"]["environment"]
                service = model["services"]["archive-store"]
                self.assertIn("ARCHIVE_AWS_ENDPOINT", app)
                self.assertIn("archive-store", app["ARCHIVE_AWS_ENDPOINT"])
                self.assertIn("${ARCHIVE_AWS_SECRET_ACCESS_KEY:-", app["ARCHIVE_AWS_SECRET_ACCESS_KEY"])
                self.assertIn("archive-store_data", model["volumes"])
                if choice == "garage":
                    self.assertEqual("archive-store:3901", service["build"]["args"]["GARAGE_RPC_PUBLIC_ADDR"])
                if choice == "silo":
                    self.assertNotIn("ports", service)

    def test_silo_variants_and_validation(self):
        default = ShipConfig("generic", "main:app", services={"storage": "silo"})
        self.assertTrue(build_compose(default, self.root)["services"]["silo"]["image"].endswith("-distroless"))
        with patch("python_ship.cli.require_uv", return_value="/fake/uv"):
            self.assertEqual(0, main(["--project-directory", str(self.root), "init", "--app", "main:app", "--storage", "silo", "--silo-image", "standard"]))
        self.assertEqual("standard", ShipConfig.read(self.root).silo_image)
        self.assertFalse(build_compose(ShipConfig.read(self.root), self.root)["services"]["silo"]["image"].endswith("-distroless"))
        (self.root / "ship.json").write_text(json.dumps({"app": "main:app", "silo_image": "bad"}))
        with self.assertRaisesRegex(ConfigError, "silo_image"):
            ShipConfig.read(self.root)


if __name__ == "__main__":
    unittest.main()
