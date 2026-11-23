"""Project configuration stored in ship.json.

Field names follow PHP Ship's ship.json so a team using both reads one format:
``services``, ``additionalServices``, ``serviceNames``, ``externalNetwork``,
``publishPorts``, ``deployCommands``, ``processes``, ``siloImage``,
``hostUser``, ``name`` and ``extensions`` mean the same thing in both. The
Python-specific fields are ``framework``, ``app``, ``python``, ``server``,
``nodeAssets`` and ``djangoCollectstatic``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import json
from pathlib import Path
import re
import shlex
from typing import Any

from .security import DEFAULT_PYTHON, problem_with

# Alternative spellings accepted in ship.json, normalized on read. PHP Ship and Python Ship
# both accept "pgsql" and "postgres" for PostgreSQL, and both write "postgres".
SERVICE_ALIASES = {"pgsql": "postgres"}
SCHEMA_URL = "https://raw.githubusercontent.com/ship-ecosystem/spec/main/schemas/ship.schema.json"


def canonical_service(key: str) -> str:
    return SERVICE_ALIASES.get(key, key)


class ConfigError(ValueError):
    """A project configuration is missing or invalid."""


SERVICE_NAME = re.compile(r"[a-z][a-z0-9]*(?:[-_][a-z0-9]+)*")
INSTANCE_NAME = re.compile(r"[a-z][a-z0-9_]*")
NETWORK_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]*")
SILO_IMAGES = ("standard", "distroless")
# Groups where a second, named instance is a coherent idea. A second app runtime,
# frontend toolchain or browser driver is not.
GROUPS_SUPPORTING_ADDITIONAL_INSTANCES = ("database", "cache", "storage", "search", "mail")
# Ship's own commands; a process or a framework console command can't take these names.
RESERVED_COMMANDS = frozenset({
    "init", "up", "down", "exec", "shell", "logs", "db", "config:test", "build", "release",
    "npm", "pnpm", "yarn", "package-manager", "test", "uv", "doctor",
})

FIELDS = (
    "$schema", "framework", "app", "python", "server", "services", "additionalServices", "serviceNames",
    "externalNetwork", "extensions", "name", "siloImage", "publishPorts", "hostUser",
    "processes", "deployCommands", "nodeAssets", "djangoCollectstatic", "node", "build",
)

Command = str | list[str]
Process = Command | dict[str, Any]


def command_argv(command: Command) -> list[str]:
    """A process or deploy command as an argument list; a string runs through ``sh -c``."""
    return ["sh", "-c", command] if isinstance(command, str) else list(command)


def command_text(command: Command) -> str:
    return command if isinstance(command, str) else shlex.join(command)


@dataclass(frozen=True)
class AdditionalService:
    group: str
    service: str
    name: str

    def as_json(self) -> dict[str, str]:
        return {"group": self.group, "service": self.service, "name": self.name}


@dataclass(frozen=True)
class ShipConfig:
    framework: str = "generic"
    app: str = "main:app"
    python: str = DEFAULT_PYTHON
    server: str | None = None
    node: str = "24"
    build: dict[str, Any] = field(default_factory=dict)
    services: dict[str, str] = field(default_factory=dict)
    additional_services: list[AdditionalService] = field(default_factory=list)
    service_names: dict[str, str] = field(default_factory=dict)
    external_network: str | list[str] | None = None
    extensions: list[str] = field(default_factory=list)
    name: str | None = None
    silo_image: str = "standard"
    publish_ports: bool = False
    host_user: bool = True
    processes: dict[str, Process] = field(default_factory=dict)
    deploy_commands: list[Command] = field(default_factory=list)
    node_assets: bool = False
    django_collectstatic: bool = False

    @property
    def external_networks(self) -> list[str]:
        value = self.external_network
        return list(dict.fromkeys([value] if isinstance(value, str) else value or []))

    @property
    def app_service(self) -> str:
        """The app's Compose service name, after any ``serviceNames`` rename."""
        return self.service_names.get("app", "app")

    @classmethod
    def read(cls, project_root: Path) -> ShipConfig:
        config, problems = cls.inspect(project_root)
        if problems:
            raise ConfigError(" ".join(problems))
        assert config is not None
        return config

    @classmethod
    def inspect(cls, project_root: Path) -> tuple[ShipConfig | None, list[str]]:
        """Read ship.json, collecting every independent problem instead of stopping at the first."""
        data, problems = _load(project_root / "ship.json")
        if data is None:
            return None, problems
        config, problems = cls.from_data(data)
        return config, problems

    @classmethod
    def lenient(cls, project_root: Path) -> ShipConfig | None:
        """The readable fields of an existing ship.json, so one bad field doesn't discard the rest.

        ``ship init`` uses this to carry over everything it doesn't prompt for.
        """
        data, _ = _load(project_root / "ship.json")
        if data is None:
            return None
        values: dict[str, Any] = {}
        for key in FIELDS:
            if key not in data or key == "$schema":
                continue
            # djangoCollectstatic is only valid together with its framework.
            probe = {"app": "main:app", "framework": "django"} if key == "djangoCollectstatic" else {"app": "main:app"}
            config, problems = cls.from_data({**probe, key: data[key]})
            if not problems and config is not None:
                attribute = _ATTRIBUTES[key]
                values[attribute] = getattr(config, attribute)
        return cls(**values)

    @classmethod
    def from_data(cls, data: dict[str, Any]) -> tuple[ShipConfig | None, list[str]]:
        problems: list[str] = []
        unknown = sorted(set(data) - set(FIELDS))
        if unknown:
            problems.append(f"ship.json has unknown fields: {', '.join(unknown)}.")

        framework = data.get("framework", "generic")
        if not isinstance(framework, str) or not SERVICE_NAME.fullmatch(framework):
            problems.append("ship.json \"framework\" must be an adapter key such as \"django\".")
        app = data.get("app")
        if not isinstance(app, str) or not app:
            problems.append("ship.json requires \"app\", an ASGI target such as \"main:app\".")
        python = data.get("python", DEFAULT_PYTHON)
        if not isinstance(python, str):
            problems.append("ship.json \"python\" must be a string such as \"3.13\".")
        else:
            python_problem = problem_with(python)
            if python_problem:
                problems.append(f"ship.json \"python\": {python_problem}")
        server = data.get("server")
        if server is not None and (not isinstance(server, str) or not SERVICE_NAME.fullmatch(server)):
            problems.append("ship.json \"server\" must be a web server key such as \"uvicorn\" or \"gunicorn\".")

        from .build_environment import validate_build
        build = data.get("build", {})
        problems.extend(validate_build(build))
        node = data.get("node", "24")
        if node not in ("22", "24", "26"):
            problems.append('ship.json "node" must select a supported branch: 22, 24 or 26.')
        services = data.get("services", {})
        if not isinstance(services, dict) or any(
            not isinstance(group, str) or not isinstance(key, str) or not key for group, key in services.items()
        ):
            problems.append("ship.json \"services\" must map each group to one service key.")
            services = {}

        additional: list[AdditionalService] = []
        raw_additional = data.get("additionalServices", [])
        if not isinstance(raw_additional, list):
            problems.append("ship.json \"additionalServices\" must be a list of {group, service, name} objects.")
        else:
            seen: set[str] = set()
            for index, entry in enumerate(raw_additional):
                where = f"ship.json \"additionalServices\"[{index}]"
                if not isinstance(entry, dict):
                    problems.append(f"{where} must be an object with group, service and name.")
                    continue
                extra = sorted(set(entry) - {"group", "service", "name"})
                if extra:
                    problems.append(f"{where} has unknown fields: {', '.join(map(str, extra))}.")
                group, service, name = entry.get("group"), entry.get("service"), entry.get("name")
                if not isinstance(group, str) or not group or not isinstance(service, str) or not service:
                    problems.append(f"{where} requires a group and a service.")
                    continue
                if group not in GROUPS_SUPPORTING_ADDITIONAL_INSTANCES:
                    problems.append(
                        f"{where}: the {group!r} group doesn't support additional instances; "
                        f"only {', '.join(GROUPS_SUPPORTING_ADDITIONAL_INSTANCES)} do."
                    )
                if not isinstance(name, str) or not INSTANCE_NAME.fullmatch(name):
                    problems.append(
                        f"{where} name {name!r} must use lowercase letters, digits and underscores, "
                        "starting with a letter (e.g. \"analytics\")."
                    )
                    continue
                if name in seen:
                    problems.append(f"ship.json \"additionalServices\" uses the name {name!r} twice.")
                seen.add(name)
                additional.append(AdditionalService(group, canonical_service(service), name))

        service_names = data.get("serviceNames", {})
        if not isinstance(service_names, dict) or any(
            not isinstance(old, str) or not isinstance(new, str) or not SERVICE_NAME.fullmatch(new)
            for old, new in service_names.items()
        ):
            problems.append(
                "ship.json \"serviceNames\" must map default Compose service names to new lowercase names."
            )
            service_names = {}
        elif len(set(service_names.values())) != len(service_names):
            problems.append("ship.json \"serviceNames\" renames two services to the same name.")

        external_network = data.get("externalNetwork")
        if external_network is not None:
            networks = [external_network] if isinstance(external_network, str) else external_network
            if (not isinstance(networks, list) or not networks or
                    any(not isinstance(item, str) or not NETWORK_NAME.fullmatch(item) for item in networks)):
                problems.append('ship.json "externalNetwork" must be a Docker network name or a nonempty list of names.')

        extensions = data.get("extensions", [])
        if not isinstance(extensions, list) or any(not isinstance(item, str) or not item for item in extensions):
            problems.append("ship.json \"extensions\" must be a list of entry point names.")
            extensions = []
        elif len(extensions) != len(set(extensions)):
            problems.append("ship.json \"extensions\" lists the same entry point twice.")

        name = data.get("name")
        if name is not None and (not isinstance(name, str) or not SERVICE_NAME.fullmatch(name)):
            problems.append(
                "ship.json \"name\" must start with a lowercase letter and use single - or _ "
                "separators between lowercase letters and digits."
            )

        silo_image = data.get("siloImage", "standard")
        if silo_image not in SILO_IMAGES:
            problems.append("ship.json \"siloImage\" must be \"standard\" or \"distroless\".")

        booleans = {}
        for key in ("publishPorts", "hostUser", "nodeAssets", "djangoCollectstatic"):
            value = data.get(key, key == "hostUser")
            if not isinstance(value, bool):
                problems.append(f"ship.json \"{key}\" must be true or false.")
            booleans[key] = value
        if booleans["djangoCollectstatic"] is True and framework != "django":
            problems.append("ship.json \"djangoCollectstatic\" requires \"framework\": \"django\".")

        processes = data.get("processes", {})
        if not isinstance(processes, dict):
            problems.append("ship.json \"processes\" must map process names to commands.")
            processes = {}
        else:
            for process, command in processes.items():
                if not isinstance(process, str) or not SERVICE_NAME.fullmatch(process):
                    problems.append(f"ship.json process name {process!r} must be a lowercase Compose service name.")
                elif process == "app" or process in RESERVED_COMMANDS:
                    problems.append(f"ship.json process name {process!r} is reserved.")
                if process_problem(command):
                    problems.append(
                        f"ship.json process {process!r}: {process_problem(command)}"
                    )

        deploy_commands = data.get("deployCommands", [])
        if not isinstance(deploy_commands, list) or any(not _valid_command(item) for item in deploy_commands):
            problems.append(
                "ship.json \"deployCommands\" must be a list of shell command strings or argument lists."
            )

        if problems:
            return None, problems
        return cls(
            framework=framework,
            app=str(app),
            python=python,
            server=server,
            node=node,
            build=dict(build),
            services={group: canonical_service(key) for group, key in services.items()},
            additional_services=additional,
            service_names={canonical_service(old): new for old, new in service_names.items()},
            external_network=external_network,
            extensions=list(extensions),
            name=name,
            silo_image=silo_image,
            publish_ports=booleans["publishPorts"],
            host_user=booleans["hostUser"],
            processes=dict(processes),
            deploy_commands=list(deploy_commands),
            node_assets=booleans["nodeAssets"],
            django_collectstatic=booleans["djangoCollectstatic"],
        ), []

    def to_json(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "$schema": SCHEMA_URL,
            "framework": self.framework,
            "app": self.app,
            "python": self.python,
        }
        if self.build:
            payload["build"] = dict(self.build)
        payload["node"] = self.node
        if self.server is not None:
            payload["server"] = self.server
        payload["services"] = dict(self.services)
        if self.additional_services:
            payload["additionalServices"] = [entry.as_json() for entry in self.additional_services]
        if self.service_names:
            payload["serviceNames"] = dict(self.service_names)
        if self.external_network is not None:
            payload["externalNetwork"] = self.external_network
        payload["extensions"] = list(self.extensions)
        if self.name is not None:
            payload["name"] = self.name
        if self.silo_image != "standard":
            payload["siloImage"] = self.silo_image
        if self.publish_ports:
            payload["publishPorts"] = True
        if not self.host_user:
            payload["hostUser"] = False
        if self.processes:
            payload["processes"] = dict(self.processes)
        if self.deploy_commands:
            payload["deployCommands"] = list(self.deploy_commands)
        if self.node_assets:
            payload["nodeAssets"] = True
        if self.django_collectstatic:
            payload["djangoCollectstatic"] = True
        return payload

    def write(self, project_root: Path) -> None:
        (project_root / "ship.json").write_text(json.dumps(self.to_json(), indent=4) + "\n", encoding="utf-8")


