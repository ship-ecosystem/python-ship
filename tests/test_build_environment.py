"""BuildKit credentials stay outside runtime images, settings and release manifests."""

from python_ship.build_environment import build_problems
from python_ship.config import ShipConfig
from python_ship.release import release_model
from python_ship.stubs import render_dockerfile, stub_problems
from tests.helpers import ProjectTestCase


class BuildEnvironmentTests(ProjectTestCase):
    def test_public_arguments_and_secret_mounts_have_separate_lifetimes(self):
        config = ShipConfig(name="example", app="main:app", build={"publicEnv": {"VITE_API_URL": "https://example.test/$literal"},
            "secrets": ["UV_INDEX_TOKEN"], "npmrc": True})
        text = render_dockerfile(config, self.root)
        self.assertIn("ARG VITE_API_URL", text)
        self.assertIn("--mount=type=secret,id=ship_build_UV_INDEX_TOKEN", text)
        self.assertNotIn("ENV UV_INDEX_TOKEN", text)
        model = self.model(config, production=True)
        self.assertEqual("https://example.test/$$literal", model["services"]["app"]["build"]["args"]["VITE_API_URL"])
        self.assertNotIn("UV_INDEX_TOKEN", model["services"]["app"]["environment"])
        development = self.model(config)
        self.assertEqual("https://example.test/$$literal", development["services"]["app"]["environment"]["VITE_API_URL"])
        exported = release_model(model)
        self.assertNotIn("secrets", exported)
        self.assertNotIn("build", exported["services"]["app"])
        self.assertTrue(build_problems(config, self.root))
        (self.root / ".ship-secrets").mkdir()
        for name in ("UV_INDEX_TOKEN", "npmrc"):
            (self.root / ".ship-secrets" / name).write_text("private")
        self.assertEqual([], build_problems(config, self.root))

    def test_invalid_visibility_and_runtime_overrides_fail_early(self):
        for build in ({"secrets": ["VITE_SECRET"]}, {"publicEnv": {"PATH": "override"}},
                      {"secrets": ["TOKEN"], "publicEnv": {"TOKEN": "value"}}, {"secrets": ["bad name"]}):
            self.assertTrue(ShipConfig.from_data({"app": "main:app", "build": build})[1])

    def test_existing_templates_require_review_when_build_inputs_change(self):
        self.init()
        config = ShipConfig(app="main:app", build={"publicEnv": {"VITE_API_URL": "https://example.test"}})
        self.assertTrue(any("refreshed" in issue for issue in stub_problems(self.root, config)))

    def test_published_template_has_a_revision_and_entrypoint(self):
        self.init()
        self.assertTrue((self.root / "ship/.ship-revision").is_file())
        self.assertTrue((self.root / "ship/app-entrypoint.py").is_file())
        self.assertIn(".ship-secrets/", (self.root / ".dockerignore").read_text())
