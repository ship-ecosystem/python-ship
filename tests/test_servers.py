"""Web servers, app interfaces (ASGI or WSGI), the Flask adapter and services that run the app image."""

import unittest

from python_ship.adapters import DjangoAdapter, FlaskAdapter, GenericAdapter, callable_interface
from python_ship.compose import build_compose, server_for
from python_ship.config import ConfigError, ShipConfig
from python_ship.contracts import WebServer
from python_ship.servers import BUILTIN_SERVERS, RECOMMENDED
from tests.helpers import FIXTURES, FakeEntryPoint, ProjectTestCase, captured, entry_points_for, fake_docker

ASGI = "async def app(scope, receive, send):\n    pass\n"
WSGI = "def app(environ, start_response):\n    return []\n"


class ServerTests(unittest.TestCase):
    def test_builtins_implement_the_protocol_and_are_pinned(self):
        self.assertEqual({"uvicorn", "hypercorn", "gunicorn", "granian"}, set(BUILTIN_SERVERS))
        for server in BUILTIN_SERVERS.values():
            with self.subTest(server=server.key):
                self.assertIsInstance(server, WebServer)
                self.assertRegex(server.requirement, r"==\d")
                for interface in server.interfaces:
                    for command in (server.development_command("main:app", interface), server.production_command("main:app", interface)):
                        self.assertIn("main:app", command)
                        self.assertTrue(any("8000" in part for part in command))
                    self.assertIn("reload", " ".join(server.development_command("main:app", interface)))
                    self.assertNotIn("reload", " ".join(server.production_command("main:app", interface)))

    def test_interfaces_and_recommendations(self):
        self.assertEqual(("asgi",), BUILTIN_SERVERS["uvicorn"].interfaces)
        self.assertEqual(("asgi",), BUILTIN_SERVERS["hypercorn"].interfaces)
        self.assertEqual(("wsgi",), BUILTIN_SERVERS["gunicorn"].interfaces)
        self.assertEqual(("asgi", "wsgi"), BUILTIN_SERVERS["granian"].interfaces)
        self.assertEqual(("uvicorn", "granian", "hypercorn"), RECOMMENDED["asgi"])
        self.assertEqual(("gunicorn", "granian"), RECOMMENDED["wsgi"])
        for interface, keys in RECOMMENDED.items():
            for key in keys:
                self.assertIn(interface, BUILTIN_SERVERS[key].interfaces)

    def test_granian_passes_the_interface(self):
        granian = BUILTIN_SERVERS["granian"]
        self.assertEqual(["python", "-m", "granian", "--interface", "wsgi", "--host", "0.0.0.0", "--port", "8000", "app:app"],
                         granian.production_command("app:app", "wsgi"))
        self.assertIn("asgi", granian.development_command("main:app", "asgi"))
        self.assertIn("[reload]", granian.requirement)


class InterfaceDetectionTests(ProjectTestCase):
    def test_plain_callables(self):
        for source, expected in ((ASGI, "asgi"), ("def app(scope, receive, send):\n    pass\n", "asgi"), (WSGI, "wsgi"),
                                 ("app = make_app()\n", None)):
            with self.subTest(source=source):
                (self.root / "main.py").write_text(source)
                self.assertEqual(expected, callable_interface(self.root, "main:app"))
        self.assertIsNone(callable_interface(self.root, "missing:app"))
        (self.root / "main.py").write_text("app = make_app()\n")
        self.assertEqual("asgi", GenericAdapter().interface(ShipConfig(app="main:app"), self.root))

    def test_django_follows_its_target(self):
        adapter = DjangoAdapter()
        self.assertEqual("asgi", adapter.interface(ShipConfig("django", "project.asgi:application"), self.root))
        self.assertEqual("wsgi", adapter.interface(ShipConfig("django", "project.wsgi:application"), self.root))
        (self.root / "manage.py").write_text("os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'site.settings')\n")
        (self.root / "site").mkdir()
        (self.root / "site" / "wsgi.py").write_text("application = get_wsgi_application()\n")
        self.assertEqual("site.wsgi:application", adapter.default_app(self.root))
        (self.root / "site" / "asgi.py").write_text("application = get_asgi_application()\n")
        self.assertEqual("site.asgi:application", adapter.default_app(self.root))

    def test_models_use_the_recommended_or_selected_server(self):
        (self.root / "main.py").write_text(WSGI)
        model = build_compose(ShipConfig(app="main:app"), self.root)
        app = model["services"]["app"]
        self.assertEqual("gunicorn==27.0.1", app["build"]["args"]["SHIP_SERVER_REQUIREMENT"])
        self.assertIn("gunicorn", app["command"])
        production = build_compose(ShipConfig(app="main:app", server="granian"), self.root, production=True)
        self.assertEqual(["python", "-m", "granian", "--interface", "wsgi", "--host", "0.0.0.0", "--port", "8000", "main:app"],
                         production["services"]["app"]["command"])
        with self.assertRaisesRegex(ConfigError, "Hypercorn \\(ASGI\\) can't serve a WSGI app"):
            build_compose(ShipConfig(app="main:app", server="hypercorn"), self.root)
        (self.root / "main.py").write_text(ASGI)
        self.assertEqual("uvicorn", server_for(ShipConfig(app="main:app"), self.root).key)
        with self.assertRaisesRegex(ConfigError, "Gunicorn \\(WSGI\\) can't serve a ASGI app"):
            build_compose(ShipConfig(app="main:app", server="gunicorn"), self.root)

    def test_config_test_reports_an_incompatible_server(self):
        self.assertEqual(0, self.init("--app", "main:app"))
        self.write_config(app="main:app", server="gunicorn")
        with captured() as output:
            self.assertEqual(1, self.ship("config:test"))
        self.assertIn("can't serve a ASGI app", output.stderr)


