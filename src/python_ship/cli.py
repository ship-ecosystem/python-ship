"""The `ship` command line, built on Typer.

The command set mirrors PHP Ship: init, up, down, exec, shell, logs, db,
config:test, build and release, plus proxies (npm, uv, and each framework
adapter's console commands such as Django's `manage`).
"""

from __future__ import annotations

from collections.abc import Callable
from contextvars import ContextVar
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
from typing import Annotated, Any, NoReturn

import typer

from . import __version__
from .compose import (
    adapter_problems,
    build_compose,
    resolve_selections,
    selection_problems,
    write_compose,
)
from .config import ConfigError, ShipConfig
from .contracts import ShipEnvironment
from .init import InitOptions, ensure_dockerignore, ensure_gitignore, interactive_terminal, run_init
from .mutagen import enabled as mutagen_enabled
from .mutagen import start as start_mutagen
from .mutagen import stop as stop_mutagen
from .plugins import Registry, console_commands, load_extensions
from .production import credential_problems, read_env
from .release import TAG_PATTERN, assemble_release, plan_images
from .runtime import records, status, wait_for_stack
from .stubs import asset_problems, production_stub_problem, published_version, stub_problems, stub_version_warning
from .uv import UV_VERSION

OVERRIDE_FILES = (
    "docker-compose.override.yml", "docker-compose.override.yaml", "compose.override.yml", "compose.override.yaml",
)
PROXY_CONTEXT = {"allow_extra_args": True, "ignore_unknown_options": True, "help_option_names": []}
UV_STATEFUL = {"sync", "run", "pip", "python", "tool", "venv"}

_raw_argv: ContextVar[list[str] | None] = ContextVar("ship_raw_argv", default=None)
_project_root: ContextVar[Path | None] = ContextVar("ship_project_root", default=None)


# Helpers ----------------------------------------------------------------------------------------

def root() -> Path:
    return _project_root.get() or Path.cwd()


def proxy_tail(command: str, count: int) -> list[str]:
    """The raw arguments after a proxy command, untouched by option parsing."""
    argv = _raw_argv.get() or []
    index = 0
    while index < len(argv):
        if argv[index] == "--project-directory":
            index += 2
        elif argv[index].startswith("--project-directory="):
            index += 1
        else:
            break
    if index >= len(argv) or argv[index] != command:
        raise ConfigError(f"Could not locate the raw arguments of `ship {command}`.")
    return argv[index + count:]


def fail(message: str) -> NoReturn:
    raise ConfigError(message)


def warn(message: str) -> None:
    typer.echo(f"ship: warning: {message}", err=True)


def require_docker() -> None:
    if shutil.which("docker") is None:
        fail("Docker is required for this command; install Docker Desktop or Docker Engine with Compose v2.")


def override_file(project: Path) -> Path | None:
    return next((project / name for name in OVERRIDE_FILES if (project / name).is_file()), None)


def compose_prefix(project: Path, config: ShipConfig) -> list[str]:
    """The `docker compose` prefix every development command shares, with the override file applied."""
    path = write_compose(config, project)
    require_docker()
    prefix = ["docker", "compose", "--project-directory", str(project), "-f", str(path)]
    override = override_file(project)
    if override is not None:
        prefix += ["-f", str(override)]
    return prefix


def tty_flags() -> list[str]:
    # Without a terminal (CI, piped input) `docker compose exec` must not allocate one.
    return [] if sys.stdin.isatty() else ["-T"]


def run(command: list[str], project: Path) -> int:
    return subprocess.run(command, cwd=project, check=False).returncode


def execute(action: Callable[[], int]) -> int:
    try:
        return action()
    except (ConfigError, OSError) as exc:
        typer.echo(f"ship: {exc}", err=True)
        return 1


def read_config(project: Path) -> ShipConfig:
    return ShipConfig.read(project)


# Checks shared by config:test, doctor, up, build and release -------------------------------------

