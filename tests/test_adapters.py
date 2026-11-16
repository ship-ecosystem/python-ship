"""Built-in framework adapters: detection, validation and their commands."""

import unittest

from python_ship.adapters import BUILTIN_ADAPTERS, DjangoAdapter, FastAPIAdapter, GenericAdapter, target_problems
from python_ship.compose import build_compose
from python_ship.config import ConfigError, ShipConfig
from python_ship.contracts import FrameworkAdapter
from tests.helpers import FIXTURES, ProjectTestCase


class AdapterContractTests(unittest.TestCase):
    def test_builtins_implement_the_public_protocol(self):
        for adapter in BUILTIN_ADAPTERS.values():
            with self.subTest(adapter=adapter.key):
                self.assertIsInstance(adapter, FrameworkAdapter)
                self.assertTrue(adapter.label)

    def test_detection_order_puts_generic_last(self):
        self.assertEqual("generic", list(BUILTIN_ADAPTERS)[-1])

    def test_console_and_release_commands(self):
        django = DjangoAdapter()
        self.assertEqual({"manage": ["python", "manage.py"]}, django.console_commands())
        self.assertEqual([["python", "manage.py", "check", "--deploy"]],
                         django.release_commands(ShipConfig("django", "project.asgi:application")))
        self.assertEqual([["python", "manage.py", "check", "--deploy"], ["python", "manage.py", "collectstatic", "--noinput"]],
                         django.release_commands(ShipConfig("django", "project.asgi:application", django_collectstatic=True)))
        for adapter in (GenericAdapter(), FastAPIAdapter()):
            self.assertEqual({}, adapter.console_commands())
            self.assertEqual([], adapter.release_commands(ShipConfig(adapter.key, "main:app")))

    def test_fixtures_validate_cleanly(self):
        for adapter, fixture, target in ((GenericAdapter(), "plain_asgi", "main:app"),
                                         (DjangoAdapter(), "django_app", "project.asgi:application"),
                                         (FastAPIAdapter(), "fastapi_app", "main:app")):
            with self.subTest(adapter=adapter.key):
                self.assertEqual([], adapter.validate(ShipConfig(adapter.key, target), FIXTURES / fixture))
                self.assertEqual(target, adapter.server_target(ShipConfig(adapter.key, target)))

    def test_fixture_detection_and_default_targets(self):
        self.assertTrue(DjangoAdapter().detect(FIXTURES / "django_app"))
        self.assertEqual("project.asgi:application", DjangoAdapter().default_app(FIXTURES / "django_app"))
        self.assertFalse(DjangoAdapter().detect(FIXTURES / "fastapi_app"))
        self.assertTrue(FastAPIAdapter().detect(FIXTURES / "fastapi_app"))
        self.assertEqual("main:app", FastAPIAdapter().default_app(FIXTURES / "fastapi_app"))
        self.assertFalse(FastAPIAdapter().detect(FIXTURES / "plain_asgi"))
        self.assertTrue(GenericAdapter().detect(FIXTURES / "plain_asgi"))
        self.assertEqual("main:app", GenericAdapter().default_app(FIXTURES / "plain_asgi"))


class DetectionTests(ProjectTestCase):
    app_source = "x = 1\n"

    def test_fastapi_app_in_a_package_or_src_layout(self):
        (self.root / "main.py").unlink()
        (self.root / "app").mkdir()
        (self.root / "app" / "server.py").write_text("from fastapi import FastAPI\n\napi: FastAPI = FastAPI(title='x')\n")
        self.assertEqual("app.server:api", FastAPIAdapter().default_app(self.root))
        (self.root / "app" / "server.py").unlink()
        (self.root / "src" / "service").mkdir(parents=True)
        (self.root / "src" / "service" / "web.py").write_text("import fastapi\napplication = fastapi.FastAPI()\n")
        self.assertEqual("service.web:application", FastAPIAdapter().default_app(self.root))

    def test_environment_and_test_directories_are_not_searched(self):
        for directory in (".venv/lib", "node_modules/pkg", "tests"):
            (self.root / directory).mkdir(parents=True)
            (self.root / directory / "x.py").write_text("from fastapi import FastAPI\napp = FastAPI()\n")
        self.assertFalse(FastAPIAdapter().detect(self.root))

    def test_django_finds_asgi_from_manage_or_by_search(self):
        (self.root / "manage.py").write_text("os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'site_config.settings')\n")
        (self.root / "site_config").mkdir()
        (self.root / "site_config" / "asgi.py").write_text("application = get_asgi_application()\n")
        self.assertEqual("site_config.asgi:application", DjangoAdapter().default_app(self.root))
        (self.root / "manage.py").write_text("# no settings module here\n")
        self.assertEqual("site_config.asgi:application", DjangoAdapter().default_app(self.root))

    def test_generic_default_app_requires_a_binding(self):
        self.assertIsNone(GenericAdapter().default_app(self.root))
        (self.root / "asgi.py").write_text("application = object()\n")
        self.assertEqual("asgi:application", GenericAdapter().default_app(self.root))

    def test_unparsable_files_are_skipped(self):
        (self.root / "main.py").write_text("def broken(:\n")
        self.assertIsNone(GenericAdapter().default_app(self.root))


class ValidationTests(ProjectTestCase):
    def test_target_problems(self):
        self.assertEqual([], target_problems(self.root, "main:app", "App", "main:app"))
        self.assertIn("module:callable", target_problems(self.root, "main", "App", "main:app")[0])
        self.assertIn("does not exist", target_problems(self.root, "missing:app", "App", "main:app")[0])
        self.assertIn("is not defined", target_problems(self.root, "main:other", "App", "main:app")[0])
        (self.root / "broken.py").write_text("def x(:\n")
        self.assertIn("syntax error at line 1", target_problems(self.root, "broken:app", "App", "main:app")[0])
        (self.root / "star.py").write_text("from elsewhere import *\n")
        self.assertEqual([], target_problems(self.root, "star:app", "App", "main:app"))
        (self.root / "imported.py").write_text("from framework import factory as app\n")
        self.assertEqual([], target_problems(self.root, "imported:app", "App", "main:app"))

    def test_bad_targets_fail_compose_generation_with_the_adapter_message(self):
        for adapter, fixture, target, marker in (
            ("django", "django_app", "project.missing:application", "Django app module"),
            ("fastapi", "fastapi_app", "missing:app", "FastAPI app module"),
            ("fastapi", "fastapi_app", "main:missing", "callable 'missing' is not defined"),
            ("generic", "plain_asgi", "main", "module:callable"),
        ):
            with self.subTest(target=target), self.assertRaisesRegex(ConfigError, marker):
                build_compose(ShipConfig(adapter, target), FIXTURES / fixture)

    def test_django_requires_manage_py(self):
        (self.root / "project").mkdir()
        (self.root / "project" / "asgi.py").write_text("application = object()\n")
        problems = DjangoAdapter().validate(ShipConfig("django", "project.asgi:application"), self.root)
        self.assertIn("requires manage.py", problems[0])


if __name__ == "__main__":
    unittest.main()
