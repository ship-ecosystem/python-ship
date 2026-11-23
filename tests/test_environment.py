"""Environment merging, setup automation, cancellation and wizard replay."""
from unittest.mock import patch

from python_ship.config import ShipConfig
from python_ship.environment import ensure_environment
from python_ship.wizard import Wizard
from tests.helpers import ProjectTestCase, captured, no_uv_prompt


class EnvironmentTests(ProjectTestCase):
    def test_preserves_values_comments_and_appends_empty_production_credentials(self):
        (self.root / ".env").write_text("# local choice\nDB_PASSWORD=custom-password\n# DB_HOST=old\n")
        (self.root / ".env.production").write_text("# production\nDB_PASSWORD=production-password\n")
        config = ShipConfig(app="main:app", services={"database": "mysql"})
        ensure_environment(config, self.root)
        development = (self.root / ".env").read_text()
        self.assertTrue(development.startswith("# local choice\nDB_PASSWORD=custom-password\n# DB_HOST=old\n"))
        self.assertIn('DB_HOST="mysql"', development)
        production = (self.root / ".env.production").read_text()
        self.assertIn("DB_ROOT_PASSWORD=\n", production)
        self.assertNotIn("rootship", production)
        ensure_environment(config, self.root)
        self.assertEqual(development, (self.root / ".env").read_text())

    def test_automation_covers_instances_http_processes_and_networks(self):
        self.assertEqual(0, self.init("--name", "example", "--additional-service", "database=mongodb:analytics",
            "--process", 'socket={"command":["python","socket.py"],"http":{"port":8081,"role":"websocket"}}',
            "--network", "proxy", "--network", "metrics", "--deploy-command", "python migrate.py"), self.init_output.stderr)
        config = ShipConfig.read(self.root)
        self.assertEqual("example", config.name)
        self.assertEqual(["proxy", "metrics"], config.external_networks)
        self.assertEqual("analytics", config.additional_services[0].name)
        self.assertEqual(0, self.init("--remove-instance", "analytics", "--remove-process", "socket", "--no-network"))
        config = ShipConfig.read(self.root)
        self.assertEqual([], config.additional_services)
        self.assertEqual({}, config.processes)
        self.assertEqual([], config.external_networks)

    def test_cancellation_writes_nothing(self):
        from python_ship.config import ConfigError
        from python_ship.init import InitOptions, _run_init
        with patch("python_ship.init.resolve_config", return_value=ShipConfig(name="example", app="main:app")), \
                patch("python_ship.init.wizard.confirm", return_value=False), no_uv_prompt(), captured(), \
                self.assertRaises(ConfigError):
            _run_init(self.root, InitOptions(interactive=True))
        self.assertEqual(["main.py"], [path.name for path in self.root.iterdir()])

    def test_wizard_rewinds_and_replays_only_the_unchanged_prefix(self):
        session = Wizard(False)
        with patch("python_ship.wizard.typer.prompt", side_effect=["first", "second", "changed"]):
            self.assertEqual("first", session.ask("text", "A"))
            self.assertEqual("second", session.ask("text", "B"))
            session.rewind()
            self.assertEqual("first", session.ask("text", "A"))
            self.assertEqual("changed", session.ask("text", "B"))