def config_problems(project: Path, *, production: bool = True) -> list[str]:
    """Every independent problem with ship.json, ship/ and both Compose models, in one pass.

    ``production=False`` skips the production model, for development-only diagnostics.
    """
    config, problems = ShipConfig.inspect(project)
    problems = list(problems)
    problems.extend(stub_problems(project, config))
    if config is None:
        return problems
    try:
        registry = load_extensions(config)
    except ConfigError as exc:
        return [*problems, str(exc)]
    problems.extend(adapter_problems(config, project, registry))
    if config.server not in registry.servers:
        problems.append(f"Unknown ASGI server {config.server!r}; use one of {', '.join(sorted(registry.servers))}.")
    problems.extend(selection_problems(config, registry))
    try:
        console_commands(registry.adapter(config))
    except ConfigError as exc:
        problems.append(str(exc))
    if not problems:
        for model_production in ((False, True) if production else (False,)):
            try:
                model = build_compose(config, project, production=model_production, registry=registry)
            except ConfigError as exc:
                problems.append(f"{'Production' if model_production else 'Development'} Compose model: {exc}")
                continue
            if model_production:
                problems.extend(credential_problems(model, project))
    problems.extend(asset_problems(project, config))
    return list(dict.fromkeys(problems))


# Production -------------------------------------------------------------------------------------

def build_production(project: Path, config: ShipConfig, tag: str) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    problem = production_stub_problem(project)
    if problem:
        fail(problem)
    problems = stub_problems(project, config)
    model = build_compose(config, project, production=True)
    problems += credential_problems(model, project) + asset_problems(project, config)
    if problems:
        fail(" ".join(problems))
    require_docker()
    ensure_dockerignore(project)
    ensure_gitignore(project)
    planned, images = plan_images(model, tag)
    path = write_compose(config, project, production=True, model=planned)
    env_file = project / ".env.production"
    # The override file is deliberately not applied: a dev-only override has no business
    # reaching a production image.
    prefix = ["docker", "compose", "--project-directory", str(project)]
    if env_file.is_file():
        prefix += ["--env-file", str(env_file)]
    command = prefix + ["-f", str(path), "build", *(image["owner"] for image in images)]
    environment = {**os.environ, **read_env(env_file)}
    if subprocess.run(command, cwd=project, env=environment, check=False).returncode != 0:
        fail("Building the production images failed; see the Docker output above.")
    return planned, images


# Commands ---------------------------------------------------------------------------------------

def version_callback(value: bool) -> None:
    if value:
        typer.echo(f"ship (python-ship) {__version__}")
        raise typer.Exit()


