"""`ship init`: detection, interactive prompts, re-running, published files and uv."""

from contextlib import contextmanager
import json
import unittest
from unittest.mock import patch

from python_ship import __version__
from python_ship.config import AdditionalService, ConfigError, ShipConfig
from python_ship.init import GITIGNORE, RELEASE_IGNORE_GUARD, ensure_gitignore
from tests.helpers import ProjectTestCase, captured, no_uv_prompt


@contextmanager
def answers(prompts, confirms=()):
    """Script the interactive prompts in order."""
    prompts, confirms = list(prompts), list(confirms)
    with patch("python_ship.cli.interactive_terminal", return_value=True), \
            patch("python_ship.init.typer.prompt", side_effect=lambda *a, **k: prompts.pop(0)) as prompt, \
            patch("python_ship.init.typer.confirm", side_effect=lambda *a, **k: confirms.pop(0)):
        yield prompt
    if prompts or confirms:
        raise AssertionError(f"Unused answers: {prompts} {confirms}")


class NonInteractiveInitTests(ProjectTestCase):
    def test_plain_app_writes_config_stubs_and_ignore_files(self):
        self.assertEqual(0, self.init())
        config = ShipConfig.read(self.root)
        self.assertEqual(("generic", "main:app", "3.13", "uvicorn"), (config.framework, config.app, config.python, config.server))
        ship = self.root / "ship"
        dockerfile = (ship / "Dockerfile").read_text()
        for marker in ("ghcr.io/astral-sh/uv:0.13.3", "uv sync --locked --no-dev", "uv pip install -r requirements.txt",
                       'uv pip install "$SHIP_SERVER_REQUIREMENT"', "AS production", "USER 10001:10001", "AS development"):
            self.assertIn(marker, dockerfile)
        self.assertNotIn("@", dockerfile.replace("@{", ""))
        self.assertEqual(__version__, (ship / ".ship-version").read_text().strip())
        self.assertTrue((ship / "check-python-security-floor.py").is_file())
        self.assertFalse((ship / "garage").exists())
        gitignore = (self.root / ".gitignore").read_text().splitlines()
        for rule in GITIGNORE:
            self.assertIn(rule, gitignore)
        dockerignore = (self.root / ".dockerignore").read_text()
        self.assertTrue(dockerignore.endswith(RELEASE_IGNORE_GUARD))
        self.assertIn("**/.env.*", dockerignore)
        self.assertIn("Run `ship up`", self.init_output.stdout)

    def test_detects_django_and_fastapi_with_their_targets(self):
        for fixture, framework, target in (("django_app", "django", "project.asgi:application"),
                                           ("fastapi_app", "fastapi", "main:app")):
            with self.subTest(fixture=fixture):
                for path in self.root.iterdir():
                    if path.is_file():
                        path.unlink()
                (self.root / "ship.json").unlink(missing_ok=True)
                self.copy_fixture(fixture)
                self.assertEqual(0, self.init("--force"))
                config = ShipConfig.read(self.root)
                self.assertEqual((framework, target), (config.framework, config.app))

    def test_flags_select_services_and_none_removes_them(self):
        self.assertEqual(0, self.init("--database", "mysql", "--cache", "redis", "--storage", "silo",
                                      "--silo-image", "distroless", "--search", "meilisearch", "--mail", "mailpit",
                                      "--testing", "playwright", "--frontend", "node", "--server", "hypercorn",
                                      "--python", "3.12"))
        config = ShipConfig.read(self.root)
        self.assertEqual({"database": "mysql", "cache": "redis", "storage": "silo", "search": "meilisearch",
                          "mail": "mailpit", "testing": "playwright", "frontend": "node"}, config.services)
        self.assertEqual(("distroless", "hypercorn", "3.12"), (config.silo_image, config.server, config.python))
        self.assertEqual(0, self.init("--cache", "none", "--testing", "none"))
        self.assertEqual({"database", "storage", "search", "mail", "frontend"}, set(ShipConfig.read(self.root).services))

    def test_invalid_flags_fail_without_writing(self):
        for arguments, message in ((["--database", "oracle"], "Unknown database service 'oracle'"),
                                   (["--framework", "flask"], "Unknown framework 'flask'"),
                                   (["--server", "granian"], "Unknown ASGI server"),
                                   (["--python", "3.10"], "past end of life"),
                                   (["--python", "3.13.2"], "known security vulnerabilities"),
                                   (["--silo-image", "tiny"], "standard or distroless"),
                                   (["--app", "missing:app"], "does not exist")):
            with self.subTest(arguments=arguments):
                self.assertEqual(1, self.init(*arguments))
                self.assertIn(message, self.init_output.stderr)
                self.assertFalse((self.root / "ship.json").exists())
                self.assertFalse((self.root / "ship").exists())

    def test_undetectable_app_asks_for_app(self):
        (self.root / "main.py").write_text("x = 1\n")
        self.assertEqual(1, self.init())
        self.assertIn("pass it with --app", self.init_output.stderr)

    def test_existing_dockerfile_without_config_needs_force(self):
        (self.root / "ship").mkdir()
        (self.root / "ship" / "Dockerfile").write_text("FROM custom\n")
        self.assertEqual(1, self.init())
        self.assertIn("use --force", self.init_output.stderr)
        self.assertEqual(0, self.init("--force"))
        self.assertIn("AS production", (self.root / "ship" / "Dockerfile").read_text())


