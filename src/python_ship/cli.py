"""Command-line entry point."""

from importlib.resources import files
from pathlib import Path
import os
import shutil
import subprocess
import sys
from contextvars import ContextVar
from types import SimpleNamespace
from typing import Annotated

import typer

from . import __version__
from .compose import build_compose, selected_services, write_compose
from .config import ConfigError, ShipConfig
from .plugins import load_extensions
from .mutagen import enabled as mutagen_enabled, start as start_mutagen, stop as stop_mutagen
from .runtime import _records, _status, wait_for_stack
from .uv import UV_VERSION, require_uv


DOCKERIGNORE = (
    ".env\n.env.*\n!.env.example\n"
    "**/.env\n**/.env.*\n**/.venv\n**/__pycache__\n"
    "**/*.pem\n**/*.key\n**/id_rsa\n**/id_ed25519\nsecrets/\n"
    ".git\ndist/\nship/compose.generated.json\nship/compose.production.json\n"
)


app = typer.Typer(help="Docker environments for Python web apps")
PROXY_CONTEXT = {"allow_extra_args": True, "ignore_unknown_options": True, "help_option_names": []}
_raw_argv: ContextVar[list[str]] = ContextVar("ship_raw_argv", default=[])
_project_root: ContextVar[Path | None] = ContextVar("ship_project_root", default=None)


@app.callback()
def root_command(
    project_directory: Annotated[Path | None, typer.Option("--project-directory", help="Project containing ship.json")] = None,
) -> None:
    """Choose a Ship project from any working directory."""
    if project_directory is not None:
        _project_root.set(project_directory.resolve())


def _proxy_tail(command: str, count: int) -> list[str]:
    argv = _raw_argv.get()
    index = 0
    while index < len(argv):
        if argv[index] == "--project-directory":
            index += 2
        elif argv[index].startswith("--project-directory="):
            index += 1
        else:
            break
    if index >= len(argv) or argv[index] != command:
        raise ConfigError(f"Could not locate raw {command} arguments.")
    return argv[index + count:]


def _execute(command: str, **values: object) -> int:
    try:
        return _run(_project_root.get() or Path.cwd(), SimpleNamespace(command=command, **values))
    except (ConfigError, OSError) as exc:
        typer.echo(f"ship: {exc}", err=True)
        return 1


@app.command("init")
def init_command(
    app_target: Annotated[str | None, typer.Option("--app", help="ASGI import target, such as main:app")] = None,
    framework: Annotated[str | None, typer.Option(help="Framework adapter")] = None,
    server: Annotated[str | None, typer.Option(help="ASGI server: uvicorn, hypercorn, or a selected extension")] = None,
    python_version: Annotated[str | None, typer.Option("--python", help="Container Python minor version")] = None,
    database: Annotated[str | None, typer.Option(help="Database service")] = None,
    cache: Annotated[str | None, typer.Option(help="Cache service")] = None,
    mail: Annotated[str | None, typer.Option(help="Mail service")] = None,
    search: Annotated[str | None, typer.Option(help="Search service")] = None,
    storage: Annotated[str | None, typer.Option(help="Storage service")] = None,
    silo_image: Annotated[str | None, typer.Option("--silo-image", help="Silo image variant: distroless or standard")] = None,
    testing: Annotated[str | None, typer.Option(help="Browser testing service")] = None,
    frontend: Annotated[str | None, typer.Option(help="Frontend service")] = None,
    force: Annotated[bool, typer.Option(help="Refresh the published Dockerfile")] = False,
) -> int:
    """Create ship.json and a project Dockerfile."""
    return _execute("init", app=app_target, framework=framework, server=server, python=python_version,
                    database=database, cache=cache, mail=mail, search=search,
                    storage=storage, silo_image=silo_image, testing=testing, frontend=frontend, force=force)


@app.command("config:test")
def config_test_command() -> int:
    """Validate configuration without starting Docker."""
    return _execute("config:test")


@app.command("config:production")
def config_production_command() -> int:
    """Write a separate production Compose model."""
    return _execute("config:production")


@app.command("doctor")
def doctor_command() -> int:
    """Report project versions and live container health."""
    return _execute("doctor")


@app.command("up")
def up_command() -> int:
    """Generate Compose and start the development stack."""
    return _execute("up")


