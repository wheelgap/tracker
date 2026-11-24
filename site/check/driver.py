"""The repo checker's Python side: it runs in Pyodide, in a Web Worker (worker.mjs), on files the worker fetched.

The worker does every network request (GitHub and PyPI, in parallel, from the browser); this module only reads the
files it was given, with wheelgap's own functions, so the page says what `uvx wheelgap` would say on the same lock:

- plan(paths): from the repo's file list, which files to fetch.
- detect(root): the targets and Pythons the repo's workflows and Dockerfiles name (wheelgap --suggest-targets).
- needs(root, path): the PyPI lookups a file that doesn't list its packages' files needs before it can be checked.
- check(root, lockfile, ...): the lockfile's gaps, from the files the lock lists or the ones PyPI answered with.
- fixes(releases, wanted): for each gap, the nearest newer release with a wheel (wheelgap --suggest).
- gap_wheels(list): which of the last check's gaps have a verified wheel, as wheelgap --fix's fallback after the
  upgrades fixes() found.

Every function takes and returns JSON text, so nothing depends on how Pyodide converts objects.
tests/test_check_driver.py runs it under CPython with the wheelgap version worker.mjs pins.
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path, PurePosixPath

import wheelgap
from packaging.utils import canonicalize_name
from wheelgap import api, fix, gapwheels, offline, origin, pypi, sources, suggest_targets
from wheelgap import suggest as release_suggest
from wheelgap.check import Row, Status
from wheelgap.sources import LOCKFILES, Package, Source, SourceError, file_kind, load_file, pythons_outside_lock
from wheelgap.targets import DEFAULT_PLATFORMS, NEWEST_PYTHON, Target, TargetError

# The lockfiles that list each package's files, so they can be checked without asking an index. The other
# checkable files (Pipfile.lock, requirements files, pixi.lock, pyproject.toml) don't, so the page checks them
# by looking each package up on PyPI from the browser, the way the CLI does: needs() lists the lookups and the
# worker fetches them, then check() fills the packages from the answers. A pyproject.toml lists direct
# requirements only, as `uvx wheelgap requirements.txt` checks them. And even a listed lockfile can leave a
# package's files out; check() catches that below.
OFFLINE = ("uv.lock", "pylock.toml", "poetry.lock", "pdm.lock")
# The readers of those lockfiles (sources.file_kind's names for them). A file a link names is read by the reader its
# name picks (pylock.dev.toml as a pylock.toml, uv-py312.lock as a uv.lock), so it's checked offline by that, not by
# its exact name: read as a lookups file, a Python its lock leaves out would show false gaps.
OFFLINE_KINDS = ("uv", "pylock", "poetry", "pdm")
LOOKUPS = tuple(name for name in LOCKFILES if name not in OFFLINE) + ("pyproject.toml",)
GAPS = (Status.SOURCE, Status.MISSING)  # no wheel; UNKNOWN (couldn't be checked) is reported apart
MAX_LOCKFILES = 30
MAX_DOCKERFILES = 40
# The installer settings files wheelgap reads from a requirements file's, a pyproject.toml's or a Pipfile.lock's
# folder and every folder above it, up to the repository root, to tell whether they point an installer at another
# index (sources._nearby_config_names_an_index, which reads sources._CONFIG_FILES): uv applies [tool.uv] to
# `uv pip install -r`, and `uv export` writes no index lines. NEARBY_CONFIG_READERS are the readers (load_file's
# `kind`) that call it. The browser has only the files the worker fetched, so the worker fetches these for the file
# being checked (configs_for), and needs() and check() keep the file private when one the repo has isn't there to
# read. tests/test_check_driver.py checks both lists against the pinned release. (The page offers no PEP 723 script
# or conda environment file, but their readers read them too.)
CONFIG_NAMES = ("uv.toml", "pyproject.toml", "pdm.toml")
NEARBY_CONFIG_READERS = ("requirements", "pyproject", "pipfile", "script", "conda-env")

# Public extra indexes: a requirements file whose only index options are an --extra-index-url to one of these is still
# looked up on PyPI (pip asks PyPI for every name too, so none is private). Adding a host is a reviewed change; each
# entry names who runs it and the packages that index may serve instead of PyPI's, which are shown as unknown.
PUBLIC_EXTRA_INDEXES: dict[str, frozenset[str]] = {
    # PyTorch Foundation (Linux Foundation): the official CUDA, ROCm and CPU wheels of PyTorch.
    "download.pytorch.org": frozenset({"torch", "torchvision", "torchaudio"}),
}
# The one option line taken as public, whole: `--extra-index-url URL` or `--extra-index-url=URL`, nothing else on it,
# the URL https on a lowercase host with a plain path: no user@, port, quote, ${VAR}, %, ?, # or backslash, which pip
# could read as something else. Anything else wheelgap counts as an index option keeps the file private.
_PUBLIC_EXTRA_LINE = re.compile(r"--extra-index-url(?:=|[ \t]+)https://([a-z0-9.-]+)(?:/[A-Za-z0-9._~/-]*)?")

# Targets of the last check(), by label, for fixes().
_targets: dict[str, Target] = {}
_last_check: tuple[list[Row], list[Target], Source] | None = None
# What fixes() found for the last check, by `name==version` as it answers: gap_wheels() orders its gap wheels after
# these upgrades, as --fix does.
_suggestions: dict[str, release_suggest.Suggestion] = {}


def gap_wheels(list_json: str) -> str:
    """The CLI's --fix gap wheel section for the last check: its eligibility rules (gapwheels.fillable) and its text.

    As in --fix, an upgrade to an official release comes first: call this after fixes(), and a gap wheel for a cell an
    upgrade fixes too (fix.alternatives) is only the fallback, `"fallback": true` in its cell and "(if you can't
    upgrade NAME)" in the text, which then starts "As a fallback, ...". `upgrades` says whether fixes() found any
    upgrade for the check's gaps, as fix.any_upgrade does for the CLI's section."""
    empty = {"cells": [], "text": "", "unofficial": "", "upgrades": False}
    if _last_check is None:
        return json.dumps(empty)
    try:
        wheels = gapwheels.parse(json.loads(list_json))
    except ValueError:
        return json.dumps(empty)
    if not wheels:
        return json.dumps(empty)
    rows, targets, source = _last_check
    source_of = {id(row.package): source for row in rows}
    fills = gapwheels.fillable(rows, targets, source_of, wheels, origin.private_names([source]))
    if not fills:
        return json.dumps(empty)
    gaps = [r for r in rows if fix.gap_targets(r, targets)]
    found = {
        id(r.package): s for r in gaps if (s := _suggestions.get(f"{r.package.name}=={r.package.version}")) is not None
    }
    # The rows fixes() has no answer for get none here; fix.plan() reads that as "not looked up", whatever the reason.
    cells = fix.plan(gaps, targets, source_of, found, fills, {})
    fallback, upgrades = fix.alternatives(cells), fix.any_upgrade(cells)
    return json.dumps(
        {
            "cells": [
                entry | {"fallback": (id(f.row), f.target) in fallback}
                for f, entry in zip(fills, gapwheels.to_json(fills, True), strict=True)
            ],
            "text": "\n".join(gapwheels.text_lines(fills, True, fallback, upgrades)).strip(),
            "unofficial": gapwheels.UNOFFICIAL,
            "upgrades": upgrades,
        }
    )


def _reads_offline(path: str) -> bool:
    """Whether wheelgap reads `path` as a lockfile that lists its packages' files (OFFLINE_KINDS), by its name."""
    try:
        return file_kind(Path(path)) in OFFLINE_KINDS
    except SourceError:  # a name wheelgap doesn't read: load_file reports it
        return False


