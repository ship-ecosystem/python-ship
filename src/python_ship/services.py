"""Built-in infrastructure services."""

from __future__ import annotations

import os
from pathlib import Path
import sys
from typing import Any

from .contracts import ServiceContext, ServiceDefinition
from .mutagen import enabled as mutagen_enabled
from .portable import BUILTIN_PORTABLE
from .production import read_env
from .storage import BUILTIN_STORAGE


def _healthcheck(test: list[str], **timing: Any) -> dict[str, Any]:
    return {"test": test, "interval": "5s", "timeout": "3s", "retries": 10, **timing}


def _host_value(root: Path, key: str) -> str | None:
    """A value Compose would interpolate: the host environment first, then the project's .env."""
    if key in os.environ:
        return os.environ[key]
    return read_env(root / ".env").get(key)


class PostgresService:
    key = "postgres"
    group = "database"
    label = "PostgreSQL"

    def compose_fragment(self, context: ServiceContext) -> dict[str, dict[str, Any]]:
        return {context.service_name: {
            "image": "postgres:18-alpine",
            "environment": {
                "POSTGRES_DB": context.setting("DB_DATABASE", "app"),
                "POSTGRES_USER": context.setting("DB_USERNAME", "app"),
                "POSTGRES_PASSWORD": context.credential("DB_PASSWORD", "ship"),
            },
            "volumes": [f"{context.resource_name}_data:/var/lib/postgresql"],
            "healthcheck": _healthcheck(["CMD-SHELL", 'pg_isready -U "$$POSTGRES_USER" -d "$$POSTGRES_DB"']),
        }}

    def app_environment(self, context: ServiceContext) -> dict[str, str]:
        prefix = context.env_prefix
        return {
            f"{prefix}DB_HOST": context.service_name,
            f"{prefix}DB_PORT": "5432",
            f"{prefix}DB_DATABASE": context.setting("DB_DATABASE", "app"),
            f"{prefix}DB_USERNAME": context.setting("DB_USERNAME", "app"),
            f"{prefix}DB_PASSWORD": context.credential("DB_PASSWORD", "ship"),
        }

    def database_shell(self, context: ServiceContext) -> list[str]:
        return ["sh", "-c", 'exec psql -U "$POSTGRES_USER" -d "$POSTGRES_DB"']


class MySqlService:
    key = "mysql"
    group = "database"
    label = "MySQL"

    def validate(self, context: ServiceContext) -> list[str]:
        variable = context.env_prefix + "DB_USERNAME"
        if (_host_value(context.project_root, variable) or "app").lower() == "root":
            return [
                f"{variable}=root is invalid: the MySQL image refuses to start with it. Use a separate "
                f"application user; the root account uses {context.env_prefix}DB_ROOT_PASSWORD."
            ]
        return []

    def compose_fragment(self, context: ServiceContext) -> dict[str, dict[str, Any]]:
        return {context.service_name: {
            "image": "mysql:9.7.4",
            "environment": {
                "MYSQL_DATABASE": context.setting("DB_DATABASE", "app"),
                "MYSQL_USER": context.setting("DB_USERNAME", "app"),
                "MYSQL_PASSWORD": context.credential("DB_PASSWORD", "ship"),
                "MYSQL_ROOT_PASSWORD": context.credential("DB_ROOT_PASSWORD", "rootship"),
            },
            "volumes": [f"{context.resource_name}_data:/var/lib/mysql"],
            # A socket ping succeeds against the image's temporary init-phase server, before TCP
            # connections are accepted, so ping over 127.0.0.1.
            "healthcheck": _healthcheck(
                ["CMD-SHELL", 'mysqladmin ping -h 127.0.0.1 -u"$$MYSQL_USER" -p"$$MYSQL_PASSWORD" --silent'],
                timeout="5s", retries=20, start_period="15s",
            ),
        }}

    def app_environment(self, context: ServiceContext) -> dict[str, str]:
        prefix = context.env_prefix
        return {
            f"{prefix}DB_HOST": context.service_name,
            f"{prefix}DB_PORT": "3306",
            f"{prefix}DB_DATABASE": context.setting("DB_DATABASE", "app"),
            f"{prefix}DB_USERNAME": context.setting("DB_USERNAME", "app"),
            f"{prefix}DB_PASSWORD": context.credential("DB_PASSWORD", "ship"),
        }

    def database_shell(self, context: ServiceContext) -> list[str]:
        return ["sh", "-c", 'exec mysql -u"$MYSQL_USER" -p"$MYSQL_PASSWORD" "$MYSQL_DATABASE"']


