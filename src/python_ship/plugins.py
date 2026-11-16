"""Resolve ship.json's ``extensions`` into a registry of adapters, services and servers."""

from __future__ import annotations

from dataclasses import dataclass, field
from importlib.metadata import entry_points
import re
from typing import Any

from .adapters import BUILTIN_ADAPTERS
from .config import RESERVED_COMMANDS, ConfigError, ShipConfig
from .contracts import ASGIServer, FrameworkAdapter, ServiceDefinition
from .servers import BUILTIN_SERVERS
from .services import BUILTIN_SERVICES

GROUPS = {"adapter": "python_ship.adapters", "service": "python_ship.services", "server": "python_ship.servers"}
KEY = re.compile(r"[a-z][a-z0-9]*(?:[-_][a-z0-9]+)*")
GROUP = re.compile(r"[a-z][a-z0-9_-]*")
COMMAND = re.compile(r"[a-z][a-z0-9:_-]*")

_REQUIRED = {
    "adapter": ("detect", "default_app", "validate", "server_target", "console_commands", "release_commands"),
    "service": ("compose_fragment", "app_environment"),
    "server": ("development_command", "production_command"),
}
_OPTIONAL = {"adapter": (), "service": ("database_shell", "validate"), "server": ()}


@dataclass
class Registry:
    adapters: dict[str, FrameworkAdapter] = field(default_factory=dict)
    services: dict[str, ServiceDefinition] = field(default_factory=dict)
    servers: dict[str, ASGIServer] = field(default_factory=dict)

    def in_group(self, group: str) -> list[ServiceDefinition]:
        return [service for service in self.services.values() if service.group == group]

    def adapter(self, config: ShipConfig) -> FrameworkAdapter:
        adapter = self.adapters.get(config.framework)
        if adapter is None:
            raise ConfigError(
                f"Unknown framework adapter {config.framework!r}; use one of "
                f"{', '.join(sorted(self.adapters))}, or install and select its extension."
            )
        return adapter

    def server(self, config: ShipConfig) -> ASGIServer:
        server = self.servers.get(config.server)
        if server is None:
            raise ConfigError(
                f"Unknown ASGI server {config.server!r}; use one of {', '.join(sorted(self.servers))}, "
                "or install and select its extension."
            )
        return server


def _check(kind: str, selected: str, extension: Any, existing: dict[str, Any]) -> None:
    key = getattr(extension, "key", None)
    if not isinstance(key, str) or not KEY.fullmatch(key):
        raise ConfigError(f"Extension {selected!r} has an invalid key {key!r}.")
    if key in existing:
        raise ConfigError(f"Extension {selected!r} reuses the {kind} key {key!r}, which is already registered.")
    label = getattr(extension, "label", None)
    if not isinstance(label, str) or not label:
        raise ConfigError(f"Extension {selected!r} needs a nonempty label.")
    missing = [name for name in _REQUIRED[kind] if not callable(getattr(extension, name, None))]
    if missing:
        raise ConfigError(
            f"Extension {selected!r} does not implement the {kind} contract; missing: {', '.join(missing)}."
        )
    invalid = [name for name in _OPTIONAL[kind] if hasattr(extension, name) and not callable(getattr(extension, name))]
    if invalid:
        raise ConfigError(f"Extension {selected!r} has non-callable optional hooks: {', '.join(invalid)}.")
    if kind == "service":
        group = getattr(extension, "group", None)
        if not isinstance(group, str) or not GROUP.fullmatch(group):
            raise ConfigError(f"Extension {selected!r} has an invalid service group {group!r}.")
    if kind == "server":
        requirement = getattr(extension, "requirement", None)
        if not isinstance(requirement, str) or not requirement.strip():
            raise ConfigError(f"Extension {selected!r} needs a nonempty PEP 508 requirement.")


def load_extensions(config: ShipConfig) -> Registry:
    """Built-ins plus every explicitly selected entry point; nothing is loaded implicitly."""
    registry = Registry(dict(BUILTIN_ADAPTERS), dict(BUILTIN_SERVICES), dict(BUILTIN_SERVERS))
    targets: dict[str, dict[str, Any]] = {
        "adapter": registry.adapters, "service": registry.services, "server": registry.servers,
    }
    for selected in config.extensions:
        matches = [
            (kind, point)
            for kind, group in GROUPS.items()
            for point in entry_points(group=group)
            if point.name == selected
        ]
        if not matches:
            raise ConfigError(
                f"Extension {selected!r} is not installed; install its distribution in the same environment "
                "as the ship command (for example `uv tool install python-ship --with DISTRIBUTION`)."
            )
        if len(matches) > 1:
            raise ConfigError(
                f"Extension {selected!r} matches {len(matches)} python_ship entry points; "
                "each extension needs a unique entry point name."
            )
        kind, point = matches[0]
        try:
            loaded = point.load()
            extension = loaded() if isinstance(loaded, type) else loaded
        except Exception as exc:
            raise ConfigError(f"Could not load extension {selected!r}: {exc}") from exc
        target = targets[kind]
        _check(kind, selected, extension, target)
        target[extension.key] = extension
    return registry


def console_commands(adapter: FrameworkAdapter) -> dict[str, list[str]]:
    """An adapter's proxy commands, validated so they can't shadow Ship's own commands."""
    try:
        commands = adapter.console_commands()
    except Exception as exc:
        raise ConfigError(f"Adapter {adapter.key!r} console commands failed: {exc}") from exc
    if not isinstance(commands, dict):
        raise ConfigError(f"Adapter {adapter.key!r} console_commands() must return a dict.")
    for name, prefix in commands.items():
        if not isinstance(name, str) or not COMMAND.fullmatch(name) or name in RESERVED_COMMANDS:
            raise ConfigError(f"Adapter {adapter.key!r} console command {name!r} is invalid or reserved.")
        if not isinstance(prefix, list) or not prefix or any(not isinstance(part, str) or not part for part in prefix):
            raise ConfigError(f"Adapter {adapter.key!r} console command {name!r} must be a nonempty argument list.")
    return commands
