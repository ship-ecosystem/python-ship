"""`ship init`: pick services, write ship.json and publish the ship/ directory."""

from __future__ import annotations

from dataclasses import dataclass, replace
import json
from pathlib import Path
import re
import sys

import typer

from . import __version__
from .compose import app_interface, build_compose
from .config import (
    GROUPS_SUPPORTING_ADDITIONAL_INSTANCES,
    INSTANCE_NAME,
    SILO_IMAGES,
    AdditionalService,
    ConfigError,
    ShipConfig,
)
from .contracts import FrameworkAdapter
from .plugins import Registry, load_extensions
from .security import DEFAULT_PYTHON, problem_with
from .stubs import asset_stub_problem, publish_stubs, server_stub_problem, stub_version_warning
from .uv import require_uv

NONE = "none"
# Groups `ship init` asks about, in order, with the label used in its prompt.
GROUPS = {
    "database": "database",
    "cache": "cache",
    "broker": "message broker",
    "tasks": "background task queue",
    "storage": "object storage",
    "search": "search engine",
    "mail": "mail capture service",
    "testing": "browser testing driver",
    "frontend": "frontend toolchain",
}

DOCKERIGNORE = (
    ".env\n.env.*\n!.env.example\n"
    "**/.env\n**/.env.*\n**/.venv\n**/__pycache__\n"
    "**/*.pem\n**/*.key\n**/id_rsa\n**/id_ed25519\nsecrets/\n"
    "node_modules/\n**/node_modules/\nstaticfiles/\n"
    ".npmrc\n**/.npmrc\nauth.json\n**/auth.json\n"
    ".git\ndist/\nship/docker-compose.generated.yml\nship/docker-compose.production.yml\n"
)
# Re-applied as the file's last lines before every production build, so a hand-edited
# .dockerignore can't re-include a secret or an earlier release into the next image.
RELEASE_IGNORE_GUARD = (
    "# python-ship release guard\n"
    ".env\n.env.*\n**/.env\n**/.env.*\n"
    "dist/ship/\n.npmrc\n**/.npmrc\nauth.json\n**/auth.json\n"
)
GITIGNORE = (
    "/.env.production",
    "/dist/ship/",
    "/ship/docker-compose.generated.yml",
    "/ship/docker-compose.production.yml",
)

VITE_SNIPPET = """\
export default defineConfig({
    // ...
    server: {
        host: '0.0.0.0',        // bind inside the container, not just loopback
        port: Number(process.env.VITE_PORT ?? 5173),
        strictPort: true,       // fail fast instead of silently picking an unpublished port
        hmr: { host: 'localhost' }, // what the browser connects back to
    },
});"""


@dataclass
class InitOptions:
    app: str | None = None
    framework: str | None = None
    server: str | None = None
    python: str | None = None
    selections: dict[str, str | None] | None = None
    silo_image: str | None = None
    node_assets: bool | None = None
    force: bool = False
    interactive: bool = False


def _article(noun: str) -> str:
    return "an" if noun[:1].lower() in "aeiou" else "a"


def interactive_terminal() -> bool:
    return sys.stdin.isatty() and sys.stdout.isatty()


def select(label: str, choices: dict[str, str], default: str | None = None) -> str:
    """A numbered picker; accepts the number, the key or the label."""
    keys = list(choices)
    default = default if default in choices else keys[0]
    typer.echo(f"\n{label}:")
    for index, key in enumerate(keys, 1):
        marker = "*" if key == default else " "
        typer.echo(f" {marker} [{index}] {choices[key]}")
    while True:
        answer = str(typer.prompt(label, default=str(keys.index(default) + 1))).strip()
        if answer.isdigit() and 1 <= int(answer) <= len(keys):
            return keys[int(answer) - 1]
        for key, text in choices.items():
            if answer.lower() in (key.lower(), text.lower()):
                return key
        typer.echo(f"{answer!r} is not a valid choice.", err=True)


def _detect_framework(registry: Registry, root: Path) -> FrameworkAdapter:
    # Specific frameworks first; the generic adapter detects everything.
    adapters = sorted(registry.adapters.values(), key=lambda adapter: adapter.key == "generic")
    for adapter in adapters:
        try:
            if adapter.detect(root):
                return adapter
        except Exception:
            continue
    return registry.adapters["generic"]


def _default_app(adapter: FrameworkAdapter, root: Path) -> str | None:
    try:
        target = adapter.default_app(root)
    except Exception:
        return None
    return target if isinstance(target, str) and target else None


def _groups(registry: Registry) -> dict[str, str]:
    groups = dict(GROUPS)
    for service in registry.services.values():
        groups.setdefault(service.group, service.group)
    return groups