class RerunTests(ProjectTestCase):
    def test_rerun_keeps_every_field_it_does_not_prompt_for_and_the_dockerfile(self):
        self.assertEqual(0, self.init())
        config = self.write_config(
            app="main:app", python="3.12", services={"cache": "redis"},
            additional_services=[AdditionalService("database", "postgres", "analytics")],
            service_names={"redis": "cache"}, external_network="shared", name="custom-name",
            publish_ports=True, host_user=False, processes={"worker": "python worker.py"},
            deploy_commands=["python migrate.py"], silo_image="distroless",
        )
        dockerfile = self.root / "ship" / "Dockerfile"
        dockerfile.write_text(dockerfile.read_text() + "# my edit\n")
        self.assertEqual(0, self.init())
        self.assertEqual(config, ShipConfig.read(self.root))
        self.assertTrue(dockerfile.read_text().endswith("# my edit\n"))
        self.assertIn("kept your ship/Dockerfile", self.init_output.stdout)

    def test_stale_stub_warns_and_force_republishes(self):
        self.assertEqual(0, self.init())
        (self.root / "ship" / ".ship-version").write_text("0.0.1\n")
        (self.root / "ship" / "Dockerfile").write_text("FROM old\n")
        self.assertEqual(0, self.init())
        self.assertIn("published by python-ship 0.0.1", self.init_output.stderr)
        self.assertEqual("FROM old\n", (self.root / "ship" / "Dockerfile").read_text())
        self.assertEqual(0, self.init("--force"))
        self.assertIn("AS production", (self.root / "ship" / "Dockerfile").read_text())
        self.assertEqual(__version__, (self.root / "ship" / ".ship-version").read_text().strip())

    def test_old_dockerfile_blocks_server_and_asset_changes_until_force(self):
        self.assertEqual(0, self.init())
        (self.root / "ship" / "Dockerfile").write_text("FROM python:3.13-slim\n")
        self.assertEqual(1, self.init("--server", "hypercorn"))
        self.assertIn("cannot install the selected ASGI server", self.init_output.stderr)
        self.assertEqual(1, self.init("--node-assets"))
        self.assertIn("nodeAssets requires a refreshed", self.init_output.stderr)
        self.assertEqual(0, self.init("--server", "hypercorn", "--node-assets", "--force"))
        dockerfile = (self.root / "ship" / "Dockerfile").read_text()
        self.assertIn("AS assets", dockerfile)
        self.assertIn("COPY --from=assets /ship-assets/ /app/dist/", dockerfile)
        self.assertEqual(0, self.init("--no-node-assets"))
        self.assertFalse(ShipConfig.read(self.root).node_assets)

    def test_garage_files_are_published_and_restored(self):
        self.assertEqual(0, self.init("--storage", "garage"))
        garage = self.root / "ship" / "garage"
        self.assertIn("__GARAGE_RPC_PUBLIC_ADDR__", (garage / "garage.toml").read_text())
        (garage / "garage.toml").unlink()
        self.assertEqual(0, self.init())
        self.assertTrue((garage / "garage.toml").is_file())

    def test_ignore_files_are_merged_once(self):
        (self.root / ".gitignore").write_text("node_modules\n/dist/ship/")
        (self.root / ".dockerignore").write_text("custom\n!.env.production\n")
        self.assertEqual(0, self.init())
        self.assertEqual(0, self.init())
        gitignore = (self.root / ".gitignore").read_text()
        self.assertEqual(1, gitignore.count("/dist/ship/"))
        self.assertEqual(1, gitignore.count("/.env.production"))
        self.assertTrue(gitignore.startswith("node_modules\n/dist/ship/\n"))
        dockerignore = (self.root / ".dockerignore").read_text()
        self.assertEqual(1, dockerignore.count("# python-ship release guard"))
        self.assertTrue(dockerignore.startswith("custom\n!.env.production\n"))
        self.assertTrue(dockerignore.endswith(RELEASE_IGNORE_GUARD))

    def test_gitignore_accepts_unanchored_existing_rules(self):
        (self.root / ".gitignore").write_text(".env.production\ndist/ship/\n")
        ensure_gitignore(self.root)
        self.assertNotIn("/.env.production", (self.root / ".gitignore").read_text())