class FlaskAdapterTests(ProjectTestCase):
    def test_detection_validation_and_wsgi(self):
        adapter = FlaskAdapter()
        root = FIXTURES / "flask_app"
        self.assertTrue(adapter.detect(root))
        self.assertEqual("app:app", adapter.default_app(root))
        self.assertEqual("wsgi", adapter.interface(ShipConfig("flask", "app:app"), root))
        self.assertEqual([], adapter.validate(ShipConfig("flask", "app:app"), root))
        self.assertIn("is not defined", adapter.validate(ShipConfig("flask", "app:missing"), root)[0])
        self.assertFalse(adapter.detect(FIXTURES / "fastapi_app"))
        self.assertFalse(adapter.detect(FIXTURES / "plain_asgi"))

    def test_models_commands_and_environment(self):
        self.copy_fixture("flask_app")
        self.assertEqual(0, self.init(), self.init_output.stderr)
        config = ShipConfig.read(self.root)
        self.assertEqual(("flask", "app:app", "gunicorn"), (config.framework, config.app, config.server))
        development = self.model(config)["services"]["app"]
        self.assertEqual("app:app", development["environment"]["FLASK_APP"])
        self.assertEqual(["python", "-m", "gunicorn", "--bind", "0.0.0.0:8000", "--reload", "--access-logfile", "-", "app:app"],
                         development["command"])
        self.assertEqual([], FlaskAdapter().release_commands(config))
        with captured() as output:
            self.assertEqual(0, self.ship("--help"))
        self.assertIn("flask", output.stdout)
        with fake_docker() as docker:
            self.assertEqual(0, self.ship("flask", "routes", "--sort", "rule"))
        self.assertEqual(["exec", "app", "flask", "routes", "--sort", "rule"], docker.last[-6:])

    def test_bad_adapter_environment_is_reported(self):
        class Adapter(FlaskAdapter):
            key = "badenv"

            def app_environment(self, config):
                return {"X": 1}

        with entry_points_for({"python_ship.adapters": [FakeEntryPoint("badenv", Adapter)]}):
            with self.assertRaisesRegex(ConfigError, "dict of strings"):
                build_compose(ShipConfig("badenv", "app:app", extensions=["badenv"]), FIXTURES / "flask_app")


class AppProcessTests(ProjectTestCase):
    def test_a_community_service_can_run_the_app_image(self):
        class Scheduler:
            key, group, label = "scheduler", "tasks", "Scheduler"

            def compose_fragment(self, context):
                return {context.service_name: context.app_process(["python", "-m", "scheduler"], environment={"ROLE": "scheduler"})}

            def app_environment(self, context):
                return {"SCHEDULER_ENABLED": "1"}

        config = ShipConfig(app="main:app", services={"tasks": "scheduler", "cache": "redis"}, extensions=["scheduler"])
        with entry_points_for({"python_ship.services": [FakeEntryPoint("scheduler", Scheduler)]}):
            model = self.model(config)
        scheduler = model["services"]["scheduler"]
        self.assertEqual(["python", "-m", "scheduler"], scheduler["command"])
        self.assertEqual("scheduler", scheduler["environment"]["ROLE"])
        self.assertEqual("1", scheduler["environment"]["SCHEDULER_ENABLED"])
        self.assertEqual(model["services"]["app"]["image"], scheduler["image"])
        self.assertIn("redis", scheduler["depends_on"])
        self.assertNotIn("scheduler", model["services"]["app"].get("depends_on", {}))
        self.assertEqual(["app", "scheduler"], model["x-ship"]["app_services"])

    def test_process_names_cannot_collide_with_app_processes(self):
        config = ShipConfig(app="main:app", services={"cache": "redis", "tasks": "celery"}, processes={"celery": "true"})
        with self.assertRaisesRegex(ConfigError, "Process 'celery' conflicts"):
            self.model(config)


if __name__ == "__main__":
    unittest.main()
