"""Run the local quality suite and expose failures as GitHub annotations."""

from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    completed = subprocess.run(
        [sys.executable, "scripts/check_local.py"],
        cwd=ROOT,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        check=False,
    )
    print(completed.stdout, end="", flush=True)
    if completed.returncode:
        message = completed.stdout[-10000:].replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")
        print(f"::error title=Python Ship quality failure::{message}", flush=True)
    return completed.returncode


if __name__ == "__main__":
    raise SystemExit(main())
