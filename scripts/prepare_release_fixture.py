"""Build a disposable release for the clean-runner Docker boot check."""

from dataclasses import replace
from pathlib import Path
import shutil
import sys
from tempfile import TemporaryDirectory

from python_ship.cli import main as ship_main
from python_ship.config import ShipConfig

APP = """import os
import psycopg

async def app(scope, receive, send):
    if scope['type'] != 'http':
        return
    with psycopg.connect(host=os.environ['DB_HOST'], dbname=os.environ['DB_DATABASE'],
                         user=os.environ['DB_USERNAME'], password=os.environ['DB_PASSWORD']) as db:
        value = db.execute('SELECT value FROM ship_release_probe').fetchone()[0].encode()
    await send({'type': 'http.response.start', 'status': 200, 'headers': []})
    await send({'type': 'http.response.body', 'body': value})
"""

MIGRATE = (
    "import os,psycopg; "
    "db=psycopg.connect(host=os.environ['DB_HOST'],dbname=os.environ['DB_DATABASE'],"
    "user=os.environ['DB_USERNAME'],password=os.environ['DB_PASSWORD']); "
    "db.execute('CREATE TABLE ship_release_probe(value text)'); "
    "db.execute(\"INSERT INTO ship_release_probe VALUES ('deployed')\"); db.commit(); db.close()"
)


def main() -> None:
    if len(sys.argv) != 2:
        raise SystemExit("Usage: python scripts/prepare_release_fixture.py OUTPUT_DIRECTORY")
    destination = Path(sys.argv[1]).resolve()
    if destination.exists():
        raise SystemExit(f"Release fixture destination already exists: {destination}")
    with TemporaryDirectory(prefix="ship-clean-runner-build-") as directory:
        project = Path(directory)
        (project / "main.py").write_text(APP, encoding="utf-8")
        (project / "requirements.txt").write_text("psycopg[binary]>=3,<4\n", encoding="utf-8")
        (project / ".env.production").write_text(
            "DB_PASSWORD=ship-ci-only-password\nAPP_PORT=18080\n", encoding="utf-8"
        )
        if main_cli(project, "init", "--no-interaction", "--app", "main:app", "--database", "postgres") != 0:
            raise SystemExit("Could not initialize the release fixture")
        config = ShipConfig.read(project)
        replace(config, name="ship-clean-release", publish_ports=True,
                deploy_commands=[["python", "-c", MIGRATE]]).write(project)
        if main_cli(project, "config:test") != 0 or main_cli(project, "release", "--tag", "ci1") != 0:
            raise SystemExit("Could not build the release fixture")
        shutil.copytree(project / "dist" / "ship" / "ci1", destination)
    print(f"Clean-runner fixture ready at {destination}")


def main_cli(project: Path, *arguments: str) -> int:
    return ship_main(["--project-directory", str(project), *arguments])


if __name__ == "__main__":
    main()
