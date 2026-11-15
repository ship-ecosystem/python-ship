"""Generate Compose models from framework and service extensions."""

from copy import deepcopy
import json
from pathlib import Path
import re
import os
import shlex
import sys
from typing import Any

from .config import ConfigError, ShipConfig
from .contracts import ServiceDefinition
from .plugins import load_extensions, load_servers
from .mutagen import enabled as mutagen_enabled


def selected_services(config: ShipConfig, services: dict[str, ServiceDefinition]) -> list[tuple[str, ServiceDefinition, dict[str, str]]]:
    """Resolve legacy selections and additional named instances."""
    selected = []
    names = {"app"}
    prefixes = set()
    for group, key in config.services.items():
        service = services.get(key)
        if service is None or service.group != group:
            raise ConfigError(f"Unknown service {key!r} in group {group!r}.")
        if key in names:
            raise ConfigError(f"Duplicate Compose service {key!r}.")
        names.add(key)
        selected.append((group, service, {"group": group, "service": key, "name": key, "prefix": ""}))
    for instance_id, raw in config.instances.items():
        if not isinstance(raw, dict):
            raise ConfigError(f"Instance {instance_id!r} must be an object.")
        instance_group, instance_key = raw.get("group"), raw.get("service")
        if not isinstance(instance_group, str) or not isinstance(instance_key, str):
            raise ConfigError(f"Instance {instance_id!r} requires a service group and key.")
        service = services.get(instance_key)
        if service is None or service.group != instance_group:
            raise ConfigError(f"Unknown service {instance_key!r} in group {instance_group!r} for instance {instance_id!r}.")
        name = raw.get("name", instance_id)
        prefix = raw.get("prefix", instance_id.upper().replace("-", "_"))
        network: str | None = raw.get("network")
        if not isinstance(name, str) or not re.fullmatch(r"[a-z][a-z0-9]*(?:[-_][a-z0-9]+)*", name):
            raise ConfigError(f"Invalid Compose service name {name!r} for instance {instance_id!r}.")
        if name in names:
            raise ConfigError(f"Duplicate Compose service {name!r}.")
        if not isinstance(prefix, str) or not re.fullmatch(r"[A-Z][A-Z0-9_]*", prefix):
            raise ConfigError(f"Invalid environment prefix {prefix!r} for instance {instance_id!r}.")
        if prefix in prefixes:
            raise ConfigError(f"Duplicate environment prefix {prefix!r}.")
        if network is not None and network not in config.networks:
            raise ConfigError(f"Unknown external network alias {network!r} for instance {instance_id!r}.")
        names.add(name)
        prefixes.add(prefix)
        spec = {"group": instance_group, "service": instance_key, "name": name, "prefix": prefix}
        if network:
            spec["network"] = network
        selected.append((instance_id, service, spec))
    return selected


def _merge(base: dict, fragment: dict, location: str) -> None:
    """Add extension values without silently discarding existing settings."""
    if not isinstance(fragment, dict):
        raise ConfigError(f"{location} must be a Compose object.")
    for key, value in fragment.items():
        path = f"{location}.{key}"
        if key not in base:
            base[key] = value
        elif isinstance(base[key], dict) and isinstance(value, dict):
            _merge(base[key], value, path)
        elif isinstance(base[key], list) and isinstance(value, list):
            base[key].extend(item for item in value if item not in base[key])
        elif base[key] != value:
            raise ConfigError(f"Conflicting Compose value at {path}.")


def _replace_strings(value, replacements: dict[str, str]):
    if isinstance(value, str):
        for old, new in replacements.items():
            value = value.replace(old, new)
        return value
    if isinstance(value, list):
        return [_replace_strings(item, replacements) for item in value]
    if isinstance(value, dict):
        return {key: _replace_strings(item, replacements) for key, item in value.items()}
    return value


