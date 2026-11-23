"""Development and production Compose model generation."""

import json
import os
import subprocess
import sys
import unittest
from unittest.mock import patch

from python_ship.compose import compose_path, project_name, render, write_compose
from python_ship.config import AdditionalService, ConfigError, ShipConfig
from python_ship.contracts import ServiceContext
from python_ship.plugins import load_extensions
from tests.helpers import DOCKER, ProjectTestCase


class ExtraService:
    """A community-style service exercising every merge path."""

    key, group, label = "extra", "search", "Extra"

    def compose_fragment(self, context: ServiceContext):
        return {
            context.service_name: {
                "image": "example/extra:1", "environment": {"VALUE": "one"},
                "volumes": [f"{context.resource_name}_data:/data"],
                "ports": ["9001:9001", {"target": 9002, "published": 9002}, 9003, "9004/udp", "0.0.0.0:9005:9005"],
                "depends_on": {"helper": {"condition": "service_started"}},
            },
            "helper": {"image": "example/helper:1"},
            "app": {"build": {"args": {"EXTRA": "yes"}}, "environment": {"EXTRA_URL": "http://extra"},
                    "volumes": ["./extra:/extra"]},
        }

    def app_environment(self, context):
        return {f"{context.env_prefix}EXTRA_FLAG": "yes"}


def registry_with(*services):
    def load(config):
        registry = load_extensions(ShipConfig())
        for service in services:
            registry.services[service.key] = service
        return registry
    return patch("python_ship.compose.load_extensions", side_effect=load)