def _skipped_folder(parts: tuple[str, ...]) -> bool:
    """A folder wheelgap --suggest-targets doesn't look in (hidden, node_modules, ...), or a vendored test fixture."""
    return any(p.startswith(".") or p in suggest_targets._SKIP_DIRS for p in parts)


# Text files in a requirements/ folder that aren't requirements files, by the start of their name (any case), as
# `wheelgap DIR` leaves them out.
_NOT_REQUIREMENTS = sources._NOT_REQUIREMENTS


def _is_extra_requirements(pp: PurePosixPath) -> bool:
    """A *.txt file whose name or folder marks it as a requirements file, under a name other than requirements.txt:
    requirements*.txt, *-requirements.txt, *_requirements.txt, or a .txt in a folder named requirements (not
    constraints, README.txt and the like). Case-sensitive, as wheelgap reads a file as requirements only by `.txt`."""
    name = pp.name
    if not name.endswith(".txt") or name in LOCKFILES or name.lower().startswith(_NOT_REQUIREMENTS):
        return False
    if name.startswith("requirements") or name.endswith(("-requirements.txt", "_requirements.txt")):
        return True
    return len(pp.parts) >= 2 and pp.parts[-2] == "requirements"


def _lockfile_sort_key(path: str) -> tuple[int, int, int, int, str]:
    """Every exactly-named file first, as before: shallowest, then in wheelgap's order, a pyproject.toml after the
    lockfiles in its folder. Requirements files under other names come after all of those, shallowest then by name:
    which one holds the project's dependencies can't be told from the name, so the page never picks one of them over
    another file, and with several it asks (autoPick() in check-logic.mjs), as `wheelgap DIR` lists them."""
    pp = PurePosixPath(path)
    if pp.name in LOCKFILES:
        return (0, path.count("/"), 0, LOCKFILES.index(pp.name), path)
    if pp.name == "pyproject.toml":
        return (0, path.count("/"), 1, 0, path)
    return (1, path.count("/"), 0, 0, path)


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
        if pp.name in LOCKFILES or pp.name == "pyproject.toml" or _is_extra_requirements(pp):
            lockfiles.append(p)
        kind = suggest_targets._container_kind(pp.name)
        if kind == "file":
            dockerfiles.append(p)
        elif kind == "template":
            templates.append(p)
    # A pyproject.toml comes after the lockfiles in the same folder; requirements files under other names come last.
    lockfiles.sort(key=_lockfile_sort_key)
    dockerfiles.sort(key=lambda p: (p.count("/"), p))
    return json.dumps(
        {
            "lockfiles": [
                {"path": p, "offline": _reads_offline(p)}
                | ({"other": True} if _is_extra_requirements(PurePosixPath(p)) else {})
                for p in lockfiles[:MAX_LOCKFILES]
            ],
            "workflows": sorted(workflows),
            "dockerfiles": dockerfiles[:MAX_DOCKERFILES],
            "more_dockerfiles": max(0, len(dockerfiles) - MAX_DOCKERFILES),
            "templates": sorted(templates),
            "python_version": ".python-version" if ".python-version" in paths else None,
            # How many more files plan() found than it lists: with any left out, the page never takes the one input
            # it sees in a subfolder for the only one (autoPick() in check-logic.mjs).
            "more_lockfiles": max(0, len(lockfiles) - MAX_LOCKFILES),
            # A root setup.py or setup.cfg makes the root a project of its own, whose dependencies wheelgap can't read,
            # so `wheelgap DIR` doesn't take the one input in a subfolder for the project's (sources._unread_project).
            "root_project_files": [name for name in ("setup.py", "setup.cfg") if name in paths],
            # Inputs `wheelgap DIR` finds up to 3 folders down (sources._nested_inputs: not in a hidden folder,
            # node_modules, site-packages or __pycache__) in a folder plan() skips (build/, dist/, target/, tests/...):
            # the page can't list them, so with any that isn't side-named it never takes the one it sees for the only one.
            "unlisted_inputs": sorted(
                p
                for p in paths
                if (pp := PurePosixPath(p)).name in (*LOCKFILES, "pyproject.toml")
                and 2 <= len(pp.parts) <= 4
                and _skipped_folder(pp.parts[:-1])
                and not any(f.startswith(".") or f in ("node_modules", "site-packages", "__pycache__") for f in pp.parts[:-1])
            ),
            # Every installer settings file in the repo, wherever it is: configs_for() picks the ones a check reads.
            # In any case: on a case-insensitive disk (macOS, Windows) wheelgap reads a Pyproject.toml too.
            "configs": sorted(p for p in paths if PurePosixPath(p).name.lower() in CONFIG_NAMES),
        }
    )


