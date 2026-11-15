"""Portable release planning, validation, and artifact safety."""

from contextlib import redirect_stderr, redirect_stdout
from copy import deepcopy
from io import StringIO
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from python_ship.cli import main
from python_ship.compose import build_compose
from python_ship.config import ConfigError, ShipConfig
from python_ship.release import _deploy_script, assemble_release, plan_images


class ReleaseTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="ship-phase7-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        (self.root / "main.py").write_text("async def app(scope, receive, send): pass\n")
        with patch("python_ship.cli.require_uv", return_value="uv"):
            self.assertEqual(0, main(["--project-directory", str(self.root), "init", "--app", "main:app"]))

    def test_identical_builds_share_one_tag_and_build_owner(self):
        config = ShipConfig("generic", "main:app", processes={"worker": ["python", "worker.py"]})
        model = build_compose(config, self.root, production=True)
        copy = deepcopy(model["services"]["app"])
        copy["command"] = ["python", "jobs.py"]
        model["services"]["jobs"] = copy
        planned, images = plan_images(model, "v1")
        self.assertEqual(1, len(images))
        self.assertEqual("app", images[0]["owner"])
        self.assertEqual(["app", "jobs"], images[0]["services"])
        tag = images[0]["image"]
        self.assertTrue(tag.endswith("-app:v1"))
        self.assertEqual(tag, planned["services"]["jobs"]["image"])
        self.assertEqual(tag, planned["services"]["worker"]["image"])

    def test_build_command_invokes_each_unique_owner_once(self):
        config = ShipConfig("generic", "main:app")
        model = build_compose(config, self.root, production=True)
        model["services"]["jobs"] = deepcopy(model["services"]["app"])
        model["services"]["jobs"]["command"] = ["python", "jobs.py"]
        with patch("python_ship.cli.build_compose", return_value=model), patch(
            "python_ship.cli.shutil.which", return_value="docker"
        ), patch("python_ship.cli.subprocess.run", return_value=subprocess.CompletedProcess([], 0)) as docker:
            self.assertEqual(0, main(["--project-directory", str(self.root), "build"]))
        self.assertEqual(["build", "app"], docker.call_args.args[0][-2:])
        written = json.loads((self.root / "ship" / "compose.production.json").read_text())
        self.assertEqual(written["services"]["app"]["image"], written["services"]["jobs"]["image"])

    def test_release_rejects_bad_tag_and_missing_environment_before_docker(self):
        with patch("python_ship.cli.sys.stdin.isatty", return_value=False), redirect_stderr(StringIO()) as errors:
            self.assertEqual(1, main(["--project-directory", str(self.root), "release"]))
        self.assertIn("requires --tag", errors.getvalue())
        for tag in ("../escape", "-bad", "bad tag"):
            with self.subTest(tag=tag), patch("python_ship.cli.subprocess.run") as docker, redirect_stderr(StringIO()):
                self.assertEqual(1, main(["--project-directory", str(self.root), "release", "--tag", tag]))
            docker.assert_not_called()
        with patch("python_ship.cli.subprocess.run") as docker, redirect_stderr(StringIO()) as errors:
            self.assertEqual(1, main(["--project-directory", str(self.root), "release", "--tag", "v1"]))
        docker.assert_not_called()
        self.assertIn(".env.production", errors.getvalue())
        ShipConfig("generic", "main:app", services={"database": "postgres"}).write(self.root)
        (self.root / ".env.production").write_text("OTHER=value\n")
        with patch("python_ship.cli.subprocess.run") as docker, redirect_stderr(StringIO()) as errors:
            self.assertEqual(1, main(["--project-directory", str(self.root), "release", "--tag", "v1"]))
        docker.assert_not_called()
        self.assertIn("DB_PASSWORD", errors.getvalue())

    def test_release_contains_only_runtime_compose_and_unique_image_archives(self):
        config = ShipConfig("generic", "main:app", services={"cache": "redis"},
                            deploy_commands=[["python", "manage.py", "migrate", "--noinput"]])
        config.write(self.root)
        (self.root / ".env.production").write_text("PRIVATE_TOKEN=top-secret-value\n")
        model, images = plan_images(build_compose(config, self.root, production=True), "v1")
        calls = []
        def run(command, **kwargs):
            calls.append(command)
            if command[:2] == ["docker", "save"]:
                Path(command[command.index("-o") + 1]).write_bytes(command[-1].encode())
            if command[:2] == ["git", "rev-parse"]:
                return subprocess.CompletedProcess(command, 0, "a" * 40 + "\n", "")
            return subprocess.CompletedProcess(command, 0, "", "")
        with patch("python_ship.release.subprocess.run", side_effect=run):
            path = assemble_release(self.root, config, model, "v1")
        release = json.loads((path / "docker-compose.yml").read_text())
        self.assertTrue(all("build" not in service for service in release["services"].values()))
        self.assertEqual(".env", release["services"]["app"]["env_file"][0]["path"])
        self.assertEqual("top-secret-value", (path / ".env").read_text().split("=", 1)[1].strip())
        manifest = json.loads((path / "release.json").read_text())
        self.assertNotIn("top-secret-value", (path / "release.json").read_text())
        self.assertEqual("v1", manifest["tag"])
        self.assertEqual(2, len(manifest["images"]))
        self.assertEqual(2, len([call for call in calls if call[:2] == ["docker", "save"]]))
        script = (path / "deploy-commands.sh").read_text()
        self.assertLess(script.index("docker compose up -d --wait redis"), script.index("docker compose run --rm --no-deps app python manage.py migrate --noinput"))
        self.assertIn("set -eu", script)
        self.assertIn("dist/ship/", (self.root / ".gitignore").read_text())
        if os.name != "nt":
            self.assertEqual(0o700, path.stat().st_mode & 0o777)
            self.assertEqual(0o600, (path / ".env").stat().st_mode & 0o777)

    def test_failed_archive_keeps_existing_release(self):
        (self.root / ".env.production").write_text("TOKEN=secret\n")
        config = ShipConfig.read(self.root)
        model, _ = plan_images(build_compose(config, self.root, production=True), "v1")
        old = self.root / "dist" / "ship" / "v1"
        old.mkdir(parents=True)
        (old / "keep.txt").write_text("old release")
        def run(command, **kwargs):
            code = 1 if command[:2] == ["docker", "save"] else 0
            return subprocess.CompletedProcess(command, code, "", "save failed" if code else "")
        with patch("python_ship.release.subprocess.run", side_effect=run):
            with self.assertRaisesRegex(ConfigError, "save failed"):
                assemble_release(self.root, config, model, "v1")
        self.assertEqual("old release", (old / "keep.txt").read_text())
        self.assertEqual([], list((self.root / "dist" / "ship").glob(".release-*")))

    def test_build_reapplies_secret_exclusions_after_user_negations(self):
        ignore = self.root / ".dockerignore"
        with ignore.open("a") as stream:
            stream.write("!.env.production\n!dist/ship/\n")
        with patch("python_ship.cli.shutil.which", return_value="docker"), patch(
            "python_ship.cli.subprocess.run", return_value=subprocess.CompletedProcess([], 0)
        ):
            self.assertEqual(0, main(["--project-directory", str(self.root), "build"]))
        lines = ignore.read_text().splitlines()
        self.assertGreater(lines.index("# python-ship release guard"), lines.index("!.env.production"))
        self.assertEqual("auth.json", lines[-2])
        self.assertIn("dist/ship/", lines)

    @unittest.skipUnless(os.name != "nt" and shutil.which("sh"), "requires a POSIX shell")
    def test_deploy_script_stops_after_first_failure(self):
        config = ShipConfig("generic", "main:app", deploy_commands=[
            ["python", "-c", "raise SystemExit(9)"],
            ["python", "-c", "print('must-not-run')"],
        ])
        model = build_compose(config, self.root, production=True)
        bin_dir = self.root / "bin"
        bin_dir.mkdir()
        docker = bin_dir / "docker"
        docker.write_text("#!/bin/sh\nprintf '%s\\n' \"$*\" >> \"$SHIP_TEST_LOG\"\ncase \"$*\" in *run*) exit 9;; esac\n")
        docker.chmod(0o700)
        (self.root / "deploy-commands.sh").write_text(_deploy_script(config, model))
        log = self.root / "commands.log"
        result = subprocess.run(["sh", "deploy-commands.sh"], cwd=self.root, env={
            **os.environ, "PATH": str(bin_dir) + os.pathsep + os.environ["PATH"], "SHIP_TEST_LOG": str(log),
        })
        self.assertEqual(9, result.returncode)
        self.assertEqual(1, len(log.read_text().splitlines()))
        self.assertNotIn("must-not-run", log.read_text())


if __name__ == "__main__":
    unittest.main()
