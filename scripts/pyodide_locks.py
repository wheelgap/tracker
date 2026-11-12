"""The Pyodide releases behind the scan's `bundled` results, read from the committed lock files in pyodide/.

pyodide/locks.json lists each pinned release as {python, pyodide, file, url, sha256}. scan.yml passes each
`file` to `python -m wheelgap.scan --pyodide-lock`, so the scan's `pyodide_locks[].source` is that repository
path. The scripts here match a scan's locks to the manifest by that path and read the same file, so they can
say which version Pyodide ships: usually not the latest release the scan checked.

A scan lock that isn't in the manifest, whose file is gone or changed (sha256), or whose file is for another
Python is left out with a warning: its bundled cells then show no Pyodide version rather than a guessed one.
Stdlib only, like the other scripts.
"""

from __future__ import annotations

import hashlib
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MANIFEST = Path("pyodide") / "locks.json"
MANIFEST_SCHEMA = 1
RELEASE_RE = re.compile(r"\d+(?:\.\d+)*", re.ASCII)


def canonical(name: str) -> str:
    """PEP 503, as wheelgap's packaging.utils.canonicalize_name does it."""
    return re.sub(r"[-_.]+", "-", name).lower()


def lock_python(data: object) -> str | None:
    """"3.13" for a lock whose info.python is "3.13.2", or None if it doesn't say."""
    info = data.get("info") if isinstance(data, dict) else None
    python = info.get("python") if isinstance(info, dict) else None
    if not isinstance(python, str) or not re.fullmatch(r"\d+\.\d+(\.\d+)?", python):
        return None
    return ".".join(python.split(".")[:2])


def lock_packages(data: dict) -> dict[str, str]:
    """Canonical name -> version for the installable wheels in a lock: the same filter as wheelgap's
    parse_pyodide_lock (package_type "package", a .whl file), so a name counts here exactly when wheelgap
    could have reported it as bundled."""
    packages = data.get("packages") if isinstance(data, dict) else None
    if not isinstance(packages, dict):
        return {}
    return {
        canonical(str(p["name"])): str(p["version"])
        for p in packages.values()
        if isinstance(p, dict)
        and p.get("package_type", "package") == "package"
        and ("file_name" not in p or str(p["file_name"]).lower().endswith(".whl"))
        and "name" in p
        and "version" in p
    }


def load_manifest(root: Path = ROOT) -> list[dict]:
    path = root / MANIFEST
    if not path.exists():
        return []
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or data.get("schema") != MANIFEST_SCHEMA or not isinstance(data.get("locks"), list):
        sys.exit(f"pyodide_locks: {path} needs schema {MANIFEST_SCHEMA} and a locks list")
    fields = ("python", "pyodide", "file", "url", "sha256")
    for entry in data["locks"]:
        if not isinstance(entry, dict) or not all(isinstance(entry.get(f), str) for f in fields):
            sys.exit(f"pyodide_locks: each entry in {path} needs string {', '.join(fields)}")
    return data["locks"]


def scan_locks_problems(scan: dict) -> list[str]:
    """What's wrong with the shape of the scan's pyodide_locks (absent is fine: a scan without locks)."""
    locks = scan.get("pyodide_locks", [])
    if not isinstance(locks, list):
        return ["pyodide_locks isn't a list"]
    out = []
    seen = set()
    for lock in locks:
        if not isinstance(lock, dict) or not all(
            isinstance(lock.get(f), str) for f in ("python", "pyodide_version", "source")
        ):
            out.append(f"pyodide_locks entry without string python, pyodide_version and source: {lock!r}"[:200])
            continue
        if lock["python"] in seen:
            out.append(f"pyodide_locks has two entries for Python {lock['python']}")
        seen.add(lock["python"])
    return out


