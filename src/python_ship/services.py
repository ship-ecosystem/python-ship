"""Built-in infrastructure services."""

import os
from pathlib import Path

from .storage import BUILTIN_STORAGE


def _value(name: str, default: str, production: bool = False) -> str:
    return f"${{{name}:?{name} is required}}" if production else f"${{{name}:-{default}}}"


def _prefix(instance: dict) -> str:
    return instance["prefix"] + "_" if instance["prefix"] else ""


def _env_value(root: Path, key: str) -> str | None:
    if key in os.environ:
        return os.environ[key]
    path = root / ".env"
    if path.is_file():
        for line in path.read_text(encoding="utf-8").splitlines():
            name, marker, value = line.partition("=")
            if marker and name.strip().removeprefix("export ").strip() == key:
                return value.strip().strip("\"'")
    return None


class PostgresService:
    key = "postgres"
    group = "database"
    label = "PostgreSQL"

    def compose_fragment(self) -> dict:
        return {
            "postgres": {
                "image": "postgres:17.12-alpine3.24",
                "environment": {
                    "POSTGRES_DB": "${DB_NAME:-app}",
                    "POSTGRES_USER": "${DB_USER:-app}",
                    "POSTGRES_PASSWORD": "${DB_PASSWORD:-ship}",
                },
                "volumes": ["postgres_data:/var/lib/postgresql/data"],
                "healthcheck": {
                    "test": ["CMD-SHELL", "pg_isready -U \"$$POSTGRES_USER\" -d \"$$POSTGRES_DB\""],
                    "interval": "5s",
                    "timeout": "3s",
                    "retries": 10,
                },
            }
        }

    def app_environment(self) -> dict[str, str]:
        return {
            "DB_HOST": "postgres",
            "DB_PORT": "5432",
            "DB_NAME": "${DB_NAME:-app}",
            "DB_USER": "${DB_USER:-app}",
            "DB_PASSWORD": "${DB_PASSWORD:-ship}",
        }

    def database_shell(self, config, instance: dict[str, str]) -> list[str]:
        return ["sh", "-c", 'exec psql -U "$POSTGRES_USER" -d "$POSTGRES_DB"']


class RedisService:
    key = "redis"
    group = "cache"
    label = "Redis"

    def compose_fragment(self) -> dict:
        return {
            "redis": {
                "image": "redis:8.10.3-alpine3.23",
                "command": ["redis-server", "--save", "60", "1", "--appendonly", "yes"],
                "volumes": ["redis_data:/data"],
                "healthcheck": {
                    "test": ["CMD", "redis-cli", "ping"],
                    "interval": "5s",
                    "timeout": "3s",
                    "retries": 10,
                },
            }
        }

    def app_environment(self) -> dict[str, str]:
        return {"REDIS_URL": "redis://redis:6379/0"}


class MySqlService:
    key = "mysql"
    group = "database"
    label = "MySQL"

    def validate(self, config, instance: dict, root: Path) -> list[str]:
        variable = _prefix(instance) + "DB_USER"
        if (_env_value(root, variable) or "app").lower() == "root":
            return [f"{variable}=root is invalid: use a separate application user; MySQL root uses DB_ROOT_PASSWORD."]
        return []

    def compose_fragment(self) -> dict:
        return self.compose_fragment_for(None, {"name": "mysql", "prefix": "", "_production": False})

    def compose_fragment_for(self, config, instance: dict) -> dict:
        name, prefix = instance["name"], _prefix(instance)
        production = instance.get("_production", False)
        return {name: {
            "image": "mysql:9.7.4",
            "environment": {
                "MYSQL_DATABASE": _value(prefix + "DB_NAME", "app"),
                "MYSQL_USER": _value(prefix + "DB_USER", "app"),
                "MYSQL_PASSWORD": _value(prefix + "DB_PASSWORD", "ship", production),
                "MYSQL_ROOT_PASSWORD": _value(prefix + "DB_ROOT_PASSWORD", "rootship", production),
            },
            "volumes": [f"{name}_data:/var/lib/mysql"],
            "healthcheck": {
                "test": ["CMD-SHELL", 'mysqladmin ping -h 127.0.0.1 -u"$$MYSQL_USER" -p"$$MYSQL_PASSWORD" --silent'],
                "interval": "5s", "timeout": "5s", "retries": 20, "start_period": "15s",
            },
        }}

    def app_environment(self) -> dict[str, str]:
        return self.app_environment_for(None, {"name": "mysql", "prefix": "", "_production": False})

    def app_environment_for(self, config, instance: dict) -> dict[str, str]:
        name, prefix = instance["name"], _prefix(instance)
        production = instance.get("_production", False)
        return {
            "DB_HOST": name, "DB_PORT": "3306", "DB_NAME": _value(prefix + "DB_NAME", "app"),
            "DB_USER": _value(prefix + "DB_USER", "app"),
            "DB_PASSWORD": _value(prefix + "DB_PASSWORD", "ship", production),
        }

    def database_shell(self, config, instance: dict) -> list[str]:
        return ["sh", "-c", 'exec mysql -u"$MYSQL_USER" -p"$MYSQL_PASSWORD" "$MYSQL_DATABASE"']


