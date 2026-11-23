"""Language-independent PHP providers adapted to Python's explicit service context."""
from copy import deepcopy
import re
from typing import Any

from .contracts import ServiceContext, ServiceDefinition
from .portable_data import PORTABLE_SERVICES
from .production import read_env


class PortableService:
    def __init__(self, key: str):
        self.key = key
        self.data = PORTABLE_SERVICES[key]
        self.label = self.data["label"]
        self.group = self.data["group"]

    def _replace(self, value: Any, context: ServiceContext) -> Any:
        original = next(iter(self.data["dev"]))
        if isinstance(value, str):
            # Resource identities never follow Compose host renames.
            value = re.sub(r"ship-" + re.escape(original) + r"-([a-z-]+)(?=[:\"\s]|$)",
                           lambda m: context.resource_name + "_" + m[1].replace("-", "_"), value)
            return value.replace("SHIP_PLACEHOLDER_", context.env_prefix).replace(original, context.service_name)
        if isinstance(value, list):
            return [self._replace(item, context) for item in value]
        if isinstance(value, dict):
            return {self._replace(key, context): self._replace(item, context) for key, item in value.items()}
        return value

    def compose_fragment(self, context: ServiceContext) -> dict[str, dict[str, Any]]:
        raw = self.data["prod" if context.production else "dev"]
        if not raw:
            return {}
        fragment = self._replace(deepcopy(raw), context)
        model = fragment[context.service_name]
        if self.key == "centrifugo":
            cache = context.config.services.get("cache")
            if cache in ("redis", "valkey"):
                hostname = context.config.service_names.get(cache, cache)
                model["environment"]["CENTRIFUGO_ENGINE_REDIS_ADDRESS"] = context.setting("CENTRIFUGO_REDIS_ADDRESS", f"redis://{hostname}:6379")
        if self.key == "cypress":
            model.setdefault("environment", {})["CYPRESS_BASE_URL"] = "http://ship-app.test:8000"
            model["profiles"] = ["ship-testing"]
            model["volumes"] = [".:/e2e", "cypress_modules:/e2e/node_modules"]
        return fragment

    def app_environment(self, context: ServiceContext) -> dict[str, str]:
        variables = self._replace(self.data["env"], context)
        # Framework defaults belong to adapters, not portable infrastructure.
        variables = {key: value for key, value in variables.items()
                     if key.removeprefix(context.env_prefix) not in {"DB_CONNECTION", "CACHE_STORE", "SESSION_DRIVER", "SCOUT_DRIVER", "DB_URL"}}
        if self.key == "mongodb":
            variables.pop(context.env_prefix + "MONGODB_URI", None)
            variables.update({context.env_prefix + "MONGODB_HOST": context.service_name,
                              context.env_prefix + "MONGODB_USERNAME": context.setting("MONGODB_USERNAME", "app"),
                              context.env_prefix + "MONGODB_PASSWORD": context.credential("MONGODB_PASSWORD", "secret")})
        if self.key == "cypress":
            return {"CYPRESS_BASE_URL": "http://ship-app.test:8000"}
        if context.production:
            # App and server must use the same required credential expressions.
            fragment = self.compose_fragment(context)
            required = re.findall(r"\$\{([A-Za-z_][A-Za-z0-9_]*):\?[^}]*\}", str(fragment))
            for variable in required:
                variables = {key: re.sub(r"\$\{" + re.escape(variable) + r":-[^}]*\}",
                    "${" + variable + ":?set a real value in .env.production}", value) for key, value in variables.items()}
        return variables

    def validate(self, context: ServiceContext) -> list[str]:
        import os
        values = {**read_env(context.project_root / (".env.production" if context.production else ".env")), **os.environ}
        variable = context.env_prefix + "DB_USERNAME"
        forbidden = {"mariadb": "root", "sqlserver": "sa"}.get(self.key)
        if forbidden and values.get(variable, "app").lower() == forbidden:
            return [f"{variable} cannot be {forbidden}; use a separate application user."]
        return []

    def http_endpoints(self, context: ServiceContext) -> list[dict[str, Any]]:
        if self.key == "centrifugo":
            return [{"port": 8000, "role": "websocket", "health": "/health", "urlSetting": "CENTRIFUGO_PUBLIC_URL"}]
        return []


class PortableDatabase(PortableService):
    def database_shell(self, context: ServiceContext) -> list[str]:
        return self._replace(self.data["shell"], context)


BUILTIN_PORTABLE: list[ServiceDefinition] = [PortableDatabase(key) if data["shell"] else PortableService(key)
                    for key, data in PORTABLE_SERVICES.items()]
