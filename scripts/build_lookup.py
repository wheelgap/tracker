"""Split a wheelgap scan into one small JSON file per package, for scripts and agents.

    python scripts/build_lookup.py data/coverage.json _site/data/packages [notes_dir]

Writes <out>/<name>.json for each package (name canonicalised as in PEP 503), <out>.json
(e.g. data/packages.json) listing every package and target, and pyodide.json next to it naming the
Pyodide release behind each Python's `bundled` results. Each package file carries the package's
current notes from notes_dir (default: the repository's notes/), for its bundled cells the
version Pyodide ships (read from the committed lock files, see pyodide_locks.py), and the build lab's
results for it from data/builds.json (see build_lab.py), each for the release it built; lab.json next
to it has the lab's results for each package's current release only, for the homepage. Each package file also has
`changes` and `drops`: the package's entries from data/changes.json and data/drops.json, the PER_TARGET newest for
each target (so every target the logs name is there, however many changed in one release), newest first (a log that can't be fully read warns and is used as far as it can be, as on the package pages). Runs at
publish time, so the files are never committed.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

if __package__:  # imported as scripts.build_lookup (the tests)
    from . import check_notes, pyodide_locks, update_changes
else:  # run as python scripts/build_lookup.py
    import check_notes
    import pyodide_locks
    import update_changes

# A canonical name is also a safe file name: no slashes, dots or leading dashes.
SCHEMA = 1  # of the files written here: bump if a field changes meaning or goes away
SCAN_SCHEMA = 1  # of the coverage.json this reads
SAFE_NAME = re.compile(r"[a-z0-9]+(-[a-z0-9]+)*")
NOTES_DIR = Path(__file__).resolve().parent.parent / "notes"
BUILDS = Path(__file__).resolve().parent.parent / "data" / "builds.json"
CHANGES = BUILDS.parent / "changes.json"
DROPS = BUILDS.parent / "drops.json"
DROPS_SCHEMA = 1  # of the drops.json this reads
PER_TARGET = 3  # newest changes, and newest drops, per target in a package file (changes.json keeps 90 days, drops.json
# a year); at most 3 x the scan's targets, and one release's events (up to one per target) are never cut
CHANGE_FIELDS = ("from", "target", "to", "version")  # plus date, from the day
DROP_FIELDS = ("date", "from", "target", "version")
# The fields of a build lab result that go into a package file (build_lab.py's merge has checked them all).
BUILD_FIELDS = (
    "target", "version", "outcome", "seconds", "wheel", "extensions", "import", "gil", "reason", "excerpt",
    "toolchains", "runner", "python", "date",
)  # fmt: skip
LAB_EXTRAS = ("gil_module", "log")  # only in lab.json


def canonical(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def write_json(path: Path, value: object) -> None:
    path.write_text(json.dumps(value, separators=(",", ":")) + "\n", encoding="utf-8")


def read_drops(path: Path | None) -> list[dict]:
    """drops.json's `drops`, keeping only well-formed entries: no path or a missing file is [], and a file that
    can't be read, has another schema or isn't the expected shape is [] with a warning, as read_changes does."""
    if path is None:
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return []
    except (OSError, ValueError, RecursionError) as e:
        print(f"::warning::{path}: isn't readable JSON ({type(e).__name__}), so the package files list no drops")
        return []
    if not isinstance(data, dict) or data.get("schema") != DROPS_SCHEMA or not isinstance(data.get("drops"), list):
        print(f"::warning::{path}: isn't a schema {DROPS_SCHEMA} drops file, so the package files list no drops")
        return []
    kept = [
        d for d in data["drops"]
        if isinstance(d, dict)
        and all(isinstance(d.get(k), str) for k in ("name", *DROP_FIELDS))
        and update_changes.DATE.fullmatch(d["date"])
    ]  # fmt: skip
    if len(kept) != len(data["drops"]):
        print(f"::warning::{path}: left out {len(data['drops']) - len(kept)} malformed drop(s)")
    return kept


def newest_first(items: list[dict]) -> list[dict]:
    """Newest date first; within a day by target, then version, then the other fields, so the output is stable."""
    items = sorted(items, key=lambda i: (i["target"], i["version"], json.dumps(i, sort_keys=True)))
    return sorted(items, key=lambda i: i["date"], reverse=True)


def per_target(items: list[dict]) -> list[dict]:
    """The PER_TARGET newest entries for each target, newest first (newest_first)."""
    kept: dict[str, int] = {}
    out = []
    for item in newest_first(items):
        if kept.get(item["target"], 0) < PER_TARGET:
            kept[item["target"]] = kept.get(item["target"], 0) + 1
            out.append(item)
    return out


def logs_by_package(changes_path: Path | None, drops_path: Path | None) -> dict[str, dict[str, list[dict]]]:
    """Each package's `changes` and `drops`, keyed by canonical name, PER_TARGET of each per target, newest first.

    A change is {date, from, target, to, version}, where from and to are results ("source" -> "wheel"), and a
    drop is {date, from, target, version}, where from is the release that had the wheel and version the one
    without it. No path (or a missing file) gives no entries."""
    days = update_changes.read_changes(changes_path) if changes_path is not None else []
    changes: dict[str, list[dict]] = {}
    for day in days:
        for kind in ("gained", "lost"):
            for item in day[kind]:
                if all(isinstance(item.get(k), str) for k in CHANGE_FIELDS):  # read_changes checks only some
                    entry = {"date": day["date"], **{k: item[k] for k in CHANGE_FIELDS}}
                    changes.setdefault(canonical(item["name"]), []).append(entry)
    drops: dict[str, list[dict]] = {}
    for d in read_drops(drops_path):
        drops.setdefault(canonical(d["name"]), []).append({k: d[k] for k in DROP_FIELDS})
    return {
        name: {
            "changes": per_target(changes.get(name, [])),
            "drops": per_target(drops.get(name, [])),
        }
        for name in changes.keys() | drops.keys()
    }


def lab_results(builds_path: Path | None) -> dict[str, list[dict]]:
    """The build lab's results by canonical package name, each with BUILD_FIELDS only; {} without the file."""
    if builds_path is None or not builds_path.is_file():
        return {}
    data = json.loads(builds_path.read_text(encoding="utf-8"))
    if data.get("schema") != 1:
        sys.exit(f"build_lookup: builds.json schema {data.get('schema')!r}, expected 1")
    out: dict[str, list[dict]] = {}
    for r in data.get("results", []):
        # Package files carry exactly BUILD_FIELDS (wheelgap reads them strictly); the homepage's lab.json also gets
        # LAB_EXTRAS, from results that have them.
        entry = {k: r[k] for k in BUILD_FIELDS} | {k: r.get(k) for k in LAB_EXTRAS}
        out.setdefault(canonical(r["name"]), []).append(entry)
    return out