def create_app(adapter_commands: dict[str, list[str]] | None = None) -> typer.Typer:
    app = typer.Typer(
        help="Docker development environments and portable releases for Python web apps.",
        no_args_is_help=True,
        add_completion=True,
        rich_markup_mode=None,
        pretty_exceptions_enable=False,
    )

    @app.callback()
    def main_options(
        project_directory: Annotated[Path | None, typer.Option(
            "--project-directory", help="Run against the project in this directory instead of the current one.",
        )] = None,
        version: Annotated[bool, typer.Option(
            "--version", "-V", callback=version_callback, is_eager=True, help="Show the python-ship version and exit.",
        )] = False,
    ) -> None:
        if project_directory is not None:
            _project_root.set(project_directory.resolve())

    @app.command("init")
    def init_command(
        app_target: Annotated[str | None, typer.Option("--app", help="ASGI import target, such as main:app.")] = None,
        framework: Annotated[str | None, typer.Option(help="Framework adapter: generic, django, fastapi, or an extension.")] = None,
        server: Annotated[str | None, typer.Option(help="ASGI server: uvicorn, hypercorn, or an extension.")] = None,
        python_version: Annotated[str | None, typer.Option("--python", help="Python branch for the image, such as 3.13.")] = None,
        database: Annotated[str | None, typer.Option(help="Database service, or none.")] = None,
        cache: Annotated[str | None, typer.Option(help="Cache service, or none.")] = None,
        storage: Annotated[str | None, typer.Option(help="Object storage service, or none.")] = None,
        search: Annotated[str | None, typer.Option(help="Search service, or none.")] = None,
        mail: Annotated[str | None, typer.Option(help="Mail capture service, or none.")] = None,
        testing: Annotated[str | None, typer.Option(help="Browser testing service, or none.")] = None,
        frontend: Annotated[str | None, typer.Option(help="Frontend tooling service, or none.")] = None,
        silo_image: Annotated[str | None, typer.Option("--silo-image", help="Silo image: standard or distroless.")] = None,
        node_assets: Annotated[bool | None, typer.Option("--node-assets/--no-node-assets", help="Build npm assets into the production image.")] = None,
        force: Annotated[bool, typer.Option("--force", help="Republish ship/Dockerfile, replacing local edits.")] = False,
        no_interaction: Annotated[bool, typer.Option("--no-interaction", "-n", help="Don't prompt; use options, the existing ship.json and detection.")] = False,
    ) -> int:
        """Pick services, write ship.json and publish the ship/ directory."""
        options = InitOptions(
            app=app_target, framework=framework, server=server, python=python_version,
            selections={"database": database, "cache": cache, "storage": storage, "search": search,
                        "mail": mail, "testing": testing, "frontend": frontend},
            silo_image=silo_image, node_assets=node_assets, force=force,
            interactive=not no_interaction and interactive_terminal(),
        )
        return execute(lambda: run_init(root(), options))

    @app.command("up")
    def up_command() -> int:
        """Generate ship/docker-compose.generated.yml and start the development stack."""
        return execute(up)

    @app.command("down")
    def down_command(
        volumes: Annotated[bool, typer.Option("--volumes", "-v", help="Also delete named volumes (database and storage data).")] = False,
    ) -> int:
        """Stop and remove the development stack."""
        def action() -> int:
            project = root()
            config = read_config(project)
            prefix = compose_prefix(project, config)
            stop_mutagen(project, required=mutagen_enabled())
            return run(prefix + ["down", *(["--volumes"] if volumes else [])], project)
        return execute(action)

    @app.command("exec", context_settings=PROXY_CONTEXT)
    def exec_command(ctx: typer.Context, service: Annotated[str, typer.Argument(help="Compose service name.")]) -> int:
        """Run a command in a running service; every following argument is passed through."""
        def action() -> int:
            arguments = proxy_tail("exec", 2)
            if not arguments:
                fail("ship exec requires a command after the service name, such as `ship exec app python -V`.")
            project = root()
            prefix = compose_prefix(project, read_config(project))
            return run(prefix + ["exec", *tty_flags(), service, *arguments], project)
        return execute(action)

    @app.command("shell")
    def shell_command(service: Annotated[str | None, typer.Argument(help="Compose service name; defaults to the app.")] = None) -> int:
        """Open an interactive shell (sh) in a running service."""
        def action() -> int:
            project = root()
            config = read_config(project)
            prefix = compose_prefix(project, config)
            return run(prefix + ["exec", *tty_flags(), service or config.app_service, "sh"], project)
        return execute(action)

    @app.command("logs")
    def logs_command(
        service: Annotated[str | None, typer.Argument(help="Compose service name; all services when omitted.")] = None,
        follow: Annotated[bool, typer.Option("--follow", "-f", help="Stream new log output.")] = False,
    ) -> int:
        """Show container logs."""
        def action() -> int:
            project = root()
            prefix = compose_prefix(project, read_config(project))
            return run(prefix + ["logs", *(["--follow"] if follow else []), *([service] if service else [])], project)
        return execute(action)

    @app.command("db")
    def db_command(instance: Annotated[str | None, typer.Argument(help="Name of an additionalServices database; the default database when omitted.")] = None) -> int:
        """Open a database's interactive client, using credentials from its own container."""
        return execute(lambda: database_shell(instance))

    @app.command("config:test")
    def config_test_command() -> int:
        """Validate ship.json, ship/ and both Compose models without starting Docker."""
        def action() -> int:
            project = root()
            problems = config_problems(project)
            for problem in problems:
                typer.echo(f"ship: {problem}", err=True)
            if problems:
                typer.echo(f"ship: {len(problems)} problem{'s' if len(problems) != 1 else ''} found.", err=True)
                return 1
            warning = stub_version_warning(project)
            if warning:
                warn(warning)
            typer.echo("ship.json, ship/ and both Compose models are valid.")
            return 0
        return execute(action)

    @app.command("build")
    def build_command() -> int:
        """Build every project-owned production image, tagged locally."""
        def action() -> int:
            project = root()
            _, images = build_production(project, read_config(project), "local")
            for image in images:
                typer.echo(f"Built {image['image']} ({', '.join(image['services'])})")
            return 0
        return execute(action)

    @app.command("release")
    def release_command(tag: Annotated[str | None, typer.Option("--tag", help="Release tag, such as 1.2.0.")] = None) -> int:
        """Build the production images and assemble the portable release in dist/ship/<tag>/."""
        def action() -> int:
            chosen = tag
            if chosen is None:
                if not sys.stdin.isatty():
                    fail("ship release requires --tag when it isn't run from a terminal.")
                chosen = str(typer.prompt("Release tag")).strip()
            if not re.fullmatch(TAG_PATTERN, chosen):
                fail("Invalid release tag; use letters, digits, _, . or -, starting with a letter, digit or _.")
            project = root()
            config = read_config(project)
            if not (project / ".env.production").is_file():
                fail("ship release requires .env.production, even when no selected service needs credentials.")
            model, _ = build_production(project, config, chosen)
            destination = assemble_release(project, config, model, chosen)
            typer.echo(f"Release ready at {destination}")
            return 0
        return execute(action)

    @app.command("doctor")
    def doctor_command() -> int:
        """Report versions, stub freshness and the state and health of every container."""
        return execute(doctor)

    @app.command("npm", context_settings=PROXY_CONTEXT)
    def npm_command(ctx: typer.Context) -> int:
        """Run npm in the Node frontend service; arguments pass through."""
        def action() -> int:
            project = root()
            config = read_config(project)
            registry = load_extensions(config)
            nodes = [item for item in resolve_selections(config, registry) if item.service.key == "node"]
            if len(nodes) != 1:
                fail("ship npm requires the Node frontend service; select it with `ship init --frontend node`.")
            prefix = compose_prefix(project, config)
            return run(prefix + ["exec", *tty_flags(), nodes[0].service_name, "npm", *proxy_tail("npm", 1)], project)
        return execute(action)

    @app.command("uv", context_settings=PROXY_CONTEXT)
    def uv_command(ctx: typer.Context) -> int:
        """Run uv dependency commands (add, remove, lock, tree, ...) in a temporary container."""
        return execute(lambda: uv(proxy_tail("uv", 1)))

    for name, command_prefix in (adapter_commands or {}).items():
        app.command(name, context_settings=PROXY_CONTEXT, help=f"Run `{' '.join(command_prefix)}` in the app container; arguments pass through.")(
            _adapter_command(name, command_prefix)
        )
    return app