class RedisService:
    key = "redis"
    group = "cache"
    label = "Redis"

    def compose_fragment(self, context: ServiceContext) -> dict[str, dict[str, Any]]:
        # RDB snapshots plus an append-only log: losing sessions on a redeploy logs out every user.
        return {context.service_name: {
            "image": "redis:8.10.3-alpine3.23",
            "command": ["redis-server", "--save", "60", "1", "--appendonly", "yes"],
            "volumes": [f"{context.resource_name}_data:/data"],
            "healthcheck": _healthcheck(["CMD", "redis-cli", "ping"]),
        }}

    def app_environment(self, context: ServiceContext) -> dict[str, str]:
        return {f"{context.env_prefix}REDIS_URL": f"redis://{context.service_name}:6379/0",
                f"{context.env_prefix}REDIS_HOST": context.service_name, f"{context.env_prefix}REDIS_PORT": "6379"}


class MailpitService:
    key = "mailpit"
    group = "mail"
    label = "Mailpit"

    def compose_fragment(self, context: ServiceContext) -> dict[str, dict[str, Any]]:
        if context.production:
            return {}
        model: dict[str, Any] = {"image": "axllent/mailpit:v1.31.1"}
        # Only the default instance publishes its web UI, so named instances never collide on a host port.
        if context.instance_name is None:
            model["ports"] = ["127.0.0.1:${MAILPIT_WEB_PORT:-8025}:8025"]
        return {context.service_name: model}

    def app_environment(self, context: ServiceContext) -> dict[str, str]:
        prefix = context.env_prefix
        return {f"{prefix}MAIL_HOST": context.service_name, f"{prefix}MAIL_PORT": "1025"}


class MeilisearchService:
    key = "meilisearch"
    group = "search"
    label = "Meilisearch"

    def compose_fragment(self, context: ServiceContext) -> dict[str, dict[str, Any]]:
        return {context.service_name: {
            "image": "getmeili/meilisearch:v1.56.2",
            "environment": {
                "MEILI_MASTER_KEY": context.credential("MEILISEARCH_KEY", "shipsearchkey"),
                "MEILI_NO_ANALYTICS": "true",
                "MEILI_ENV": "production" if context.production else "development",
            },
            "volumes": [f"{context.resource_name}_data:/meili_data"],
            "healthcheck": _healthcheck(
                ["CMD-SHELL", "curl -fsS http://127.0.0.1:7700/health >/dev/null"], start_period="5s",
            ),
        }}

    def app_environment(self, context: ServiceContext) -> dict[str, str]:
        prefix = context.env_prefix
        return {
            f"{prefix}MEILISEARCH_URL": f"http://{context.service_name}:7700",
            f"{prefix}MEILISEARCH_KEY": context.credential("MEILISEARCH_KEY", "shipsearchkey"),
        }


