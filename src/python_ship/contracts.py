"""Public interfaces for framework and infrastructure extensions."""

from pathlib import Path
from typing import Protocol, TYPE_CHECKING

if TYPE_CHECKING:
    from .config import ShipConfig


class FrameworkAdapter(Protocol):
    """Required adapter members. Commands are Docker Compose command lists."""

    key: str

    def detect(self, project_root: Path) -> bool: ...

    def validate(self, config: "ShipConfig", project_root: Path) -> list[str]: ...

    def development_command(self, config: "ShipConfig") -> list[str]: ...

    def production_command(self, config: "ShipConfig") -> list[str]: ...

    def console_commands(self) -> dict[str, list[str]]: ...

    def release_commands(self, config: "ShipConfig") -> list[list[str]]: ...


class ServerTargetCapability(Protocol):
    """Optional adapter hook for using a selected ASGI server."""

    def server_target(self, config: "ShipConfig") -> str: ...


class ProductionStartupCapability(Protocol):
    """Optional adapter hook run before each production server start."""

    def production_startup_commands(self, config: "ShipConfig") -> list[list[str]]: ...


class ServiceDefinition(Protocol):
    key: str
    group: str
    label: str

    def compose_fragment(self) -> dict: ...

    def app_environment(self) -> dict[str, str]: ...


class ConfigAwareServiceDefinition(ServiceDefinition, Protocol):
    """Optional hooks for services that need their instance settings."""

    def compose_fragment_for(self, config: "ShipConfig", instance: dict[str, str]) -> dict: ...

    def app_environment_for(self, config: "ShipConfig", instance: dict[str, str]) -> dict[str, str]: ...


class DatabaseShellCapability(Protocol):
    """Optional command used by `ship db`."""

    def database_shell(self, config: "ShipConfig", instance: dict[str, str]) -> list[str]: ...


class ASGIServerDefinition(Protocol):
    """Installed through a python_ship.servers entry point and selected in ship.json."""

    key: str
    requirement: str

    def development_command(self, target: str) -> list[str]: ...

    def production_command(self, target: str) -> list[str]: ...
