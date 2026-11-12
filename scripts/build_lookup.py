"""Split a wheelgap scan into one small JSON file per package, for scripts and agents.

    python scripts/build_lookup.py data/coverage.json _site/data/packages [notes_dir]

Writes <out>/<name>.json for each package (name canonicalised as in PEP 503) and <out>.json
(e.g. data/packages.json) listing every package and target. Each package file carries the
package's current notes from notes_dir (default: the repository's notes/). Runs at publish
time, so the files are never committed.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

if __package__:  # imported as scripts.build_lookup (the tests)
    from . import check_notes
else:  # run as python scripts/build_lookup.py
    import check_notes

# A canonical name is also a safe file name: no slashes, dots or leading dashes.
SCHEMA = 1  # of the files written here: bump if a field changes meaning or goes away
SCAN_SCHEMA = 1  # of the coverage.json this reads
SAFE_NAME = re.compile(r"[a-z0-9]+(-[a-z0-9]+)*")
NOTES_DIR = Path(__file__).resolve().parent.parent / "notes"


def canonical(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def write_json(path: Path, value: object) -> None:
    path.write_text(json.dumps(value, separators=(",", ":")) + "\n", encoding="utf-8")


def build(coverage_path: Path, out: Path, notes_dir: Path | None = None) -> int:
    """Write the files; notes come from notes_dir, or none when it is None."""
    scan = json.loads(coverage_path.read_text(encoding="utf-8"))
    if scan.get("schema") != SCAN_SCHEMA:
        sys.exit(f"build_lookup: coverage.json schema {scan.get('schema')!r}, expected {SCAN_SCHEMA}")
    notes = check_notes.load(notes_dir, scan) if notes_dir is not None else {}
    labels = [t["label"] for t in scan["targets"]]
    out.mkdir(parents=True, exist_ok=True)
    index = {}
    for p in scan["packages"]:
        name = canonical(p["name"])
        if not SAFE_NAME.fullmatch(name) or name in index:
            print(f"::warning::build_lookup: skipping {p['name']!r}", file=sys.stderr)
            continue
        index[name] = p["version"]
        write_json(out / f"{name}.json", {
            "schema": SCHEMA,
            "name": name,
            "version": p["version"],
            "rank": p["rank"],
            "pure": p["pure"],
            "error": p["error"],
            "generated": scan["generated"],
            "wheelgap": scan["wheelgap"],
            "results": dict(zip(labels, p["results"], strict=True)),
            "notes": [
                check_notes.as_json(n) for n in check_notes.current(notes.get(name, []), p, scan["targets"])
            ],
        })
    write_json(out.with_suffix(".json"), {
        "schema": SCHEMA,
        "generated": scan["generated"],
        "wheelgap": scan["wheelgap"],
        "targets": scan["targets"],
        "packages": index,
    })
    return len(index)


if __name__ == "__main__":
    coverage, out = (Path(p) for p in sys.argv[1:3])
    notes_dir = Path(sys.argv[3]) if len(sys.argv) > 3 else NOTES_DIR
    print(f"lookup: {build(coverage, out, notes_dir)} packages in {out}")