@app.command("down")
def down_command(volumes: Annotated[bool, typer.Option(help="Delete named data volumes too")] = False) -> int:
    """Stop the development stack."""
    return _execute("down", volumes=volumes)


@app.command("logs")
def logs_command(
    service: Annotated[str | None, typer.Argument(help="Compose service name")] = None,
    follow: Annotated[bool, typer.Option("--follow", "-f", help="Stream logs")] = False,
) -> int:
    """Show container logs."""
    return _execute("logs", service=service, follow=follow)


@app.command("shell")
def shell_command(service: Annotated[str, typer.Argument(help="Compose service name")] = "app") -> int:
    """Open an interactive shell in a running container."""
    return _execute("shell", service=service)


@app.command("db")
def db_command(service: Annotated[str | None, typer.Option("--service", help="Database instance name")] = None) -> int:
    """Open an interactive shell for a selected database."""
    return _execute("db", service=service)


@app.command("exec", context_settings=PROXY_CONTEXT)
def exec_command(ctx: typer.Context, service: Annotated[str, typer.Argument(help="Compose service name")]) -> int:
    """Run a command in a service container; remaining arguments pass through."""
    return _execute("exec", service=service, args=_proxy_tail("exec", 2))


@app.command("manage", context_settings=PROXY_CONTEXT)
def manage_command(ctx: typer.Context) -> int:
    """Run a Django manage.py command; all arguments pass through."""
    return _execute("manage", args=_proxy_tail("manage", 1))


@app.command("npm", context_settings=PROXY_CONTEXT)
def npm_command(ctx: typer.Context) -> int:
    """Run npm inside the optional Node container."""
    return _execute("npm", args=_proxy_tail("npm", 1))


@app.command("uv", context_settings=PROXY_CONTEXT)
def uv_command(ctx: typer.Context) -> int:
    """Run project dependency commands in an isolated uv and Python container."""
    return _execute("uv", args=_proxy_tail("uv", 1))


def _ensure_dockerignore(root: Path) -> None:
    path = root / ".dockerignore"
    existing = path.read_text(encoding="utf-8") if path.exists() else ""
    missing = [line for line in DOCKERIGNORE.splitlines() if line not in existing.splitlines()]
    if missing:
        with path.open("a", encoding="utf-8") as stream:
            if existing and not existing.endswith("\n"):
                stream.write("\n")
            stream.write("\n".join(missing) + "\n")


def _init(root: Path, args: SimpleNamespace) -> int:
    config_path = root / "ship.json"
    dockerfile = root / "ship" / "Dockerfile"
    existing = ShipConfig.read(root) if config_path.exists() else None
    if not args.app and existing is None:
        raise ConfigError("ship init requires --app for a new project.")
    if dockerfile.exists() and existing is None and not args.force:
        raise ConfigError("ship/Dockerfile already exists; use --force to replace it.")
    services = dict(existing.services) if existing else {}
    for group in ("database", "cache", "mail", "search", "storage", "testing", "frontend"):
        selected = getattr(args, group)
        if selected == "none":
            services.pop(group, None)
        elif selected is not None:
            services[group] = selected
    config = ShipConfig(
        args.framework or (existing.framework if existing else "generic"),
        args.app or (existing.app if existing else ""),
        args.python or (existing.python if existing else "3.13"),
        services,
        list(existing.extensions) if existing else [],
        existing.name if existing else None,
        dict(existing.instances) if existing else {},
        dict(existing.networks) if existing else {},
        args.silo_image or (existing.silo_image if existing else "distroless"),
        args.server or (existing.server if existing else "uvicorn"),
    )
    # Validate the complete model before writing any project files.
    build_compose(config, root)
    server_stub_problem = _server_stub_problem(root, config)
    if server_stub_problem and not args.force:
        raise ConfigError(server_stub_problem)
    needs_garage = "garage" in config.services.values() or any(
        selection.get("service") == "garage" for selection in config.instances.values()
    )
    require_uv()
    dockerfile.parent.mkdir(exist_ok=True)
    version_file = dockerfile.parent / ".ship-version"
    published = args.force or not dockerfile.exists()
    if published:
        dockerfile.write_text(
            files("python_ship").joinpath("templates/Dockerfile")
            .read_text(encoding="utf-8")
            .replace("@UV_VERSION@", UV_VERSION),
            encoding="utf-8",
        )
        version_file.write_text(__version__ + "\n", encoding="utf-8")
    elif not version_file.is_file() or version_file.read_text(encoding="utf-8").strip() != __version__:
        typer.echo("ship: warning: published Dockerfile version differs from this Ship installation; use `ship init --force` to refresh it.", err=True)
    if needs_garage:
        garage_dir = dockerfile.parent / "garage"
        garage_dir.mkdir(exist_ok=True)
        for filename in ("Dockerfile", "garage.toml"):
            target = garage_dir / filename
            if args.force or not target.exists():
                target.write_text(
                    files("python_ship").joinpath("templates", "garage", filename).read_text(encoding="utf-8"),
                    encoding="utf-8",
                )
    config.write(root)
    _ensure_dockerignore(root)
    print("Updated ship.json and kept the existing Dockerfile." if existing and not published else
          "Created ship.json and ship/Dockerfile. Run `ship up` to start the stack.")
    return 0


