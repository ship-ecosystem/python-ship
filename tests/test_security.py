"""The Python security floor, in Ship and in the image-build check."""

import os
from pathlib import Path
import subprocess
import sys
import unittest

from python_ship import security
from python_ship.compose import build_compose
from python_ship.config import ShipConfig
from tests.helpers import ProjectTestCase

SCRIPT = Path(security.__file__).parent / "templates" / "check-python-security-floor.py"


class SecurityFloorTests(unittest.TestCase):
    def test_build_arg_lists_every_branch_oldest_first(self):
        values = security.build_arg().split()
        self.assertEqual(list(security.MINIMUM_PATCHED.values()), values)
        branches = [tuple(int(part) for part in value.split(".")[:2]) for value in values]
        self.assertEqual(sorted(branches), branches)

    def test_problem_with(self):
        self.assertIsNone(security.problem_with("3.13"))
        self.assertIsNone(security.problem_with("3.15"))
        self.assertIsNone(security.problem_with("3.16"))  # Newer than any floor: nothing to compare yet.
        self.assertIsNone(security.problem_with("3.13.18"))
        self.assertIsNone(security.problem_with("3.13.20"))
        self.assertIn("past end of life", security.problem_with("3.10") or "")
        self.assertIn("past end of life", security.problem_with("3.9.20") or "")
        self.assertIn("known security vulnerabilities", security.problem_with("3.13.17") or "")
        self.assertIn("\"3.12\"", security.problem_with("3.12.1") or "")
        self.assertIn("must be a branch", security.problem_with("3.x") or "")

    def test_default_python_is_supported(self):
        self.assertIn(security.DEFAULT_PYTHON, security.MINIMUM_PATCHED)


class FloorScriptTests(unittest.TestCase):
    def run_script(self, version: str, floor: str = security.build_arg()) -> subprocess.CompletedProcess[str]:
        environment = {**os.environ, "SHIP_PYTHON_FLOOR": floor, "SHIP_BASE_IMAGE": "python:3.13-slim"}
        return subprocess.run([sys.executable, str(SCRIPT), version], env=environment, capture_output=True, text=True)

    def test_patched_releases_pass(self):
        for version in (*security.MINIMUM_PATCHED.values(), "3.13.99", "3.16.0", "3.16.0a1"):
            with self.subTest(version=version):
                self.assertEqual(0, self.run_script(version).returncode)

    def test_stale_image_fails_with_the_pull_command(self):
        result = self.run_script("3.13.17")
        self.assertEqual(1, result.returncode)
        self.assertIn("docker pull python:3.13-slim", result.stderr)

    def test_end_of_life_branch_fails(self):
        result = self.run_script("3.10.22")
        self.assertEqual(1, result.returncode)
        self.assertIn("past end of life", result.stderr)

    def test_empty_floor_skips_the_check(self):
        self.assertEqual(0, self.run_script("3.8.0", floor="").returncode)

    def test_runs_without_arguments_on_the_current_interpreter(self):
        result = subprocess.run([sys.executable, str(SCRIPT)], env={**os.environ, "SHIP_PYTHON_FLOOR": "3.0.0"},
                                capture_output=True, text=True)
        self.assertEqual(0, result.returncode, result.stderr)


class FloorInComposeTests(ProjectTestCase):
    def test_both_models_pass_the_floor_and_production_pulls(self):
        config = ShipConfig(app="main:app")
        development = build_compose(config, self.root)["services"]["app"]["build"]
        production = build_compose(config, self.root, production=True)["services"]["app"]["build"]
        self.assertEqual(security.build_arg(), development["args"]["SHIP_PYTHON_FLOOR"])
        self.assertEqual(security.build_arg(), production["args"]["SHIP_PYTHON_FLOOR"])
        self.assertNotIn("pull", development)
        self.assertTrue(production["pull"])

    def test_published_dockerfile_runs_the_check_in_the_shared_base_stage(self):
        self.assertEqual(0, self.init("--app", "main:app"))
        dockerfile = (self.root / "ship" / "Dockerfile").read_text()
        self.assertTrue((self.root / "ship" / "check-python-security-floor.py").is_file())
        base = dockerfile.index("AS base")
        self.assertLess(base, dockerfile.index("check-python-security-floor.py"))
        self.assertLess(dockerfile.index("check-python-security-floor.py"), dockerfile.index("AS builder"))
        self.assertIn("FROM base AS production", dockerfile)


if __name__ == "__main__":
    unittest.main()
