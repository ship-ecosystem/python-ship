"""Loading community adapters, services and servers from entry points."""

import unittest

from python_ship.compose import build_compose
from python_ship.config import ConfigError, ShipConfig
from python_ship.plugins import console_commands, load_extensions
from tests.helpers import FakeEntryPoint, ProjectTestCase, captured, entry_points_for


class CommunityService:
    key, group, label = "community", "queue", "Community queue"

    def compose_fragment(self, context):
        if context.production:
            return {}
        return {context.service_name: {"image": "example/queue:1", "volumes": [f"{context.resource_name}_data:/data"]}}

    def app_environment(self, context):
        return {f"{context.env_prefix}QUEUE_URL": f"amqp://{context.service_name}"}


class CommunityAdapter:
    key, label = "community", "Community framework"

    def detect(self, root):
        return (root / "community.toml").is_file()

    def default_app(self, root):
        return "site:app"

    def validate(self, config, root):
        return []

    def server_target(self, config):
        return config.app

    def console_commands(self):
        return {"community": ["python", "-m", "community"]}

    def release_commands(self, config):
        return [["python", "-m", "community", "warm"]]


class CommunityServer:
    key, label, requirement = "granian", "Granian", "granian==2.6.0"

    def development_command(self, target):
        return ["granian", "--interface", "asgi", "--reload", target]

    def production_command(self, target):
        return ["granian", "--interface", "asgi", target]


class LoaderTests(ProjectTestCase):
    def load(self, groups, *selected):
        with entry_points_for(groups):
            return load_extensions(ShipConfig(app="main:app", extensions=list(selected)))

    def test_only_explicitly_selected_extensions_load(self):
        groups = {"python_ship.services": [FakeEntryPoint("community", CommunityService)]}
        self.assertNotIn("community", self.load(groups).services)
        self.assertIn("community", self.load(groups, "community").services)

    def test_all_three_kinds_load_and_instances_or_classes_work(self):
        groups = {
            "python_ship.services": [FakeEntryPoint("svc", CommunityService)],
            "python_ship.adapters": [FakeEntryPoint("adp", CommunityAdapter())],
            "python_ship.servers": [FakeEntryPoint("srv", CommunityServer)],
        }
        registry = self.load(groups, "svc", "adp", "srv")
        self.assertIn("community", registry.services)
        self.assertIn("community", registry.adapters)
        self.assertIn("granian", registry.servers)
        self.assertEqual([registry.services["community"]], registry.in_group("queue"))

    def test_community_extensions_build_both_models(self):
        groups = {
            "python_ship.services": [FakeEntryPoint("svc", CommunityService)],
            "python_ship.adapters": [FakeEntryPoint("adp", CommunityAdapter)],
            "python_ship.servers": [FakeEntryPoint("srv", CommunityServer)],
        }
        config = ShipConfig("community", "main:app", server="granian", services={"queue": "community"},
                            extensions=["svc", "adp", "srv"])
        with entry_points_for(groups):
            development = build_compose(config, self.root)
            production = build_compose(config, self.root, production=True)
        self.assertEqual(["granian", "--interface", "asgi", "--reload", "main:app"], development["services"]["app"]["command"])
        self.assertEqual("granian==2.6.0", development["services"]["app"]["build"]["args"]["SHIP_SERVER_REQUIREMENT"])
        self.assertEqual("amqp://community", development["services"]["app"]["environment"]["QUEUE_URL"])
        self.assertIn("community_data", development["volumes"])
        self.assertNotIn("community", production["services"])
        self.assertNotIn("QUEUE_URL", production["services"]["app"]["environment"])
        self.assertEqual(["sh", "-c", "python -m community warm && exec granian --interface asgi main:app"],
                         production["services"]["app"]["command"])

    def test_failures_are_configuration_errors(self):
        class Broken:
            def __init__(self):
                raise RuntimeError("constructor exploded")

        class NoLabel(CommunityService):
            label = ""

        class BadGroup(CommunityService):
            group = "Bad Group"

        class NotCallable(CommunityService):
            compose_fragment = "nope"

        class BadOptional(CommunityService):
            database_shell = "nope"

        class Incomplete:
            key, label = "incomplete", "Incomplete"

            def detect(self, root):
                return False

        class DuplicateKey(CommunityService):
            key = "redis"

        class BadKey(CommunityService):
            key = "Bad Key"

        class NoRequirement(CommunityServer):
            requirement = " "

        cases = (
            ({}, "missing", "is not installed"),
            ({"python_ship.services": [FakeEntryPoint("dup", CommunityService)],
              "python_ship.adapters": [FakeEntryPoint("dup", CommunityAdapter)]}, "dup", "matches 2 python_ship entry points"),
            ({"python_ship.services": [FakeEntryPoint("x", Broken)]}, "x", "constructor exploded"),
            ({"python_ship.services": [FakeEntryPoint("x", NoLabel)]}, "x", "nonempty label"),
            ({"python_ship.services": [FakeEntryPoint("x", BadGroup)]}, "x", "invalid service group"),
            ({"python_ship.services": [FakeEntryPoint("x", NotCallable)]}, "x", "missing: compose_fragment"),
            ({"python_ship.services": [FakeEntryPoint("x", BadOptional)]}, "x", "non-callable optional hooks: database_shell"),
            ({"python_ship.adapters": [FakeEntryPoint("x", Incomplete)]}, "x", "missing: default_app, validate"),
            ({"python_ship.services": [FakeEntryPoint("x", DuplicateKey)]}, "x", "reuses the service key 'redis'"),
            ({"python_ship.services": [FakeEntryPoint("x", BadKey)]}, "x", "invalid key"),
            ({"python_ship.servers": [FakeEntryPoint("x", NoRequirement)]}, "x", "PEP 508 requirement"),
        )
        for groups, selected, message in cases:
            with self.subTest(message=message), self.assertRaisesRegex(ConfigError, message):
                self.load(groups, selected)

    def test_unknown_adapter_and_server_name_the_choices(self):
        registry = self.load({})
        with self.assertRaisesRegex(ConfigError, "django, fastapi, generic"):
            registry.adapter(ShipConfig("flask", "main:app"))
        with self.assertRaisesRegex(ConfigError, "hypercorn, uvicorn"):
            registry.server(ShipConfig(app="main:app", server="granian"))