_ATTRIBUTES = {
    "$schema": "", "framework": "framework", "app": "app", "python": "python", "server": "server",
    "services": "services", "additionalServices": "additional_services",
    "serviceNames": "service_names", "externalNetwork": "external_network",
    "extensions": "extensions", "name": "name", "siloImage": "silo_image",
    "publishPorts": "publish_ports", "hostUser": "host_user", "processes": "processes",
    "deployCommands": "deploy_commands", "nodeAssets": "node_assets",
    "djangoCollectstatic": "django_collectstatic", "node": "node", "build": "build",
}


def _valid_command(command: object) -> bool:
    if isinstance(command, str):
        return bool(command.strip()) and "\0" not in command
    return isinstance(command, list) and bool(command) and isinstance(command[0], str) and bool(command[0]) and all(isinstance(part, str) and "\0" not in part for part in command)


def _load(path: Path) -> tuple[dict[str, Any] | None, list[str]]:
    def unique(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise ConfigError(f"ship.json has a duplicate key {key!r}.")
            result[key] = value
        return result

    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"), object_pairs_hook=unique)
    except FileNotFoundError:
        return None, ["No ship.json found; run `ship init` first."]
    except json.JSONDecodeError as exc:
        return None, [f"ship.json is not valid JSON: {exc}"]
    except ConfigError as exc:
        return None, [str(exc)]
    if not isinstance(data, dict):
        return None, ["ship.json must contain a JSON object."]
    return data, []


def process_problem(value: object) -> str | None:
    if not isinstance(value, dict):
        return None if _valid_command(value) else "requires a shell command or argv with a nonempty executable."
    if set(value) - {"command", "healthcheck", "http"} or not _valid_command(value.get("command")):
        return "requires command, with optional healthcheck and http fields only."
    if "healthcheck" in value and (not isinstance(value["healthcheck"], str) or not value["healthcheck"].strip()):
        return "healthcheck must be a nonempty shell command."
    if "http" in value:
        return http_problem(value["http"])
    return None


def http_problem(value: object) -> str | None:
    if not isinstance(value, dict) or set(value) - {"port", "role", "health", "urlSetting"}:
        return "http must contain port, role and optional health/urlSetting."
    port = value.get("port")
    if type(port) is not int or not 1 <= port <= 65535:
        return "http.port must be an integer between 1 and 65535."
    if not isinstance(value.get("role"), str) or not value["role"].strip():
        return "http.role must be a nonempty string."
    health, setting = value.get("health"), value.get("urlSetting")
    if health is not None and (not isinstance(health, str) or not health.startswith("/") or "\0" in health):
        return "http.health must be a path starting with / or null."
    if setting is not None and (not isinstance(setting, str) or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", setting)):
        return "http.urlSetting must be a setting name or null."
    return None


def process_definition(value: Process) -> dict[str, Any]:
    return dict(value) if isinstance(value, dict) else {"command": value}