def _compose_prefix(root: Path, config: ShipConfig) -> list[str]:
    compose = write_compose(config, root)
    if shutil.which("docker") is None:
        raise ConfigError("Docker is required for this command.")
    prefix = ["docker", "compose", "--project-directory", str(root), "-f", str(compose)]
    override = root / "compose.override.yaml"
    if override.exists():
        prefix.extend(["-f", str(override)])
    return prefix


def _stub_warning(root: Path) -> str | None:
    version_file = root / "ship" / ".ship-version"
    if not version_file.is_file() or version_file.read_text(encoding="utf-8").strip() != __version__:
        return "published Dockerfile version differs from this Ship installation; run `ship init --force` to refresh it."
    return None


def _server_stub_problem(root: Path, config: ShipConfig) -> str | None:
    dockerfile = root / "ship" / "Dockerfile"
    if config.server != "uvicorn" and dockerfile.is_file() and "SHIP_SERVER_REQUIREMENT" not in dockerfile.read_text(encoding="utf-8"):
        return "ship/Dockerfile cannot install the selected ASGI server; run `ship init --force` to refresh it."
    return None


def _production_stub_problem(root: Path) -> str | None:
    dockerfile = root / "ship" / "Dockerfile"
    if not dockerfile.is_file() or "AS production" not in dockerfile.read_text(encoding="utf-8"):
        return "ship/Dockerfile has no production stage; run `ship init --force` to refresh it."
    return None


def _garage_stub_problems(root: Path, config: ShipConfig) -> list[str]:
    selected = "garage" in config.services.values() or any(
        instance.get("service") == "garage" for instance in config.instances.values()
    )
    if not selected:
        return []
    return [
        f"ship/garage/{filename} is missing; run `ship init` to publish Garage's build files."
        for filename in ("Dockerfile", "garage.toml")
        if not (root / "ship" / "garage" / filename).is_file()
    ]


def _config_problems(root: Path) -> list[str]:
    config, problems = ShipConfig.inspect(root)
    if not (root / "ship" / "Dockerfile").is_file():
        problems.append("ship/Dockerfile is missing; run `ship init`.")
    if config is None:
        return problems
    server_stub_problem = _server_stub_problem(root, config)
    if server_stub_problem:
        problems.append(server_stub_problem)
    problems.extend(_garage_stub_problems(root, config))
    try:
        adapters, services = load_extensions(config)
    except ConfigError as exc:
        problems.append(str(exc))
        return problems
    adapter = adapters.get(config.framework)
    if adapter is None:
        problems.append(f"Unknown framework adapter {config.framework!r}.")
    else:
        try:
            adapter_problems = adapter.validate(config, root)
            if not isinstance(adapter_problems, list) or any(not isinstance(item, str) for item in adapter_problems):
                raise ConfigError(f"Adapter {config.framework!r} returned invalid validation results.")
            problems.extend(adapter_problems)
        except Exception as exc:
            problems.append(f"Adapter {config.framework!r} validation failed: {exc}")
    for group, key in config.services.items():
        service = services.get(key)
        if service is None or service.group != group:
            problems.append(f"Unknown service {key!r} in group {group!r}.")
    try:
        selected_services(config, services)
    except ConfigError as exc:
        if str(exc) not in problems:
            problems.append(str(exc))
    if not problems:
        try:
            build_compose(config, root)
        except ConfigError as exc:
            problems.append(str(exc))
    return problems


