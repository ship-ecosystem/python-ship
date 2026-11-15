"""Load explicitly selected extensions from installed Python distributions."""

from importlib.metadata import entry_points
import re

from .adapters import BUILTIN_ADAPTERS
from .config import ConfigError, ShipConfig
from .services import BUILTIN_SERVICES
from .servers import BUILTIN_SERVERS


def load_extensions(config: ShipConfig) -> tuple[dict, dict]:
    adapters = dict(BUILTIN_ADAPTERS)
    services = dict(BUILTIN_SERVICES)
    for selected in config.extensions:
        matches = [
            (kind, point)
            for kind, group in (("adapter", "python_ship.adapters"), ("service", "python_ship.services"), ("server", "python_ship.servers"))
            for point in entry_points(group=group)
            if point.name == selected
        ]
        if not matches:
            raise ConfigError(f"Extension {selected!r} is not installed as a python_ship entry point; install its distribution in the Ship CLI environment.")
        if len(matches) > 1:
            raise ConfigError(f"Extension {selected!r} matches multiple python_ship entry points; give each extension a unique entry point name.")
        kind, point = matches[0]
        if kind == "server":
            continue
        try:
            extension = point.load()()
        except Exception as exc:
            raise ConfigError(f"Could not load extension {selected!r}: {exc}") from exc
        target = adapters if kind == "adapter" else services
        if not isinstance(getattr(extension, "key", None), str) or not re.fullmatch(r"[a-z][a-z0-9]*(?:[-_][a-z0-9]+)*", extension.key) or extension.key in target:
            raise ConfigError(f"Extension {selected!r} has an invalid or duplicate key.")
        expected = ("detect", "validate", "development_command", "production_command", "console_commands", "release_commands") if kind == "adapter" else ("group", "label", "compose_fragment", "app_environment")
        if any(not callable(getattr(extension, attr, None)) for attr in expected if attr not in {"group", "label"}):
            raise ConfigError(f"Extension {selected!r} does not implement the {kind} contract.")
        optional: tuple[str, ...]
        if kind == "service":
            if not isinstance(getattr(extension, "group", None), str) or not re.fullmatch(r"[a-z][a-z0-9_-]*", extension.group):
                raise ConfigError(f"Extension {selected!r} has an invalid service group.")
            if not isinstance(getattr(extension, "label", None), str) or not extension.label:
                raise ConfigError(f"Extension {selected!r} has an invalid service label.")
            optional = ("compose_fragment_for", "app_environment_for", "validate", "database_shell")
        else:
            optional = ("server_target", "production_startup_commands")
        if any(hasattr(extension, method) and not callable(getattr(extension, method)) for method in optional):
            raise ConfigError(f"Extension {selected!r} has an invalid optional {kind} hook.")
        target[extension.key] = extension
    return adapters, services


def load_servers(config: ShipConfig) -> dict:
    """Load built-in servers and explicitly selected community server entry points."""
    servers = dict(BUILTIN_SERVERS)
    for selected in config.extensions:
        matches = [point for point in entry_points(group="python_ship.servers") if point.name == selected]
        if not matches:
            continue
        if len(matches) != 1:
            raise ConfigError(f"Extension {selected!r} matches multiple python_ship server entry points; give each extension a unique entry point name.")
        try:
            server = matches[0].load()()
        except Exception as exc:
            raise ConfigError(f"Could not load server extension {selected!r}: {exc}") from exc
        if not isinstance(getattr(server, "key", None), str) or not re.fullmatch(r"[a-z][a-z0-9]*(?:[-_][a-z0-9]+)*", server.key) or server.key in servers:
            raise ConfigError(f"Server extension {selected!r} has an invalid or duplicate key.")
        if not isinstance(getattr(server, "requirement", None), str) or not server.requirement or any(
            not callable(getattr(server, method, None)) for method in ("development_command", "production_command")
        ):
            raise ConfigError(f"Server extension {selected!r} does not implement the server contract.")
        servers[server.key] = server
    return servers