def _pyproject_is_project(path: Path) -> bool:
    """Whether a pyproject.toml wheelgap couldn't read still describes a project of its own, as `wheelgap DIR` decides
    (sources._unread_project): a [project] or [build-system] table, [tool.flit.metadata], PDM-legacy [tool.pdm]
    dependencies, Poetry dependencies other than python, or a file that isn't valid TOML. A root that is one isn't
    taken for a subfolder input's project (autoPick() in check-logic.mjs). A setup.py or setup.cfg beside it isn't
    fetched: the page has plan()'s `root_project_files` for those."""
    return sources._unread_project(path.parent) is not None


def _reads_nearby_configs(path: str) -> bool:
    """Whether wheelgap's reader for `path` reads the installer settings files near it (NEARBY_CONFIG_READERS)."""
    try:
        return file_kind(Path(path)) in NEARBY_CONFIG_READERS
    except SourceError:  # not a file wheelgap reads: check() says so
        return False


def _configs_near(path: str, configs: list[str], *, any_case: bool = False) -> list[str]:
    """The paths in `configs` that wheelgap reads for `path`: in its folder or a folder above it, by CONFIG_NAMES.
    `any_case`: also the ones named so in another case (Pyproject.toml), which wheelgap reads on a case-insensitive
    disk but not on the browser's, so they can only ever count as unread."""
    folder = PurePosixPath(path).parent
    folders = {folder, *folder.parents}
    return sorted(
        c
        for c in configs
        if (PurePosixPath(c).name.lower() if any_case else PurePosixPath(c).name) in CONFIG_NAMES
        and PurePosixPath(c).parent in folders
    )