def releases(scan: dict, root: Path = ROOT, warn=None) -> dict[str, dict]:
    """Per Python, the Pyodide release the scan used: {"pyodide", "url", "packages"}.

    Only locks that match the manifest (by `source` path, sha256 and Python) are returned; the others are
    reported through warn and left out.
    """
    warn = warn or (lambda message: print(f"::warning::{message}", file=sys.stderr))
    if scan_locks_problems(scan):
        warn("pyodide_locks: malformed in coverage.json, so no Pyodide versions are shown")
        return {}
    manifest = {entry["file"]: entry for entry in load_manifest(root)}
    out: dict[str, dict] = {}
    for lock in scan.get("pyodide_locks", []):
        entry = manifest.get(lock["source"])
        if entry is None or entry["python"] != lock["python"]:
            warn(f"pyodide_locks: {lock['source']} (Python {lock['python']}) isn't in {MANIFEST}")
            continue
        path = root / entry["file"]
        if not path.is_file():
            warn(f"pyodide_locks: {entry['file']} is missing")
            continue
        raw = path.read_bytes()
        if hashlib.sha256(raw).hexdigest() != entry["sha256"]:
            warn(f"pyodide_locks: {entry['file']} doesn't match its sha256 in {MANIFEST}")
            continue
        data = json.loads(raw)
        if lock_python(data) != entry["python"]:
            warn(f"pyodide_locks: {entry['file']} is for Python {lock_python(data)}, not {entry['python']}")
            continue
        out[entry["python"]] = {"pyodide": entry["pyodide"], "url": entry["url"], "packages": lock_packages(data)}
    return out


def _release(text: str) -> tuple[int, ...]:
    return tuple(int(part) for part in text.split("."))


def compare(bundled: str, scanned: object) -> str:
    """"same", "older" or "different": Pyodide's version against the scanned (latest) release.

    "older" only when both are plain N(.N)* releases; anything else that isn't equal is "different".
    """
    if not isinstance(scanned, str):
        return "different"
    if RELEASE_RE.fullmatch(bundled) and RELEASE_RE.fullmatch(scanned):
        a, b = _release(bundled), _release(scanned)
        width = max(len(a), len(b))
        a, b = a + (0,) * (width - len(a)), b + (0,) * (width - len(b))
        return "same" if a == b else "older" if a < b else "different"
    return "same" if bundled == scanned else "different"


def pyodide_targets(targets: list[dict], found: dict[str, dict]) -> dict[str, dict]:
    """Per pyodide target label whose Python had a matching lock: {"pyodide": release, "lock": URL}."""
    return {
        t["label"]: {"pyodide": found[t["python"]]["pyodide"], "lock": found[t["python"]]["url"]}
        for t in targets
        if t.get("platform") == "pyodide" and t.get("python") in found
    }


def bundled(package: dict, targets: list[dict], found: dict[str, dict]) -> dict[str, dict]:
    """Per bundled cell's label: {"pyodide", "bundled_version", "vs_latest"}, all None when unknown."""
    if package.get("error"):
        return {}
    out = {}
    key = canonical(package["name"])
    for target, result in zip(targets, package["results"]):
        if result != "bundled":
            continue
        release = found.get(target.get("python"))
        version = release["packages"].get(key) if release else None
        if version is None:
            out[target["label"]] = {"pyodide": None, "bundled_version": None, "vs_latest": None}
        else:
            out[target["label"]] = {
                "pyodide": release["pyodide"],
                "bundled_version": version,
                "vs_latest": compare(version, package.get("version")),
            }
    return out


def describe(info: dict | None) -> str:
    """The line under a bundled cell, e.g. "Pyodide 0.29.5 ships 2.3.3, older than the latest"; same text as
    bundledDetail() in site/logic.js."""
    if not info or not info.get("pyodide") or not info.get("bundled_version"):
        return ""
    text = f"Pyodide {info['pyodide']} ships {info['bundled_version']}"
    if info.get("vs_latest") == "older":
        return text + ", older than the latest"
    if info.get("vs_latest") == "different":
        return text + ", not the latest"
    return text