def _local_ports(ports: list, service_name: str) -> list:
    localized: list[Any] = []
    for port in ports:
        if isinstance(port, dict):
            localized.append({"host_ip": "127.0.0.1", **port})
        elif isinstance(port, int):
            localized.append({"target": port, "host_ip": "127.0.0.1"})
        elif isinstance(port, str):
            endpoint, separator, protocol = port.partition("/")
            if re.fullmatch(r"\d+:\d+", endpoint):
                endpoint = "127.0.0.1:" + endpoint
            elif re.fullmatch(r"\d+", endpoint):
                localized.append({"target": int(endpoint), "host_ip": "127.0.0.1", **({"protocol": protocol} if separator else {})})
                continue
            localized.append(endpoint + (separator + protocol if separator else ""))
        else:
            raise ConfigError(f"Compose service {service_name!r} has an invalid port entry.")
    return localized


def _service_parts(service, config: ShipConfig, spec: dict[str, str], *, production: bool = False) -> tuple[dict, dict]:
    context = {**spec, "_production": production}
    try:
        fragment = service.compose_fragment_for(config, context) if hasattr(service, "compose_fragment_for") else service.compose_fragment()
        environment = service.app_environment_for(config, context) if hasattr(service, "app_environment_for") else service.app_environment()
    except Exception as exc:
        raise ConfigError(f"Service {service.key!r} failed to build its Compose fragment: {exc}") from exc
    if not isinstance(fragment, dict) or not isinstance(environment, dict):
        raise ConfigError(f"Service {service.key!r} must return Compose and environment objects.")
    fragment, environment = deepcopy(fragment), deepcopy(environment)
    # A legacy plugin can still be instantiated under another service name.
    if not hasattr(service, "compose_fragment_for") and spec["name"] != service.key:
        old_names = [name for name in fragment if name != "app"]
        rename = {name: spec["name"] if name == service.key else f"{spec['name']}-{name}" for name in old_names}
        replacements = {f"//{old}:": f"//{new}:" for old, new in rename.items()}
        replacements.update({f"//{old}/": f"//{new}/" for old, new in rename.items()})
        fragment = {rename.get(name, name): _replace_strings(model, replacements) for name, model in fragment.items()}
        environment = _replace_strings(environment, replacements)
        environment = {key: rename.get(value, value) for key, value in environment.items()}
        for model in fragment.values():
            if not isinstance(model, dict):
                continue
            if isinstance(model.get("depends_on"), dict):
                model["depends_on"] = {rename.get(key, key): value for key, value in model["depends_on"].items()}
            if isinstance(model.get("volumes"), list):
                mounts = []
                for mount in model["volumes"]:
                    if not isinstance(mount, str) or ":" not in mount:
                        mounts.append(mount)
                        continue
                    source, rest = mount.split(":", 1)
                    if source.startswith((".", "/", "~")):
                        mounts.append(mount)
                    else:
                        source = source.replace(service.key, spec["name"], 1) if source.startswith(service.key) else f"{spec['name']}_{source}"
                        mounts.append(source + ":" + rest)
                model["volumes"] = mounts
    if spec["prefix"]:
        prefix = spec["prefix"] + "_"
        environment = {prefix + key: value for key, value in environment.items()}
        fragment = _replace_strings(fragment, {"${DB_": "${" + prefix + "DB_"})
        environment = _replace_strings(environment, {"${DB_": "${" + prefix + "DB_"})
    return fragment, environment