class DevelopmentModelTests(ProjectTestCase):
    def test_plain_app_development_model(self):
        model = self.model(ShipConfig(app="main:app"))
        app = model["services"]["app"]
        self.assertEqual(["python", "-m", "uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000", "--reload"], app["command"])
        self.assertEqual("development", app["build"]["target"])
        self.assertEqual("ship/Dockerfile", app["build"]["dockerfile"])
        self.assertEqual("uvicorn==0.54.0", app["build"]["args"]["SHIP_SERVER_REQUIREMENT"])
        self.assertEqual("3.13", app["build"]["args"]["PYTHON_VERSION"])
        self.assertEqual([".:/app"], app["volumes"])
        self.assertEqual(["127.0.0.1:${APP_PORT:-8000}:8000"], app["ports"])
        self.assertEqual([{"path": ".env", "required": False}], app["env_file"])
        self.assertEqual(["host.docker.internal:host-gateway"], app["extra_hosts"])
        self.assertEqual("unless-stopped", app["restart"])
        self.assertNotIn("volumes", model)

    def test_host_user_on_linux_and_opt_out(self):
        linux_user = sys.platform.startswith("linux") and hasattr(os, "getuid") and os.getuid() != 0
        app = self.model(ShipConfig(app="main:app"))["services"]["app"]
        if linux_user:
            self.assertEqual(f"{os.getuid()}:{os.getgid()}", app["user"])
        else:
            self.assertNotIn("user", app)
        self.assertNotIn("user", self.model(ShipConfig(app="main:app", host_user=False))["services"]["app"])
        self.assertNotIn("user", self.model(ShipConfig(app="main:app"), production=True)["services"]["app"])
        with patch("python_ship.compose.sys.platform", "win32"):
            self.assertNotIn("user", self.model(ShipConfig(app="main:app"))["services"]["app"])

    def test_services_wire_environment_dependencies_and_volumes(self):
        model = self.model(ShipConfig(app="main:app", services={"database": "postgres", "cache": "redis"}))
        app = model["services"]["app"]
        self.assertEqual("postgres", app["environment"]["DB_HOST"])
        self.assertEqual("redis://redis:6379/0", app["environment"]["REDIS_URL"])
        self.assertEqual({"postgres": {"condition": "service_healthy"}, "redis": {"condition": "service_healthy"}}, app["depends_on"])
        self.assertEqual({"postgres_data": None, "redis_data": None}, model["volumes"])

    def test_merges_fragments_without_erasing_settings_and_localizes_ports(self):
        with registry_with(ExtraService()):
            model = self.model(ShipConfig(app="main:app", services={"search": "extra"}))
        app = model["services"]["app"]
        self.assertEqual("yes", app["build"]["args"]["EXTRA"])
        self.assertIn("SHIP_SERVER_REQUIREMENT", app["build"]["args"])
        self.assertEqual([".:/app", "./extra:/extra"], app["volumes"])
        self.assertEqual("http://extra", app["environment"]["EXTRA_URL"])
        self.assertEqual("yes", app["environment"]["EXTRA_FLAG"])
        self.assertEqual({"condition": "service_started"}, app["depends_on"]["extra"])
        self.assertEqual([
            "127.0.0.1:9001:9001", {"host_ip": "127.0.0.1", "target": 9002, "published": 9002},
            {"target": 9003, "host_ip": "127.0.0.1"}, {"target": 9004, "host_ip": "127.0.0.1", "protocol": "udp"},
            "0.0.0.0:9005:9005",
        ], model["services"]["extra"]["ports"])
        self.assertIn("helper", model["services"])
        self.assertIn("extra_data", model["volumes"])

    def test_conflicting_values_and_bad_fragments_are_errors(self):
        class Conflict(ExtraService):
            def compose_fragment(self, context):
                return {context.service_name: {"image": "x"}, "app": {"working_dir": "/elsewhere"}}

        class Missing(ExtraService):
            def compose_fragment(self, context):
                return {"other": {"image": "x"}}

        class Broken(ExtraService):
            def compose_fragment(self, context):
                raise RuntimeError("fragment exploded")

        class BadPorts(ExtraService):
            def compose_fragment(self, context):
                return {context.service_name: {"image": "x", "ports": "8000"}}

        for service, message in ((Conflict(), "Conflicting Compose value at services.app.working_dir"),
                                 (Missing(), "did not define its Compose service 'extra'"),
                                 (Broken(), "fragment exploded"), (BadPorts(), "ports must be a list")):
            with self.subTest(message=message), registry_with(service), self.assertRaisesRegex(ConfigError, message):
                self.model(ShipConfig(app="main:app", services={"search": "extra"}))

    def test_named_instances_get_their_own_name_prefix_and_volume(self):
        config = ShipConfig(app="main:app", services={"database": "postgres", "cache": "redis"}, additional_services=[
            AdditionalService("database", "postgres", "analytics"), AdditionalService("cache", "redis", "queue"),
        ])
        model = self.model(config)
        app = model["services"]["app"]
        self.assertEqual({"app", "postgres", "redis", "postgres-analytics", "redis-queue"}, set(model["services"]))
        self.assertEqual({"postgres_data", "redis_data", "postgres-analytics_data", "redis-queue_data"}, set(model["volumes"]))
        self.assertEqual("postgres-analytics", app["environment"]["ANALYTICS_DB_HOST"])
        self.assertEqual("${ANALYTICS_DB_NAME:-app}", app["environment"]["ANALYTICS_DB_NAME"])
        self.assertEqual("${ANALYTICS_DB_PASSWORD:-ship}", model["services"]["postgres-analytics"]["environment"]["POSTGRES_PASSWORD"])
        self.assertEqual("redis://redis-queue:6379/0", app["environment"]["QUEUE_REDIS_URL"])
        self.assertIn("postgres-analytics", app["depends_on"])

    def test_service_names_rename_services_and_hosts_but_not_volumes(self):
        config = ShipConfig(app="main:app", services={"database": "postgres", "storage": "garage"},
                            service_names={"app": "client-app", "postgres": "client-db", "garage": "client-s3"})
        model = self.model(config)
        self.assertEqual({"client-app", "client-db", "client-s3"}, set(model["services"]))
        app = model["services"]["client-app"]
        self.assertEqual("client-db", app["environment"]["DB_HOST"])
        self.assertEqual("http://client-s3:3900", app["environment"]["AWS_ENDPOINT"])
        self.assertIn("client-db", app["depends_on"])
        self.assertEqual("client-s3:3901", model["services"]["client-s3"]["build"]["args"]["GARAGE_RPC_PUBLIC_ADDR"])
        self.assertIn("postgres_data", model["volumes"])

    def test_service_name_collisions_and_unknown_renames(self):
        cases = (
            (ShipConfig(app="main:app", services={"database": "postgres"}, service_names={"postgres": "app"}), "used by both"),
            (ShipConfig(app="main:app", service_names={"mysql": "db"}), "not \"app\" or a selected service"),
            (ShipConfig(app="main:app", services={"database": "postgres"}, processes={"postgres": "true"}), "conflicts with"),
            (ShipConfig(app="main:app", services={"cache": "postgres"}), "belongs to the 'database' group"),
            (ShipConfig(app="main:app", services={"database": "nope"}), "Unknown service 'nope'"),
            (ShipConfig(app="main:app", additional_services=[AdditionalService("cache", "postgres", "x")]), "belongs to"),
        )
        for config, message in cases:
            with self.subTest(message=message), self.assertRaisesRegex(ConfigError, message):
                self.model(config)

    def test_external_network_attaches_the_app_and_processes(self):
        model = self.model(ShipConfig(app="main:app", external_network="shared_infra", processes={"worker": "python w.py"}), production=True)
        self.assertEqual(["default", "external_0"], model["services"]["app"]["networks"])
        self.assertEqual(["default", "external_0"], model["services"]["worker"]["networks"])
        self.assertEqual({"default": {}, "external_0": {"external": True, "name": "shared_infra"}}, model["networks"])

    def test_browser_testing_gives_the_app_a_dotted_alias_in_development_only(self):
        config = ShipConfig(app="main:app", services={"testing": "playwright"}, processes={"worker": "sleep 1"})
        development = self.model(config)
        self.assertEqual(["ship-app.test"], development["services"]["app"]["networks"]["default"]["aliases"])
        self.assertNotIn("worker", development["services"])
        self.assertNotIn("networks", self.model(config, production=True)["services"]["app"])

    def test_processes_share_the_app_image_and_escape_dollars(self):
        config = ShipConfig(app="main:app", processes={"worker": "python worker.py --queue $QUEUE", "beat": ["python", "beat.py", "$HOME"]})
        self.assertNotIn("worker", self.model(config)["services"])
        for production in (True,):
            services = self.model(config, production=production)["services"]
            for name in ("worker", "beat"):
                self.assertEqual(services["app"]["image"], services[name]["image"])
                for key in ("build", "ports", "healthcheck", "extra_hosts"):
                    self.assertNotIn(key, services[name])
                self.assertEqual("60s", services[name]["stop_grace_period"])
            self.assertEqual(["sh", "-c", "python worker.py --queue $$QUEUE"], services["worker"]["command"])
            self.assertEqual(["python", "beat.py", "$$HOME"], services["beat"]["command"])
            self.assertEqual([".:/app"] if not production else None, services["worker"].get("volumes"))
        self.assertTrue(self.model(config, production=True)["services"]["app"]["image"].endswith("-app:local"))

    def test_mutagen_replaces_the_bind_mount_and_host_user(self):
        with patch.dict(os.environ, {"SHIP_MUTAGEN": "1"}):
            app = self.model(ShipConfig(app="main:app"))["services"]["app"]
        self.assertNotIn("volumes", app)
        self.assertNotIn("user", app)

    def test_every_service_with_the_project_mount_is_synced(self):
        config = ShipConfig(app="main:app", services={"frontend": "node", "cache": "redis"}, processes={"worker": "python w.py"})
        with patch.dict(os.environ, {"SHIP_MUTAGEN": ""}):
            model = self.model(config)
        self.assertEqual(["app", "node"], sorted(model["x-ship"]["sync_services"]))
        self.assertEqual(["app"], model["x-ship"]["app_services"])
        self.assertIn(".:/app", model["services"]["node"]["volumes"])
        with patch.dict(os.environ, {"SHIP_MUTAGEN": "1"}):
            model = self.model(config)
        self.assertEqual(["app", "node"], sorted(model["x-ship"]["sync_services"]))
        self.assertEqual(["node_modules:/app/node_modules"], model["services"]["node"]["volumes"])
        self.assertNotIn("worker", model["services"])
        self.assertNotIn("sync_services", self.model(config, production=True)["x-ship"])

    def test_vite_watcher_polls_only_on_a_windows_bind_mount(self):
        config = ShipConfig(app="main:app", services={"frontend": "node"})
        for platform, mutagen, polls in (("linux", "", False), ("darwin", "", False), ("win32", "", True), ("win32", "1", False)):
            with self.subTest(platform=platform, mutagen=mutagen), patch("python_ship.services.sys.platform", platform), \
                    patch.dict(os.environ, {"SHIP_MUTAGEN": mutagen}):
                environment = self.model(config)["services"]["node"]["environment"]
            self.assertEqual(polls, environment.get("CHOKIDAR_USEPOLLING") == "true")


