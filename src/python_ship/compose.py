"""Generate the development and production Compose models from ship.json."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
import json
import os
from pathlib import Path
import re
import shlex
import sys
from typing import Any

from . import __version__
from .config import ConfigError, ShipConfig, command_argv, process_definition
from .contracts import APP_PROCESS, Interface, ServiceContext, ServiceDefinition, ShipEnvironment, WebServer
from .mutagen import enabled as mutagen_enabled
from .plugins import Registry, load_extensions
from .security import build_arg as python_floor
from .services import BROWSER_APP_HOST

# The development bind mount; under Mutagen every service with it is synced instead.
PROJECT_MOUNT = ".:/app"

DEVELOPMENT_FILE = "docker-compose.generated.yml"
PRODUCTION_FILE = "docker-compose.production.yml"
SERVICE_NAME = re.compile(r"[a-z][a-z0-9]*(?:[-_][a-z0-9]+)*")
PROCESS_STOP_GRACE = "60s"


@dataclass(frozen=True)
class Selection:
    """One selected service: the default of its group, or a named additional instance."""

    service: ServiceDefinition
    group: str
    instance_name: str | None
    service_name: str

    def context(self, config: ShipConfig, root: Path, environment: ShipEnvironment) -> ServiceContext:
        return ServiceContext(environment, config, root, self.service.key, self.instance_name, self.service_name)


def resolve_selections(config: ShipConfig, registry: Registry) -> list[Selection]:
    """Every selected service with its final Compose name, rejecting unknown or colliding ones."""
    problems = selection_problems(config, registry)
    if problems:
        raise ConfigError(" ".join(problems))
    return _selections(config, registry)


def _selections(config: ShipConfig, registry: Registry) -> list[Selection]:
    selections = []
    for group, key in config.services.items():
        service = registry.services.get(key)
        if service is not None and service.group == group:
            selections.append(Selection(service, group, None, config.service_names.get(key, key)))
    for entry in config.additional_services:
        service = registry.services.get(entry.service)
        if service is not None and service.group == entry.group:
            selections.append(Selection(service, entry.group, entry.name, config.service_names.get(f"{entry.service}-{entry.name}", f"{entry.service}-{entry.name}")))
    return selections


def selection_problems(config: ShipConfig, registry: Registry) -> list[str]:
    problems = []
    for group, key in config.services.items():
        service = registry.services.get(key)
        if service is None:
            problems.append(f"Unknown service {key!r} in group {group!r}.")
        elif service.group != group:
            problems.append(f"Service {key!r} belongs to the {service.group!r} group, not {group!r}.")
    for entry in config.additional_services:
        service = registry.services.get(entry.service)
        if service is None:
            problems.append(f"Unknown service {entry.service!r} in additional instance {entry.name!r}.")
        elif service.group != entry.group:
            problems.append(
                f"Additional instance {entry.name!r}: {entry.service!r} belongs to the "
                f"{service.group!r} group, not {entry.group!r}."
            )
    renamable = {"app", *config.services.values(), *(f"{entry.service}-{entry.name}" for entry in config.additional_services)}
    for old in config.service_names:
        if old not in renamable:
            problems.append(
                f"ship.json \"serviceNames\" renames {old!r}, which is not \"app\" or a selected service."
            )
    names: dict[str, str] = {config.app_service: "the app"}
    for selection in _selections(config, registry):
        owner = names.get(selection.service_name)
        if owner is not None:
            problems.append(f"Compose service name {selection.service_name!r} is used by both {owner} and {selection.service.key!r}.")
        names[selection.service_name] = repr(selection.service.key)
    for process in config.processes:
        if process in names:
            problems.append(f"Process {process!r} conflicts with the Compose service of {names[process]}.")
    return problems


def _merge(base: dict[str, Any], fragment: dict[str, Any], location: str) -> None:
    """Add a fragment's values without silently discarding existing settings."""
    if not isinstance(fragment, dict):
        raise ConfigError(f"{location} must be a Compose object.")
    for key, value in fragment.items():
        path = f"{location}.{key}"
        if key not in base:
            base[key] = deepcopy(value)
        elif isinstance(base[key], dict) and isinstance(value, dict):
            _merge(base[key], value, path)
        elif isinstance(base[key], list) and isinstance(value, list):
            base[key].extend(deepcopy(item) for item in value if item not in base[key])
        elif base[key] != value:
            raise ConfigError(f"Conflicting Compose value at {path}.")