def _adapter_command(name: str, command_prefix: list[str]) -> Callable[[typer.Context], int]:
    def command(ctx: typer.Context) -> int:
        def action() -> int:
            project = root()
            config = read_config(project)
            prefix = compose_prefix(project, config)
            return run(prefix + ["exec", *tty_flags(), config.app_service, *command_prefix, *proxy_tail(name, 1)], project)
        return execute(action)
    command.__name__ = f"{name}_command"
    return command


def up() -> int:
    project = root()
    config = read_config(project)
    problems = stub_problems(project, config)
    if problems:
        fail(" ".join(problems))
    warning = stub_version_warning(project)
    if warning:
        warn(warning)
    prefix = compose_prefix(project, config)
    # A session left by an earlier container must go before Compose recreates it.
    stop_mutagen(project, required=mutagen_enabled())
    code = run(prefix + ["up", "--build", "--detach"], project)
    if code != 0:
        return code
    model = build_compose(config, project)
    if mutagen_enabled():
        try:
            start_mutagen(prefix, project, (config.app_service, *config.processes))
        except ConfigError:
            run(prefix + ["down"], project)
            raise
    port = wait_for_stack(prefix, set(model["services"]), config.app_service)
    typer.echo(f"The stack is running: http://127.0.0.1:{port}")
    return 0


