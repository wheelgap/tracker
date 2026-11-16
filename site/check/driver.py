"""The repo checker's Python side: it runs in Pyodide, in a Web Worker (worker.mjs), on files the worker fetched.

The worker does every network request (GitHub and PyPI, in parallel, from the browser); this module only reads the
files it was given, with wheelgap's own functions, so the page says what `uvx wheelgap` would say on the same lock:

- plan(paths): from the repo's file list, which files to fetch.
- detect(root): the targets and Pythons the repo's workflows and Dockerfiles name (wheelgap --suggest-targets).
- check(root, lockfile, ...): the lockfile's gaps, offline, from the files the lock lists.
- fixes(releases, wanted): for each gap, the nearest newer release with a wheel (wheelgap --suggest).

Every function takes and returns JSON text, so nothing depends on how Pyodide converts objects.
tests/test_check_driver.py runs it under CPython with the wheelgap version worker.mjs pins.
"""

from __future__ import annotations

import json
from pathlib import Path, PurePosixPath

import wheelgap
from packaging.utils import canonicalize_name
from wheelgap import api, origin, suggest_targets
from wheelgap import suggest as release_suggest
from wheelgap.check import Status
from wheelgap.sources import LOCKFILES, SourceError, pythons_outside_lock
from wheelgap.targets import DEFAULT_PLATFORMS, NEWEST_PYTHON, Target, TargetError

# The lockfiles that list each package's files, so they can be checked without asking an index. The others
# (Pipfile.lock, requirements files) need PyPI lookups, which the page doesn't make yet.
OFFLINE = ("uv.lock", "pylock.toml", "poetry.lock", "pdm.lock", "pixi.lock")
GAPS = (Status.SOURCE, Status.MISSING)  # no wheel; UNKNOWN (couldn't be checked) is reported apart
MAX_LOCKFILES = 30
MAX_DOCKERFILES = 40

# Targets of the last check(), by label, for fixes().
_targets: dict[str, Target] = {}


def _skipped_folder(parts: tuple[str, ...]) -> bool:
    """A folder wheelgap --suggest-targets doesn't look in (hidden, node_modules, ...), or a vendored test fixture."""
    return any(p.startswith(".") or p in suggest_targets._SKIP_DIRS for p in parts)


def plan(paths_json: str) -> str:
    """The files to fetch, from the repo tree's paths (blobs only): lockfiles, workflows, Dockerfiles and the root
    .python-version. Lockfiles come shallowest first, then in wheelgap's order, so the first is the one
    `wheelgap` would pick in the repo's root (or the nearest folder that has one)."""
    paths = [p for p in json.loads(paths_json) if isinstance(p, str)]
    lockfiles, workflows, dockerfiles, templates = [], [], [], []
    for p in paths:
        pp = PurePosixPath(p)
        folders = pp.parts[:-1]
        if len(pp.parts) == 3 and folders == (".github", "workflows") and pp.suffix in (".yml", ".yaml"):
            workflows.append(p)
            continue
        if _skipped_folder(folders):
            continue
        if pp.name in LOCKFILES:
            lockfiles.append(p)
        kind = suggest_targets._container_kind(pp.name)
        if kind == "file":
            dockerfiles.append(p)
        elif kind == "template":
            templates.append(p)
    lockfiles.sort(key=lambda p: (p.count("/"), LOCKFILES.index(PurePosixPath(p).name), p))
    dockerfiles.sort(key=lambda p: (p.count("/"), p))
    return json.dumps(
        {
            "lockfiles": [{"path": p, "offline": PurePosixPath(p).name in OFFLINE} for p in lockfiles[:MAX_LOCKFILES]],
            "workflows": sorted(workflows),
            "dockerfiles": dockerfiles[:MAX_DOCKERFILES],
            "more_dockerfiles": max(0, len(dockerfiles) - MAX_DOCKERFILES),
            "templates": sorted(templates),
            "python_version": ".python-version" if ".python-version" in paths else None,
        }
    )


def _python_version_file(root: Path) -> str | None:
    """The root .python-version's first version, as wheelgap reads it for its -p default (3.13, 3.14t)."""
    try:
        text = (root / ".python-version").read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None
    for line in text.splitlines():
        line = line.split("#", 1)[0].strip()
        if line:
            parts = line.removeprefix("cpython-").removeprefix("python").split(".")
            if len(parts) >= 2 and parts[0] == "3" and parts[1].rstrip("t").isdigit():
                return f"3.{parts[1]}"
            return None
    return None


def detect(root: str) -> str:
    """wheelgap --suggest-targets on the fetched files, plus the defaults the page starts from when it finds none."""
    found = suggest_targets.suggest(Path(root))
    config = found.config()
    pythons = list(config.get("python") or [])
    python_from = "ci" if pythons else None
    if not pythons and (v := _python_version_file(Path(root))):
        pythons, python_from = [v], ".python-version"
    if not pythons:
        pythons, python_from = [f"{NEWEST_PYTHON[0]}.{NEWEST_PYTHON[1]}"], "default"
    platforms = list(config.get("targets") or [])
    return json.dumps(
        {
            "pythons": pythons,
            "python_from": python_from,
            "platforms": platforms or list(DEFAULT_PLATFORMS),
            "platforms_from": "ci" if platforms else "default",
            "glibc": config.get("glibc"),
            "musl": config.get("musl"),
            "macos": config.get("macos"),
            "found": [
                {"kind": kind, "value": f.value, "path": f.path, "line": f.line, "source": f.source}
                for kind, items in (("target", found.targets), ("python", found.pythons))
                for f in items
            ],
            "unreadable": [{"path": u.path, "line": u.line, "reason": u.reason} for u in found.unreadable],
        }
    )