def _local_ports(ports: list[Any], service_name: str) -> list[Any]:
    """Development ports bind 127.0.0.1 unless a host address is given explicitly."""
    localized: list[Any] = []
    for port in ports:
        if isinstance(port, dict):
            localized.append({"host_ip": "127.0.0.1", **port})
        elif isinstance(port, int):
            localized.append({"target": port, "host_ip": "127.0.0.1"})
        elif isinstance(port, str):
            endpoint, separator, protocol = port.partition("/")
            if endpoint.count(":") == 1 and not endpoint.startswith("["):
                endpoint = "127.0.0.1:" + endpoint
            elif endpoint.isdigit():
                localized.append({"target": int(endpoint), "host_ip": "127.0.0.1",
                                  **({"protocol": protocol} if separator else {})})
                continue
            localized.append(endpoint + (separator + protocol if separator else ""))
        else:
            raise ConfigError(f"Compose service {service_name!r} has an invalid port entry.")
    return localized


def project_name(config: ShipConfig, project_root: Path) -> str:
    """``name`` from ship.json, or a sanitized form of the project directory's name."""
    if config.name:
        return config.name
    name = re.sub(r"[^a-z0-9]+", "-", project_root.resolve().name.lower()).strip("-")
    if not name or not name[0].isalpha():
        name = "ship-" + name if name else "ship"
    return name


def _command_list(value: Any, what: str) -> list[str]:
    if not isinstance(value, list) or not value or not value[0] or any(not isinstance(part, str) or "\0" in part for part in value):
        raise ConfigError(f"{what} must be a nonempty list of strings.")
    return value


def _escape_dollars(argv: list[str]) -> list[str]:
    # Compose interpolates $VAR in a service's command; a shell variable meant for the container
    # would otherwise be blanked out silently.
    return [part.replace("$", "$$") for part in argv]


def _app_command(config: ShipConfig, root: Path, registry: Registry, environment: ShipEnvironment) -> list[str]:
    adapter = registry.adapter(config)
    interface = app_interface(config, root, registry)
    server = registry.server(config, interface)
    try:
        target = adapter.server_target(config)
    except Exception as exc:
        raise ConfigError(f"Adapter {adapter.key!r} could not provide a server target: {exc}") from exc
    if not isinstance(target, str) or not target:
        raise ConfigError(f"Adapter {adapter.key!r} returned an invalid server target.")
    try:
        if environment.is_production:
            command = server.production_command(target, interface)
        else:
            command = server.development_command(target, interface)
    except Exception as exc:
        raise ConfigError(f"Web server {server.key!r} command failed: {exc}") from exc
    command = _command_list(command, f"Web server {server.key!r} command")
    if environment.is_production:
        try:
            release = adapter.release_commands(config)
        except Exception as exc:
            raise ConfigError(f"Adapter {adapter.key!r} release commands failed: {exc}") from exc
        if not isinstance(release, list):
            raise ConfigError(f"Adapter {adapter.key!r} release_commands() must return a list.")
        steps = [shlex.join(_command_list(step, f"Adapter {adapter.key!r} release command")) for step in release]
        if steps:
            command = ["sh", "-c", " && ".join([*steps, "exec " + shlex.join(command)]).replace("$", "$$")]
    return command


