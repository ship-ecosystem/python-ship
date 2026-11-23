"""Portable PHP catalog parity, credential separation and named resource identities."""
from python_ship.config import AdditionalService, ShipConfig
from python_ship.contracts import ServiceContext, ShipEnvironment
from python_ship.services import BUILTIN_SERVICES
from tests.helpers import ProjectTestCase


class PortableCatalogTests(ProjectTestCase):
    def test_complete_catalog_without_flower(self):
        self.assertEqual(23, len(BUILTIN_SERVICES))
        self.assertNotIn("flower", BUILTIN_SERVICES)
        for key in ("mariadb", "mongodb", "sqlserver", "valkey", "memcached", "elasticsearch", "typesense", "centrifugo", "cypress"):
            service = BUILTIN_SERVICES[key]
            config = ShipConfig(app="main:app", services={service.group: key})
            for production in (False, True):
                model = self.model(config, production=production)
                if key == "cypress" and production:
                    self.assertNotIn(key, model["services"])
                else:
                    self.assertIn(key, model["services"])
                    if key != "cypress":
                        self.assertIn("healthcheck", model["services"][key])
                self.assertNotIn("SHIP_PLACEHOLDER", str(model))

    def test_named_database_hosts_credentials_and_volumes_survive_renames(self):
        for key in ("mariadb", "mongodb", "sqlserver"):
            config = ShipConfig(app="main:app", additional_services=[AdditionalService("database", key, "analytics")],
                                service_names={key + "-analytics": "reports"})
            model = self.model(config, production=True)
            database = model["services"]["reports"]
            self.assertTrue(database["volumes"][0].startswith(key + "-analytics_data:"))
            env = model["services"]["app"]["environment"]
            self.assertEqual("reports", env.get("ANALYTICS_DB_HOST", env.get("ANALYTICS_MONGODB_HOST")))
            self.assertNotIn("ROOT_PASSWORD", str(env.keys()))
            self.assertIn("ANALYTICS_", str(database["environment"]))
            service = BUILTIN_SERVICES[key]
            context = ServiceContext(ShipEnvironment.PRODUCTION, config, self.root, key, "analytics", "reports")
            self.assertTrue(service.database_shell(context))

    def test_centrifugo_redis_follows_cache_rename(self):
        config = ShipConfig(app="main:app", services={"cache": "valkey", "broadcasting": "centrifugo"},
                            service_names={"valkey": "kv"})
        model = self.model(config)
        env = model["services"]["centrifugo"]["environment"]
        self.assertIn("redis://kv:6379", str(env))

    def test_cypress_is_on_demand_and_uses_browser_alias(self):
        model = self.model(ShipConfig(app="main:app", services={"testing": "cypress"}))
        self.assertEqual(["ship-testing"], model["services"]["cypress"]["profiles"])
        self.assertEqual("http://ship-app.test:8000", model["services"]["cypress"]["environment"]["CYPRESS_BASE_URL"])
        self.assertNotIn("cypress", model["services"]["app"].get("depends_on", {}))
