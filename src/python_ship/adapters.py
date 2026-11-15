"""Built-in web framework adapters."""

import ast
from pathlib import Path
import re

from .config import ShipConfig


def _module_file(project_root: Path, target: str) -> Path:
    module = target.split(":", 1)[0]
    relative = Path(*module.split(".")).with_suffix(".py")
    direct = project_root / relative
    return direct if direct.is_file() else project_root / "src" / relative


def _target_problem(project_root: Path, target: str, label: str, example: str) -> list[str]:
    syntax = r"[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*:[A-Za-z_]\w*"
    if not re.fullmatch(syntax, target):
        return [f"{label} must use a module:callable target, e.g. {example}."]
    module = _module_file(project_root, target)
    if not module.is_file():
        return [f"{label} module for {target!r} does not exist; expected {module.relative_to(project_root)}."]
    try:
        tree = ast.parse(module.read_text(encoding="utf-8"), filename=str(module))
    except SyntaxError as exc:
        return [f"{label} module {module.relative_to(project_root)} has a syntax error at line {exc.lineno}: {exc.msg}."]
    callable_name = target.partition(":")[2]
    bindings = set()
    wildcard_import = False
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            bindings.add(node.name)
        elif isinstance(node, (ast.Assign, ast.AnnAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            bindings.update(name.id for target_node in targets for name in ast.walk(target_node) if isinstance(name, ast.Name))
        elif isinstance(node, (ast.Import, ast.ImportFrom)):
            wildcard_import |= any(alias.name == "*" for alias in node.names)
            bindings.update(alias.asname or alias.name.split(".")[0] for alias in node.names if alias.name != "*")
    if callable_name not in bindings and not wildcard_import:
        return [f"{label} callable {callable_name!r} is not defined in {module.relative_to(project_root)}."]
    return []


class GenericAdapter:
    """Run a plain ASGI callable without a web framework."""

    key = "generic"

    def detect(self, project_root: Path) -> bool:
        return True

    def validate(self, config: ShipConfig, project_root: Path) -> list[str]:
        return _target_problem(project_root, config.app, "App", "main:app")

    def server_target(self, config: ShipConfig) -> str:
        return config.app

    def development_command(self, config: ShipConfig) -> list[str]:
        return ["python", "-m", "uvicorn", config.app, "--host", "0.0.0.0", "--port", "8000", "--reload"]

    def production_command(self, config: ShipConfig) -> list[str]:
        return ["python", "-m", "uvicorn", config.app, "--host", "0.0.0.0", "--port", "8000"]

    def console_commands(self) -> dict[str, list[str]]:
        return {}

    def release_commands(self, config: ShipConfig) -> list[list[str]]:
        return []


class DjangoAdapter:
    key = "django"

    def detect(self, project_root: Path) -> bool:
        return (project_root / "manage.py").is_file()

    def validate(self, config: ShipConfig, project_root: Path) -> list[str]:
        problems = []
        if not self.detect(project_root):
            problems.append("Django adapter requires manage.py in the project root.")
        if not config.app.endswith(":application"):
            problems.append("Django app must point to an ASGI application target, e.g. project.asgi:application.")
        else:
            problems.extend(_target_problem(project_root, config.app, "Django app", "project.asgi:application"))
        return problems

    def server_target(self, config: ShipConfig) -> str:
        return config.app

    def development_command(self, config: ShipConfig) -> list[str]:
        return ["python", "-m", "uvicorn", config.app, "--host", "0.0.0.0", "--port", "8000", "--reload"]

    def production_command(self, config: ShipConfig) -> list[str]:
        return ["python", "-m", "uvicorn", config.app, "--host", "0.0.0.0", "--port", "8000"]

    def console_commands(self) -> dict[str, list[str]]:
        return {"manage": ["python", "manage.py"]}

    def release_commands(self, config: ShipConfig) -> list[list[str]]:
        return []

    def production_startup_commands(self, config: ShipConfig) -> list[list[str]]:
        commands = [["python", "manage.py", "check", "--deploy"]]
        if config.django_collectstatic:
            commands.append(["python", "manage.py", "collectstatic", "--noinput"])
        return commands


class FastAPIAdapter:
    key = "fastapi"

    def detect(self, project_root: Path) -> bool:
        # Import targets vary too much to guess from filenames alone.
        return False

    def validate(self, config: ShipConfig, project_root: Path) -> list[str]:
        return _target_problem(project_root, config.app, "FastAPI app", "app.main:app")

    def server_target(self, config: ShipConfig) -> str:
        return config.app

    def development_command(self, config: ShipConfig) -> list[str]:
        return ["python", "-m", "uvicorn", config.app, "--host", "0.0.0.0", "--port", "8000", "--reload"]

    def production_command(self, config: ShipConfig) -> list[str]:
        return ["python", "-m", "uvicorn", config.app, "--host", "0.0.0.0", "--port", "8000"]

    def console_commands(self) -> dict[str, list[str]]:
        return {}

    def release_commands(self, config: ShipConfig) -> list[list[str]]:
        return []


BUILTIN_ADAPTERS = {adapter.key: adapter for adapter in (GenericAdapter(), DjangoAdapter(), FastAPIAdapter())}