class MailpitService:
    key = "mailpit"
    group = "mail"
    label = "Mailpit"
    development_only = True

    def compose_fragment(self) -> dict:
        return self.compose_fragment_for(None, {"name": "mailpit", "prefix": ""})

    def compose_fragment_for(self, config, instance: dict) -> dict:
        name = instance["name"]
        model = {"image": "axllent/mailpit:v1.31.1"}
        if not instance["prefix"]:
            model["ports"] = ["127.0.0.1:${MAILPIT_WEB_PORT:-8025}:8025"]
        return {name: model}

    def app_environment(self) -> dict[str, str]:
        return self.app_environment_for(None, {"name": "mailpit", "prefix": ""})

    def app_environment_for(self, config, instance: dict) -> dict[str, str]:
        return {"MAIL_HOST": instance["name"], "MAIL_PORT": "1025"}


class MeilisearchService:
    key = "meilisearch"
    group = "search"
    label = "Meilisearch"

    def compose_fragment(self) -> dict:
        return self.compose_fragment_for(None, {"name": "meilisearch", "prefix": "", "_production": False})

    def compose_fragment_for(self, config, instance: dict) -> dict:
        name, prefix = instance["name"], _prefix(instance)
        return {name: {
            "image": "getmeili/meilisearch:v1.56.2",
            "environment": {
                "MEILI_MASTER_KEY": _value(prefix + "MEILISEARCH_KEY", "shipsearchkey", instance.get("_production", False)),
                "MEILI_NO_ANALYTICS": "true",
            },
            "volumes": [f"{name}_data:/meili_data"],
        }}

    def app_environment(self) -> dict[str, str]:
        return self.app_environment_for(None, {"name": "meilisearch", "prefix": "", "_production": False})

    def app_environment_for(self, config, instance: dict) -> dict[str, str]:
        prefix = _prefix(instance)
        return {
            "MEILISEARCH_URL": f"http://{instance['name']}:7700",
            "MEILISEARCH_KEY": _value(prefix + "MEILISEARCH_KEY", "shipsearchkey", instance.get("_production", False)),
        }


class SeleniumService:
    key = "selenium"
    group = "testing"
    label = "Selenium Chrome"
    development_only = True

    def compose_fragment(self) -> dict:
        return {"selenium": {
            "image": "selenium/standalone-chrome:4.52.0-20261101",
            "shm_size": "2gb",
            "healthcheck": {"test": ["CMD-SHELL", "/opt/bin/check-grid.sh --host 127.0.0.1 --port 4444"],
                            "interval": "10s", "timeout": "10s", "retries": 12, "start_period": "20s"},
        }}

    def app_environment(self) -> dict[str, str]:
        return {"SELENIUM_REMOTE_URL": "http://selenium:4444/wd/hub", "APP_URL": "http://ship-app.test:8000"}


class PlaywrightService:
    key = "playwright"
    group = "testing"
    label = "Playwright browsers"
    development_only = True
    version = "1.64.1"

    def compose_fragment(self) -> dict:
        return self.compose_fragment_for(None, {"name": self.key, "prefix": ""})

    def compose_fragment_for(self, config, instance: dict) -> dict:
        name = instance["name"]
        return {name: {
            "image": f"mcr.microsoft.com/playwright:v{self.version}-noble",
            "init": True,
            "user": "pwuser",
            "working_dir": "/home/pwuser",
            "shm_size": "2gb",
            "command": ["/bin/sh", "-c", f"npx -y playwright@{self.version} run-server --port 3000 --host 0.0.0.0"],
            "healthcheck": {
                "test": ["CMD", "node", "-e", "const s=require('net').connect(3000,'127.0.0.1'); s.on('connect',()=>{s.end();process.exit(0)}); s.on('error',()=>process.exit(1))"],
                "interval": "10s", "timeout": "5s", "retries": 12, "start_period": "30s",
            },
        }}

    def app_environment(self) -> dict[str, str]:
        return self.app_environment_for(None, {"name": self.key, "prefix": ""})

    def app_environment_for(self, config, instance: dict) -> dict[str, str]:
        return {"PLAYWRIGHT_WS_URL": f"ws://{instance['name']}:3000/", "APP_URL": "http://ship-app.test:8000"}


class NodeService:
    key = "node"
    group = "frontend"
    label = "Node.js"
    development_only = True

    def compose_fragment(self) -> dict:
        return {"node": {
            "image": "node:24.18.0-alpine3.24",
            "working_dir": "/app", "volumes": [".:/app", "node_modules:/app/node_modules"],
            "command": ["sleep", "infinity"],
            "ports": ["127.0.0.1:${SHIP_HMR_PORT:-5173}:5173"],
        }}

    def app_environment(self) -> dict[str, str]:
        return {}


BUILTIN_SERVICES = {service.key: service for service in (
    PostgresService(), MySqlService(), RedisService(), MailpitService(),
    MeilisearchService(), SeleniumService(), PlaywrightService(), NodeService(),
)}
BUILTIN_SERVICES.update(BUILTIN_STORAGE)