def database_shell(instance: str | None) -> int:
    project = root()
    config = read_config(project)
    registry = load_extensions(config)
    databases = [item for item in resolve_selections(config, registry) if item.group == "database"]
    if not databases:
        fail("No database is selected; add one with `ship init --database postgres` (or mysql).")
    if instance is None:
        chosen = next((item for item in databases if item.instance_name is None), None)
        if chosen is None:
            names = ", ".join(str(item.instance_name) for item in databases)
            fail(f"No default database is selected; name one of the additional instances: {names}.")
    else:
        chosen = next((item for item in databases if item.instance_name == instance), None)
        if chosen is None:
            names = ", ".join(str(item.instance_name) for item in databases if item.instance_name) or "none"
            fail(f"No database instance named {instance!r}; additional database instances: {names}.")
    assert chosen is not None
    shell = getattr(chosen.service, "database_shell", None)
    if shell is None:
        fail(f"The {chosen.service.key!r} service doesn't provide a database shell.")
    command = shell(chosen.context(config, project, ShipEnvironment.DEVELOPMENT))
    prefix = compose_prefix(project, config)
    return run(prefix + ["exec", *tty_flags(), chosen.service_name, *command], project)


def uv(arguments: list[str]) -> int:
    project = root()
    config = read_config(project)
    if not arguments:
        fail("ship uv requires uv arguments, such as `ship uv add httpx` or `ship uv lock`.")
    if arguments[0] in UV_STATEFUL:
        fail(
            f"`uv {arguments[0]}` would change an environment that disappears with the temporary container. "
            "Run `ship up` to install dependencies into the app image, or `ship exec app ...` to run app commands."
        )
    require_docker()
    python = ".".join(config.python.split(".")[:2])
    image = f"ghcr.io/astral-sh/uv:{UV_VERSION}-python{python}-trixie-slim"
    command = [
        "docker", "run", "--rm", "--interactive",
        "--volume", f"{project.resolve()}:/app", "--workdir", "/app",
        "--env", "HOME=/tmp", "--env", "UV_CACHE_DIR=/tmp/ship-uv-cache",
        "--env", "UV_PROJECT_ENVIRONMENT=/tmp/ship-uv-venv",
        "--env", "UV_NO_SYNC=1", "--env", f"UV_PYTHON=python{python}",
    ]
    if sys.platform.startswith("linux") and hasattr(os, "getuid"):
        command += ["--user", f"{os.getuid()}:{os.getgid()}"]
    if sys.stdin.isatty() and sys.stdout.isatty():
        command.append("--tty")
    return run(command + [image, "uv", *arguments], project)


