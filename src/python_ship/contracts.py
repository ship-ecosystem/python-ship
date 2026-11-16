"""Public extension contracts.

Three kinds of extension plug into Ship, each registered under its own Python
entry point group and selected by name in ship.json's ``extensions`` list:

- ``python_ship.services``: a :class:`ServiceDefinition`, one optional piece of
  infrastructure (a database, a cache, object storage, ...).
- ``python_ship.adapters``: a :class:`FrameworkAdapter`, which keeps
  framework-specific knowledge (Django's ``manage.py``) out of the core.
- ``python_ship.servers``: an :class:`ASGIServer`, which serves an adapter's
  ASGI target (Uvicorn and Hypercorn are built in).

These protocols are the stable public API; everything else in the package is
an implementation detail.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

if TYPE_CHECKING:
    from .config import ShipConfig


class ShipEnvironment(StrEnum):
    """Which Compose model is being generated."""

    DEVELOPMENT = "development"
    PRODUCTION = "production"

    @property
    def is_development(self) -> bool:
        return self is ShipEnvironment.DEVELOPMENT

    @property
    def is_production(self) -> bool:
        return self is ShipEnvironment.PRODUCTION


@dataclass(frozen=True)
class ServiceContext:
    """Everything a service needs to build its fragment for one selection.

    ``instance_name`` is ``None`` for the default selection in ship.json's
    ``services`` and the entry's name for an ``additionalServices`` entry.
    ``service_name`` is the final Compose service name, after any
    ``serviceNames`` rename, so hostnames a service injects into the app must
    use it. ``resource_name`` stays stable across renames and is meant for
    named volumes, so renaming a service never orphans its data.
    """

    environment: ShipEnvironment
    config: ShipConfig
    project_root: Path
    key: str
    instance_name: str | None
    service_name: str

    @property
    def production(self) -> bool:
        return self.environment.is_production

    @property
    def env_prefix(self) -> str:
        """``""`` for the default instance, ``"ANALYTICS_"`` for one named analytics."""
        return f"{self.instance_name.upper()}_" if self.instance_name else ""

    @property
    def resource_name(self) -> str:
        return f"{self.key}-{self.instance_name}" if self.instance_name else self.key

    def credential(self, name: str, development_default: str) -> str:
        """A Compose expression for a secret: a default in development, required in production."""
        variable = self.env_prefix + name
        if self.production:
            return f"${{{variable}:?{variable} is required in .env.production}}"
        return f"${{{variable}:-{development_default}}}"

    def setting(self, name: str, default: str) -> str:
        """A Compose expression for a non-secret value with a default in every environment."""
        variable = self.env_prefix + name
        return f"${{{variable}:-{default}}}"


@runtime_checkable
class ServiceDefinition(Protocol):
    """One optional piece of the stack, selected per group in ship.json."""

    key: str
    """Unique key used in ship.json, such as ``"postgres"``."""

    label: str
    """Human-readable name shown by ``ship init``."""

    group: str
    """The group this service belongs to (database, cache, storage, ...)."""

    def compose_fragment(self, context: ServiceContext) -> dict[str, dict[str, Any]]:
        """Compose services this selection contributes, keyed by Compose service name.

        The fragment must define ``context.service_name``. It may also extend
        the ``app`` service; values are merged, never silently overwritten.
        Return ``{}`` to leave the service out of that environment entirely, as
        development-only services (Mailpit, browsers) do in production; the app
        then receives none of its environment variables either.
        """
        ...

    def app_environment(self, context: ServiceContext) -> dict[str, str]:
        """Environment variables injected into the app, such as ``DB_HOST``.

        For a named instance, prefix every variable with ``context.env_prefix``.
        """
        ...


@runtime_checkable
class ProvidesDatabaseShell(Protocol):
    """Optional: lets ``ship db`` open this database's interactive client."""

    def database_shell(self, context: ServiceContext) -> list[str]:
        """The command run inside ``context.service_name`` by ``ship db``."""
        ...


@runtime_checkable
class ValidatesConfiguration(Protocol):
    """Optional: report problems before Docker is invoked."""

    def validate(self, context: ServiceContext) -> list[str]:
        ...


@runtime_checkable
class FrameworkAdapter(Protocol):
    """Framework knowledge that the core package deliberately lacks."""

    key: str
    """Unique key used as ship.json's ``framework``, such as ``"django"``."""

    label: str
    """Human-readable name shown by ``ship init``."""

    def detect(self, project_root: Path) -> bool:
        """Whether this adapter applies to the project, used to suggest a default in ``ship init``."""
        ...

    def default_app(self, project_root: Path) -> str | None:
        """The ASGI target ``ship init`` suggests, such as ``"project.asgi:application"``."""
        ...

    def validate(self, config: ShipConfig, project_root: Path) -> list[str]:
        """Problems with the project or config; an empty list when it is usable."""
        ...

    def server_target(self, config: ShipConfig) -> str:
        """The ``module:callable`` target the selected ASGI server serves."""
        ...

    def console_commands(self) -> dict[str, list[str]]:
        """Proxy commands this adapter adds, such as ``{"manage": ["python", "manage.py"]}``.

        Each becomes ``ship <name> ARGS...``, run in the app container with the
        arguments forwarded unchanged. Names must not clash with Ship's own commands.
        """
        ...

    def release_commands(self, config: ShipConfig) -> list[list[str]]:
        """Commands run at every production container boot, before the server starts.

        For checks and cache warming that need real environment values, such as
        Django's ``check --deploy``. Never put migrations here: every container
        would run them at once. Use ship.json's ``deployCommands`` for those.
        """
        ...


@runtime_checkable
class ASGIServer(Protocol):
    """An ASGI server Ship installs in the app image and runs the target with."""

    key: str
    """Unique key used as ship.json's ``server``, such as ``"uvicorn"``."""

    label: str
    """Human-readable name shown by ``ship init``."""

    requirement: str
    """One PEP 508 requirement installed into the image, such as ``"uvicorn==0.54.0"``."""

    def development_command(self, target: str) -> list[str]:
        """Serve ``target`` on 0.0.0.0:8000 with reload."""
        ...

    def production_command(self, target: str) -> list[str]:
        """Serve ``target`` on 0.0.0.0:8000 for production."""
        ...
