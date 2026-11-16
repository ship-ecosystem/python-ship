"""Built-in framework adapters: plain ASGI, Django and FastAPI."""

from __future__ import annotations

import ast
from pathlib import Path
import re

from .config import ShipConfig

TARGET = re.compile(r"[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*:[A-Za-z_]\w*")
# Directories never searched when detecting a project's app module.
_SKIP = {".git", ".venv", "venv", "node_modules", "__pycache__", "dist", "build", "ship", "tests", ".tox"}


def module_file(project_root: Path, target: str) -> Path:
    """The file an import target refers to, at the project root or under ``src/``."""
    relative = Path(*target.split(":", 1)[0].split(".")).with_suffix(".py")
    direct = project_root / relative
    return direct if direct.is_file() else project_root / "src" / relative


def _module_name(project_root: Path, path: Path) -> str:
    relative = path.relative_to(project_root).with_suffix("")
    parts = relative.parts[1:] if relative.parts[0] == "src" else relative.parts
    return ".".join(parts)


def _bindings(path: Path) -> tuple[set[str], bool]:
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    except (OSError, SyntaxError, UnicodeDecodeError, ValueError):
        return set(), False
    names: set[str] = set()
    wildcard = False
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names.add(node.name)
        elif isinstance(node, (ast.Assign, ast.AnnAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            names.update(name.id for target in targets for name in ast.walk(target) if isinstance(name, ast.Name))
        elif isinstance(node, (ast.Import, ast.ImportFrom)):
            wildcard |= any(alias.name == "*" for alias in node.names)
            names.update(alias.asname or alias.name.split(".")[0] for alias in node.names if alias.name != "*")
    return names, wildcard


def target_problems(project_root: Path, target: str, label: str, example: str) -> list[str]:
    """Check an import target statically, without importing project code on the host."""
    if not TARGET.fullmatch(target):
        return [f"{label} must be a module:callable target, e.g. {example}."]
    module = module_file(project_root, target)
    if not module.is_file():
        return [f"{label} module for {target!r} does not exist; expected {module.relative_to(project_root)}."]
    try:
        ast.parse(module.read_text(encoding="utf-8"), filename=str(module))
    except SyntaxError as exc:
        return [f"{label} module {module.relative_to(project_root)} has a syntax error at line {exc.lineno}: {exc.msg}."]
    names, wildcard = _bindings(module)
    callable_name = target.partition(":")[2]
    if callable_name not in names and not wildcard:
        return [f"{label} callable {callable_name!r} is not defined in {module.relative_to(project_root)}."]
    return []


def python_files(project_root: Path, depth: int = 3) -> list[Path]:
    """Project Python files, shallowest first, skipping environments and build output."""
    found: list[Path] = []

    def walk(directory: Path, level: int) -> None:
        try:
            entries = sorted(directory.iterdir())
        except OSError:
            return
        found.extend(entry for entry in entries if entry.suffix == ".py" and entry.is_file())
        if level < depth:
            for entry in entries:
                if entry.is_dir() and entry.name not in _SKIP and not entry.name.startswith("."):
                    walk(entry, level + 1)

    walk(project_root, 1)
    return sorted(found, key=lambda path: (len(path.relative_to(project_root).parts), str(path)))


class GenericAdapter:
    """Serve any ASGI callable; no web framework required."""

    key = "generic"
    label = "Plain ASGI (no framework)"

    def detect(self, project_root: Path) -> bool:
        return True

    def default_app(self, project_root: Path) -> str | None:
        for module in ("main", "app", "asgi", "server"):
            path = project_root / f"{module}.py"
            if path.is_file():
                names, _ = _bindings(path)
                for name in ("app", "application"):
                    if name in names:
                        return f"{module}:{name}"
        return None

    def validate(self, config: ShipConfig, project_root: Path) -> list[str]:
        return target_problems(project_root, config.app, "App", "main:app")

    def server_target(self, config: ShipConfig) -> str:
        return config.app

    def console_commands(self) -> dict[str, list[str]]:
        return {}

    def release_commands(self, config: ShipConfig) -> list[list[str]]:
        return []


class DjangoAdapter:
    key = "django"
    label = "Django"

    def detect(self, project_root: Path) -> bool:
        return (project_root / "manage.py").is_file()

    def default_app(self, project_root: Path) -> str | None:
        manage = project_root / "manage.py"
        if manage.is_file():
            match = re.search(r"""DJANGO_SETTINGS_MODULE['"]\s*,\s*['"]([\w.]+)\.settings['"]""",
                              manage.read_text(encoding="utf-8", errors="replace"))
            if match and module_file(project_root, f"{match.group(1)}.asgi:application").is_file():
                return f"{match.group(1)}.asgi:application"
        for path in python_files(project_root):
            if path.name == "asgi.py" and "application" in _bindings(path)[0]:
                return f"{_module_name(project_root, path)}:application"
        return None

    def validate(self, config: ShipConfig, project_root: Path) -> list[str]:
        problems = []
        if not self.detect(project_root):
            problems.append("The Django adapter requires manage.py in the project root.")
        problems.extend(target_problems(project_root, config.app, "Django app", "project.asgi:application"))
        return problems

    def server_target(self, config: ShipConfig) -> str:
        return config.app

    def console_commands(self) -> dict[str, list[str]]:
        return {"manage": ["python", "manage.py"]}

    def release_commands(self, config: ShipConfig) -> list[list[str]]:
        commands = [["python", "manage.py", "check", "--deploy"]]
        if config.django_collectstatic:
            commands.append(["python", "manage.py", "collectstatic", "--noinput"])
        return commands


_FASTAPI_APP = re.compile(r"^([A-Za-z_]\w*)\s*(?::[^=\n]+)?=\s*(?:fastapi\.)?FastAPI\(", re.MULTILINE)


class FastAPIAdapter:
    key = "fastapi"
    label = "FastAPI"

    def _candidates(self, project_root: Path) -> list[tuple[Path, str]]:
        found = []
        for path in python_files(project_root):
            try:
                source = path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            if "fastapi" not in source:
                continue
            match = _FASTAPI_APP.search(source)
            if match:
                found.append((path, match.group(1)))
        return found

    def detect(self, project_root: Path) -> bool:
        return bool(self._candidates(project_root))

    def default_app(self, project_root: Path) -> str | None:
        candidates = self._candidates(project_root)
        if not candidates:
            return None
        path, name = candidates[0]
        return f"{_module_name(project_root, path)}:{name}"

    def validate(self, config: ShipConfig, project_root: Path) -> list[str]:
        return target_problems(project_root, config.app, "FastAPI app", "app.main:app")

    def server_target(self, config: ShipConfig) -> str:
        return config.app

    def console_commands(self) -> dict[str, list[str]]:
        return {}

    def release_commands(self, config: ShipConfig) -> list[list[str]]:
        return []


# Detection order for `ship init`: the most specific framework first, plain ASGI last.
BUILTIN_ADAPTERS = {adapter.key: adapter for adapter in (DjangoAdapter(), FastAPIAdapter(), GenericAdapter())}