def _configs_listing(configs_json: str | None) -> list[str] | None:
    """plan()'s `configs` as the worker passes it, or None when it doesn't know them (a truncated file list)."""
    try:
        configs = json.loads(configs_json) if configs_json else None
    except ValueError:
        return None
    if isinstance(configs, list) and all(isinstance(c, str) for c in configs):
        return configs
    return None


def configs_for(path: str, configs_json: str | None) -> str:
    """The installer settings files the worker fetches before `path` is checked: the ones in the repo (plan()'s
    `configs`) that wheelgap would read for it from disk, in its folder or a folder above it. Empty for a file whose
    reader reads none (a lockfile read offline, a pixi.lock), or when the repo's list isn't known."""
    configs = _configs_listing(configs_json)
    if configs is None or not _reads_nearby_configs(path):
        return json.dumps([])
    return json.dumps(_configs_near(path, configs))


def _unread_configs(root: str, path: str, configs_json: str | None) -> dict | None:
    """Why `path` has to stay private because wheelgap couldn't read every installer settings file it would on disk,
    or None when it could (or reads none). `unknown`: the repo's list of them isn't known (GitHub cut the file list
    short, or the worker didn't pass it), so one may be missing. `unread`: ones the repo has that the worker didn't
    manage to fetch, or that are named in another case (Pyproject.toml: read on a macOS or Windows checkout, never
    fetched here). Either way wheelgap would take the file as PyPI-only without them, so the page doesn't."""
    if not _reads_nearby_configs(path):
        return None
    configs = _configs_listing(configs_json)
    if configs is None:
        return {"unknown": True, "unread": []}
    unread = [
        c
        for c in _configs_near(path, configs, any_case=True)
        if PurePosixPath(c).name not in CONFIG_NAMES or not (Path(root) / c).is_file()
    ]
    return {"unknown": False, "unread": unread} if unread else None


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


