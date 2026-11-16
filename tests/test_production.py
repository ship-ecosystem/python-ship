"""`ship build`, production credentials, .env parsing and published-stub checks."""

import json
import unittest
from unittest.mock import patch

from python_ship.config import ShipConfig
from python_ship.production import DEVELOPMENT_DEFAULTS, credential_problems, read_env, required_variables
from python_ship.stubs import asset_problems, production_stub_problem, stub_problems
from tests.helpers import ProjectTestCase, captured, fake_docker


class EnvFileTests(ProjectTestCase):
    def test_compose_env_file_syntax(self):
        (self.root / ".env").write_text(
            "﻿PLAIN=value\n"
            "export EXPORTED = spaced\n"
            "# comment\n"
            "COMMENTED=value # trailing comment\n"
            "HASH=value#not-a-comment\n"
            "DOUBLE=\"quoted \\\"value\\\" \\\\ $x\" # comment\n"
            "SINGLE='literal $HOME # kept'\n"
            "EMPTY=\n"
            "NOEQUALS\n"
            "1BAD=ignored\n",
            encoding="utf-8",  # The BOM is not encodable in Windows' legacy default code page.
        )
        values = read_env(self.root / ".env")
        self.assertEqual({
            "PLAIN": "value", "EXPORTED": "spaced", "COMMENTED": "value", "HASH": "value#not-a-comment",
            "DOUBLE": 'quoted "value" \\ $x', "SINGLE": "literal $HOME # kept", "EMPTY": "",
        }, values)
        self.assertEqual({}, read_env(self.root / "missing"))


class CredentialTests(ProjectTestCase):
    def test_missing_and_development_default_credentials(self):
        model = self.model(ShipConfig(app="main:app", services={"database": "mysql", "search": "meilisearch"}), production=True)
        self.assertEqual(["DB_PASSWORD", "DB_ROOT_PASSWORD", "MEILISEARCH_KEY"], required_variables(model))
        problems = credential_problems(model, self.root)
        self.assertEqual(["Production credentials missing from .env.production: DB_PASSWORD, DB_ROOT_PASSWORD, MEILISEARCH_KEY."], problems)
        (self.root / ".env.production").write_text("DB_PASSWORD=Ship\nDB_ROOT_PASSWORD=rootship\nMEILISEARCH_KEY=shipsearchkey\n")
        self.assertEqual(["Production credentials still use a development default: DB_PASSWORD, DB_ROOT_PASSWORD, MEILISEARCH_KEY."],
                         credential_problems(model, self.root))
        (self.root / ".env.production").write_text("DB_PASSWORD='a real secret'\nDB_ROOT_PASSWORD=b\nMEILISEARCH_KEY=c\n")
        self.assertEqual([], credential_problems(model, self.root))

    def test_every_built_in_secret_default_is_rejected(self):
        for value in ("ship", "rootship", "shipsecret", "shipsecretplaceholder", "shipsearchkey", "0" * 64):
            self.assertIn(value, DEVELOPMENT_DEFAULTS)

    def test_no_required_variables_means_no_env_file_needed(self):
        self.assertEqual([], credential_problems(self.model(ShipConfig(app="main:app"), production=True), self.root))


