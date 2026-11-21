"""Leave names that PyPI no longer has out of a scan, before check_scan.py validates it.

    python scripts/drop_missing_names.py data/coverage.json

Only an exact `not on index` error is a missing project. Timeouts and other lookup
failures stay in the scan. Refuse a large batch of missing names rather than hide
a possible outage, and leave a scan with nothing to drop byte-for-byte unchanged.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

MAX_DROPPED_NAMES = 25


def drop(path: Path) -> int:
    scan = json.loads(path.read_text(encoding="utf-8"))
    missing = [p for p in scan["packages"] if p.get("error") == "not on index"]
    if not missing:
        return 0
    names = scan.get("dropped_names", {}).get("names", []) + [p["name"] for p in missing]
    if len(names) > MAX_DROPPED_NAMES:
        raise ValueError(f"refusing to drop {len(names)} names (limit {MAX_DROPPED_NAMES}); check the scan")

    errors = scan.get("lookup_errors", sum(bool(p.get("error")) for p in scan["packages"]))
    for p in missing:
        for target, result in zip(scan["targets"], p["results"], strict=True):
            scan["summary"][target["label"]][result] -= 1
    scan["packages"] = [p for p in scan["packages"] if p.get("error") != "not on index"]
    scan["lookup_errors"] = errors - len(missing)
    scan["dropped_names"] = {"count": len(names), "names": names}
    path.write_text(json.dumps(scan, separators=(",", ":")) + "\n", encoding="utf-8")
    return len(missing)


if __name__ == "__main__":
    try:
        count = drop(Path(sys.argv[1]))
    except ValueError as exc:
        print(f"drop_missing_names: {exc}", file=sys.stderr)
        sys.exit(1)
    print(f"drop_missing_names: {count} names left out")
