"""`ship release`: image planning, artifact layout, permissions and the deploy script."""

from copy import deepcopy
import json
import os
from pathlib import Path
import shutil
import subprocess
import unittest
from unittest.mock import patch

from python_ship.config import ConfigError, ShipConfig
from python_ship.release import assemble_release, deploy_script, plan_images, release_model
from tests.helpers import ProjectTestCase, captured, fake_docker


def fake_release_docker(command, **kwargs):
    if command[:2] == ["docker", "save"]:
        Path(command[command.index("-o") + 1]).write_bytes(command[-1].encode())
    if command[:2] == ["git", "rev-parse"]:
        return subprocess.CompletedProcess(command, 0, "a" * 40 + "\n", "")
    return subprocess.CompletedProcess(command, 0, "", "")


class PlanTests(ProjectTestCase):
    def test_identical_builds_share_one_tag_and_owner(self):
        model = self.model(ShipConfig(app="main:app", processes={"worker": "python w.py"}, services={"storage": "garage"}), production=True)
        model["services"]["jobs"] = deepcopy(model["services"]["app"])
        planned, images = plan_images(model, "v1")
        owners = {image["owner"]: image for image in images}
        self.assertEqual({"app", "garage"}, set(owners))
        self.assertEqual(["app", "jobs", "worker"], owners["app"]["services"])
        tag = owners["app"]["image"]
        self.assertTrue(tag.endswith("-production-app:v1"))
        for name in ("app", "jobs", "worker"):
            self.assertEqual(tag, planned["services"][name]["image"])
        self.assertTrue(owners["garage"]["image"].endswith("-production-garage:v1"))

    def test_renamed_app_still_owns_its_image(self):
        model = self.model(ShipConfig(app="main:app", service_names={"app": "web"}, processes={"a-worker": "x"}), production=True)
        _, images = plan_images(model, "v2")
        self.assertEqual("web", images[0]["owner"])

    def test_release_model_drops_build_and_uses_the_copied_env(self):
        release = release_model(self.model(ShipConfig(app="main:app"), production=True))
        app = release["services"]["app"]
        self.assertNotIn("build", app)
        self.assertEqual([{"path": ".env", "required": True}], app["env_file"])


class DeployScriptTests(ProjectTestCase):
    def test_starts_infrastructure_then_runs_each_command_once(self):
        config = ShipConfig(app="main:app", services={"database": "postgres", "cache": "redis"},
                            processes={"worker": "python w.py"},
                            deploy_commands=["python manage.py migrate --noinput && echo $DONE", ["python", "seed.py", "a b"]])
        script = deploy_script(config, release_model(self.model(config, production=True)))
        lines = script.splitlines()
        self.assertEqual("#!/bin/sh", lines[0])
        self.assertIn("set -eu", lines)
        self.assertIn("docker compose up -d --wait postgres redis", lines)
        self.assertIn("docker compose run --rm --no-deps app sh -c 'python manage.py migrate --noinput && echo $DONE'", lines)
        self.assertIn("docker compose run --rm --no-deps app python seed.py 'a b'", lines)
        self.assertLess(lines.index("docker compose up -d --wait postgres redis"), lines.index("docker compose run --rm --no-deps app python seed.py 'a b'"))
        self.assertNotIn("worker", script)

    @unittest.skipUnless(os.name != "nt" and shutil.which("sh"), "needs a POSIX shell")
    def test_stops_at_the_first_failure(self):
        config = ShipConfig(app="main:app", deploy_commands=["exit 9", "echo must-not-run"])
        (self.root / "deploy-commands.sh").write_text(deploy_script(config, self.model(config, production=True)))
        bin_dir = self.root / "bin"
        bin_dir.mkdir()
        docker = bin_dir / "docker"
        docker.write_text("#!/bin/sh\nprintf '%s\\n' \"$*\" >> \"$SHIP_TEST_LOG\"\ncase \"$*\" in *'exit 9'*) exit 9;; esac\n")
        docker.chmod(0o700)
        log = self.root / "docker.log"
        result = subprocess.run(["sh", "deploy-commands.sh"], cwd=self.root, capture_output=True, text=True,
                                env={**os.environ, "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}", "SHIP_TEST_LOG": str(log)})
        self.assertEqual(9, result.returncode)
        self.assertEqual(1, len(log.read_text().splitlines()))
        self.assertNotIn("must-not-run", log.read_text())