class BuildCommandTests(ProjectTestCase):
    def setUp(self):
        super().setUp()
        self.assertEqual(0, self.init("--app", "main:app"))

    def test_build_writes_the_production_model_and_builds_each_owner_once(self):
        self.write_config(app="main:app", processes={"worker": "python worker.py"}, services={"storage": "garage"})
        self.assertEqual(0, self.init())
        (self.root / ".env.production").write_text(f"AWS_SECRET_ACCESS_KEY=s\nGARAGE_RPC_SECRET={'1' * 64}\nGARAGE_ADMIN_TOKEN=t\nX=1\n")
        with fake_docker() as docker, captured() as output:
            self.assertEqual(0, self.ship("build"))
        command, kwargs = docker.calls[-1]
        self.assertEqual(["build", "app", "garage"], command[-3:])
        self.assertIn("--env-file", command)
        self.assertEqual("1", kwargs["env"]["X"])
        path = self.root / "ship" / "docker-compose.production.yml"
        self.assertIn(str(path), command)
        model = json.loads(path.read_text().split("\n", 1)[1])
        self.assertEqual(model["services"]["app"]["image"], model["services"]["worker"]["image"])
        self.assertTrue(model["services"]["app"]["image"].endswith("-production-app:local"))
        self.assertFalse((self.root / "ship" / "docker-compose.generated.yml").exists())
        self.assertIn("-production-app:local (app, worker)", output.stdout)

    def test_override_file_never_reaches_production(self):
        (self.root / "docker-compose.override.yml").write_text("services: {}\n")
        with fake_docker() as docker, captured():
            self.assertEqual(0, self.ship("build"))
        self.assertNotIn(str(self.root / "docker-compose.override.yml"), docker.last)

    def test_credentials_and_stubs_are_checked_before_docker(self):
        self.write_config(app="main:app", services={"database": "postgres"})
        with fake_docker() as docker, captured() as output:
            self.assertEqual(1, self.ship("build"))
        self.assertEqual([], docker.calls)
        self.assertIn("DB_PASSWORD", output.stderr)
        (self.root / "ship" / "Dockerfile").write_text("FROM python:3.13-slim\n")
        with fake_docker() as docker, captured() as output:
            self.assertEqual(1, self.ship("build"))
        self.assertEqual([], docker.calls)
        self.assertIn("no production stage", output.stderr)

    def test_failed_docker_build(self):
        with fake_docker(returncode=1), captured() as output:
            self.assertEqual(1, self.ship("build"))
        self.assertIn("Building the production images failed", output.stderr)

    def test_build_reapplies_the_ignore_guard_after_user_negations(self):
        ignore = self.root / ".dockerignore"
        with ignore.open("a") as stream:
            stream.write("!.env.production\n!dist/ship/\n")
        (self.root / ".gitignore").write_text("")
        with fake_docker(), captured():
            self.assertEqual(0, self.ship("build"))
        lines = ignore.read_text().splitlines()
        self.assertGreater(lines.index("# python-ship release guard"), lines.index("!.env.production"))
        self.assertEqual("**/auth.json", lines[-1])
        self.assertIn("/.env.production", (self.root / ".gitignore").read_text())


class StubCheckTests(ProjectTestCase):
    def test_stub_and_asset_problems(self):
        self.assertEqual(["ship/Dockerfile is missing; run `ship init`."], stub_problems(self.root, None))
        self.assertIsNotNone(production_stub_problem(self.root))
        self.assertEqual(0, self.init("--app", "main:app", "--storage", "garage"))
        config = ShipConfig.read(self.root)
        self.assertEqual([], stub_problems(self.root, config))
        (self.root / "ship" / "garage" / "Dockerfile").unlink()
        self.assertIn("ship/garage/Dockerfile is missing", stub_problems(self.root, config)[0])
        assets = ShipConfig(app="main:app", node_assets=True)
        self.assertEqual(["nodeAssets requires package.json with a build script.",
                          "nodeAssets requires package-lock.json, for a reproducible `npm ci`."], asset_problems(self.root, assets))
        (self.root / "package.json").write_text("{bad")
        self.assertIn("valid package.json", asset_problems(self.root, assets)[0])
        (self.root / "package.json").write_text(json.dumps({"scripts": {"dev": "vite"}}))
        self.assertIn('"build" script', asset_problems(self.root, assets)[0])
        (self.root / "package.json").write_text(json.dumps({"scripts": {"build": "vite build"}}))
        (self.root / "package-lock.json").write_text("{}")
        self.assertEqual([], asset_problems(self.root, assets))
        self.assertEqual([], asset_problems(self.root, ShipConfig(app="main:app")))

    def test_config_test_reports_asset_problems(self):
        self.assertEqual(0, self.init("--app", "main:app", "--node-assets"))
        with captured() as output:
            self.assertEqual(1, self.ship("config:test"))
        self.assertIn("package.json", output.stderr)

    def test_node_assets_stage_is_rendered(self):
        with patch("python_ship.stubs.UV_VERSION", "9.9.9"):
            self.assertEqual(0, self.init("--app", "main:app", "--node-assets"))
        dockerfile = (self.root / "ship" / "Dockerfile").read_text()
        self.assertIn("FROM node:24.18.0-alpine3.24 AS assets", dockerfile)
        self.assertIn("ghcr.io/astral-sh/uv:9.9.9", dockerfile)
        self.assertLess(dockerfile.index("AS assets"), dockerfile.index("AS base"))


if __name__ == "__main__":
    unittest.main()