class InteractiveInitTests(ProjectTestCase):
    def run_init(self, *arguments):
        with no_uv_prompt(), captured() as output:
            code = self.ship("init", *arguments)
        return code, output

    def test_picks_every_group_and_python(self):
        # framework, app, server, database, cache, storage, search, mail, testing, frontend, python
        with answers(["generic", "main:app", "uvicorn", "postgres", "2", "1", "None", "1", "playwright", "Node.js (npm, Vite HMR)", "3.12"],
                     confirms=[False]):
            code, output = self.run_init()
        self.assertEqual(0, code, output.stderr)
        config = ShipConfig.read(self.root)
        self.assertEqual("generic", config.framework)
        self.assertEqual({"database": "postgres", "cache": "redis", "testing": "playwright", "frontend": "node"}, config.services)
        self.assertEqual("3.12", config.python)
        self.assertIn("Select a database", output.stdout)
        self.assertIn("Plain ASGI (no framework)", output.stdout)

    def test_defaults_keep_an_existing_selection(self):
        self.write_config(app="main:app", services={"database": "mysql", "cache": "redis"}, python="3.14")
        defaults = []

        def accept_default(*args, **kwargs):
            defaults.append(kwargs.get("default"))
            return kwargs.get("default")

        with patch("python_ship.cli.interactive_terminal", return_value=True), \
                patch("python_ship.init.typer.prompt", side_effect=accept_default), \
                patch("python_ship.init.typer.confirm", return_value=False):
            code, output = self.run_init()
        self.assertEqual(0, code, output.stderr)
        config = ShipConfig.read(self.root)
        self.assertEqual({"database": "mysql", "cache": "redis"}, config.services)
        self.assertEqual("3.14", config.python)

    def test_additional_instances_silo_image_and_validation_retries(self):
        prompts = ["generic", "main:app", "1",
                   "1", "1", "silo", "1", "1", "1", "1",           # groups: none except storage=silo
                   "database", "mysql", "Bad Name", "analytics",    # first named instance (one invalid name)
                   "1", "1", "analytics", "reports",                # second: database/postgres, name reused, then fixed
                   "distroless",                                    # silo image
                   "3.9", "3.13"]                                   # python (end-of-life first)
        with answers(prompts, confirms=[True, True, False]):
            code, output = self.run_init()
        self.assertEqual(0, code, output.stderr)
        config = ShipConfig.read(self.root)
        self.assertEqual([AdditionalService("database", "mysql", "analytics"), AdditionalService("database", "postgres", "reports")],
                         config.additional_services)
        self.assertEqual("distroless", config.silo_image)
        self.assertIn("x86-64-v2", output.stdout)
        self.assertIn("lowercase letters", output.stderr)
        self.assertIn("already used", output.stderr)
        self.assertIn("past end of life", output.stderr)

    def test_invalid_choice_is_asked_again(self):
        with answers(["9", "generic", "main:app", "uvicorn", *(["None"] * 7), "3.13"], confirms=[False]):
            code, output = self.run_init()
        self.assertEqual(0, code, output.stderr)
        self.assertIn("'9' is not a valid choice", output.stderr)

    def test_flags_skip_their_questions(self):
        with answers(["None"] * 5, confirms=[False]):
            code, output = self.run_init("--framework", "generic", "--app", "main:app", "--server", "uvicorn",
                                         "--database", "postgres", "--cache", "redis", "--python", "3.13")
        self.assertEqual(0, code, output.stderr)
        self.assertEqual({"database": "postgres", "cache": "redis"}, ShipConfig.read(self.root).services)

    def test_no_interaction_flag_never_prompts(self):
        with patch("python_ship.cli.interactive_terminal", return_value=True), \
                patch("python_ship.init.typer.prompt", side_effect=AssertionError("prompted")):
            self.assertEqual(0, self.init())