def _public_extra_hosts(root: str, path: str, configs_json: str | None) -> list[str]:
    """The PUBLIC_EXTRA_INDEXES hosts a requirements file installs from, when they're certain to be its only other
    index; [] (the file stays private, as before) whenever that isn't certain.

    The option lines are the ones wheelgap's own reader collects as possibly naming an index
    (sources._read_requirements: the file and its includes, read as utf-8-sig with pip's line joining, comments and
    abbreviations; an include it can't read counts too), so this never reads a line differently from it. Each must be
    a whole _PUBLIC_EXTRA_LINE on a listed host.

    And no installer settings file may be in the file's folder or above. First, every one the repo has there must
    have been read (_unread_configs: the worker fetches them, configs_for; without the repo's list, or with one it
    couldn't fetch, nothing is certain), since _private() returns before it reads source.pypi_only for a file taken
    as public, and so would otherwise skip that check. Then, even a read one keeps the file private, whether or not it
    names an index (wheelgap's on-disk check below would already catch one that does): the allowlist is for a plain
    requirements file, and a project's own uv or pdm settings are another installer setup this doesn't vouch for.
    When the worker's wheelgap pin changes, re-check that _read_requirements still has this signature: if it doesn't,
    this fails closed."""
    if _unread_configs(root, path, configs_json):
        return []
    configs = _configs_listing(configs_json)
    if configs is None or _configs_near(path, configs, any_case=True):
        return []
    file = Path(root) / path
    found: list[str] = []
    seen: dict[bool, set[Path]] = {False: set(), True: set()}
    try:
        if sources._nearby_config_names_an_index(file):
            return []
        sources._read_requirements(file, seen, [], index_options=found)
        # pip shlex-splits an option line and expands ${VAR} in it, where wheelgap's collector splits on whitespace:
        # -'i' URL, --"index-url" URL, --ext\ra-index-url URL, --no-ind''ex or -${OPT} URL is an index option to pip
        # (checked with pip 25.3) that the collector misses. Any option line in the file or its includes with a
        # quote, a backslash or a $ isn't certain, so the file stays private.
        for read in seen[False] | seen[True]:
            for raw in sources._join_continued_lines(read.read_text(encoding="utf-8-sig")):
                line = sources._COMMENT_RE.split(raw, 1)[0].strip()
                if line.startswith("-") and any(c in line for c in "'\"\\$"):
                    return []
    except Exception:  # noqa: BLE001 - anything wheelgap can't read is never certain
        return []
    hosts = set()
    for line in found:
        m = _PUBLIC_EXTRA_LINE.fullmatch(line)
        if m is None or m.group(1) not in PUBLIC_EXTRA_INDEXES:
            return []
        hosts.add(m.group(1))
    return sorted(hosts)


def _private(source: Source, pkg: Package, *, allow_public_extra: bool = False) -> bool:
    """Whether a package that needs a lookup has to stay off pypi.org.

    One whose lock names another index for it (a Pipfile.lock source, a pixi.lock index) always does, as with the
    CLI. A pixi.lock records where each of its files came from, so that is all there is to ask of it. A
    requirements file, a pyproject.toml or a Pipfile.lock names an index for the whole input, if it names one
    (an --index-url or --extra-index-url line, an installer's index setting, a Pipfile source), so none of its
    packages is known to be PyPI's: the page looks none of them up and says so, where the CLI would ask PyPI. That
    includes a uv.toml, pyproject.toml or pdm.toml beside it or above it naming one, and one the page couldn't read
    (see _unread_configs). `allow_public_extra`: the file's only other index is a public one (_public_extra_hosts,
    which makes every one of those checks itself first), so its packages are looked up on PyPI after all.
    """
    if not pypi.may_look_up(pkg.registry):
        return True
    if allow_public_extra:
        return False
    return source.kind != "pixi" and not source.pypi_only


def _lookups(
    source: Source, *, allow_public_extra: bool = False, extra_pkg_hosts: dict[str, str] | None = None
) -> list[Package]:
    """The packages of `source` to look up on pypi.org, as pypi.fill_all() picks them: not skipped, no files (or only
    the ones a pixi.lock recorded), and no error from parsing, which a lookup would hide. One that has to stay off
    pypi.org is marked skipped as `private index` instead."""
    todo = []
    for pkg in source.packages:
        if pkg.skip or not (pkg.files is None or pkg.partial_files):
            continue
        if _private(source, pkg, allow_public_extra=allow_public_extra):
            pkg.skip = "private index"
        elif extra_pkg_hosts and canonicalize_name(pkg.name) in extra_pkg_hosts:
            host = extra_pkg_hosts[canonicalize_name(pkg.name)]
            pkg.error = f"may be installed from {host}, which this page doesn't read"
        elif not pkg.error:
            todo.append(pkg)
    return todo