class RabbitMQService:
    key = "rabbitmq"
    group = "broker"
    label = "RabbitMQ"

    def compose_fragment(self, context: ServiceContext) -> dict[str, dict[str, Any]]:
        model: dict[str, Any] = {
            "image": "rabbitmq:4.3.7-management-alpine",
            "environment": {
                "RABBITMQ_DEFAULT_USER": context.setting("RABBITMQ_USER", "app"),
                "RABBITMQ_DEFAULT_PASS": context.credential("RABBITMQ_PASSWORD", "ship"),
            },
            "volumes": [f"{context.resource_name}_data:/var/lib/rabbitmq"],
            "healthcheck": _healthcheck(["CMD", "rabbitmq-diagnostics", "-q", "ping"],
                                        interval="10s", timeout="10s", retries=12, start_period="20s"),
        }
        if context.environment.is_development and context.instance_name is None:
            model["ports"] = ["127.0.0.1:${RABBITMQ_MANAGEMENT_PORT:-15672}:15672"]
        return {context.service_name: model}

    def app_environment(self, context: ServiceContext) -> dict[str, str]:
        user = context.setting("RABBITMQ_USER", "app")
        password = context.credential("RABBITMQ_PASSWORD", "ship")
        return {f"{context.env_prefix}AMQP_URL": f"amqp://{user}:{password}@{context.service_name}:5672//"}


class CeleryService:
    """Celery workers from the app's own image, with a broker and a result backend wired in.

    The broker is RabbitMQ when selected, otherwise Redis. Results go to Redis when it is
    selected (chords and other Canvas primitives need a real result backend), else to RPC.
    """

    key = "celery"
    group = "tasks"
    label = "Celery worker"

    def _redis(self, context: ServiceContext) -> str | None:
        cache = context.config.services.get("cache")
        if cache in ("redis", "valkey"):
            return context.config.service_names.get(cache, cache)
        return None

    def _rabbitmq(self, context: ServiceContext) -> str | None:
        if context.config.services.get("broker") == "rabbitmq":
            return context.config.service_names.get("rabbitmq", "rabbitmq")
        return None

    def validate(self, context: ServiceContext) -> list[str]:
        if self._redis(context) is None and self._rabbitmq(context) is None:
            return ["Celery needs a broker: select RabbitMQ (`ship init --broker rabbitmq`) or Redis (`ship init --cache redis`)."]
        return []

    def _celery_app(self, context: ServiceContext) -> str:
        # An explicitly configured Celery target takes precedence; do not import application code.
        settings = read_env(context.project_root / (".env.production" if context.production else ".env"))
        if settings.get("CELERY_APP"):
            return settings["CELERY_APP"]
        for path in ("tasks.py", "celery_app.py"):
            if (context.project_root / path).is_file() and "Celery(" in (context.project_root / path).read_text(encoding="utf-8"):
                return path.removesuffix(".py")
        module = context.config.app.partition(":")[0]
        if context.config.framework == "django":
            # project.asgi -> project, where Django projects define their Celery app.
            return module.rsplit(".", 1)[0] if "." in module else module
        return module

    def compose_fragment(self, context: ServiceContext) -> dict[str, dict[str, Any]]:
        # $$ reaches the container as $, so the shell expands the variables at start.
        arguments = "-A $${CELERY_APP} worker --loglevel=$${CELERY_LOG_LEVEL:-info}"
        worker = f"exec celery {arguments}"
        if context.environment.is_development:
            # Restart the worker on code changes when watchfiles is installed. It runs Celery's own
            # entry point, because watchfiles doesn't expand variables in a command string.
            worker = (f"if python -c 'import watchfiles' 2>/dev/null; then exec watchfiles --filter python "
                      f"--args \"{arguments}\" celery.__main__.main /app; else {worker}; fi")
        return {context.service_name: context.app_process(["sh", "-c", worker], healthcheck={
            "test": ["CMD-SHELL", 'celery -A "$$CELERY_APP" inspect ping -d "celery@$$HOSTNAME" --timeout=3'],
            "interval": "15s", "timeout": "5s", "retries": 5, "start_period": "30s"})}

    def app_environment(self, context: ServiceContext) -> dict[str, str]:
        redis = self._redis(context)
        rabbitmq = self._rabbitmq(context)
        if rabbitmq is not None:
            user = context.setting("RABBITMQ_USER", "app")
            password = context.credential("RABBITMQ_PASSWORD", "ship")
            broker = f"amqp://{user}:{password}@{rabbitmq}:5672//"
        else:
            broker = f"redis://{redis}:6379/1"
        return {
            "CELERY_APP": context.setting("CELERY_APP", self._celery_app(context)),
            "CELERY_BROKER_URL": broker,
            "CELERY_RESULT_BACKEND": f"redis://{redis}:6379/2" if redis else "rpc://",
        }


