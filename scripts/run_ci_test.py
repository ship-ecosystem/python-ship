"""Run unittest and surface failures as GitHub job annotations."""

import subprocess
import sys


def main() -> int:
    completed = subprocess.run(
        [sys.executable, "-m", "unittest", *sys.argv[1:]],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        check=False,
    )
    print(completed.stdout, end="", flush=True)
    if completed.returncode:
        start = completed.stdout.find("\n======================================================================\n")
        end = completed.stdout.find("\nFAILED (", start)
        endline = completed.stdout.find("\n", end + 1) if end >= 0 else -1
        summary = completed.stdout[start:endline] if start >= 0 and endline >= 0 else completed.stdout[-6000:]
        message = summary[-10000:].replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")
        print(f"::error title=Python Ship test failure::{message}", flush=True)
    return completed.returncode


if __name__ == "__main__":
    raise SystemExit(main())