def doctor() -> int:
    project = root()
    problems = config_problems(project, production=False)
    for problem in problems:
        typer.echo(f"ship: {problem}", err=True)
    if problems:
        return 1
    config = read_config(project)
    model = build_compose(config, project)
    typer.echo(f"python-ship {__version__}; project {model['name']}; Python {config.python}; "
               f"framework {config.framework}; ASGI server {config.server}")
    typer.echo(f"Published ship/Dockerfile: python-ship {published_version(project) or 'unknown'}")
    warning = stub_version_warning(project)
    if warning:
        warn(warning)
    prefix = compose_prefix(project, config)
    result = subprocess.run(prefix + ["ps", "--all", "--format", "json"], cwd=project, capture_output=True, text=True, check=False)
    if result.returncode != 0:
        fail(f"Could not inspect Docker Compose: {result.stderr.strip()}. Check that Docker is running.")
    found = records(result.stdout)
    fatal, pending, port = status(found, set(model["services"]), config.app_service)
    for name, service in model["services"].items():
        record = found.get(name, {})
        health = record.get("Health")
        typer.echo(f"{name}: {service.get('image', 'built from ' + service.get('build', {}).get('dockerfile', 'a Dockerfile'))}; "
                   f"{record.get('State', 'missing')}" + (f"; health {health}" if health else ""))
    if fatal or pending:
        for problem in (*fatal, *pending):
            typer.echo(f"ship: {problem}", err=True)
        typer.echo("ship: run `ship logs SERVICE` to see why, then `ship up` to rebuild or restart the stack.", err=True)
        return 1
    typer.echo(f"Every service is running; the app is published at http://127.0.0.1:{port}")
    return 0


# Entry point ------------------------------------------------------------------------------------

def _root_from_argv(argv: list[str]) -> Path:
    for index, argument in enumerate(argv):
        if argument == "--project-directory" and index + 1 < len(argv):
            return Path(argv[index + 1]).resolve()
        if argument.startswith("--project-directory="):
            return Path(argument.split("=", 1)[1]).resolve()
        if not argument.startswith("-"):
            break
    return Path.cwd()


def discover_adapter_commands(project: Path) -> dict[str, list[str]]:
    """The selected adapter's console commands, registered like PHP Ship's artisan/console.

    A project whose ship.json can't be read yet simply gets no extra commands;
    `ship config:test` reports why.
    """
    if not (project / "ship.json").is_file():
        return {}
    try:
        config = ShipConfig.read(project)
        registry: Registry = load_extensions(config)
        return console_commands(registry.adapter(config))
    except (ConfigError, OSError):
        return {}


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    token = _raw_argv.set(argv)
    root_token = _project_root.set(None)
    # Typer detects the shell from the parent process; accept an explicit one for scripts.
    explicit_completion = argv[:1] == ["--show-completion"] and len(argv) > 1 and argv[1] in {
        "bash", "zsh", "fish", "powershell", "pwsh",
    }
    previous = os.environ.get("_TYPER_COMPLETE_TEST_DISABLE_SHELL_DETECTION")
    if explicit_completion:
        os.environ["_TYPER_COMPLETE_TEST_DISABLE_SHELL_DETECTION"] = "1"
    try:
        app = create_app(discover_adapter_commands(_root_from_argv(argv)))
        # A bare `ship` lists the commands and succeeds, like PHP Ship.
        result = app(args=argv or ["--help"], prog_name="ship", standalone_mode=False)
        return result if isinstance(result, int) else 0
    except KeyboardInterrupt:
        typer.echo("\nInterrupted.", err=True)
        return 130
    except Exception as exc:
        # Typer's (and, before 0.28, Click's) usage errors, Exit and Abort. Matched by shape
        # because Typer 0.28+ vendors Click privately.
        if type(exc).__name__ == "Abort":
            typer.echo("\nAborted.", err=True)
            return 130
        exit_code = getattr(exc, "exit_code", None)
        if isinstance(exit_code, int):
            show = getattr(exc, "show", None)
            if callable(show):
                show()
            return exit_code
        raise
    finally:
        _raw_argv.reset(token)
        _project_root.reset(root_token)
        if explicit_completion:
            if previous is None:
                os.environ.pop("_TYPER_COMPLETE_TEST_DISABLE_SHELL_DETECTION", None)
            else:
                os.environ["_TYPER_COMPLETE_TEST_DISABLE_SHELL_DETECTION"] = previous

