"""A host-side Starlette adapter; Starlette itself is a dependency of the app image."""

from pathlib import Path
import re

from python_ship.adapters import target_problems
from python_ship.config import ShipConfig


class ExampleStarletteAdapter:
    key = "example-starlette"
    label = "Starlette (example adapter)"

    def detect(self, project_root: Path) -> bool:
        requirements = project_root / "requirements.txt"
        return requirements.is_file() and "starlette" in requirements.read_text(encoding="utf-8").lower()

    def default_app(self, project_root: Path) -> str | None:
        source = project_root / "main.py"
        if source.is_file() and re.search(r"^app\s*=\s*Starlette\(", source.read_text(encoding="utf-8"), re.MULTILINE):
            return "main:app"
        return None

    def validate(self, config: ShipConfig, project_root: Path) -> list[str]:
        problems = target_problems(project_root, config.app, "Starlette app", "main:app")
        if not self.detect(project_root):
            problems.append("The Starlette adapter requires starlette in requirements.txt.")
        return problems

    def server_target(self, config: ShipConfig) -> str:
        return config.app

    def console_commands(self) -> dict[str, list[str]]:
        # Becomes `ship routes`, run in the app container.
        return {"routes": ["python", "-c", "import main; print('\\n'.join(r.path for r in main.app.routes))"]}

    def release_commands(self, config: ShipConfig) -> list[list[str]]:
        # Fail the production boot early if the app can't be imported.
        return [["python", "-c", f"import {config.app.split(':')[0]}"]]
