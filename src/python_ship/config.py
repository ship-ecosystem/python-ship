"""Project configuration stored in ship.json."""

from dataclasses import dataclass, field
import json
from pathlib import Path
import re


class ConfigError(ValueError):
    """A project configuration is missing or invalid."""


@dataclass(frozen=True)
class ShipConfig:
    framework: str
    app: str
    python: str = "3.13"
    services: dict[str, str] = field(default_factory=dict)
    extensions: list[str] = field(default_factory=list)
    name: str | None = None
    instances: dict[str, dict[str, str]] = field(default_factory=dict)
    networks: dict[str, str] = field(default_factory=dict)
    silo_image: str = "distroless"
    server: str = "uvicorn"
    publish_ports: bool = False
    processes: dict[str, list[str]] = field(default_factory=dict)
    django_collectstatic: bool = False
    node_assets: bool = False
    deploy_commands: list[list[str]] = field(default_factory=list)

    @classmethod
    def read(cls, project_root: Path) -> "ShipConfig":
        config, problems = cls.inspect(project_root)
        if problems:
            raise ConfigError("; ".join(problems))
        assert config is not None
        return config

    @classmethod
    def inspect(cls, project_root: Path) -> tuple["ShipConfig | None", list[str]]:
        """Read every independent schema error for config:test."""
        path = project_root / "ship.json"
        def unique_pairs(pairs):
            result = {}
            for key, value in pairs:
                if key in result:
                    raise ConfigError(f"Duplicate ship.json key {key!r}.")
                result[key] = value
            return result
        try:
            data = json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=unique_pairs)
        except FileNotFoundError:
            return None, ["No ship.json found; run `ship init` first."]
        except json.JSONDecodeError as exc:
            return None, [f"Invalid ship.json: {exc}"]
        except ConfigError as exc:
            return None, [str(exc)]
        if not isinstance(data, dict):
            return None, ["ship.json must contain a JSON object."]
        problems = []
        allowed = {"framework", "app", "python", "services", "extensions", "name", "instances", "networks", "silo_image", "server", "publish_ports", "processes", "django_collectstatic", "node_assets", "deploy_commands"}
        unknown = set(data) - allowed
        if unknown:
            problems.append(f"Unknown ship.json fields: {', '.join(sorted(unknown))}")
        framework = data.get("framework", "generic")
        if not isinstance(framework, str) or not framework:
            problems.append("ship.json framework must be a nonempty adapter name.")
        if not isinstance(data.get("app"), str) or not data["app"]:
            problems.append("ship.json requires a nonempty app import target.")
        python = data.get("python", "3.13")
        if not isinstance(python, str) or not re.fullmatch(r"3\.\d{1,2}", python):
            problems.append("ship.json python must be a minor version such as 3.13.")
        services = data.get("services", {})
        if not isinstance(services, dict) or any(
            not isinstance(k, str) or not isinstance(v, str) for k, v in services.items()
        ):
            problems.append("ship.json services must map group names to service names.")
        instances = data.get("instances", {})
        if not isinstance(instances, dict):
            problems.append("ship.json instances must map instance names to service selections.")
        else:
            for instance_name, selection in instances.items():
                if not isinstance(instance_name, str) or not re.fullmatch(r"[a-z][a-z0-9]*(?:[-_][a-z0-9]+)*", instance_name):
                    problems.append(f"ship.json instance name {instance_name!r} is invalid.")
                if not isinstance(selection, dict):
                    problems.append(f"ship.json instance {instance_name!r} must be an object.")
                    continue
                unknown_selection = set(selection) - {"group", "service", "name", "prefix", "network"}
                if unknown_selection:
                    problems.append(f"ship.json instance {instance_name!r} has unknown fields: {', '.join(sorted(map(str, unknown_selection)))}.")
                for required in ("group", "service"):
                    if not isinstance(selection.get(required), str) or not selection[required]:
                        problems.append(f"ship.json instance {instance_name!r} requires a nonempty {required}.")
                selected_name = selection.get("name", instance_name)
                if not isinstance(selected_name, str) or not re.fullmatch(r"[a-z][a-z0-9]*(?:[-_][a-z0-9]+)*", selected_name):
                    problems.append(f"ship.json instance {instance_name!r} has an invalid Compose service name.")
                prefix = selection.get("prefix", instance_name.upper().replace("-", "_"))
                if not isinstance(prefix, str) or not re.fullmatch(r"[A-Z][A-Z0-9_]*", prefix):
                    problems.append(f"ship.json instance {instance_name!r} has an invalid environment prefix.")
                if "network" in selection and (not isinstance(selection["network"], str) or not selection["network"]):
                    problems.append(f"ship.json instance {instance_name!r} has an invalid network alias.")
        networks = data.get("networks", {})
        if not isinstance(networks, dict) or any(
            not isinstance(alias, str) or not re.fullmatch(r"[a-z][a-z0-9_-]*", alias) or alias == "default"
            or not isinstance(external, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*", external)
            for alias, external in networks.items()
        ):
            problems.append("ship.json networks must map non-default aliases to external Docker network names.")
        extensions = data.get("extensions", [])
        if not isinstance(extensions, list) or any(
            not isinstance(item, str) for item in extensions
        ):
            problems.append("ship.json extensions must be a list of entry point names.")
        elif len(extensions) != len(set(extensions)):
            problems.append("ship.json extensions contains duplicate entry point names.")
        name = data.get("name")
        if name is not None and (not isinstance(name, str) or not re.fullmatch(r"[a-z][a-z0-9]*(?:[-_][a-z0-9]+)*", name)):
            problems.append("ship.json name must start with a lowercase letter and use single - or _ separators between lowercase letters or digits.")
        silo_image = data.get("silo_image", "distroless")
        if silo_image not in ("distroless", "standard"):
            problems.append("ship.json silo_image must be 'distroless' or 'standard'.")
        server = data.get("server", "uvicorn")
        if not isinstance(server, str) or not re.fullmatch(r"[a-z][a-z0-9]*(?:[-_][a-z0-9]+)*", server):
            problems.append("ship.json server must be a nonempty ASGI server key.")
        publish_ports = data.get("publish_ports", False)
        if not isinstance(publish_ports, bool):
            problems.append("ship.json publish_ports must be a boolean.")
        processes = data.get("processes", {})
        if not isinstance(processes, dict):
            problems.append("ship.json processes must map service names to command argument lists.")
        else:
            for process_name, command in processes.items():
                if not isinstance(process_name, str) or not re.fullmatch(r"[a-z][a-z0-9]*(?:[-_][a-z0-9]+)*", process_name) or process_name == "app":
                    problems.append(f"ship.json process name {process_name!r} is invalid or reserved.")
                if not isinstance(command, list) or not command or any(not isinstance(part, str) or not part for part in command):
                    problems.append(f"ship.json process {process_name!r} must have a nonempty argument list.")
        django_collectstatic = data.get("django_collectstatic", False)
        if not isinstance(django_collectstatic, bool):
            problems.append("ship.json django_collectstatic must be a boolean.")
        elif django_collectstatic and framework != "django":
            problems.append("ship.json django_collectstatic requires the Django adapter.")
        node_assets = data.get("node_assets", False)
        if not isinstance(node_assets, bool):
            problems.append("ship.json node_assets must be a boolean.")
        deploy_commands = data.get("deploy_commands", [])
        if not isinstance(deploy_commands, list) or any(
            not isinstance(command, list) or not command or any(not isinstance(part, str) or not part for part in command)
            for command in deploy_commands
        ):
            problems.append("ship.json deploy_commands must be a list of nonempty command argument lists.")
        if problems:
            return None, problems
        return cls(framework, data["app"], python, services, extensions, name, instances, networks, silo_image, server, publish_ports, processes, django_collectstatic, node_assets, deploy_commands), []

    def write(self, project_root: Path) -> None:
        payload = {
            "framework": self.framework,
            "app": self.app,
            "python": self.python,
            "services": self.services,
            "extensions": self.extensions,
        }
        if self.name is not None:
            payload["name"] = self.name
        if self.instances:
            payload["instances"] = self.instances
        if self.networks:
            payload["networks"] = self.networks
        if self.silo_image != "distroless":
            payload["silo_image"] = self.silo_image
        if self.server != "uvicorn":
            payload["server"] = self.server
        if self.publish_ports:
            payload["publish_ports"] = True
        if self.processes:
            payload["processes"] = self.processes
        if self.django_collectstatic:
            payload["django_collectstatic"] = True
        if self.node_assets:
            payload["node_assets"] = True
        if self.deploy_commands:
            payload["deploy_commands"] = self.deploy_commands
        (project_root / "ship.json").write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