def check(root: str, lockfile: str, options_json: str) -> str:
    """The lockfile's packages with a gap on any target, read offline from the files it lists.

    options: {"pythons": [...], "platforms": [...], "glibc"?, "musl"?, "macos"?}. A target Python the lock's
    requires-python leaves out is reported in `outside_lock` and none of its cells count as gaps: uv keeps no wheels
    for it in the lock, so they'd be false gaps (the CLI rechecks them on PyPI; the page says so instead).
    """
    options = json.loads(options_json)
    limits = {k: options[k] for k in ("glibc", "musl", "macos") if isinstance(options.get(k), str)}
    try:
        targets = api.make_targets(options["pythons"], options["platforms"], **limits)
    except (TargetError, ValueError) as e:
        return json.dumps({"error": str(e), "kind": "targets"})
    path = Path(root) / lockfile
    if path.name not in OFFLINE:
        return json.dumps({"error": f"{path.name} needs PyPI lookups", "kind": "needs-index"})
    try:
        # 0.12.0 also returns the check rows; only the results and the source are used here.
        results, source, *_ = api._check_lockfile(path, targets, online=False, offline=True)
    except (SourceError, ValueError, OSError) as e:  # ValueError: a lockfile that isn't valid TOML or JSON
        return json.dumps({"error": str(e).replace(f"{root}/", ""), "kind": "source"})
    _targets.clear()
    _targets.update((t.label(True), t) for t in targets)
    outside = set(pythons_outside_lock(source, targets))
    counted = [t for t in targets if t.python not in outside]
    per_target = {t.label(True): {s.value: 0 for s in (Status.WHEEL, *GAPS, Status.UNKNOWN)} for t in targets}
    # --suggest's rule (origin.known_pypi): only packages positively from PyPI are looked up there, so a private
    # package's name never goes to pypi.org, and a same-named PyPI project is never offered as its fix.
    private = origin.private_names([source])
    on_pypi: dict[tuple[str, str | None], bool] = {}
    for original in source.packages:
        for p in original.variants:
            key = (canonicalize_name(p.name), p.version)
            on_pypi[key] = on_pypi.get(key, True) and origin.known_pypi(p, source, private=private)
    rows = []
    for r in results:
        statuses = {t.label(True): r.statuses.get(t, Status.NOT_NEEDED).value for t in targets}
        for t in counted:
            s = r.statuses.get(t)
            if s is not None and s.value in per_target[t.label(True)]:
                per_target[t.label(True)][s.value] += 1
        gaps = [t.label(True) for t in counted if r.statuses.get(t) in GAPS]
        unknown = [t.label(True) for t in counted if r.statuses.get(t) is Status.UNKNOWN]
        if gaps or unknown:
            keep = [t.label(True) for t in counted if r.statuses.get(t) is Status.WHEEL]
            lookup = bool(gaps and r.version) and on_pypi.get((canonicalize_name(r.name), r.version), False)
            rows.append(
                {
                    "name": r.name,
                    "version": r.version,
                    "statuses": statuses,
                    "gaps": gaps,
                    "unknown": unknown,
                    "keep": keep,
                    "lookup": lookup,
                }
            )
    return json.dumps(
        {
            "wheelgap": wheelgap.__version__,
            "lockfile": lockfile,
            "targets": [{"label": t.label(True), "python": t.python, "platform": t.platform} for t in targets],
            "requires_python": source.requires_python,
            "outside_lock": sorted(outside),
            "packages": len(results),
            "skipped": sorted({p.name for p in source.packages if p.skip}),
            "per_target": per_target,
            "rows": rows,
        }
    )


def fixes(releases_json: str, wanted_json: str) -> str:
    """For each wanted row ({name, version, gaps, keep} from check()), the release --suggest names for its gaps.

    releases: by name, the `releases` mapping of PyPI's project JSON. A name missing from it, or a row check() didn't
    mark for lookup (not positively a PyPI package), gets no answer: the page shows "not looked up", never "no newer
    release"."""
    releases, wanted = json.loads(releases_json), json.loads(wanted_json)
    out = {}
    for row in wanted:
        rel = releases.get(row["name"])
        if not row.get("lookup") or not row.get("gaps") or not isinstance(rel, dict) or not row.get("version"):
            continue
        gaps = [_targets[g] for g in row["gaps"] if g in _targets]
        keep = [_targets[k] for k in row["keep"] if k in _targets]
        try:
            found = release_suggest.suggest(rel, row["version"], gaps, keep)
        except ValueError:  # an invalid pinned version: nothing to compare against
            continue
        out[f"{row['name']}=={row['version']}"] = {
            "found": {t.label(True): v for t, v in found.found.items()},
            "loses": [t.label(True) for t in found.loses],
        }
    return json.dumps(out)
