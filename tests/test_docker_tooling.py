"""Live frontend switching, BuildKit boundaries and browser execution."""
from dataclasses import replace
import json
import subprocess
import unittest

from python_ship.config import ShipConfig
from tests.helpers import DockerProjectTestCase, captured, flag, free_port


@unittest.skipUnless(flag("SHIP_TOOLING_DOCKER_TEST"), "set SHIP_TOOLING_DOCKER_TEST=1 for tooling integration")
class ToolingDockerTests(DockerProjectTestCase):
    def setUp(self):
        super().setUp()
        # Browser images can create root-owned screenshots on the test bind mount.
        def restore_ownership():
            import os
            if hasattr(os, "getuid"):
                subprocess.run(["docker", "run", "--rm", "-v", str(self.root) + ":/work", "alpine:3.22",
                                "chown", "-R", f"{os.getuid()}:{os.getgid()}", "/work"], capture_output=True)
        self.addCleanup(restore_ownership)

    def start(self, *arguments):
        (self.root / ".env").write_text(f"APP_PORT={free_port()}\nVITE_PORT={free_port()}\n")
        self.assertEqual(0, self.init(*arguments), self.init_output.stderr)
        with captured() as output:
            code = self.ship("up")
        self.assertEqual(0, code, output.stderr + output.stdout[-3000:])

    def test_package_manager_switches_rebuild_and_execute(self):
        (self.root / "package.json").write_text('{"name":"ship-tooling","version":"1.0.0","private":true}')
        self.start("--frontend", "node")
        for tool, version in (("npm", "12.2.2"), ("pnpm", "12.21.0"), ("yarn", "4.18.1")):
            if tool != "npm":
                with captured() as output:
                    self.assertEqual(0, self.ship("package-manager", tool + "@" + version, "--yes"), output.stderr)
                    self.assertEqual(0, self.ship("up"), output.stderr)
            node = self.compose("exec", "-T", "node", tool, "--version")
            self.assertEqual(0, node.returncode, node.stderr)
            self.assertEqual(version, node.stdout.strip())
        self.assertTrue((self.root / "yarn.lock").is_file())
        self.assertFalse((self.root / "pnpm-lock.yaml").exists())

    def test_buildkit_secrets_are_temporary_and_public_values_reach_assets(self):
        command = "node -e \"const fs=require('fs');fs.mkdirSync('dist');fs.writeFileSync('dist/probe.txt',process.env.VITE_API_URL+':'+Boolean(process.env.PRIVATE_TOKEN))\""
        (self.root / "package.json").write_text(json.dumps({"name": "ship-assets", "version": "1.0.0", "scripts": {"build": command}}))
        (self.root / "package-lock.json").write_text('{"name":"ship-assets","version":"1.0.0","lockfileVersion":3,"packages":{"":{"name":"ship-assets","version":"1.0.0"}}}')
        self.init()
        config = replace(ShipConfig.read(self.root), node_assets=True,
                         build={"publicEnv": {"VITE_API_URL": "https://api.example.test"}, "secrets": ["PRIVATE_TOKEN"]})
        config.write(self.root)
        (self.root / ".ship-secrets").mkdir()
        (self.root / ".ship-secrets/PRIVATE_TOKEN").write_text("build-only-private-token")
        self.assertEqual(0, self.init("--force"), self.init_output.stderr)
        with captured() as output:
            code = self.ship("build")
        self.assertEqual(0, code, output.stderr + output.stdout[-2000:])
        image = self.model(production=True)["services"]["app"]["image"]
        check = "import os; assert open('/app/dist/probe.txt').read()=='https://api.example.test:true'; assert not os.path.exists('/app/.ship-secrets'); assert not os.path.exists('/run/secrets/ship_build_PRIVATE_TOKEN'); assert 'PRIVATE_TOKEN' not in os.environ"
        result = subprocess.run(["docker", "run", "--rm", image, "python", "-c", check], capture_output=True, text=True)
        self.assertEqual(0, result.returncode, result.stderr)

    def test_cypress_executes_a_real_browser_against_python(self):
        (self.root / "main.py").write_text(self.app_source.replace("text/plain", "text/html"))
        (self.root / "cypress.config.cjs").write_text("const {defineConfig}=require('cypress'); module.exports=defineConfig({video:false,e2e:{specPattern:'probe.cy.js',supportFile:false}});\n")
        (self.root / "probe.cy.js").write_text("describe('Python Ship',()=>{it('serves the app',()=>{cy.visit('/');cy.contains('hello from plain ASGI');});});\n")
        self.start("--testing", "cypress")
        with captured() as output:
            code = self.ship("test", "--", "--browser", "electron", "--spec", "probe.cy.js")
        self.assertEqual(0, code, output.stderr + output.stdout[-3000:])