class AssembleTests(ProjectTestCase):
    def setUp(self):
        super().setUp()
        self.assertEqual(0, self.init("--app", "main:app"))

    def test_artifact_layout_contents_and_permissions(self):
        config = self.write_config(app="main:app", services={"cache": "redis"}, deploy_commands=["python migrate.py"])
        (self.root / ".env.production").write_text("PRIVATE_TOKEN=top-secret-value\n")
        model, _ = plan_images(self.model(config, production=True), "v1")
        with patch("python_ship.release.subprocess.run", side_effect=fake_release_docker):
            path = assemble_release(self.root, config, model, "v1")
        self.assertEqual(self.root / "dist" / "ship" / "v1", path)
        self.assertEqual({".env", "docker-compose.yml", "release.json", "README.md", "deploy-commands.sh", "images"},
                         {item.name for item in path.iterdir()})
        compose = json.loads((path / "docker-compose.yml").read_text().split("\n", 1)[1])
        self.assertTrue(all("build" not in service for service in compose["services"].values()))
        self.assertEqual("PRIVATE_TOKEN=top-secret-value\n", (path / ".env").read_text())
        manifest = json.loads((path / "release.json").read_text())
        self.assertEqual("v1", manifest["tag"])
        self.assertEqual({"package": "python-ship", "version": manifest["ship"]["version"]}, manifest["ship"])
        self.assertEqual("a" * 40, manifest["git_commit"])
        self.assertEqual({"images/app.tar", "images/redis.tar"}, {image["file"] for image in manifest["images"]})
        self.assertTrue(all(len(image["sha256"]) == 64 for image in manifest["images"]))
        self.assertNotIn("top-secret-value", (path / "release.json").read_text())
        readme = (path / "README.md").read_text()
        self.assertLess(readme.index("docker load -i images/app.tar"), readme.index("sh deploy-commands.sh"))
        self.assertLess(readme.index("sh deploy-commands.sh"), readme.index("docker compose up -d"))
        if os.name != "nt":
            self.assertEqual(0o700, path.stat().st_mode & 0o777)
            self.assertEqual(0o600, (path / ".env").stat().st_mode & 0o777)
            self.assertEqual(0o700, (path / "deploy-commands.sh").stat().st_mode & 0o777)

    def test_missing_images_are_pulled_and_rebuilt_releases_replace_old_ones(self):
        config = self.write_config(app="main:app", services={"cache": "redis"})
        (self.root / ".env.production").write_text("X=1\n")
        old = self.root / "dist" / "ship" / "v1"
        old.mkdir(parents=True)
        (old / "stale.txt").write_text("old")
        calls = []

        def run(command, **kwargs):
            calls.append(command)
            if command[:3] == ["docker", "image", "inspect"]:
                return subprocess.CompletedProcess(command, 1, "", "")
            return fake_release_docker(command, **kwargs)

        model, _ = plan_images(self.model(config, production=True), "v1")
        with patch("python_ship.release.subprocess.run", side_effect=run):
            path = assemble_release(self.root, config, model, "v1")
        self.assertFalse((path / "stale.txt").exists())
        self.assertIn(["docker", "pull", "redis:8.10.3-alpine3.23"], calls)

    def test_failed_archive_keeps_the_existing_release_and_cleans_up(self):
        config = ShipConfig.read(self.root)
        (self.root / ".env.production").write_text("X=1\n")
        old = self.root / "dist" / "ship" / "v1"
        old.mkdir(parents=True)
        (old / "keep.txt").write_text("old release")

        def run(command, **kwargs):
            failed = command[:2] == ["docker", "save"]
            return subprocess.CompletedProcess(command, 1 if failed else 0, "", "save failed" if failed else "")

        model, _ = plan_images(self.model(config, production=True), "v1")
        with patch("python_ship.release.subprocess.run", side_effect=run), self.assertRaisesRegex(ConfigError, "save failed"):
            assemble_release(self.root, config, model, "v1")
        self.assertEqual("old release", (old / "keep.txt").read_text())
        self.assertEqual([], list((self.root / "dist" / "ship").glob(".release-*")))

    @unittest.skipIf(os.name == "nt", "symlinks need privileges on Windows")
    def test_symlinked_destination_is_refused(self):
        config = ShipConfig.read(self.root)
        (self.root / ".env.production").write_text("X=1\n")
        target = self.root / "elsewhere"
        target.mkdir()
        (self.root / "dist" / "ship").mkdir(parents=True)
        (self.root / "dist" / "ship" / "v1").symlink_to(target)
        model, _ = plan_images(self.model(config, production=True), "v1")
        with patch("python_ship.release.subprocess.run", side_effect=fake_release_docker), self.assertRaisesRegex(ConfigError, "symlink"):
            assemble_release(self.root, config, model, "v1")


