"""Write the scan as a CSV for spreadsheets and notebooks.

    python scripts/build_csv.py data/coverage.json _site/data/coverage.csv

Writes one row per package: rank, name, version, downloads, pure, error, then one
column per target, named by its label, holding wheel/bundled/source/missing/unknown/n/a.
Runs at publish time, so the file is never committed.
"""

from __future__ import annotations

import csv
import json
import sys
from pathlib import Path

SCAN_SCHEMA = 1  # of the coverage.json this reads


def cell(value: object) -> object:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    return value


def build(coverage_path: Path, out: Path) -> int:
    scan = json.loads(coverage_path.read_text(encoding="utf-8"))
    if scan.get("schema") != SCAN_SCHEMA:
        sys.exit(f"build_csv: coverage.json schema {scan.get('schema')!r}, expected {SCAN_SCHEMA}")
    labels = [t["label"] for t in scan["targets"]]
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["rank", "name", "version", "downloads", "pure", "error", *labels])
        for package in scan["packages"]:
            writer.writerow(
                [
                    cell(package["rank"]),
                    package["name"],
                    cell(package.get("version")),
                    cell(package.get("downloads")),
                    cell(package.get("pure")),
                    cell(package.get("error")),
                    *package["results"],
                ]
            )
    return len(scan["packages"])


if __name__ == "__main__":
    coverage, out = (Path(p) for p in sys.argv[1:3])
    print(f"csv: {build(coverage, out)} packages in {out}")
