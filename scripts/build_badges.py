"""Build shields.io endpoint JSON badges from a wheelgap coverage scan.

    python scripts/build_badges.py data/coverage.json _site/badge

Writes <out>/<name>.json for each package (PEP 503 canonical name). Run at
publish time; the files are not committed.

Each badge counts the native platforms that have a wheel for the newest
non-free-threaded Python in the scan (today: 3.15, eight platforms). Pyodide is
left out: its wheels mostly come with Pyodide itself, not from PyPI. Colour: all
platforms brightgreen, at least three-quarters green, at least half yellow, any
orange, none red. A package whose lookup failed gets a grey "unknown" badge. The
label names the Python ("wheels py3.15"), so a badge says what it counts when the
scan moves to a newer Python.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

SCAN_SCHEMA = 1
# Results that install without a build. `bundled` is Pyodide's own build (wheelgap --pyodide-lock); the badge
# leaves Pyodide out today, but a bundled package still mustn't lose a platform if that changes.
INSTALLABLE = ("wheel", "bundled")
SAFE_NAME = re.compile(r"[a-z0-9]+(-[a-z0-9]+)*")
CACHE_SECONDS = 12 * 3600  # the scan runs nightly; shields.io's default cache is much shorter


def canonical(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def python_key(value: str) -> tuple[int, int]:
    major, minor = value.split(".", 1)
    return int(major), int(minor)


def newest_non_free_threaded(targets: list[dict]) -> str:
    pys = {t["python"] for t in targets if not str(t["python"]).endswith("t")}
    if not pys:
        sys.exit("build_badges: no non-free-threaded Python in coverage.json")
    return max(pys, key=python_key)


def color(wheel: int, total: int) -> str:
    if total <= 0:
        return "lightgrey"
    if wheel == total:
        return "brightgreen"
    if wheel * 4 >= total * 3:
        return "green"
    if wheel * 2 >= total:
        return "yellow"
    if wheel > 0:
        return "orange"
    return "red"


def badge_for(package: dict, indexes: list[int], python: str) -> dict:
    badge = {"schemaVersion": 1, "label": f"wheels py{python}", "cacheSeconds": CACHE_SECONDS}
    if package.get("error"):
        return badge | {"message": "unknown", "color": "lightgrey"}
    results = package["results"]
    total = len(indexes)
    wheel = sum(1 for i in indexes if results[i] in INSTALLABLE)
    noun = "platform" if total == 1 else "platforms"
    return badge | {"message": f"{wheel}/{total} {noun}", "color": color(wheel, total)}


def write_json(path: Path, value: object) -> None:
    path.write_text(json.dumps(value, separators=(",", ":")) + "\n", encoding="utf-8")


def build(coverage_path: Path, out: Path) -> int:
    scan = json.loads(coverage_path.read_text(encoding="utf-8"))
    if scan.get("schema") != SCAN_SCHEMA:
        sys.exit(f"build_badges: coverage.json schema {scan.get('schema')!r}, expected {SCAN_SCHEMA}")
    python = newest_non_free_threaded(scan["targets"])
    indexes = [i for i, t in enumerate(scan["targets"]) if t["python"] == python and t["platform"] != "pyodide"]
    if not indexes:
        sys.exit(f"build_badges: no targets for Python {python}")
    out.mkdir(parents=True, exist_ok=True)
    seen: set[str] = set()
    for p in scan["packages"]:
        if len(p["results"]) != len(scan["targets"]):
            sys.exit(f"build_badges: {p['name']!r} has {len(p['results'])} results for {len(scan['targets'])} targets")
        name = canonical(p["name"])
        if not SAFE_NAME.fullmatch(name) or name in seen:
            print(f"::warning::build_badges: skipping {p['name']!r}", file=sys.stderr)
            continue
        seen.add(name)
        write_json(out / f"{name}.json", badge_for(p, indexes, python))
    return len(seen)


if __name__ == "__main__":
    coverage, out = (Path(p) for p in sys.argv[1:3])
    print(f"badges: {build(coverage, out)} packages in {out}")