class ReleaseCommandTests(ProjectTestCase):
    def setUp(self):
        super().setUp()
        self.assertEqual(0, self.init("--app", "main:app"))

    def test_tag_is_required_without_a_terminal_and_validated(self):
        with fake_docker(tty=False) as docker, captured() as output:
            self.assertEqual(1, self.ship("release"))
        self.assertIn("requires --tag", output.stderr)
        for tag in ("../escape", "-bad", "bad tag", "x" * 129, ""):
            with self.subTest(tag=tag), fake_docker() as docker, captured():
                self.assertEqual(1, self.ship("release", "--tag", tag))
            self.assertEqual([], docker.calls)

    def test_interactive_tag_prompt(self):
        (self.root / ".env.production").write_text("X=1\n")
        with fake_docker(tty=True), patch("python_ship.cli.typer.prompt", return_value=" 1.2.0 ") as prompt, \
                patch("python_ship.cli.assemble_release", return_value=self.root / "dist" / "ship" / "1.2.0") as assemble, captured():
            self.assertEqual(0, self.ship("release"))
        prompt.assert_called_once()
        self.assertEqual("1.2.0", assemble.call_args.args[3])

    def test_env_production_and_credentials_are_required_before_docker(self):
        with fake_docker() as docker, captured() as output:
            self.assertEqual(1, self.ship("release", "--tag", "v1"))
        self.assertIn("requires .env.production", output.stderr)
        self.write_config(app="main:app", services={"database": "postgres"})
        (self.root / ".env.production").write_text("OTHER=1\n")
        with fake_docker() as docker, captured() as output:
            self.assertEqual(1, self.ship("release", "--tag", "v1"))
        self.assertIn("DB_PASSWORD", output.stderr)
        self.assertEqual([], docker.calls)

    def test_release_builds_then_assembles(self):
        (self.root / ".env.production").write_text("X=1\n")
        calls = []

        def run(command, **kwargs):
            calls.append(command)
            return fake_release_docker(command, **kwargs)

        # cli and release share the subprocess module, so one patch serves both.
        with fake_docker(), patch("subprocess.run", side_effect=run), captured() as output:
            self.assertEqual(0, self.ship("release", "--tag", "2.0.0"))
        builds = [command for command in calls if "build" in command]
        self.assertEqual(["build", "app"], builds[0][-2:])
        self.assertLess(calls.index(builds[0]), next(i for i, c in enumerate(calls) if c[:2] == ["docker", "save"]))
        self.assertIn("Release ready at", output.stdout)
        self.assertTrue((self.root / "dist" / "ship" / "2.0.0" / "images" / "app.tar").is_file())


if __name__ == "__main__":
    unittest.main()
