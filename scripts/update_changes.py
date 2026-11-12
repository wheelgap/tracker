"""Record which packages gained or lost a wheel since the previous scan (one entry per day).

    python scripts/update_changes.py previous.json data/coverage.json data/changes.json

previous.json is the committed data/coverage.json, copied aside before the scan overwrites it.
Only changes between `wheel` and something else count, except `bundled` (Pyodide ships its own
build): it installs as a wheel does, so `bundled` <-> `wheel` is no change either way. A cell that
was or is `unknown` (a lookup error) is skipped, so a flaky lookup doesn't show up as a lost and
then a regained wheel. Targets or packages that are only in one of the two scans are skipped too.
A night whose scan used a different wheelgap version is skipped as a whole: its differences come
from wheelgap, not from the packages. Entries older than 90 days are dropped, so the file stays
small.
"""

from __future__ import annotations

import json
import sys
from datetime import date, timedelta
from pathlib import Path

KEEP_DAYS = 90
INSTALLABLE = ("wheel", "bundled")


def counts(frm: str, to: str) -> bool:
    """Whether a cell going from `frm` to `to` is a wheel gained or lost."""
    if frm == to or "unknown" in (frm, to) or "wheel" not in (frm, to):
        return False
    return not (frm in INSTALLABLE and to in INSTALLABLE)


def diff(old: dict, new: dict) -> tuple[list[dict], list[dict]]:
    """Return (gained, lost) between two scans, in rank order then target order."""
    old_labels = [t["label"] for t in old.get("targets", [])]
    new_labels = [t["label"] for t in new["targets"]]
    shared = [(new_labels.index(label), old_labels.index(label)) for label in new_labels if label in old_labels]
    old_packages = {p["name"]: p for p in old.get("packages", [])}
    gained, lost = [], []
    for pkg in new["packages"]:
        before = old_packages.get(pkg["name"])
        if before is None:
            continue
        for new_i, old_i in shared:
            frm, to = before["results"][old_i], pkg["results"][new_i]
            if not counts(frm, to):
                continue
            item = {
                "name": pkg["name"],
                "version": pkg["version"],
                "target": new_labels[new_i],
                "from": frm,
                "to": to,
            }
            (gained if to == "wheel" else lost).append(item)
    return gained, lost


def merge(earlier: dict, gained: list[dict], lost: list[dict]) -> tuple[list[dict], list[dict]]:
    """Fold a second scan on the same day into that day's entry.

    The day's change for a (package, target) runs from the first scan's `from` to the latest `to`,
    so a wheel lost and regained the same day is no change at all.
    """
    cells = {(i["name"], i["target"]): dict(i) for i in earlier["gained"] + earlier["lost"]}
    for item in gained + lost:
        key = (item["name"], item["target"])
        if key in cells:
            cells[key].update(version=item["version"], to=item["to"])
        else:
            cells[key] = dict(item)
    kept = [i for i in cells.values() if counts(i["from"], i["to"])]
    return [i for i in kept if i["to"] == "wheel"], [i for i in kept if i["from"] == "wheel"]


def update(previous_path: Path, coverage_path: Path, changes_path: Path) -> dict | str:
    """Record today's entry and return it, or return why nothing was compared."""
    new = json.loads(coverage_path.read_text(encoding="utf-8"))
    changes = json.loads(changes_path.read_text(encoding="utf-8")) if changes_path.exists() else []
    old = json.loads(previous_path.read_text(encoding="utf-8")) if previous_path.exists() else None
    entry: dict | str
    if old is None:
        entry = f"no {previous_path}, nothing to compare"
    elif old.get("wheelgap") != new.get("wheelgap"):
        entry = f"wheelgap {old.get('wheelgap')} -> {new.get('wheelgap')}, so differences aren't the packages'"
    else:
        gained, lost = diff(old, new)
        today = new["generated"][:10]
        same_day = [c for c in changes if c["date"] == today]
        if same_day:
            gained, lost = merge(same_day[0], gained, lost)
        entry = {"date": today, "gained": gained, "lost": lost}
        changes = [c for c in changes if c["date"] != today] + [entry]
        cutoff = (date.fromisoformat(today) - timedelta(days=KEEP_DAYS - 1)).isoformat()
        changes = sorted((c for c in changes if c["date"] >= cutoff), key=lambda c: c["date"])
    changes_path.write_text(json.dumps(changes, separators=(",", ":")) + "\n", encoding="utf-8")
    return entry


if __name__ == "__main__":
    previous, coverage, changes = (Path(p) for p in sys.argv[1:4])
    entry = update(previous, coverage, changes)
    if isinstance(entry, str):
        print(f"changes: nothing recorded ({entry})")
    else:
        print(f"changes: {entry['date']}, {len(entry['gained'])} gained, {len(entry['lost'])} lost")
