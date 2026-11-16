"""The build lab: does a package with no wheel for a platform build from its source there, and does the result work?

    python3 scripts/build_lab.py plan data/coverage.json data/builds.json [--max N] [--github-output FILE]
    python scripts/build_lab.py run --name N --version V --target T --runner R --out result.json
    python3 scripts/build_lab.py merge data/builds.json results/ plan.json

.github/workflows/build-lab.yml runs the three steps:

- plan (our code only) picks the most-downloaded packages whose current release in data/coverage.json has an sdist
  but no wheel for a lab target, skipping pairs built recently, and prints the build matrix.
- run, in a job that runs the package's own build code, first checks that the interpreter really is the target's,
  then builds only that package from its PyPI sdist the way pip does when there's no wheel (`pip wheel --no-binary
  NAME`: build dependencies still come as wheels). It records the wheel it got and installs it. Then it imports the
  wheel's shallowest modules, which must work (a module that needs one from outside the wheel, an optional or
  undeclared dependency missing on every platform alike, is left out), and, in a fresh interpreter, every extension
  module it can (optional backends, test helpers and ctypes libraries named like modules may fail: at least one must
  load; any other failure, a crash or a timeout there is recorded as a failure). On a free-threaded Python it
  records whether either check turned the GIL back on. A download or resolution failure, a wheel with nothing it
  could import, or a check that exits without an answer writes no result, so it's retried next time instead of
  recorded as a failure. Nothing it builds is kept or published.
- merge (our code only, the one job that can write) accepts a result only if it is exactly what run writes, for the
  pair the plan assigned to that job, and dates it itself. Then it updates data/builds.json.

What merge can't rule out: build code has the runner's admin rights, so it could in principle read the job's
artifact token and upload a well-formed result under another job's name. The results are a lab's observations, not
signed facts: they're published with their date and runner, and nothing is installed from them.

Standard library only, so plan and merge never import third-party code.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import shutil
import subprocess
import sys
import sysconfig
import tempfile
import time
import zipfile
from pathlib import Path

SCHEMA = 1
# Each lab target: the tracker's target label, the GitHub runner it builds on, the Python it builds for, the
# interpreter platform (sysconfig.get_platform()) that must be running it, and the wheel platform tags that count as
# a build for it. An image means the build runs in that container (Alpine: with its C toolchain, build-base, added).
TARGETS: dict[str, dict[str, str]] = {
    "3.14 win-arm64": {"runner": "windows-11-arm", "python": "3.14", "platform": "win-arm64", "wheel": r"win_arm64"},
    "3.14t win-arm64": {"runner": "windows-11-arm", "python": "3.14t", "platform": "win-arm64", "wheel": r"win_arm64"},
    "3.14t linux-x86_64": {
        "runner": "ubuntu-24.04",
        "python": "3.14t",
        "platform": "linux-x86_64",
        "wheel": r"(?:many)?linux[_0-9]*_x86_64",
    },
    "3.14 musl-aarch64": {
        "runner": "ubuntu-24.04-arm",
        "python": "3.14",
        "platform": "linux-aarch64",
        "musl": "yes",
        "wheel": r"(?:musl)?linux[_0-9]*_aarch64",
        "image": "python:3.14-alpine@sha256:5862d971d1f0ae26b258cefa4e893556b1d3aba81bd2ff30888704ad378bab67",
    },
    # The newest Python on the two most common platforms: a month after 3.15.0, hundreds of the top packages still
    # have no wheel for it on either.
    "3.15 linux-x86_64": {
        "runner": "ubuntu-24.04",
        "python": "3.15",
        "platform": "linux-x86_64",
        "wheel": r"(?:many)?linux[_0-9]*_x86_64",
    },
    "3.15 win-amd64": {"runner": "windows-2025", "python": "3.15", "platform": "win-amd64", "wheel": r"win_amd64"},
}
REFRESH_DAYS = 90  # a result for the same release is rebuilt after this long (toolchains and runners change)
DEFAULT_MAX = 80  # builds a week, shared in turn between the targets
BUILD_TIMEOUT = 40 * 60
INSTALL_TIMEOUT = 15 * 60
IMPORT_TIMEOUT = 5 * 60
MAX_EXCERPT = 400
MAX_EXTENSIONS = 200  # extension modules imported from one wheel
MAX_RESULT_BYTES = 20_000
MAX_BUILDS_BYTES = 5_000_000

OUTCOMES = frozenset({"built", "failed", "timeout"})
IMPORTS = frozenset({"ok", "failed", "deps-failed"})  # or None when nothing was built
GIL = frozenset({"kept-off", "re-enabled"})  # or None: not free-threaded, no extension module, or no import
# Why a build failed, read from the log's error lines only; the first that matches wins.
REASONS: list[tuple[str, re.Pattern[str]]] = [
    (
        "needs OpenSSL",
        re.compile(
            r"openssl\W[^\n]{0,80}(not found|could not find|no such file|missing|required)|"
            r"(could not find|cannot find|missing)[^\n]{0,40}openssl",
            re.IGNORECASE,
        ),
    ),
    (
        "needs a Rust toolchain",
        re.compile(
            r"\b(cargo|rustc)\b[^\n]{0,60}(is not installed|not found|could not be "
            r"found)|install rust",
            re.IGNORECASE,
        ),
    ),
    (
        "needs a C/C++ compiler",
        re.compile(
            r"Microsoft Visual C\+\+ \d|\b(gcc|cc|cl\.exe|g\+\+)\W+(command )?not "
            r"found|unable to find vcvarsall",
            re.IGNORECASE,
        ),
    ),
    ("needs CMake", re.compile(r"\bcmake\W+(command )?not found|could not find cmake|no cmake", re.IGNORECASE)),
    (
        "needs a system library",
        re.compile(
            r"fatal error: [\w/.+-]+\.h: No such file|cannot find -l\w+|"
            r"(pkg-config|pg_config)\b[^\n]{0,40}(not found|failed)",
            re.IGNORECASE,
        ),
    ),
    (
        "build dependency failed",
        re.compile(
            r"installing build dependencies( for \S+)? did not run successfully|"
            r"failed to build installable wheels for some pyproject",
            re.IGNORECASE,
        ),
    ),
    # The package's own message that it doesn't support the platform (uvloop: "does not support Windows").
    (
        "unsupported platform",
        re.compile(
            r"does not support (windows|macos|linux|this platform|free-threaded|the free-threaded)|"
            r"not supported on|unsupported (platform|architecture)",
            re.IGNORECASE,
        ),
    ),
    ("compile error", re.compile(r"\berror\b", re.IGNORECASE)),
]
OTHER = "other"
REASON_NAMES = frozenset({name for name, _ in REASONS} | {OTHER})
# pip couldn't fetch or resolve something: not the build's fault, so nothing is recorded.
UNRECORDED = re.compile(
    r"Max retries exceeded|Read timed out|ConnectionError|Temporary failure in name resolution|HTTP error \d|"
    r"\b(429|5\d\d) Server Error",
    re.IGNORECASE,
)


def unrecorded(log: str, name: str, version: str) -> bool:
    """Whether a failed build shouldn't be recorded: network trouble anywhere, or pip couldn't find the package's own
    sdist. A build dependency pip can't find is a real build failure, and is recorded."""
    own = re.escape(f"{name}=={version}")
    return bool(UNRECORDED.search(log)) or bool(
        re.search(
            rf"(Could not find a version that satisfies the requirement|No matching distribution found for) "
            rf"{own}(\s|$)",
            log,
            re.IGNORECASE,
        )
    )


