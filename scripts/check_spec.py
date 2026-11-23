"""Check offline schemas against a local checkout or the published canonical spec."""
import argparse
from pathlib import Path
from urllib.request import urlopen

ROOT = Path(__file__).resolve().parents[1]
BASE = "https://raw.githubusercontent.com/ship-ecosystem/spec/main/schemas/"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("spec", nargs="?", help="Local shared spec checkout; otherwise reads published main.")
    args = parser.parse_args()
    for name in ("ship.schema.json", "ship-release.schema.json"):
        expected = (Path(args.spec) / "schemas" / name).read_bytes() if args.spec else urlopen(BASE + name, timeout=30).read()
        for copy in (ROOT / "schema" / name, ROOT / "src/python_ship/schema" / name):
            if copy.read_bytes() != expected:
                raise SystemExit(f"Canonical schema drift: {copy.relative_to(ROOT)}. Review the spec and refresh the offline copy.")
        print(f"Canonical schema matches: {name}")


if __name__ == "__main__":
    main()
