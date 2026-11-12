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
# PEP 440's version pattern, as in packaging.version (the build scripts are stdlib only, so no packaging).
VERSION_RE = re.compile(
    r"""
    v?
    (?:(?P<epoch>[0-9]+)!)?
    (?P<release>[0-9]+(?:\.[0-9]+)*)
    (?P<pre>[-_.]?(?P<pre_l>alpha|a|beta|b|preview|pre|c|rc)[-_.]?(?P<pre_n>[0-9]+)?)?
    (?P<post>(?:-(?P<post_n1>[0-9]+))|(?:[-_.]?(?P<post_l>post|rev|r)[-_.]?(?P<post_n2>[0-9]+)?))?
    (?P<dev>[-_.]?(?P<dev_l>dev)[-_.]?(?P<dev_n>[0-9]+)?)?
    (?:\+(?P<local>[a-z0-9]+(?:[-_.][a-z0-9]+)*))?
    """,
    re.VERBOSE | re.IGNORECASE | re.ASCII,
)
PRE_LETTERS = {"alpha": "a", "a": "a", "beta": "b", "b": "b", "c": "rc", "pre": "rc", "preview": "rc", "rc": "rc"}


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


def version_key(text: str) -> tuple | None:
    """A sort key that orders and equates versions as PEP 440 (packaging.version) does, or None if it isn't one.

    So 2.0rc1 == 2.0.0rc1, 2.0.post1 == 2.0-1 and 2.4 == 2.4.0, and 2.0rc1 < 2.0 < 2.0.post1.
    """
    m = VERSION_RE.fullmatch(text.strip())
    if not m:
        return None
    release = [int(part) for part in m["release"].split(".")]
    while release and release[-1] == 0:
        release.pop()
    pre = (1, PRE_LETTERS[m["pre_l"].lower()], int(m["pre_n"] or 0)) if m["pre"] else None
    post = int(m["post_n1"] or m["post_n2"] or 0) if m["post"] else None
    dev = int(m["dev_n"] or 0) if m["dev"] else None
    # packaging's _cmpkey: a dev release with no pre or post sorts before the pre-releases.
    pre_key = (0,) if pre is None and post is None and dev is not None else (2,) if pre is None else pre
    post_key = (0,) if post is None else (1, post)
    dev_key = (2,) if dev is None else (1, dev)
    local = m["local"]
    local_key = (0,) if local is None else (1, tuple(
        (1, int(part), "") if part.isdigit() else (0, 0, part.lower()) for part in re.split(r"[-_.]", local)
    ))
    return int(m["epoch"] or 0), tuple(release), pre_key, post_key, dev_key, local_key


def compare(bundled: str, scanned: object) -> str:
    """"same", "older" or "different": Pyodide's version against the scanned (latest) release, by PEP 440.

    "different" covers a version newer than the scanned one, and one that isn't a valid version and isn't
    spelled the same.
    """
    if not isinstance(scanned, str):
        return "different"
    a, b = version_key(bundled), version_key(scanned)
    if a is None or b is None:
        return "same" if bundled == scanned else "different"
    return "same" if a == b else "older" if a < b else "different"


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