def build(
    coverage_path: Path,
    out: Path,
    notes_dir: Path | None = None,
    root: Path = pyodide_locks.ROOT,
    builds_path: Path | None = None,
    changes_path: Path | None = None,
    drops_path: Path | None = None,
) -> int:
    """Write the files; notes come from notes_dir, or none when it is None. root holds pyodide/locks.json.
    builds_path is the build lab's data/builds.json (None: no lab results); changes_path and drops_path are
    data/changes.json and data/drops.json (None: empty `changes` and `drops`)."""
    scan = json.loads(coverage_path.read_text(encoding="utf-8"))
    if scan.get("schema") != SCAN_SCHEMA:
        sys.exit(f"build_lookup: coverage.json schema {scan.get('schema')!r}, expected {SCAN_SCHEMA}")
    notes = check_notes.load(notes_dir, scan) if notes_dir is not None else {}
    lab = lab_results(builds_path)
    logs = logs_by_package(changes_path, drops_path)
    current: list[dict] = []
    labels = [t["label"] for t in scan["targets"]]
    found = pyodide_locks.releases(scan, root)
    per_target = pyodide_locks.pyodide_targets(scan["targets"], found)
    out.mkdir(parents=True, exist_ok=True)
    index = {}
    for p in scan["packages"]:
        name = canonical(p["name"])
        if not SAFE_NAME.fullmatch(name) or name in index:
            print(f"::warning::build_lookup: skipping {p['name']!r}", file=sys.stderr)
            continue
        index[name] = p["version"]
        entry = {
            "schema": SCHEMA,
            "name": name,
            "version": p["version"],
            "rank": p["rank"],
            "pure": p["pure"],
            "error": p["error"],
            "generated": scan["generated"],
            "wheelgap": scan["wheelgap"],
            "results": dict(zip(labels, p["results"], strict=True)),
            "pyodide": per_target,
            "bundled": pyodide_locks.bundled(p, scan["targets"], found),
            "notes": [
                check_notes.as_json(n) for n in check_notes.current(notes.get(name, []), p, scan["targets"])
            ],
            "builds": sorted(({k: r[k] for k in BUILD_FIELDS} for r in lab.get(name, [])), key=lambda r: r["target"]),
            **logs.get(name, {"changes": [], "drops": []}),
        }
        for key in ("install", "way_out", "issues_url"):
            if key in p:
                entry[key] = p[key]
        write_json(out / f"{name}.json", entry)
        for r in lab.get(name, []):
            if r["version"] == p["version"]:  # the homepage shows only the current release's results
                current.append({"name": name, "rank": p["rank"], **r})
    write_json(out.with_suffix(".json"), {
        "schema": SCHEMA,
        "generated": scan["generated"],
        "wheelgap": scan["wheelgap"],
        "targets": scan["targets"],
        "packages": index,
    })
    if builds_path is not None and builds_path.is_file():
        write_json(out.parent / "lab.json", {
            "schema": SCHEMA,
            "generated": scan["generated"],
            "results": sorted(current, key=lambda r: (r["target"], r["rank"])),
        })
    write_json(out.parent / "pyodide.json", {
        "schema": SCHEMA,
        "generated": scan["generated"],
        "pythons": {py: {"pyodide": r["pyodide"], "lock": r["url"]} for py, r in sorted(found.items())},
    })
    return len(index)


if __name__ == "__main__":
    coverage, out = (Path(p) for p in sys.argv[1:3])
    notes_dir = Path(sys.argv[3]) if len(sys.argv) > 3 else NOTES_DIR
    count = build(coverage, out, notes_dir, builds_path=BUILDS, changes_path=CHANGES, drops_path=DROPS)
    print(f"lookup: {count} packages in {out}")