ERROR_LINE = re.compile(
    r"error|fatal|not found|failed|could not|not installed|is required|did not run successfully", re.IGNORECASE
)
TOOLCHAINS = ("rustc", "cargo", "gcc", "cl", "cmake")
NAME = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9._-]{0,98}[A-Za-z0-9])?", re.ASCII)
VERSION = re.compile(r"[0-9][0-9A-Za-z.+!_-]{0,39}", re.ASCII)
PYTHON = re.compile(r"3\.[0-9]{1,2}\.[0-9]{1,3}t?", re.ASCII)
WHEEL_TAG = re.compile(r"[a-z0-9_.]{1,40}-[a-z0-9_.]{1,40}-[a-z0-9_.]{1,80}", re.ASCII)
DATE = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", re.ASCII)
RESULT_KEYS = frozenset({
    "schema", "name", "version", "target", "runner", "python", "outcome", "seconds", "wheel", "extensions",
    "import", "gil", "gil_module", "reason", "excerpt", "toolchains",
})  # fmt: skip


def canonical(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def today() -> str:
    return dt.datetime.now(dt.UTC).date().isoformat()


# --- plan ---------------------------------------------------------------------------------------------------------


def plan(
    coverage: dict,
    builds: dict | None,
    limit: int,
    now: str,
    top: int | None = None,
    refresh: bool = False,
    only: set[str] | None = None,
) -> list[dict]:
    """The pairs to build, most-downloaded first and taking turns between the lab targets, at most `limit`; with
    `top`, only packages ranked that high (a census of the top N); with `refresh`, recent results are rebuilt too;
    with `only` (canonical names), just those packages, recent results included."""
    labels = [t["label"] for t in coverage["targets"]]
    recent = set()
    cutoff = dt.date.fromisoformat(now) - dt.timedelta(days=REFRESH_DAYS)
    for r in [] if refresh or only else (builds or {}).get("results", []):
        if dt.date.fromisoformat(r["date"]) >= cutoff:
            recent.add((canonical(r["name"]), r["version"], r["target"]))
    queues: dict[str, list[dict]] = {t: [] for t in TARGETS}
    for p in sorted(coverage["packages"], key=lambda p: p["rank"]):
        if top is not None and p["rank"] > top:
            break
        name, version = p["name"], p["version"]
        if p.get("error") or not isinstance(name, str) or not isinstance(version, str):
            continue
        if not NAME.fullmatch(name) or not VERSION.fullmatch(version):
            continue
        if only is not None and canonical(name) not in only:
            continue
        for target in TARGETS:
            if target not in labels or p["results"][labels.index(target)] != "source":
                continue
            if (canonical(name), version, target) in recent:
                continue
            queues[target].append({"name": name, "version": version, "target": target, "rank": p["rank"]})
    picked: list[dict] = []
    while len(picked) < limit and any(queues.values()):
        for target in TARGETS:
            if queues[target] and len(picked) < limit:
                picked.append(queues[target].pop(0))
    for i, job in enumerate(picked):
        spec = TARGETS[job["target"]]
        job.update(id=str(i), runner=spec["runner"], python=spec["python"])
        if "image" in spec:
            job["image"] = spec["image"]
    return picked


def cmd_plan(args: argparse.Namespace) -> int:
    coverage = json.loads(Path(args.coverage).read_text(encoding="utf-8"))
    builds_path = Path(args.builds)
    builds = json.loads(builds_path.read_text(encoding="utf-8")) if builds_path.exists() else None
    only = {canonical(n.strip()) for n in args.only.split(",") if n.strip()} if args.only else None
    jobs = plan(coverage, builds, args.max, today(), args.top, args.refresh, only)
    hosted = [j for j in jobs if "image" not in j]
    docker = [j for j in jobs if "image" in j]
    print(
        f"{len(jobs)} builds: " + ", ".join(f"{j['name']} {j['version']} ({j['target']})" for j in jobs),
        file=sys.stderr,
    )
    if args.github_output:
        with open(args.github_output, "a", encoding="utf-8") as f:
            f.write(f"count={len(jobs)}\n")
            f.write(f"hosted={json.dumps(hosted)}\nhosted_count={len(hosted)}\n")
            f.write(f"docker={json.dumps(docker)}\ndocker_count={len(docker)}\n")
            f.write(f"all={json.dumps(jobs)}\n")
    else:
        print(json.dumps(jobs, indent=1))
    return 0


# --- run (inside the build job) -----------------------------------------------------------------------------------


def error_lines(log: str) -> list[str]:
    return [line.strip() for line in log.splitlines() if ERROR_LINE.search(line)]


def classify(log: str) -> str:
    """Why a build failed, from the lines that report errors (a log merely mentioning OpenSSL proves nothing)."""
    text = "\n".join(error_lines(log))
    for name, pattern in REASONS:
        if pattern.search(text):
            return name
    return OTHER


def excerpt(log: str) -> str:
    """The last lines that say what went wrong, as printable ASCII: it ends up on a web page and in terminals."""
    lines = error_lines(log)
    text = " | ".join(lines[-4:]) or " | ".join(line.strip() for line in log.splitlines()[-3:])
    text = re.sub(r"\x1b\[[0-9;]*[A-Za-z]", "", text)
    text = "".join(c if 32 <= ord(c) < 127 else "?" for c in text)
    return text[-MAX_EXCERPT:]


def wheel_tag(wheel: Path) -> str:
    """python-abi-platform from a wheel's file name (the last three dash-separated fields)."""
    return "-".join(wheel.name[: -len(".whl")].split("-")[-3:]).lower()


def wheel_modules(wheel: Path) -> tuple[list[str], list[str], int]:
    """What to import from a wheel: its shallowest packages and modules (namespace packages included, tests left out),
    its extension modules by dotted name (test helpers left out), and how many extension modules there are."""
    with zipfile.ZipFile(wheel) as z:
        entries = [e for e in z.namelist() if not e.split("/", 1)[0].endswith((".dist-info", ".data"))]
    dotted = re.compile(r"[A-Za-z_][A-Za-z0-9_]*(\.[A-Za-z_][A-Za-z0-9_]*)*", re.ASCII)
    extensions = []
    for e in entries:
        tagged = re.search(r"\.(cpython-[^/]+|abi3t?|cp3\d+t?-[^/]+)\.so$", e) is not None
        if e.endswith(".pyd") or tagged:  # a plain .so is a shared library (llvmlite's, via ctypes), not a module
            parts = e.split("/")
            name = ".".join([*parts[:-1], parts[-1].split(".", 1)[0]])
            if dotted.fullmatch(name):
                extensions.append(name)
    shipped = [e for e in extensions if not any(p in ("tests", "test") for p in e.split("."))]  # not test helpers
    modules = set(shipped)
    for e in entries:
        if not e.endswith(".py"):
            continue
        parts = e[:-3].split("/")
        if parts[-1] == "__init__":
            parts = parts[:-1]
        name = ".".join(parts)
        if parts and dotted.fullmatch(name) and not any(p in ("tests", "test") for p in parts):
            modules.add(name)
    shallow = sorted(modules, key=lambda m: (m.count("."), m))
    return shallow[:3], sorted(set(shipped))[:MAX_EXTENSIONS], len(set(extensions))


def wheel_names(wheel: Path) -> dict[str, list[str]]:
    """Every module a wheel installs, and its regular packages (those with an __init__), by dotted name: a missing
    module is the wheel's own if it's one of the modules or inside one of the packages, and otherwise a dependency."""
    with zipfile.ZipFile(wheel) as z:
        entries = [e for e in z.namelist() if not e.split("/", 1)[0].endswith((".dist-info", ".data"))]
    dotted = re.compile(r"[A-Za-z_][A-Za-z0-9_]*(\.[A-Za-z_][A-Za-z0-9_]*)*", re.ASCII)
    modules, packages = set(), set()
    for e in entries:
        parts = e.split("/")
        if not parts[-1].endswith((".py", ".pyd", ".so")):
            continue
        last = parts[-1].split(".", 1)[0]
        if last == "__init__":
            packages.add(".".join(parts[:-1]))
        else:
            modules.add(".".join([*parts[:-1], last]))
    return {"modules": sorted(n for n in modules if dotted.fullmatch(n)),
            "packages": sorted(n for n in packages if dotted.fullmatch(n))}  # fmt: skip


# argv: "top" or "extensions", a JSON file from wheel_names, then the modules. "top" must import each module, but
# leaves out one that needs a module from outside the wheel (an optional or undeclared dependency, missing on every
# platform alike). "extensions" also excuses the interpreter's own modules failing (no Tk in the image) and ctypes
# libraries named like extension modules; anything else is a failure there too.
IMPORT_CHECK = """
import importlib, json, os, re, sys, sysconfig, warnings
mode, names = sys.argv[1], sys.argv[3:]
with open(sys.argv[2], encoding="utf-8") as f:
    wheel = json.load(f)
modules, packages = set(wheel["modules"]), set(wheel["packages"])
paths = sysconfig.get_paths()
real = lambda p: os.path.normcase(os.path.realpath(p))
installed = [real(paths[k]) for k in ("purelib", "platlib")]

def own(name):
    parts = name.split(".")
    return name in modules or any(".".join(parts[:i]) in packages for i in range(1, len(parts) + 1))

def interpreters_own(e):
    # Somewhere in the chain, one of the interpreter's own modules failed to load (_tkinter without libtk): an
    # ImportError named for a stdlib module, from outside site-packages. Named, not by path: on Windows the stdlib's
    # extension modules are in DLLs, not Lib.
    seen = set()
    while e is not None and id(e) not in seen:
        seen.add(id(e))
        if isinstance(e, ImportError) and e.name and e.name.split(".")[0] in sys.stdlib_module_names:
            where = real(e.path) if e.path else ""
            if not any(where.startswith(d + os.sep) for d in installed):
                return True
        e = e.__cause__ or e.__context__
    return False

def excused(e):
    if isinstance(e, ModuleNotFoundError) and e.name:
        if e.name.split(".")[0] in sys.stdlib_module_names:
            return mode == "extensions"
        return not own(e.name)
    if mode != "extensions":
        return False
    text = str(e)
    if isinstance(e, ImportError) and "does not define module export function" in text:
        return True  # a ctypes library named like an extension module
    if isinstance(e, SystemError) and "failed without raising an exception" in text:
        return True  # the same, on Windows
    return interpreters_own(e)

module = None
loaded = []
gil = getattr(sys, "_is_gil_enabled", None)
with warnings.catch_warnings(record=True) as caught:
    warnings.simplefilter("always")
    for m in names:
        try:
            importlib.import_module(m)
        except Exception as e:
            if not excused(e):
                raise
            continue
        loaded.append(m)
for w in caught:
    found = re.search(r"has been enabled to load module '([A-Za-z0-9_.]{1,100})'", str(w.message))
    if found and module is None:
        module = found.group(1)
print("WHEELGAP-LAB " + json.dumps({"gil_enabled": gil() if gil else None, "module": module, "loaded": loaded}))
"""
GIL_MODULE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*(\.[A-Za-z_][A-Za-z0-9_]*){0,9}", re.ASCII)


def interpreter_problem(target: str) -> str | None:
    """Why this Python isn't the target's (None if it is): the platform, free-threading and, for musl, the libc."""
    spec = TARGETS[target]
    if sysconfig.get_platform() != spec["platform"]:
        return f"running on {sysconfig.get_platform()}, not {spec['platform']}"
    if bool(sysconfig.get_config_var("Py_GIL_DISABLED")) != spec["python"].endswith("t"):
        return "free-threading doesn't match the target"
    if f"{sys.version_info[0]}.{sys.version_info[1]}" != spec["python"].rstrip("t"):
        return f"Python {sys.version_info[0]}.{sys.version_info[1]}, not {spec['python']}"
    if spec.get("musl") and not any(Path("/lib").glob("ld-musl-*.so.1")):
        return "not a musl libc"
    return None


def run_build(name: str, version: str, target: str, work: Path) -> dict | None:
    """Build, install and import; the result fields that depend on what happened, or None if nothing should be
    recorded (pip couldn't download or resolve something)."""
    env = dict(os.environ, PIP_DISABLE_PIP_VERSION_CHECK="1", PIP_NO_INPUT="1")
    free_threaded = TARGETS[target]["python"].endswith("t")
    wheels = work / "wheels"
    cmd = [sys.executable, "-m", "pip", "wheel", "--no-deps", "--no-binary", name, "--no-cache-dir", "-w", str(wheels),
           f"{name}=={version}"]  # fmt: skip
    start = time.monotonic()
    try:
        p = subprocess.run(cmd, check=False, capture_output=True, text=True, errors="replace", timeout=BUILD_TIMEOUT,
                           env=env)  # fmt: skip
    except subprocess.TimeoutExpired:
        return {"outcome": "timeout", "seconds": BUILD_TIMEOUT, "wheel": None, "extensions": None, "import": None,
                "gil": None, "gil_module": None, "reason": None, "excerpt": ""}  # fmt: skip
    seconds = round(time.monotonic() - start)
    log = p.stdout + p.stderr
    print(log[-6000:])
    built = sorted(wheels.glob("*.whl"))
    if p.returncode != 0 or not built:
        if unrecorded(log, name, version):
            print("build_lab: pip couldn't download or resolve something; not recording this", file=sys.stderr)
            return None
        return {"outcome": "failed", "seconds": seconds, "wheel": None, "extensions": None, "import": None,
                "gil": None, "gil_module": None, "reason": classify(log), "excerpt": excerpt(log)}  # fmt: skip
    modules, extension_modules, extensions = wheel_modules(built[0])
    ours = work / "wheel-modules.json"
    ours.write_text(json.dumps(wheel_names(built[0])), encoding="utf-8")
    result = {"outcome": "built", "seconds": seconds, "wheel": wheel_tag(built[0]), "extensions": extensions,
              "import": None, "gil": None, "gil_module": None, "reason": None, "excerpt": ""}  # fmt: skip
    install = [sys.executable, "-m", "pip", "install", "--no-cache-dir", str(built[0])]
    try:
        i = subprocess.run(install, check=False, capture_output=True, text=True, errors="replace",
                           timeout=INSTALL_TIMEOUT, env=env)  # fmt: skip
    except subprocess.TimeoutExpired:
        i = None
    if i is None or i.returncode != 0:
        print((i.stdout + i.stderr)[-3000:] if i else "dependency install timed out")
        result["import"] = "deps-failed"
        return result
    if not modules:
        print("build_lab: the wheel has nothing to import; not recording this", file=sys.stderr)
        return None
    answers = []
    for mode, names in (("top", modules), ("extensions", extension_modules)):
        if mode == "extensions" and not extensions:
            break
        check = _import_check(work, env, mode, ours, names)
        if check is None:
            print(f"build_lab: the {mode} import check gave no answer; not recording this", file=sys.stderr)
            return None
        code, answer, stderr = check
        if code != 0 or answer is None:
            result["import"] = "failed"
            result["excerpt"] = excerpt(stderr)
            return result
        if not answer["loaded"]:
            print(f"build_lab: nothing loaded in the {mode} import check; not recording this", file=sys.stderr)
            return None
        answers.append(answer)
    result["import"] = "ok"
    if free_threaded and extensions:
        # Either check may be the one that turned it back on (a top-level import can load a dependency's module).
        on = [a for a in answers if a["gil_enabled"]]
        result["gil"] = "re-enabled" if on else "kept-off"
        module = next((a["module"] for a in on if a.get("module")), None)
        if on and isinstance(module, str) and GIL_MODULE.fullmatch(module):
            result["gil_module"] = module  # the module CPython's RuntimeWarning names
    return result


def _import_check(
    work: Path, env: dict, mode: str, ours: Path, modules: list[str], python: str = sys.executable
) -> tuple[int, dict | None, str] | None:
    """Run IMPORT_CHECK in a fresh interpreter (`python`: gap_wheels runs it in a fresh venv): (exit code, its
    answer or None, stderr). A timeout is a failure; an exit without an answer and without an error (something
    called sys.exit(0) on import) is None: no answer."""
    try:
        c = subprocess.run([python, "-c", IMPORT_CHECK, mode, str(ours), *modules], check=False,
                           capture_output=True, text=True, errors="replace", timeout=IMPORT_TIMEOUT, cwd=work,
                           env=env)  # fmt: skip
    except subprocess.TimeoutExpired:
        print("import timed out")
        return 1, None, "import timed out"
    line = next((ln for ln in reversed(c.stdout.splitlines()) if ln.startswith("WHEELGAP-LAB ")), None)
    if c.returncode != 0 or line is None:
        print((c.stdout + c.stderr)[-3000:])
    try:
        answer = json.loads(line.removeprefix("WHEELGAP-LAB ")) if line else None
    except ValueError:
        answer = None
    if not isinstance(answer, dict) or not isinstance(answer.get("loaded"), list):
        answer = None
    if c.returncode == 0 and answer is None:
        return None
    return c.returncode, answer, c.stderr


def cmd_run(args: argparse.Namespace) -> int:
    target = args.target
    if target not in TARGETS or not NAME.fullmatch(args.name) or not VERSION.fullmatch(args.version):
        print(f"build_lab: not a lab job: {args.name} {args.version} {target}", file=sys.stderr)
        return 2
    if (why := interpreter_problem(target)) is not None:
        print(f"build_lab: this Python isn't {target}'s: {why}", file=sys.stderr)
        return 3
    with tempfile.TemporaryDirectory() as tmp:
        fields = run_build(args.name, args.version, target, Path(tmp))
    if fields is None:
        return 4
    result = {
        "schema": SCHEMA,
        "name": args.name,
        "version": args.version,
        "target": target,
        "runner": args.runner,
        "python": ".".join(map(str, sys.version_info[:3])) + ("t" if target.split()[0].endswith("t") else ""),
        **fields,
        "toolchains": [t for t in TOOLCHAINS if shutil.which(t)],
    }
    Path(args.out).write_text(json.dumps(result) + "\n", encoding="utf-8")
    print(json.dumps(result, indent=1))
    return 0


# --- merge --------------------------------------------------------------------------------------------------------


def _int(value: object, top: int) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and 0 <= value <= top


def _one_of(value: object, allowed: frozenset[str]) -> bool:
    return isinstance(value, str) and value in allowed


def problems(result: object, job: dict) -> list[str]:
    """Why `result` isn't exactly what run writes for this plan job (empty if it is). Every value is untrusted, so
    each is type-checked before it's compared or looked up."""
    if not isinstance(result, dict):
        return ["not an object"]
    if set(result) != RESULT_KEYS:
        return [f"keys {sorted(str(k) for k in set(result) ^ RESULT_KEYS)}"]
    out = []
    for key in ("name", "version", "target", "runner"):
        if not isinstance(result[key], str) or result[key] != job[key]:
            out.append(f"{key} isn't the planned one")
    if result["schema"] != SCHEMA or isinstance(result["schema"], bool):
        out.append("schema")
    python = result["python"]
    planned = job["python"]
    if not (isinstance(python, str) and PYTHON.fullmatch(python)):
        out.append("python")
    elif python.endswith("t") != planned.endswith("t") or not python.startswith(planned.rstrip("t") + "."):
        out.append("python isn't the planned one")
    if not _one_of(result["outcome"], OUTCOMES):
        return [*out, "outcome"]
    built = result["outcome"] == "built"
    if not _int(result["seconds"], BUILD_TIMEOUT):
        out.append("seconds")
    wheel, extensions, imported, gil = result["wheel"], result["extensions"], result["import"], result["gil"]
    if built:
        platform = TARGETS[job["target"]]["wheel"]
        if not (isinstance(wheel, str) and WHEEL_TAG.fullmatch(wheel)):
            out.append("wheel")
        elif not (wheel.endswith("-none-any") or re.fullmatch(rf"[^-]+-[^-]+-{platform}", wheel, re.ASCII)):
            out.append("wheel isn't for the target")
        if not _int(extensions, 10_000) or (extensions > 0 and isinstance(wheel, str) and wheel.endswith("-any")):
            out.append("extensions")
        if not _one_of(imported, IMPORTS):
            out.append("import")
    elif wheel is not None or extensions is not None or imported is not None:
        out.append("wheel, extensions and import are only for a build that worked")
    # The GIL is checked exactly when a free-threaded build with an extension module imported.
    gil_expected = built and imported == "ok" and planned.endswith("t") and _int(extensions, 10_000) and extensions > 0
    if gil_expected != _one_of(gil, GIL) or (gil is not None and not _one_of(gil, GIL)):
        out.append("gil")
    module = result["gil_module"]
    if module is not None and not (
        isinstance(module, str) and len(module) <= 100 and gil == "re-enabled" and GIL_MODULE.fullmatch(module)
    ):
        out.append("gil_module")
    reason = result["reason"]
    if result["outcome"] == "failed":
        if not _one_of(reason, REASON_NAMES):
            out.append("reason")
    elif reason is not None:
        out.append("reason")
    text = result["excerpt"]
    if not (isinstance(text, str) and len(text) <= MAX_EXCERPT and all(32 <= ord(c) < 127 for c in text)):
        out.append("excerpt")
    tools = result["toolchains"]
    if not (
        isinstance(tools, list)
        and all(isinstance(t, str) and t in TOOLCHAINS for t in tools)
        and len(set(tools)) == len(tools)
    ):
        out.append("toolchains")
    return out


def merge(
    builds: dict | None, results: dict[str, object], jobs: list[dict], now: str, log: str | None = None
) -> tuple[dict, list[str]]:
    """data/builds.json with the valid results in, each dated by this job: one entry per (package, target), the
    newest release's."""
    notes = []
    current = {(canonical(r["name"]), r["target"]): r for r in (builds or {}).get("results", [])}
    for job in jobs:
        result = results.get(job["id"])
        label = f"{job['name']} {job['version']} {job['target']}"
        if result is None:
            notes.append(f"{label}: no result (the build wasn't recorded, or the job failed or was cancelled)")
            continue
        try:
            bad = problems(result, job)
        except Exception as e:  # noqa: BLE001 - whatever an untrusted file does, it's rejected, never fatal
            bad = [f"check failed ({type(e).__name__})"]
        if bad:
            notes.append(f"{label}: rejected ({'; '.join(bad)})")
            continue
        current[(canonical(job["name"]), job["target"])] = {**result, "date": now, "log": log}
    ordered = sorted(current.values(), key=lambda r: (canonical(r["name"]), r["target"]))
    targets = {label: {k: v for k, v in spec.items() if k in ("runner", "python")} for label, spec in TARGETS.items()}
    return {"schema": SCHEMA, "updated": now, "targets": targets, "results": ordered}, notes


def read_result(path: Path) -> object:
    if not path.is_file() or path.is_symlink() or path.stat().st_size > MAX_RESULT_BYTES:
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001 - deep nesting (RecursionError), bad UTF-8, bad JSON: all just unreadable
        return "unreadable"


def cmd_merge(args: argparse.Namespace) -> int:
    builds_path, results_dir = Path(args.builds), Path(args.results)
    builds = json.loads(builds_path.read_text(encoding="utf-8")) if builds_path.exists() else None
    jobs = json.loads(Path(args.plan).read_text(encoding="utf-8"))
    results = {}
    for job in jobs:
        result = read_result(results_dir / f"result-{job['id']}" / "result.json")
        if result is not None:
            results[job["id"]] = result
    # The run's own URL, from the merge job's environment (never from a result file).
    server, repo, run = (os.environ.get(k, "") for k in ("GITHUB_SERVER_URL", "GITHUB_REPOSITORY", "GITHUB_RUN_ID"))
    log = f"{server}/{repo}/actions/runs/{run}" if server == "https://github.com" and repo and run.isdigit() else None
    merged, notes = merge(builds, results, jobs, today(), log)
    for note in notes:
        print(f"::warning::build_lab: {note!r}")
    text = json.dumps(merged, indent=1) + "\n"
    if len(text.encode()) > MAX_BUILDS_BYTES:
        print("::error::build_lab: builds.json would be too big; not written")
        return 1
    builds_path.write_text(text, encoding="utf-8")
    print(f"build_lab: {len(jobs) - len(notes)} of {len(jobs)} results merged")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("plan")
    p.add_argument("coverage")
    p.add_argument("builds")
    p.add_argument("--max", type=int, default=DEFAULT_MAX)
    p.add_argument("--top", type=int, help="only packages ranked this high")
    p.add_argument("--refresh", action="store_true", help="rebuild recent results too")
    p.add_argument("--only", help="just these packages (comma-separated), recent results included")
    p.add_argument("--github-output")
    p.set_defaults(func=cmd_plan)
    r = sub.add_parser("run")
    for flag in ("--name", "--version", "--target", "--runner", "--out"):
        r.add_argument(flag, required=True)
    r.set_defaults(func=cmd_run)
    m = sub.add_parser("merge")
    m.add_argument("builds")
    m.add_argument("results")
    m.add_argument("plan")
    m.set_defaults(func=cmd_merge)
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
