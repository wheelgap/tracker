"""Refuse to publish a scan that looks wrong, before it reaches the site or the history.

    python scripts/check_scan.py data/coverage.json data/history.json 5000

Checks: the shape scripts/build_lookup.py needs, results the scripts know (`bundled` only on a pyodide
target), enough packages, every target summarised, few lookup errors, and no target's wheel count
dropping sharply since the last entry in the history (a PyPI outage, not a real change).
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

MIN_PACKAGE_SHARE = 0.95
MAX_ERROR_SHARE = 0.05
MAX_DROP = 0.10
# wheelgap's results, which the scripts and the site handle. `bundled` (wheelgap --pyodide-lock) means Pyodide
# ships its own build, so it only makes sense on a pyodide target. `n/a` (environment markers rule the target
# out) can't come from a scan, which has no markers, but isn't wrong. A new value from a newer wheelgap stops
# the scan here, so it's taught to the scripts before it reaches the site.
STATUSES = ("wheel", "source", "missing", "unknown", "bundled", "n/a")


def problems(scan: dict, history: list[dict], top: int) -> list[str]:
    out = []
    n = len(scan.get("packages", []))
    if n < MIN_PACKAGE_SHARE * top:
        out.append(f"only {n} packages, expected about {top}")
    labels = [t["label"] for t in scan.get("targets", [])]
    pyodide = [t.get("platform") == "pyodide" for t in scan.get("targets", [])]
    missing = [label for label in labels if label not in scan.get("summary", {})]
    if not labels or missing:
        out.append(f"targets without a summary: {missing or 'no targets at all'}")
    # What build_lookup.py needs at publish time: catch it here, so bad data is never committed.
    if scan.get("schema") != 1:
        out.append(f"schema {scan.get('schema')!r}, expected 1 (update scripts/build_lookup.py first)")
    fields = ("name", "version", "rank", "pure", "error", "results")
    for p in scan.get("packages", []):
        if any(f not in p for f in fields) or not isinstance(p.get("name"), str):
            out.append(f"package entry missing fields: {p.get('name')!r}")
            break
        if len(p["results"]) != len(labels):
            out.append(f"{p['name']}: {len(p['results'])} results for {len(labels)} targets")
            break
        strange = sorted({str(r) for r in p["results"] if r not in STATUSES})
        if strange:
            out.append(f"{p['name']}: unexpected results {strange} (teach the scripts first)")
            break
        if any(r == "bundled" and not on_pyodide for r, on_pyodide in zip(p["results"], pyodide)):
            out.append(f"{p['name']}: bundled on a target that isn't pyodide")
            break
    errors = scan.get("lookup_errors", sum(1 for p in scan.get("packages", []) if p.get("error")))
    if n and errors > MAX_ERROR_SHARE * n:
        out.append(f"{errors} of {n} lookups failed")
    if history and not os.environ.get("ALLOW_DROP"):
        previous = history[-1].get("summary", {})
        for label, counts in scan.get("summary", {}).items():
            before = previous.get(label, {}).get("wheel")
            if before and counts.get("wheel", 0) < (1 - MAX_DROP) * before:
                out.append(
                    f"{label}: wheels fell from {before} to {counts.get('wheel', 0)} "
                    "(if that's real, run the workflow by hand with allow_drop)"
                )
    return out


if __name__ == "__main__":
    scan_path, history_path, top = Path(sys.argv[1]), Path(sys.argv[2]), int(sys.argv[3])
    scan = json.loads(scan_path.read_text(encoding="utf-8"))
    history = json.loads(history_path.read_text(encoding="utf-8")) if history_path.exists() else []
    found = problems(scan, history, top)
    for p in found:
        print(f"check_scan: {p}", file=sys.stderr)
    sys.exit(1 if found else 0)
