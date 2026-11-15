"""A host-side Starlette adapter; Starlette itself lives in the app image."""

from pathlib import Path
import re


class ExampleStarletteAdapter:
    key = "example-starlette"

    def detect(self, project_root: Path) -> bool:
        requirements = project_root / "requirements.txt"
        return requirements.is_file() and "starlette" in requirements.read_text(encoding="utf-8").lower()

    def validate(self, config, project_root: Path) -> list[str]:
        if not re.fullmatch(r"[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*:[A-Za-z_]\w*", config.app):
            return ["Starlette app must use a module:callable target, such as main:app."]
        module = project_root.joinpath(*config.app.partition(":")[0].split(".")).with_suffix(".py")
        if not module.is_file():
            return [f"Starlette app module {module.relative_to(project_root)} is missing."]
        if not self.detect(project_root):
            return ["Starlette adapter requires starlette in requirements.txt."]
        return []

    def development_command(self, config) -> list[str]:
        return ["python", "-m", "uvicorn", config.app, "--host", "0.0.0.0", "--port", "8000", "--reload"]

    def production_command(self, config) -> list[str]:
        return ["python", "-m", "uvicorn", config.app, "--host", "0.0.0.0", "--port", "8000", "--lifespan", "on"]

    def production_startup_commands(self, config) -> list[list[str]]:
        return [["python", "-c", "import starlette"]]

    def console_commands(self) -> dict[str, list[str]]:
        return {}

    def release_commands(self, config) -> list[list[str]]:
        return []