class ConsoleCommandTests(unittest.TestCase):
    def test_valid_and_invalid_commands(self):
        self.assertEqual({"community": ["python", "-m", "community"]}, console_commands(CommunityAdapter()))
        for commands, message in (({"up": ["x"]}, "reserved"), ({"Bad": ["x"]}, "invalid"), ({"ok": []}, "nonempty"),
                                  (["x"], "must return a dict")):
            adapter = CommunityAdapter()
            adapter.console_commands = lambda commands=commands: commands
            with self.subTest(commands=commands), self.assertRaisesRegex(ConfigError, message):
                console_commands(adapter)


class AdapterCommandRegistrationTests(ProjectTestCase):
    def test_selected_adapter_commands_become_ship_commands(self):
        groups = {"python_ship.adapters": [FakeEntryPoint("adp", CommunityAdapter)]}
        ShipConfig("community", "main:app", extensions=["adp"]).write(self.root)
        with entry_points_for(groups), captured() as output:
            self.assertEqual(0, self.ship("--help"))
        self.assertIn("community", output.stdout)
        ShipConfig("generic", "main:app").write(self.root)
        with captured() as output:
            self.assertEqual(0, self.ship("--help"))
        self.assertNotIn("manage", output.stdout)

    def test_unreadable_config_registers_nothing_and_does_not_crash(self):
        (self.root / "ship.json").write_text("{broken")
        with captured() as output:
            self.assertEqual(0, self.ship("--help"))
        self.assertIn("config:test", output.stdout)


if __name__ == "__main__":
    unittest.main()