class ViteReminderTests(ProjectTestCase):
    def test_reminder_only_when_vite_config_lacks_a_host(self):
        (self.root / "package.json").write_text(json.dumps({"devDependencies": {"vite": "^7"}}))
        self.assertEqual(0, self.init("--frontend", "node"))
        self.assertIn("host: '0.0.0.0'", self.init_output.stderr)
        (self.root / "vite.config.js").write_text("export default { server: { host: '0.0.0.0' } }\n")
        self.assertEqual(0, self.init())
        self.assertNotIn("host: '0.0.0.0'", self.init_output.stderr)


class UvRequirementTests(ProjectTestCase):
    def test_declining_or_failing_uv_writes_nothing(self):
        for answer, failure in (("no", None), ("yes", ConfigError("installer failed"))):
            with self.subTest(answer=answer), \
                    patch("python_ship.uv.locate_uv", return_value=None), \
                    patch("builtins.input", return_value=answer), \
                    patch("python_ship.uv._install_uv", side_effect=failure) as installer, captured() as output:
                self.assertEqual(1, self.ship("init", "-n"))
            self.assertEqual(answer == "yes", installer.called)
            self.assertIn("uv", output.stderr)
            self.assertEqual(["main.py"], sorted(path.name for path in self.root.iterdir()))

    def test_accepting_installs_then_initializes(self):
        with patch("python_ship.uv.locate_uv", side_effect=[None, "/home/user/.local/bin/uv"]), \
                patch("builtins.input", return_value="y"), patch("python_ship.uv._install_uv") as installer, captured():
            self.assertEqual(0, self.ship("init", "-n"))
        installer.assert_called_once_with()
        self.assertTrue((self.root / "ship.json").is_file())

    def test_uv_is_checked_after_validation(self):
        with patch("python_ship.init.require_uv") as require, captured():
            self.assertEqual(1, self.ship("init", "-n", "--app", "missing:app"))
        require.assert_not_called()


if __name__ == "__main__":
    unittest.main()