class ProductionModelTests(ProjectTestCase):
    def test_production_model(self):
        config = ShipConfig(app="main:app", services={"database": "postgres", "mail": "mailpit", "testing": "selenium",
                                                      "frontend": "node", "storage": "silo"})
        development = self.model(config)
        production = self.model(config, production=True)
        self.assertEqual(development["name"], production["name"])
        app = production["services"]["app"]
        self.assertEqual("production", app["build"]["target"])
        self.assertTrue(app["build"]["pull"])
        self.assertEqual([{"path": ".env.production", "required": False}], app["env_file"])
        for key in ("volumes", "ports", "user", "extra_hosts"):
            self.assertNotIn(key, app)
        self.assertEqual(["python", "-m", "uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers"],
                         app["command"])
        self.assertTrue(app["image"].endswith("-app:local"))
        for name in ("mailpit", "selenium", "node"):
            self.assertIn(name, development["services"])
            self.assertNotIn(name, production["services"])
        for variable in ("MAIL_HOST", "SELENIUM_REMOTE_URL", "APP_URL"):
            self.assertNotIn(variable, app["environment"])
        self.assertNotIn("ports", production["services"]["silo"])
        self.assertEqual("${DB_PASSWORD:?DB_PASSWORD is required in .env.production}", app["environment"]["DB_PASSWORD"])

    def test_publish_ports_is_explicit(self):
        app = self.model(ShipConfig(app="main:app", publish_ports=True), production=True)["services"]["app"]
        self.assertEqual(["${APP_PORT:-8000}:8000"], app["ports"])

    def test_release_commands_run_before_the_server_and_escape_dollars(self):
        (self.root / "manage.py").write_text("")
        (self.root / "project").mkdir()
        (self.root / "project" / "asgi.py").write_text("application = object()\n")
        config = ShipConfig("django", "project.asgi:application", django_collectstatic=True)
        command = self.model(config, production=True)["services"]["app"]["command"]
        self.assertEqual(["sh", "-c"], command[:2])
        self.assertEqual(
            "python manage.py check --deploy && python manage.py collectstatic --noinput && "
            "exec python -m uvicorn project.asgi:application --host 0.0.0.0 --port 8000 --proxy-headers",
            command[2],
        )
        self.assertNotIn("check", " ".join(self.model(config)["services"]["app"]["command"]))

    def test_host_bind_mounts_are_rejected_in_production(self):
        class Binder(ExtraService):
            def compose_fragment(self, context):
                return {context.service_name: {"image": "x", "volumes": ["./data:/data"]}}

        with registry_with(Binder()), self.assertRaisesRegex(ConfigError, "cannot use a host bind mount"):
            self.model(ShipConfig(app="main:app", services={"search": "extra"}), production=True)

    def test_project_built_services_get_a_local_tag_and_pull(self):
        garage = self.model(ShipConfig(app="main:app", services={"storage": "garage"}), production=True)["services"]["garage"]
        self.assertTrue(garage["image"].endswith("-garage:local"))
        self.assertTrue(garage["build"]["pull"])