def _lookup_key(pkg: Package) -> str:
    """How the worker and check() name a lookup: `name==version` for a pin, `name` for a range or a bare name."""
    name = canonicalize_name(pkg.name)
    return f"{name}=={pkg.version}" if pkg.version else name


def _keep_private_without_configs(source: Source, root: str, path: str, configs_json: str | None) -> dict | None:
    """As wheelgap does with a settings file it can't read: a file whose nearby settings files weren't all read is
    not known to be PyPI-only (_unread_configs says why)."""
    why = _unread_configs(root, path, configs_json)
    if why:
        source.pypi_only = False
    return why


def _missing_include(e: Exception) -> Path | None:
    """The absolute path of the include a requirements file names that isn't there, or None for any other error.
    wheelgap reports it as a SourceError ("FILE:LINE: -r NAME: file not found") raised from the FileNotFoundError,
    which has the path."""
    cause = e if isinstance(e, FileNotFoundError) else e.__cause__
    if isinstance(cause, FileNotFoundError) and cause.filename:
        return Path(os.fsdecode(cause.filename))
    return None


def needs(root: str, path: str, configs_json: str | None = None) -> str:
    """The PyPI lookups checking `path` needs: every package wheelgap's pypi.fill_all() would look up, with its
    canonical name and pinned version or null, each once. A package that has to stay off pypi.org (see _private) is
    never listed. A lockfile check() reads offline needs none: it's checked from the files it lists, or not at all.

    Requirements includes (-r, -c) are read from disk, so the worker fetches them first: a missing one comes back
    as an error of kind `include` with its `path` in the repo, and the worker fetches it and asks again. One outside
    the repo has no `path`. Any other error is left to check(), which reports it.

    configs: plan()'s `configs`, the repo's installer settings files, or null when GitHub cut the file list short.
    The worker fetches the ones configs_for() names first; with any of them missing, or no list, nothing is looked up.
    """
    if _reads_offline(path):
        return json.dumps({"lookups": [], "total": 0})
    try:
        source = load_file(Path(root) / path)
    except (SourceError, ValueError, OSError) as e:  # ValueError: a file that isn't valid TOML or JSON
        if (missing := _missing_include(e)) is None:
            return json.dumps({"error": str(e).replace(f"{root}/", ""), "kind": "source"})
        # An include (-r, -c) the worker hasn't fetched yet: name it. wheelgap makes an include's path absolute
        # (os.path.abspath, which folds `..` but follows no link) before reading it, so compare it with the root made
        # absolute the same way: resolve() would follow a link in the root's own path, and nothing would match.
        base = Path(os.path.abspath(root))
        if not missing.is_relative_to(base):
            error = f"{PurePosixPath(path).name} includes a file outside the repository, which can't be read here"
            return json.dumps({"error": error, "kind": "include", "path": None})
        inc = missing.relative_to(base).as_posix()
        return json.dumps({"error": f"{inc} (an include) isn't fetched yet", "kind": "include", "path": inc})
    _keep_private_without_configs(source, root, path, configs_json)
    extra_hosts = (
        _public_extra_hosts(root, path, configs_json) if source.kind == "requirements" and not source.pypi_only else []
    )
    extra_pkg_hosts = {pkg: host for host in extra_hosts for pkg in PUBLIC_EXTRA_INDEXES[host]}
    lookups = {
        _lookup_key(pkg): {"name": canonicalize_name(pkg.name), "version": pkg.version or None}
        for pkg in _lookups(source, allow_public_extra=bool(extra_hosts), extra_pkg_hosts=extra_pkg_hosts)
    }
    return json.dumps({"lookups": list(lookups.values()), "total": len(lookups)})