def app_interface(config: ShipConfig, root: Path, registry: Registry) -> Interface:
    """ASGI or WSGI, as the selected adapter sees the app."""
    adapter = registry.adapter(config)
    try:
        interface = adapter.interface(config, root)
    except Exception as exc:
        raise ConfigError(f"Adapter {adapter.key!r} could not tell the app's interface: {exc}") from exc
    if interface not in ("asgi", "wsgi"):
        raise ConfigError(f"Adapter {adapter.key!r} returned an invalid interface {interface!r}.")
    return interface


def server_for(config: ShipConfig, root: Path, registry: Registry | None = None) -> WebServer:
    """The server that will run the app: the configured one, or the recommended one."""
    registry = registry or load_extensions(config)
    return registry.server(config, app_interface(config, root, registry))


def adapter_problems(config: ShipConfig, root: Path, registry: Registry) -> list[str]:
    try:
        adapter = registry.adapter(config)
    except ConfigError as exc:
        return [str(exc)]
    try:
        problems = adapter.validate(config, root)
    except Exception as exc:
        return [f"Adapter {adapter.key!r} validation failed: {exc}"]
    if not isinstance(problems, list) or any(not isinstance(problem, str) for problem in problems):
        return [f"Adapter {adapter.key!r} returned invalid validation results."]
    return problems