def _prompt_additional(registry: Registry, existing: list[AdditionalService]) -> list[AdditionalService]:
    additional = list(existing)
    used = {entry.name for entry in additional}
    while typer.confirm(
        "Add a named additional service instance (e.g. a second database)?" if not additional else "Add another one?",
        default=False,
    ):
        groups = {group: group for group in GROUPS_SUPPORTING_ADDITIONAL_INSTANCES if registry.in_group(group)}
        if not groups:
            break
        group = select("Which kind of service?", groups)
        service = select("Which one?", {item.key: item.label for item in registry.in_group(group)})
        while True:
            name = str(typer.prompt(
                "Name this instance (its env var prefix and Compose service suffix, e.g. \"analytics\")"
            )).strip().lower()
            if not INSTANCE_NAME.fullmatch(name):
                typer.echo(f"\"{name}\" must use lowercase letters, digits and underscores, starting with a letter.", err=True)
            elif name in used:
                typer.echo(f"\"{name}\" is already used; pick a different name.", err=True)
            else:
                break
        used.add(name)
        additional.append(AdditionalService(group, service, name))
    return additional


def _prompt_silo_image(current: str) -> str:
    typer.echo(
        "\nSilo publishes two images of the same release. The standard one is built on RHEL 9, which\n"
        "requires a CPU with the x86-64-v2 instruction set: on an older CPU, or a virtual machine that\n"
        "exposes a generic CPU model, its container fails to start. The distroless one has no such\n"
        "requirement, but no shell either, so `ship shell` can't open one in it.\n"
        "Pick distroless if any machine that will run this stack (yours, a teammate's, the production\n"
        "server) might be affected. On Linux, check a machine with:\n"
        "    /lib64/ld-linux-x86-64.so.2 --help | grep x86-64-v2\n"
        "You can switch later with ship.json's \"siloImage\"; both images use the same data volume."
    )
    return select("Which Silo image?", {
        "standard": "Standard (needs an x86-64-v2 CPU)",
        "distroless": "Distroless (any CPU, no shell)",
    }, current)


def _prompt_python(current: str) -> str:
    while True:
        answer = str(typer.prompt("Python version", default=current)).strip()
        problem = problem_with(answer)
        if problem is None:
            return answer
        typer.echo(problem, err=True)


def _silo_selected(services: dict[str, str], additional: list[AdditionalService]) -> bool:
    return services.get("storage") == "silo" or any(entry.service == "silo" for entry in additional)


def resolve_config(root: Path, options: InitOptions) -> ShipConfig:
    """Combine flags, answers, the existing ship.json and detection into the new config."""
    existing = ShipConfig.lenient(root) if (root / "ship.json").exists() else None
    base = existing or ShipConfig()
    registry = load_extensions(base)
    interactive = options.interactive

    if options.framework and options.framework not in registry.adapters:
        raise ConfigError(f"Unknown framework {options.framework!r}; use one of {', '.join(sorted(registry.adapters))}.")
    detected = _detect_framework(registry, root)
    framework = options.framework or (existing.framework if existing else detected.key)
    if interactive and not options.framework:
        framework = select("Select a framework", {key: adapter.label for key, adapter in registry.adapters.items()}, framework)
    adapter = registry.adapters[framework]

    suggested = existing.app if existing and existing.framework == framework else _default_app(adapter, root)
    app = options.app
    if app is None and interactive:
        app = str(typer.prompt("App target (module:callable)", default=suggested or "main:app")).strip()
    app = app or suggested
    if not app:
        raise ConfigError(
            f"Could not find the {adapter.label} app to serve; pass it with --app, such as `--app main:app`."
        )

    # Suggest servers that can run this app: ASGI (FastAPI, Django's asgi.py) or WSGI (Flask, wsgi.py).
    interface = app_interface(replace(base, framework=framework, app=app), root, registry)
    compatible = registry.servers_for(interface)
    if options.server and options.server not in registry.servers:
        raise ConfigError(f"Unknown web server {options.server!r}; use one of {', '.join(sorted(registry.servers))}.")
    current = base.server if base.server and base.server in [item.key for item in compatible] else None
    server = options.server or current or compatible[0].key
    if interactive and not options.server:
        choices = {item.key: item.label + (" (recommended)" if index == 0 else "") for index, item in enumerate(compatible)}
        server = select(f"Select a web server for this {interface.upper()} app", choices, server)
    registry.server(replace(base, server=server), interface)  # Rejects a server that can't run this app.

    services = dict(base.services)
    flags = options.selections or {}
    for group, label in _groups(registry).items():
        choices = {NONE: "None", **{item.key: item.label for item in registry.in_group(group)}}
        flagged = flags.get(group)
        if flagged is not None:
            if flagged != NONE and flagged not in choices:
                raise ConfigError(f"Unknown {group} service {flagged!r}; use one of {', '.join(choices)}.")
            answer = flagged
        elif interactive and len(choices) > 1:
            answer = select(f"Select {_article(label)} {label}", choices, services.get(group, NONE))
        else:
            continue
        if answer == NONE:
            services.pop(group, None)
        else:
            services[group] = answer

    additional = list(base.additional_services)
    if interactive:
        additional = _prompt_additional(registry, additional)

    silo_image = options.silo_image or base.silo_image
    if options.silo_image and options.silo_image not in SILO_IMAGES:
        raise ConfigError("--silo-image must be standard or distroless.")
    if interactive and not options.silo_image and _silo_selected(services, additional):
        silo_image = _prompt_silo_image(silo_image)

    python = options.python or base.python
    if options.python:
        problem = problem_with(options.python)
        if problem:
            raise ConfigError(problem)
    elif interactive:
        python = _prompt_python(base.python or DEFAULT_PYTHON)

    return replace(
        base,
        framework=framework,
        app=app,
        server=server,
        python=python,
        services=services,
        additional_services=additional,
        silo_image=silo_image,
        node_assets=base.node_assets if options.node_assets is None else options.node_assets,
        django_collectstatic=base.django_collectstatic and framework == "django",
    )