def _fill_lookups(
    source: Source,
    targets: list[Target],
    lookups: dict,
    *,
    allow_public_extra: bool = False,
    extra_pkg_hosts: dict[str, str] | None = None,
) -> None:
    """Fill each package that needs it from the PyPI JSON the worker fetched, the way pypi.fill() does: a pinned
    package from its release's files, a range resolved for each target Python's major.minor (3.14 for 3.14t, as the
    CLI passes them: Requires-Python is compared with a version, and `3.14t` isn't one). A package that wasn't
    looked up (over the cap, failed, or 404) gets an error, so the check reports it as unknown, never as a gap.
    So does one whose answer isn't PyPI's JSON: a pin's or a bare name's files are the answer's `urls`, which
    _fill_json_data reads as an empty list when the key is missing, and no files reads as "can't install"."""
    base = pypi.index_url()
    pythons = tuple(dict.fromkeys(f"{t.major}.{t.minor}" for t in targets))
    for pkg in _lookups(source, allow_public_extra=allow_public_extra, extra_pkg_hosts=extra_pkg_hosts):
        data = lookups.get(_lookup_key(pkg))
        reads_urls = bool(pkg.version) or not pkg.spec  # a range reads `releases`, and says so when it's missing
        if isinstance(data, dict) and (not reads_urls or isinstance(data.get("urls"), list)):
            pypi._resolve_pythons(pkg, pythons, lambda p, py, d=data: pypi._fill_json_data(p, d, py, base), None)
        else:
            pkg.error = "not looked up"
        if pkg.partial_files and pkg.error:
            pkg.files = None  # as fill_all(): never judged on the files a pixi.lock happened to record


