"""Publish the 50 highest-ranked packages with a source or missing gap for each target.

`bundled` (Pyodide ships its own build) isn't a gap, and nor is `unknown` (a failed lookup).

    python scripts/build_most_wanted.py data/coverage.json _site/data/most-wanted.json

Built at publish time; the generated file is not committed.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

SCAN_SCHEMA = 1
GAP = ("source", "missing")


def build(coverage_path: Path, out: Path) -> int:
    scan = json.loads(coverage_path.read_text(encoding="utf-8"))
    if scan.get("schema") != SCAN_SCHEMA:
        sys.exit(f"build_most_wanted: coverage.json schema {scan.get('schema')!r}, expected {SCAN_SCHEMA}")
    labels = [target["label"] for target in scan["targets"]]
    wanted = {label: [] for label in labels}
    for package in sorted(scan["packages"], key=lambda p: p["rank"]):
        if package["error"]:
            continue
        for label, status in zip(labels, package["results"], strict=True):
            if status in GAP and len(wanted[label]) < 50:
                wanted[label].append({
                    "rank": package["rank"],
                    "name": package["name"],
                    "version": package["version"],
                    "status": status,
                })
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({
        "generated": scan["generated"],
        "targets": wanted,
    }, separators=(",", ":")) + "\n", encoding="utf-8")
    return sum(len(packages) for packages in wanted.values())


if __name__ == "__main__":
    coverage, out = (Path(p) for p in sys.argv[1:3])
    print(f"most-wanted: {build(coverage, out)} gaps in {out}")
