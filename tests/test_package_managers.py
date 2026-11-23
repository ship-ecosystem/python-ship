"""Frontend tool selection, frozen production installs and failed-switch recovery."""
import json
from unittest.mock import patch

from python_ship.config import ConfigError
from python_ship.package_managers import asset_stage, inspect_manager, switch_manager
from tests.helpers import ProjectTestCase, captured, fake_docker


class PackageManagerTests(ProjectTestCase):
    def test_lockfile_authority_and_exact_versions(self):
        (self.root / "package.json").write_text('{"packageManager":"pnpm@12.21.0"}')
        (self.root / "pnpm-lock.yaml").write_text("lockfileVersion: '9.0'\n")
        self.assertEqual("pnpm", inspect_manager(self.root).name)
        self.assertIn("pnpm install --frozen-lockfile", asset_stage(self.root, "24"))
        (self.root / "package-lock.json").write_text("{}")
        with self.assertRaisesRegex(ConfigError, "Conflicting"):
            inspect_manager(self.root)

    def test_failed_switch_restores_locks_manifest_and_configuration(self):
        manifest = '{"name":"example","packageManager":"npm@12.2.2"}\n'
        (self.root / "package.json").write_text(manifest)
        (self.root / "package-lock.json").write_text("original lock\n")
        calls = []
        def runner(argv):
            calls.append(argv)
            if argv == ["yarn", "install"]:
                (self.root / "yarn.lock").write_text("failed partial install")
                return 1
            return 0
        with self.assertRaisesRegex(ConfigError, "restored"):
            switch_manager(self.root, "yarn@4.18.1", runner)
        self.assertEqual(manifest, (self.root / "package.json").read_text())
        self.assertEqual("original lock\n", (self.root / "package-lock.json").read_text())
        self.assertFalse((self.root / "yarn.lock").exists())
        self.assertFalse((self.root / ".yarnrc.yml").exists())
        self.assertTrue(any(argv[:2] == ["sh", "-c"] and "cp -a" in argv[2] for argv in calls))

    def test_successful_switch_records_tool_and_yarn_linker(self):
        (self.root / "package.json").write_text('{"name":"example"}')
        switch_manager(self.root, "yarn", lambda argv: 0)
        self.assertEqual("yarn@4.18.1", json.loads((self.root / "package.json").read_text())["packageManager"])
        self.assertIn("nodeLinker: node-modules", (self.root / ".yarnrc.yml").read_text())

    def test_browser_command_runs_cypress_on_demand(self):
        self.init("--testing", "cypress")
        with fake_docker(tty=False) as docker, captured():
            self.assertEqual(0, self.ship("test", "--", "--spec", "test.cy.js"))
        self.assertEqual(["run", "--rm", "--no-deps", "-T", "cypress", "--", "--spec", "test.cy.js"], docker.last[-8:])

    def test_doctor_does_not_regenerate_the_compose_file(self):
        self.init()
        path = self.root / "ship/docker-compose.generated.yml"
        path.write_text("# existing operator model\n")
        before = path.read_bytes()
        with fake_docker(), captured(), patch("python_ship.cli.status", return_value=([], [], 8000)):
            self.ship("doctor")
        self.assertEqual(before, path.read_bytes())