def check(
    root: str, lockfile: str, options_json: str, lookups_json: str | None = None, configs_json: str | None = None
) -> str:
    """The lockfile's packages with a gap on any target, read offline from the files it lists.

    options: {"pythons": [...], "platforms": [...], "glibc"?, "musl"?, "macos"?}. A target Python the lock's
    requires-python leaves out is reported in `outside_lock` and none of its cells count as gaps: uv keeps no wheels
    for it in the lock, so they'd be false gaps (the CLI rechecks them on PyPI; the page says so instead).

    A file that doesn't list every package's files (LOOKUPS: Pipfile.lock, requirements files, pixi.lock,
    pyproject.toml) is checked from PyPI JSON the worker fetched for the lookups needs() named, keyed
    `name` (a range) or `name==version` (a pin). A package with no answer is reported as unknown. PyPI has each
    release's files for every Python, so nothing is `outside_lock` there: like the CLI, a pyproject.toml is checked
    on the targets asked for, whatever its requires-python.

    configs: as for needs(). The result's `configs` are the installer settings files beside the file or above it that
    were read; `configs_unread` the ones the repo has there that couldn't be fetched, and `configs_unknown` says the
    repo's list of them wasn't known: either keeps every package of the file off pypi.org.

    The result's `private` names the packages left off pypi.org as a private index's (they're in `skipped` too),
    and `looked_up` says whether the files came from PyPI rather than from the lockfile. `listed` counts the packages
    the file lists, checked or not: none at all for a pyproject.toml whose [project] has no dependencies.
    `private_count` counts entries too, not distinct names: two requirements for one name with different markers
    must both count when the page asks whether every entry was skipped as private.
    """
    global _last_check
    _last_check = None
    _suggestions.clear()
    options = json.loads(options_json)
    limits = {k: options[k] for k in ("glibc", "musl", "macos") if isinstance(options.get(k), str)}
    try:
        targets = api.make_targets(options["pythons"], options["platforms"], **limits)
    except (TargetError, ValueError) as e:
        return json.dumps({"error": str(e), "kind": "targets"})
    path = Path(root) / lockfile
    try:
        source = load_file(path)
    except (SourceError, ValueError, OSError) as e:  # ValueError: a lockfile that isn't valid TOML or JSON
        error = {"error": str(e).replace(f"{root}/", ""), "kind": "source"}
        if path.name == "pyproject.toml":
            error["project"] = _pyproject_is_project(path)
        return json.dumps(error)
    looked_up = not _reads_offline(lockfile)
    unread = _keep_private_without_configs(source, root, lockfile, configs_json) or {"unknown": False, "unread": []}
    read_configs = [c for c in json.loads(configs_for(lockfile, configs_json)) if (Path(root) / c).is_file()]
    extra_hosts = (
        _public_extra_hosts(root, lockfile, configs_json)
        if looked_up and source.kind == "requirements" and not source.pypi_only
        else []
    )
    extra_pkg_hosts = {pkg: host for host in extra_hosts for pkg in PUBLIC_EXTRA_INDEXES[host]}
    if looked_up:
        _fill_lookups(
            source,
            targets,
            json.loads(lookups_json) if lookups_json else {},
            allow_public_extra=bool(extra_hosts),
            extra_pkg_hosts=extra_pkg_hosts,
        )
        # As api._check_lockfile() builds them, which can't be asked: it refuses a pyproject.toml and does its own
        # lookups with urllib. A package no target installs (its marker rules it out everywhere) isn't a row.
        check_rows = [r for r in api.check([source], targets) if r.needed]
        results = [
            api.Result(name=r.package.name, version=r.package.version, statuses=dict(r.results)) for r in check_rows
        ]
        outside: set[str] = set()
    else:
        try:
            # A listed lockfile can still leave a package's files out, so that reading it offline would need the
            # index; that's a "needs PyPI lookups" answer, not a lockfile the page failed to read.
            offline.require_offline([source])
        except SourceError as e:
            return json.dumps({"error": str(e), "kind": "needs-index"})
        try:
            results, source, check_rows = api._check_lockfile(path, targets, online=False, offline=True)
        except (SourceError, ValueError, OSError) as e:  # ValueError: a lockfile that isn't valid TOML or JSON
            return json.dumps({"error": str(e).replace(f"{root}/", ""), "kind": "source"})
        outside = set(pythons_outside_lock(source, targets))
    # --suggest's rule (origin.known_pypi): only packages positively from PyPI are looked up there, so a private
    # package's name never goes to pypi.org, and a same-named PyPI project is never offered as its fix. A package
    # resolved for each Python is judged by its resolutions, which the lookups mark, as origin.private_names() does.
    private = origin.private_names([source])
    on_pypi: dict[tuple[str, str | None], bool] = {}
    for original in source.packages:
        for p in original.resolutions.values() or (original,):
            key = (canonicalize_name(p.name), p.version)
            on_pypi[key] = on_pypi.get(key, True) and origin.known_pypi(p, source, private=private)
    _targets.clear()
    _targets.update((t.label(True), t) for t in targets)
    counted = [t for t in targets if t.python not in outside]
    _last_check = (check_rows, counted, source)
    per_target = {t.label(True): {s.value: 0 for s in (Status.WHEEL, *GAPS, Status.UNKNOWN)} for t in targets}
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
            row_entry = {
                "name": r.name,
                "version": r.version,
                "statuses": statuses,
                "gaps": gaps,
                "unknown": unknown,
                "keep": keep,
                "lookup": lookup,
            }
            if host := extra_pkg_hosts.get(canonicalize_name(r.name)):
                row_entry["reason"] = f"may be installed from {host}, which this page doesn't read"
            rows.append(row_entry)
    return json.dumps(
        {
            "wheelgap": wheelgap.__version__,
            "lockfile": lockfile,
            "targets": [{"label": t.label(True), "python": t.python, "platform": t.platform} for t in targets],
            "requires_python": source.requires_python,
            "outside_lock": sorted(outside),
            "packages": len(results),
            "listed": len(source.packages),
            "skipped": sorted({p.name for p in source.packages if p.skip}),
            "private": sorted({p.name for p in source.packages if p.skip == "private index"}),
            "private_count": sum(p.skip == "private index" for p in source.packages),
            "configs": read_configs,
            "configs_unread": unread["unread"],
            "configs_unknown": unread["unknown"],
            "extra_indexes": extra_hosts,
            "reader": source.kind,
            "looked_up": looked_up,
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
    _suggestions.clear()
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
        _suggestions[f"{row['name']}=={row['version']}"] = found
        out[f"{row['name']}=={row['version']}"] = {
            "found": {t.label(True): v for t, v in found.found.items()},
            "loses": [t.label(True) for t in found.loses],
        }
    return json.dumps(out)