class ProjectNameTests(ProjectTestCase):
    def test_names_are_sanitized_and_explicit_names_win(self):
        self.assertEqual("custom", project_name(ShipConfig(app="main:app", name="custom"), self.root))
        for directory, expected in (("My_Project", "my-project"), ("123", "ship-123"), ("--", "ship"), ("a-_b", "a-b")):
            with self.subTest(directory=directory):
                path = self.root / directory
                path.mkdir()
                self.assertEqual(expected, project_name(ShipConfig(app="main:app"), path))


class WrittenFileTests(ProjectTestCase):
    def test_files_are_json_with_a_yaml_comment_header(self):
        config = ShipConfig(app="main:app", services={"database": "postgres"})
        path = write_compose(config, self.root)
        self.assertEqual(compose_path(self.root), path)
        self.assertEqual("docker-compose.generated.yml", path.name)
        text = path.read_text()
        self.assertTrue(text.startswith("# Generated by python-ship"))
        self.assertEqual(self.model(config), json.loads(text.split("\n", 1)[1]))
        self.assertEqual("docker-compose.production.yml", write_compose(config, self.root, production=True).name)
        self.assertTrue(render({"services": {}}).endswith("}\n"))

    @unittest.skipUnless(DOCKER, "Docker CLI is required for `docker compose config`")
    def test_docker_compose_accepts_every_model(self):
        configs = (
            ShipConfig(app="main:app"),
            ShipConfig(app="main:app", services={"database": "mysql", "cache": "redis", "storage": "seaweedfs",
                                                 "search": "meilisearch", "mail": "mailpit", "testing": "playwright",
                                                 "frontend": "node"},
                       additional_services=[AdditionalService("database", "postgres", "analytics")],
                       service_names={"app": "web"}, external_network="shared", processes={"worker": "python w.py $X"},
                       publish_ports=True),
        )
        (self.root / ".env.production").write_text(
            "DB_PASSWORD=a\nDB_ROOT_PASSWORD=b\nAWS_SECRET_ACCESS_KEY=c\nMEILISEARCH_KEY=d\nANALYTICS_DB_PASSWORD=e\n")
        for config in configs:
            for production in (False, True):
                with self.subTest(production=production, services=sorted(config.services)):
                    path = write_compose(config, self.root, production=production)
                    command = ["docker", "compose", "--project-directory", str(self.root)]
                    if production:
                        command += ["--env-file", str(self.root / ".env.production")]
                    result = subprocess.run(command + ["-f", str(path), "config", "--quiet"], capture_output=True, text=True)
                    self.assertEqual(0, result.returncode, result.stderr)


if __name__ == "__main__":
    unittest.main()