def build_compose(config: ShipConfig, project_root: Path, *, production: bool = False,
                  registry: Registry | None = None) -> dict[str, Any]:
    environment = ShipEnvironment.PRODUCTION if production else ShipEnvironment.DEVELOPMENT
    registry = registry or load_extensions(config)
    problems = adapter_problems(config, project_root, registry)
    if problems:
        raise ConfigError(" ".join(problems))
    selections = resolve_selections(config, registry)
    server = server_for(config, project_root, registry)
    name = project_name(config, project_root)
    app_name = config.app_service

    app: dict[str, Any] = {
        "build": {
            "context": ".",
            "dockerfile": "ship/Dockerfile",
            "target": environment.value,
            "args": {
                "PYTHON_VERSION": config.python,
                "SHIP_PYTHON_FLOOR": python_floor(),
                "SHIP_SERVER_REQUIREMENT": server.requirement,
            },
        },
        "command": _app_command(config, project_root, registry, environment),
        "working_dir": "/app",
        "env_file": [{"path": ".env.production" if production else ".env", "required": False}],
        "environment": {},
        "restart": "unless-stopped",
        "init": True,
        "stop_grace_period": PROCESS_STOP_GRACE,
    }
    if production:
        # Never build a release on a stale, locally cached base image.
        app["build"]["pull"] = True
        if config.publish_ports:
            app["ports"] = ["${APP_PORT:-8000}:8000"]
    else:
        app["volumes"] = [PROJECT_MOUNT]
        app["ports"] = ["127.0.0.1:${APP_PORT:-8000}:8000"]
        # Lets a debugger in the container (debugpy) reach the IDE on the host.
        app["extra_hosts"] = ["host.docker.internal:host-gateway"]
        user = host_user(config)
        if user:
            app["user"] = user

    hook = getattr(registry.adapter(config), "app_environment", None)
    if hook is not None:
        try:
            adapter_environment = hook(config)
        except Exception as exc:
            raise ConfigError(f"Adapter {config.framework!r} app environment failed: {exc}") from exc
        if not isinstance(adapter_environment, dict) or any(
            not isinstance(key, str) or not isinstance(value, str) for key, value in adapter_environment.items()
        ):
            raise ConfigError(f"Adapter {config.framework!r} app_environment() must return a dict of strings.")
        app["environment"].update(adapter_environment)

    services: dict[str, Any] = {app_name: app}
    compose: dict[str, Any] = {"name": name, "services": services}
    app_processes: list[tuple[str, dict[str, Any]]] = []
    volumes: dict[str, None] = {}

    for selection in selections:
        context = selection.context(config, project_root, environment)
        service = selection.service
        validate = getattr(service, "validate", None)
        if validate is not None:
            try:
                issues = validate(context)
            except Exception as exc:
                raise ConfigError(f"Service {service.key!r} validation failed: {exc}") from exc
            if issues:
                raise ConfigError(" ".join(issues))
        try:
            fragment = service.compose_fragment(context)
            variables = service.app_environment(context) if fragment else {}
        except Exception as exc:
            raise ConfigError(f"Service {service.key!r} failed to build its Compose fragment: {exc}") from exc
        if not isinstance(fragment, dict) or not isinstance(variables, dict):
            raise ConfigError(f"Service {service.key!r} must return a Compose mapping and an environment mapping.")
        if not fragment:
            continue  # Absent in this environment, as development-only services are in production.
        if selection.service_name not in fragment:
            raise ConfigError(f"Service {service.key!r} did not define its Compose service {selection.service_name!r}.")
        for service_name, model in deepcopy(fragment).items():
            target_name = app_name if service_name == "app" else service_name
            if not isinstance(service_name, str) or not SERVICE_NAME.fullmatch(service_name):
                raise ConfigError(f"Invalid Compose service name {service_name!r} from {service.key!r}.")
            if not isinstance(model, dict):
                raise ConfigError(f"Compose service {service_name!r} must be an object.")
            if APP_PROCESS in model:
                # Runs from the app's image, with its environment; built once the app is complete.
                if target_name in services or any(name == target_name for name, _ in app_processes):
                    raise ConfigError(f"Duplicate Compose service {target_name!r}.")
                app_processes.append((target_name, model))
                continue
            if "ports" in model:
                if not isinstance(model["ports"], list):
                    raise ConfigError(f"Compose service {service_name!r} ports must be a list.")
                if environment.is_development:
                    model["ports"] = _local_ports(model["ports"], service_name)
                elif not config.publish_ports:
                    model.pop("ports")
            mounts = model.get("volumes", [])
            if not isinstance(mounts, list):
                raise ConfigError(f"Compose service {service_name!r} volumes must be a list.")
            for mount in mounts:
                if isinstance(mount, str):
                    source = mount.split(":", 1)[0]
                    if not source.startswith((".", "/", "~", "$")):
                        volumes[source] = None
            if target_name == app_name:
                try:
                    _merge(app, model, f"services.{app_name}")
                except ConfigError as exc:
                    raise ConfigError(f"{exc} (set by the {service.key!r} service)") from exc
                continue
            if target_name in services:
                raise ConfigError(f"Duplicate Compose service {target_name!r}.")
            services[target_name] = {"restart": "unless-stopped"}
            _merge(services[target_name], model, f"services.{target_name}")
            condition = "service_healthy" if "healthcheck" in model else "service_started"
            if not model.get("profiles"):
                _merge(app, {"depends_on": {target_name: {"condition": condition}}}, f"services.{app_name}")
        try:
            _merge(app["environment"], variables, f"services.{app_name}.environment")
        except ConfigError as exc:
            raise ConfigError(f"{exc} (set by the {service.key!r} service)") from exc

    if config.external_networks:
        keys = {f"external_{i}": {"external": True, "name": network} for i, network in enumerate(config.external_networks)}
        _merge(app, {"networks": ["default", *keys]}, f"services.{app_name}")
        compose["networks"] = {"default": {}, **keys}

    if environment.is_development and any(selection.group == "testing" for selection in selections):
        networks = app.get("networks", ["default"])
        if isinstance(networks, list):
            networks = {network: {} for network in networks}
        aliases = networks.setdefault("default", {}).setdefault("aliases", [])
        if BROWSER_APP_HOST not in aliases:
            aliases.append(BROWSER_APP_HOST)
        app["networks"] = networks

    processes = []
    if production:
        for process, value in config.processes.items():
            definition = process_definition(value)
            settings: dict[str, Any] = {APP_PROCESS: _escape_dollars(command_argv(definition["command"]))}
            if definition.get("healthcheck"):
                settings["healthcheck"] = {"test": ["CMD-SHELL", definition["healthcheck"].replace("$", "$$")],
                                         "interval": "10s", "timeout": "5s", "retries": 5}
            elif definition.get("http", {}).get("health"):
                endpoint = definition["http"]
                url = f"http://127.0.0.1:{endpoint['port']}{endpoint['health']}"
                settings["healthcheck"] = {"test": ["CMD", "python", "-c",
                    f"import urllib.request; urllib.request.urlopen({url!r}, timeout=4)"],
                    "interval": "10s", "timeout": "5s", "retries": 5}
            if definition.get("http"):
                settings["expose"] = [str(definition["http"]["port"])]
            processes.append((process, settings))
    processes += app_processes
    if processes:
        app["image"] = f"{name}-{app_name}:{'local' if production else 'development'}"
    for process, settings in processes:
        if process in services:
            raise ConfigError(f"Process {process!r} conflicts with another Compose service.")
        model = deepcopy(app)
        for key in ("build", "ports", "healthcheck", "extra_hosts"):
            model.pop(key, None)
        if isinstance(model.get("networks"), dict):
            model["networks"]["default"].pop("aliases", None)
        extra = dict(settings)
        model["command"] = _command_list(extra.pop(APP_PROCESS), f"Process {process!r} command")
        model["stop_grace_period"] = PROCESS_STOP_GRACE
        _merge(model, extra, f"services.{process}")
        services[process] = model
    # Every container that runs the app's code, for Mutagen sync and the deploy script.
    compose["x-ship"] = {"app_services": [app_name, *(process for process, _ in processes)]}
    if not production:
        # Every container that sees the project at /app (the app, its processes, Node for Vite).
        synced = [service_name for service_name, model in services.items() if PROJECT_MOUNT in model.get("volumes", [])]
        compose["x-ship"]["sync_services"] = synced
        if mutagen_enabled():
            for service_name in synced:
                mounts = [mount for mount in services[service_name]["volumes"] if mount != PROJECT_MOUNT]
                if mounts:
                    services[service_name]["volumes"] = mounts
                else:
                    del services[service_name]["volumes"]

    if production:
        for service_name, model in services.items():
            for mount in model.get("volumes", []):
                bind = (isinstance(mount, str) and mount.split(":", 1)[0].startswith((".", "/", "~"))) or (
                    isinstance(mount, dict) and mount.get("type") == "bind"
                )
                if bind:
                    raise ConfigError(f"Production service {service_name!r} cannot use a host bind mount.")
            if "build" in model:
                model["build"].setdefault("pull", True)
                model.setdefault("image", f"{name}-{service_name}:local")
    if volumes:
        compose["volumes"] = volumes
    return compose


def host_user(config: ShipConfig) -> str | None:
    """The host UID:GID the development app runs as, or None where it doesn't apply."""
    if not config.host_user or mutagen_enabled() or not sys.platform.startswith("linux"):
        return None
    getuid = getattr(os, "getuid", None)
    getgid = getattr(os, "getgid", None)
    if getuid is None or getgid is None or getuid() == 0:
        return None
    return f"{getuid()}:{getgid()}"


def compose_path(project_root: Path, *, production: bool = False) -> Path:
    return project_root / "ship" / (PRODUCTION_FILE if production else DEVELOPMENT_FILE)


def render(model: dict[str, Any]) -> str:
    # JSON is valid YAML, so the file needs no YAML library and Compose reads it unchanged.
    header = f"# Generated by python-ship {__version__} from ship.json. Edits are overwritten.\n"
    return header + json.dumps(model, indent=2) + "\n"


def write_compose(config: ShipConfig, project_root: Path, *, production: bool = False,
                  model: dict[str, Any] | None = None) -> Path:
    model = build_compose(config, project_root, production=production) if model is None else model
    destination = compose_path(project_root, production=production)
    destination.parent.mkdir(exist_ok=True)
    destination.write_text(render(model), encoding="utf-8")
    return destination