def ensure_dockerignore(root: Path) -> None:
    path = root / ".dockerignore"
    existing = path.read_text(encoding="utf-8") if path.exists() else ""
    missing = [line for line in DOCKERIGNORE.splitlines() if line not in existing.splitlines()]
    if missing:
        with path.open("a", encoding="utf-8") as stream:
            if existing and not existing.endswith("\n"):
                stream.write("\n")
            stream.write("\n".join(missing) + "\n")
    current = path.read_text(encoding="utf-8")
    if not current.endswith(RELEASE_IGNORE_GUARD):
        current = current.replace(RELEASE_IGNORE_GUARD, "")
        path.write_text(current.rstrip("\n") + "\n" + RELEASE_IGNORE_GUARD, encoding="utf-8")


def ensure_gitignore(root: Path) -> None:
    """Merge the secret and generated-file rules into .gitignore without touching other entries."""
    path = root / ".gitignore"
    current = path.read_text(encoding="utf-8") if path.is_file() else ""
    lines = {line.strip() for line in current.splitlines()}
    missing = [rule for rule in GITIGNORE if rule not in lines and rule.lstrip("/") not in lines]
    if not missing:
        return
    header = "# Added by `ship init`: production secrets, release output and generated files.\n"
    with path.open("a", encoding="utf-8") as stream:
        if current and not current.endswith("\n"):
            stream.write("\n")
        if current:
            stream.write("\n")
        stream.write(header + "\n".join(missing) + "\n")


def _vite_reminder(root: Path, config: ShipConfig) -> str | None:
    package = root / "package.json"
    if config.services.get("frontend") != "node" or not package.is_file():
        return None
    try:
        data = json.loads(package.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    dependencies = {**data.get("dependencies", {}), **data.get("devDependencies", {})} if isinstance(data, dict) else {}
    if "vite" not in dependencies:
        return None
    for name in ("vite.config.js", "vite.config.ts", "vite.config.mjs", "vite.config.mts"):
        config_file = root / name
        if config_file.is_file() and re.search(r"0\.0\.0\.0|host:\s*true", config_file.read_text(encoding="utf-8")):
            return None
    return ("Vite runs in the node container, so its dev server must bind 0.0.0.0 and tell the browser where "
            "to connect for HMR. Add this to your vite.config.js:\n\n" + VITE_SNIPPET)


def run_init(root: Path, options: InitOptions) -> int:
    dockerfile = root / "ship" / "Dockerfile"
    had_config = (root / "ship.json").exists()
    if dockerfile.exists() and not had_config and not options.force:
        raise ConfigError("ship/Dockerfile already exists; use --force to replace it.")
    config = resolve_config(root, options)
    # Validate the complete model before writing any project file.
    build_compose(config, root)
    for problem in (server_stub_problem(root, config), asset_stub_problem(root, config)):
        if problem and dockerfile.exists() and not options.force:
            raise ConfigError(problem)
    require_uv()
    published = publish_stubs(root, config, force=options.force)
    config.write(root)
    ensure_dockerignore(root)
    ensure_gitignore(root)
    if not published:
        warning = stub_version_warning(root)
        if warning:
            typer.echo(f"ship: warning: {warning}", err=True)
    reminder = _vite_reminder(root, config)
    if reminder:
        typer.echo(f"ship: note: {reminder}", err=True)
    if published:
        typer.echo(f"Wrote ship.json and published the ship/ directory (python-ship {__version__}). "
                   "Run `ship up` to build and start the environment.")
    else:
        typer.echo("Updated ship.json and kept your ship/Dockerfile. Run `ship up` to apply the changes.")
    return 0