# Browsers force a single-label host such as "app" to HTTPS, so the app also gets this
# network alias in development whenever a browser-testing service is selected.
BROWSER_APP_HOST = "ship-app.test"


class SeleniumService:
    key = "selenium"
    group = "testing"
    label = "Selenium (Chrome)"

    def compose_fragment(self, context: ServiceContext) -> dict[str, dict[str, Any]]:
        if context.production:
            return {}
        return {context.service_name: {
            "image": "selenium/standalone-chrome:4.52.0-20261101",
            "shm_size": "2gb",
            "healthcheck": _healthcheck(
                ["CMD-SHELL", "/opt/bin/check-grid.sh --host 127.0.0.1 --port 4444"],
                interval="10s", timeout="10s", retries=12, start_period="20s",
            ),
        }}

    def app_environment(self, context: ServiceContext) -> dict[str, str]:
        return {
            "SELENIUM_REMOTE_URL": f"http://{context.service_name}:4444/wd/hub",
            "APP_URL": f"http://{BROWSER_APP_HOST}:8000",
        }


class PlaywrightService:
    key = "playwright"
    group = "testing"
    label = "Playwright (browser server)"
    version = "1.64.1"

    def compose_fragment(self, context: ServiceContext) -> dict[str, dict[str, Any]]:
        if context.production:
            return {}
        probe = (
            "const s=require('net').connect(3000,'127.0.0.1');"
            "s.on('connect',()=>{s.end();process.exit(0)});s.on('error',()=>process.exit(1))"
        )
        return {context.service_name: {
            "image": f"mcr.microsoft.com/playwright:v{self.version}-noble",
            "init": True,
            "user": "pwuser",
            "working_dir": "/home/pwuser",
            "shm_size": "2gb",
            "command": ["/bin/sh", "-c", f"npx -y playwright@{self.version} run-server --port 3000 --host 0.0.0.0"],
            "healthcheck": _healthcheck(
                ["CMD", "node", "-e", probe], interval="10s", timeout="5s", retries=12, start_period="30s",
            ),
        }}

    def app_environment(self, context: ServiceContext) -> dict[str, str]:
        return {
            "PLAYWRIGHT_WS_URL": f"ws://{context.service_name}:3000/",
            "APP_URL": f"http://{BROWSER_APP_HOST}:8000",
        }


class NodeService:
    key = "node"
    group = "frontend"
    label = "Node.js (npm, Vite HMR)"

    def compose_fragment(self, context: ServiceContext) -> dict[str, dict[str, Any]]:
        if context.production:
            return {}
        environment = {"VITE_PORT": "${VITE_PORT:-5173}"}
        if sys.platform == "win32" and not mutagen_enabled():
            # Docker Desktop doesn't forward file events from Windows folders into containers,
            # so Vite's watcher (chokidar) polls; under Mutagen the files are local and events work.
            environment["CHOKIDAR_USEPOLLING"] = "true"
        return {context.service_name: {
            "image": "node:24.18.0-alpine3.24",
            "init": True,
            "working_dir": "/app",
            "volumes": [".:/app", "node_modules:/app/node_modules"],
            "command": ["sleep", "infinity"],
            "ports": ["127.0.0.1:${VITE_PORT:-5173}:${VITE_PORT:-5173}"],
            "environment": environment,
        }}

    def app_environment(self, context: ServiceContext) -> dict[str, str]:
        return {}


_BUILTINS: list[ServiceDefinition] = [
    PostgresService(), MySqlService(), RedisService(), RabbitMQService(), CeleryService(), MailpitService(),
    MeilisearchService(), SeleniumService(), PlaywrightService(), NodeService(), *BUILTIN_STORAGE, *BUILTIN_PORTABLE,
]
BUILTIN_SERVICES: dict[str, ServiceDefinition] = {service.key: service for service in _BUILTINS}