def build_compose(config: ShipConfig, project_root: Path, *, production: bool = False) -> dict:
    if config.silo_image not in ("distroless", "standard"):
        raise ConfigError("ship.json silo_image must be 'distroless' or 'standard'.")
    adapters, services = load_extensions(config)
    server = load_servers(config).get(config.server)
    if server is None:
        raise ConfigError(f"Unknown ASGI server {config.server!r}; select an installed python_ship.servers extension or use uvicorn/hypercorn.")
    if not isinstance(server.requirement, str) or not server.requirement:
        raise ConfigError(f"ASGI server {config.server!r} has no package requirement.")
    adapter = adapters.get(config.framework)
    if adapter is None:
        raise ConfigError(f"Unknown framework adapter {config.framework!r}.")
    try:
        problems = adapter.validate(config, project_root)
    except Exception as exc:
        raise ConfigError(f"Adapter {config.framework!r} validation failed: {exc}") from exc
    if not isinstance(problems, list) or any(not isinstance(problem, str) for problem in problems):
        raise ConfigError(f"Adapter {config.framework!r} returned invalid validation results.")
    if problems:
        raise ConfigError(" ".join(problems))
    name = config.name or re.sub(r"[^a-z0-9]+", "-", project_root.name.lower()).strip("-")
    if not name or not name[0].isalpha():
        name = "ship-" + name
    if not re.fullmatch(r"[a-z][a-z0-9]*(?:[-_][a-z0-9]+)*", name):
        raise ConfigError(f"Invalid Compose project name {name!r}.")
    if production:
        name += "-production"
    try:
        if hasattr(adapter, "server_target"):
            target = adapter.server_target(config)
            if not isinstance(target, str) or not target:
                raise ConfigError(f"Adapter {config.framework!r} returned an invalid ASGI server target.")
            command = server.production_command(target) if production else server.development_command(target)
        elif config.server == "uvicorn":
            command = adapter.production_command(config) if production else adapter.development_command(config)
        else:
            raise ConfigError(f"Adapter {config.framework!r} does not support selectable ASGI servers.")
    except Exception as exc:
        raise ConfigError(f"Adapter {config.framework!r} command failed: {exc}") from exc
    if not isinstance(command, list) or not command or any(not isinstance(part, str) or not part for part in command):
        raise ConfigError(f"Adapter {config.framework!r} returned an invalid command.")
    if production:
        try:
            startup: list[list[str]] = getattr(adapter, "production_startup_commands", lambda config: [])(config)
        except Exception as exc:
            raise ConfigError(f"Adapter {config.framework!r} production startup commands failed: {exc}") from exc
        if not isinstance(startup, list) or any(
            not isinstance(parts, list) or not parts or any(not isinstance(part, str) or not part for part in parts)
            for parts in startup
        ):
            raise ConfigError(f"Adapter {config.framework!r} returned invalid production startup commands.")
        if startup:
            command = ["sh", "-c", " && ".join([*(shlex.join(parts) for parts in startup), "exec " + shlex.join(command)])]
    app: dict[str, Any] = {
        "build": {"context": ".", "dockerfile": "ship/Dockerfile", "target": "production" if production else "development", "args": {"PYTHON_VERSION": config.python, "SHIP_SERVER_REQUIREMENT": server.requirement}},
        "command": command,
        "working_dir": "/app",
        "env_file": [{"path": ".env.production", "required": False}] if production else [{"path": ".env", "required": False}],
        "environment": {},
        "restart": "unless-stopped",
    }
    if not production:
        if not mutagen_enabled():
            app["volumes"] = [".:/app"]
        app["ports"] = ["127.0.0.1:${SHIP_APP_PORT:-8000}:8000"]
        if not mutagen_enabled() and sys.platform.startswith("linux") and hasattr(os, "getuid"):
            app["user"] = f"{os.getuid()}:{os.getgid()}"
    elif config.publish_ports:
        app["ports"] = ["${SHIP_PRODUCTION_PORT:-8000}:8000"]
    compose: dict[str, Any] = {"name": name, "services": {"app": app}}
    volumes: dict[str, None] = {}
    extra_networks = set()
    selections = selected_services(config, services)
    for instance_id, service, spec in selections:
        if production and getattr(service, "development_only", False):
            continue
        validate = getattr(service, "validate", None)
        if validate is not None:
            try:
                issues = validate(config, spec, project_root)
            except Exception as exc:
                raise ConfigError(f"Service {service.key!r} validation failed: {exc}") from exc
            if issues:
                raise ConfigError(" ".join(issues))
        fragment, environment = _service_parts(service, config, spec, production=production)
        own_names = set(fragment) - {"app"}
        if spec["name"] not in own_names:
            raise ConfigError(f"Service {service.key!r} did not define its selected Compose name {spec['name']!r}.")
        for service_name, model in fragment.items():
            if not isinstance(service_name, str) or not re.fullmatch(r"[a-z][a-z0-9]*(?:[-_][a-z0-9]+)*", service_name):
                raise ConfigError(f"Invalid Compose service name {service_name!r} from {service.key!r}.")
            if not isinstance(model, dict):
                raise ConfigError(f"Compose service {service_name!r} must be an object.")
            if "ports" in model:
                if not isinstance(model["ports"], list):
                    raise ConfigError(f"Compose service {service_name!r} ports must be a list.")
                model["ports"] = _local_ports(model["ports"], service_name)
            mounts = model.get("volumes", [])
            if not isinstance(mounts, list):
                raise ConfigError(f"Compose service {service_name!r} volumes must be a list.")
            for mount in mounts:
                if isinstance(mount, str):
                    source = mount.split(":", 1)[0]
                    if not source.startswith((".", "/", "~")):
                        volumes[source] = None
            if service_name == "app":
                _merge(app, model, f"services.app from {instance_id}")
                continue
            if service_name in compose["services"]:
                raise ConfigError(f"Duplicate Compose service {service_name!r}.")
            compose["services"][service_name] = {"restart": "unless-stopped"}
            _merge(compose["services"][service_name], model, f"services.{service_name}")
            if "healthcheck" in model:
                _merge(app, {"depends_on": {service_name: {"condition": "service_healthy"}}}, "services.app")
        _merge(app["environment"], environment, f"services.app.environment from {instance_id}")
        if spec.get("network"):
            alias = spec["network"]
            extra_networks.add(alias)
            for service_name in own_names:
                _merge(compose["services"][service_name], {"networks": ["default", alias]}, f"services.{service_name}")
    if volumes:
        compose["volumes"] = volumes
    if extra_networks:
        _merge(app, {"networks": ["default", *sorted(extra_networks)]}, "services.app")
        compose["networks"] = {"default": {}}
        for alias in sorted(extra_networks):
            compose["networks"][alias] = {"external": True, "name": config.networks[alias]}
    if not production and any(service.group == "testing" for _, service, _ in selections):
        networks = app.get("networks", ["default"])
        if isinstance(networks, list):
            networks = {network: {} for network in networks}
        if not isinstance(networks, dict) or "default" not in networks:
            raise ConfigError("Browser testing requires the app on the default Compose network.")
        default = networks["default"]
        if not isinstance(default, dict):
            raise ConfigError("Browser testing requires a valid default network configuration.")
        aliases = default.setdefault("aliases", [])
        if not isinstance(aliases, list):
            raise ConfigError("Browser testing requires a list of default network aliases.")
        if "ship-app.test" not in aliases:
            aliases.append("ship-app.test")
        app["networks"] = networks
    if config.processes:
        app_image = f"{name}-app:{'local' if production else 'development'}"
        app["image"] = app_image
        for process_name, command in config.processes.items():
            if process_name in compose["services"]:
                raise ConfigError(f"Process {process_name!r} conflicts with another Compose service.")
            process = deepcopy(app)
            for key in ("build", "ports", "healthcheck"):
                process.pop(key, None)
            process["command"] = command
            compose["services"][process_name] = process
    if production:
        for service_name, model in compose["services"].items():
            for mount in model.get("volumes", []):
                if isinstance(mount, str) and mount.split(":", 1)[0].startswith((".", "/", "~")):
                    raise ConfigError(f"Production service {service_name!r} cannot use a host bind mount.")
                if isinstance(mount, dict) and mount.get("type") == "bind":
                    raise ConfigError(f"Production service {service_name!r} cannot use a host bind mount.")
            if "build" in model:
                model.setdefault("image", f"{name}-{service_name}:local")
    return compose


def write_compose(config: ShipConfig, project_root: Path, *, production: bool = False, model: dict | None = None) -> Path:
    model = build_compose(config, project_root, production=production) if model is None else model
    destination = project_root / "ship" / ("compose.production.json" if production else "compose.generated.json")
    destination.parent.mkdir(exist_ok=True)
    destination.write_text(json.dumps(model, indent=2) + "\n", encoding="utf-8")
    return destination