def _run(root: Path, args: SimpleNamespace) -> int:
    if args.command == "init":
        return _init(root, args)
    if args.command == "uv":
        config = ShipConfig.read(root)
        if not args.args:
            raise ConfigError("ship uv requires uv arguments, such as `ship uv add httpx` or `ship uv lock`.")
        if args.args[0] in {"sync", "run", "pip", "python", "tool"}:
            raise ConfigError("ship uv runs dependency metadata commands in a temporary container. Use `ship up` to install dependencies in the app image, or `ship exec app` to run app commands.")
        if shutil.which("docker") is None:
            raise ConfigError("Docker is required for `ship uv`.")
        image = f"ghcr.io/astral-sh/uv:{UV_VERSION}-python{config.python}-trixie-slim"
        command = [
            "docker", "run", "--rm", "--interactive",
            "--volume", f"{root.resolve()}:/app", "--workdir", "/app",
            "--env", "HOME=/tmp", "--env", "UV_CACHE_DIR=/tmp/ship-uv-cache",
            "--env", "UV_PROJECT_ENVIRONMENT=/tmp/ship-uv-venv",
            "--env", "UV_NO_SYNC=1", "--env", f"UV_PYTHON=python{config.python}",
        ]
        if sys.platform.startswith("linux") and hasattr(os, "getuid"):
            command += ["--user", f"{os.getuid()}:{os.getgid()}"]
        if sys.stdin.isatty() and sys.stdout.isatty():
            command.append("--tty")
        command += [image, "uv", *args.args]
        return subprocess.run(command, cwd=root, check=False).returncode
    if args.command == "config:test":
        problems = _config_problems(root)
        for problem in problems:
            typer.echo(f"ship: {problem}", err=True)
        if problems:
            return 1
        warning = _stub_warning(root)
        if warning:
            typer.echo(f"ship: warning: {warning}", err=True)
        print("ship.json and the development Compose model are valid.")
        return 0
    if args.command == "config:production":
        config = ShipConfig.read(root)
        problem = _production_stub_problem(root)
        if problem:
            raise ConfigError(problem)
        path = write_compose(config, root, production=True)
        print(path)
        return 0
    if args.command == "doctor":
        problems = _config_problems(root)
        for problem in problems:
            typer.echo(f"ship: {problem}", err=True)
        if problems:
            return 1
        config = ShipConfig.read(root)
        model = build_compose(config, root)
        print(f"Ship {__version__}; project {model['name']}; Python {config.python}; ASGI server {config.server}")
        published_version = root / "ship" / ".ship-version"
        print("Published Dockerfile: " + (published_version.read_text(encoding="utf-8").strip() if published_version.is_file() else "unversioned"))
        warning = _stub_warning(root)
        if warning:
            typer.echo(f"ship: warning: {warning}", err=True)
        prefix = _compose_prefix(root, config)
        result = subprocess.run(prefix + ["ps", "--all", "--format", "json"], cwd=root, capture_output=True, text=True, check=False)
        if result.returncode != 0:
            raise ConfigError(f"Could not inspect Docker Compose: {result.stderr.strip()}. Check Docker and run `ship up`.")
        records = _records(result.stdout)
        fatal, pending, _ = _status(records, set(model["services"]))
        for name, service in model["services"].items():
            record = records.get(name, {})
            image = service.get("image", "local build")
            state = record.get("State", "missing")
            health = record.get("Health")
            print(f"{name}: {image}; {state}" + (f"; health {health}" if health else ""))
        if fatal or pending:
            for problem in [*fatal, *pending]:
                typer.echo(f"ship: {problem}", err=True)
            typer.echo("ship: run `ship logs SERVICE` for failures, then `ship up` to rebuild or restart the stack.", err=True)
            return 1
        print("All selected services are running and the app port is published.")
        return 0
    config = ShipConfig.read(root)
    if args.command == "up":
        server_stub_problem = _server_stub_problem(root, config)
        if server_stub_problem:
            raise ConfigError(server_stub_problem)
        stub_problems = _garage_stub_problems(root, config)
        if stub_problems:
            raise ConfigError(" ".join(stub_problems))
        warning = _stub_warning(root)
        if warning:
            typer.echo(f"ship: warning: {warning}", err=True)
    prefix = _compose_prefix(root, config)
    if args.command == "up":
        # Terminate any session from a prior container before Compose recreates it.
        stop_mutagen(root, required=mutagen_enabled())
        command = prefix + ["up", "--build", "-d"]
    elif args.command == "down":
        stop_mutagen(root, required=mutagen_enabled())
        command = prefix + ["down"] + (["--volumes"] if args.volumes else [])
    elif args.command == "logs":
        command = prefix + ["logs"] + (["--follow"] if args.follow else []) + ([args.service] if args.service else [])
    elif args.command == "shell":
        command = prefix + ["exec", args.service, "sh"]
    elif args.command == "db":
        _, definitions = load_extensions(config)
        databases = [(id, service, spec) for id, service, spec in selected_services(config, definitions) if spec["group"] == "database"]
        chosen = next((item for item in databases if item[0] == args.service), None) if args.service else next((item for item in databases if item[0] == "database"), None)
        if chosen is None and args.service is None and len(databases) == 1:
            chosen = databases[0]
        if chosen is None:
            raise ConfigError("Select a database instance with `ship db --service NAME`.")
        _, service, spec = chosen
        shell = getattr(service, "database_shell", None)
        if shell is None:
            raise ConfigError(f"Database service {service.key!r} does not provide an interactive shell.")
        command = prefix + ["exec", spec["name"], *shell(config, spec)]
    elif args.command == "exec":
        if not args.args:
            raise ConfigError("ship exec requires a command after the service name.")
        command = prefix + ["exec", args.service, *args.args]
    elif args.command == "manage":
        adapters, _ = load_extensions(config)
        adapter = adapters[config.framework]
        manage_prefix = adapter.console_commands().get("manage")
        if manage_prefix is None:
            raise ConfigError("The selected framework has no manage command.")
        command = prefix + ["exec", "app", *manage_prefix, *args.args]
    elif args.command == "npm":
        _, definitions = load_extensions(config)
        node_services = [(id, spec) for id, service, spec in selected_services(config, definitions) if service.key == "node"]
        if len(node_services) != 1:
            raise ConfigError("ship npm requires exactly one Node frontend service.")
        command = prefix + ["exec", node_services[0][1]["name"], "npm", *args.args]
    else:
        raise ConfigError(f"Unknown command {args.command!r}.")
    result = subprocess.run(command, cwd=root, check=False)
    if args.command == "up" and result.returncode == 0:
        if mutagen_enabled():
            try:
                start_mutagen(prefix, root)
            except ConfigError:
                subprocess.run(prefix + ["down"], cwd=root, check=False)
                raise
        wait_for_stack(prefix, set(build_compose(config, root)["services"]))
        print("Ship stack is running and the app port is reachable.")
    return result.returncode


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    token = _raw_argv.set(argv)
    root_token = _project_root.set(None)
    # Typer detects the shell from the parent process by default. Accept an
    # explicit shell for scripts and terminals where detection is unavailable.
    explicit_completion = argv[:1] == ["--show-completion"] and len(argv) > 1 and argv[1] in {
        "bash", "zsh", "fish", "powershell", "pwsh"
    }
    old_completion_mode = os.environ.get("_TYPER_COMPLETE_TEST_DISABLE_SHELL_DETECTION")
    if explicit_completion:
        os.environ["_TYPER_COMPLETE_TEST_DISABLE_SHELL_DETECTION"] = "1"
    try:
        return app(args=argv, prog_name="ship", standalone_mode=False) or 0
    except Exception as exc:
        exit_code = getattr(exc, "exit_code", None)
        if isinstance(exit_code, int) and hasattr(exc, "show"):
            exc.show()
            return exit_code
        raise
    finally:
        _raw_argv.reset(token)
        _project_root.reset(root_token)
        if explicit_completion:
            if old_completion_mode is None:
                os.environ.pop("_TYPER_COMPLETE_TEST_DISABLE_SHELL_DETECTION", None)
            else:
                os.environ["_TYPER_COMPLETE_TEST_DISABLE_SHELL_DETECTION"] = old_completion_mode
