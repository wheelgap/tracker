"""The repo checker's promise: site/check/driver.py says what `uvx wheelgap` says on the same file.

Every file format the page reads goes through both the CLI (`python -m wheelgap ... --json`, in a subprocess) and
driver.check(), on the same Pythons and platforms, and the two answers are compared cell by cell. driver.check()
works its counts, rows and skipped list out for itself on top of wheelgap's functions, so a change on either side
(the pin moves with every wheelgap release) could make the page and the CLI disagree; this is the test that fails.

No network: the lockfiles that list their files are read with --offline. The others (requirements files,
pyproject.toml, Pipfile.lock, pixi.lock) are looked up, the CLI on a server this test runs on 127.0.0.1
(WHEELGAP_INDEX_URL) and the driver from the same canned PyPI JSON, handed over the way worker.mjs does.

CI runs this file with `--with wheelgap==<the version worker.mjs pins>`; without wheelgap it's skipped.
"""

import json
import os
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote

import pytest

pytest.importorskip("wheelgap.suggest_targets")
from wheelgap import origin
from pypi_json import recorded_project, trim_json

SITE = Path(__file__).resolve().parent.parent / "site" / "check"
sys.path.insert(0, str(SITE))
import driver

# Both inside every fixture's requires-python: outside it the page and the CLI differ on purpose (see check()'s
# docstring), which test_check_driver.py covers.
PYTHONS = ["3.13", "3.14"]
PLATFORMS = ["linux-x86_64", "linux-aarch64", "musl-aarch64", "win-arm64"]
GAPS = ("source", "missing")
COUNTED = ("wheel", "source", "missing", "unknown")

# The same packages in every format: a wheel everywhere (purewheel), only an sdist for some targets (fastlib:
# source), no file at all for some (nosdist: missing), limited by a marker (winonly: n/a off Windows), one no target
# here installs (maconly: not a package of the result at all), and the project itself or a local folder (skipped).
FILES = {
    ("purewheel", "3.1"): ["purewheel-3.1-py3-none-any.whl"],
    ("fastlib", "2.0.0"): [
        "fastlib-2.0.0-cp313-cp313-manylinux_2_17_x86_64.manylinux2014_x86_64.whl",
        "fastlib-2.0.0-cp314-cp314-manylinux_2_17_x86_64.manylinux2014_x86_64.whl",
        "fastlib-2.0.0-cp313-cp313-musllinux_1_2_aarch64.whl",
        "fastlib-2.0.0.tar.gz",
    ],
    ("nosdist", "1.0"): [
        "nosdist-1.0-cp313-cp313-manylinux_2_28_x86_64.whl",
        "nosdist-1.0-cp314-cp314-manylinux_2_28_x86_64.whl",
        "nosdist-1.0-cp313-cp313-manylinux_2_28_aarch64.whl",
    ],
    ("winonly", "1.0"): ["winonly-1.0-cp313-cp313-win_arm64.whl", "winonly-1.0.tar.gz"],
    ("maconly", "1.0"): ["maconly-1.0.tar.gz"],
}
MARKERS = {"winonly": "sys_platform == 'win32'", "maconly": "sys_platform == 'darwin'"}
URL = "https://files.pythonhosted.org/packages/aa"


def _uv_lock() -> str:
    out = [
        'version = 1\nrevision = 3\nrequires-python = ">=3.13"\n',
        '[[package]]\nname = "demo"\nversion = "0.1.0"\nsource = { editable = "." }\ndependencies = [',
        *(
            f'    {{ name = "{name}"' + (f', marker = "{MARKERS[name]}"' if name in MARKERS else "") + " },"
            for name, _ in FILES
        ),
        "]\n",
    ]
    for (name, version), files in FILES.items():
        out.append(f'[[package]]\nname = "{name}"\nversion = "{version}"')
        out.append('source = { registry = "https://pypi.org/simple" }')
        for f in files:
            if f.endswith(".tar.gz"):
                out.append(f'sdist = {{ url = "{URL}/{f}", hash = "sha256:00", size = 1 }}')
        out.append("wheels = [")
        out += [f'    {{ url = "{URL}/{f}", hash = "sha256:00", size = 1 }},' for f in files if f.endswith(".whl")]
        out.append("]\n")
    return "\n".join(out)


def _poetry_lock() -> str:
    out = []
    for (name, version), files in FILES.items():
        out.append(f'[[package]]\nname = "{name}"\nversion = "{version}"\ndescription = ""\noptional = false')
        out.append('python-versions = ">=3.13"\ngroups = ["main"]')
        if name in MARKERS:
            out.append(f'markers = "{MARKERS[name]}"')
        out.append("files = [")
        out += [f'    {{file = "{f}", hash = "sha256:00"}},' for f in files]
        out.append("]\n")
    out.append('[[package]]\nname = "localpkg"\nversion = "0.1"\ndescription = ""\noptional = false')
    out.append('python-versions = "*"\ngroups = ["main"]\nfiles = []\n')
    out.append('[package.source]\ntype = "directory"\nurl = "../localpkg"\n')
    out.append('[metadata]\nlock-version = "2.1"\npython-versions = ">=3.13"\ncontent-hash = "00"\n')
    return "\n".join(out)


def _pdm_lock() -> str:
    out = ['[metadata]\ngroups = ["default"]\nstrategy = ["inherit_metadata"]\n']
    for (name, version), files in FILES.items():
        out.append(f'[[package]]\nname = "{name}"\nversion = "{version}"\nrequires_python = ">=3.13"')
        if name in MARKERS:
            out.append(f'marker = "{MARKERS[name]}"')
        out.append("files = [")
        out += [f'    {{file = "{f}", hash = "sha256:00"}},' for f in files]
        out.append("]\n")
    out.append('[[package]]\nname = "localpkg"\nversion = "0.1"\nrequires_python = ">=3.13"')
    out.append('path = "../localpkg"\nfiles = []\n')
    return "\n".join(out)


def _pylock() -> str:
    out = ['lock-version = "1.0"\ncreated-by = "pip"\nrequires-python = ">=3.13"\n']
    for (name, version), files in FILES.items():
        out.append(f'[[packages]]\nname = "{name}"\nversion = "{version}"')
        if name in MARKERS:
            out.append(f'marker = "{MARKERS[name]}"')
        for f in files:
            if f.endswith(".tar.gz"):
                out.append(f'sdist = {{ name = "{f}", url = "{URL}/{f}", hashes = {{ sha256 = "00" }} }}')
        out.append("wheels = [")
        out += [
            f'    {{ name = "{f}", url = "{URL}/{f}", hashes = {{ sha256 = "00" }} }},'
            for f in files
            if f.endswith(".whl")
        ]
        out.append("]\n")
    out.append('[[packages]]\nname = "localpkg"\nversion = "0.1"\ndirectory = { path = "../localpkg" }\n')
    return "\n".join(out)


LOCKS = {"uv.lock": _uv_lock, "poetry.lock": _poetry_lock, "pdm.lock": _pdm_lock, "pylock.toml": _pylock}


def run_cli(lock: Path, *flags: str, env: dict[str, str] | None = None) -> dict:
    """What `uvx wheelgap <lock> -p ... -t ...` prints with --json: the command the page's "Run the same check
    locally" box hands out, on the wheelgap this test runs with."""
    cmd = [sys.executable, "-m", "wheelgap", lock.name, *flags, "--json"]
    cmd += ["-p", ",".join(PYTHONS), "-t", ",".join(PLATFORMS)]
    # A cache folder of the test's own: no lookup is read from, or saved to, the developer's. XDG_CACHE_HOME only
    # moves the cache on Linux, so the looked-up tests also pass --no-cache.
    # And no proxy between the CLI and the index this test serves on 127.0.0.1.
    run_env = {**os.environ, "XDG_CACHE_HOME": str(lock.parent / ".cache"), "NO_COLOR": "1"} | (env or {})
    run_env |= {"NO_PROXY": "127.0.0.1", "no_proxy": "127.0.0.1"}
    for name in list(run_env):  # a developer's shell may name another index; the browser has no such settings
        if origin.index_env({name: run_env[name]}) is not None:
            del run_env[name]
    # Exit code 1 is "gaps found", which every fixture has; anything else is the CLI failing to read the file.
    done = subprocess.run(cmd, cwd=lock.parent, env=run_env, capture_output=True, text=True, timeout=120)
    assert done.returncode in (0, 1), done.stderr
    return json.loads(done.stdout)


def configs(root: Path) -> str:
    """plan()'s `configs` for a repo whose files are root's, as the worker hands it to needs() and check()."""
    paths = [p.relative_to(root).as_posix() for p in root.rglob("*") if p.is_file()]
    return json.dumps(json.loads(driver.plan(json.dumps(paths)))["configs"])


def run_driver(lock: Path, lookups: dict | None = None) -> dict:
    options = json.dumps({"pythons": PYTHONS, "platforms": PLATFORMS})
    lookups_json = None if lookups is None else json.dumps(lookups)
    result = json.loads(driver.check(str(lock.parent), lock.name, options, lookups_json, configs(lock.parent)))
    assert "error" not in result, result
    return result


def assert_same_answer(cli: dict, page: dict) -> None:
    labels = [t["label"] for t in cli["targets"]]
    assert page["targets"] == cli["targets"]  # the keys of every result below
    assert len(labels) == len(PYTHONS) * len(PLATFORMS)
    assert page["outside_lock"] == []
    assert page["wheelgap"] == cli["version"]

    # Every status, on every target. The page keeps a row only for a package with a gap or an unknown cell, so
    # a package with no row must have neither in the CLI's answer.
    rows = {(r["name"], r["version"]): r for r in page["rows"]}
    assert len(rows) == len(page["rows"])
    expected_rows = {}
    for p in cli["packages"]:
        assert set(p["results"]) == set(labels)
        if any(s in (*GAPS, "unknown") for s in p["results"].values()):
            expected_rows[(p["name"], p["version"])] = p["results"]
    assert {key: row["statuses"] for key, row in rows.items()} == expected_rows
    for key, row in rows.items():
        cells = expected_rows[key]
        assert row["gaps"] == [t for t in labels if cells[t] in GAPS], key
        assert row["unknown"] == [t for t in labels if cells[t] == "unknown"], key
        assert row["keep"] == [t for t in labels if cells[t] == "wheel"], key
    with_gaps = {key for key, cells in expected_rows.items() if any(s in GAPS for s in cells.values())}
    assert {key for key, row in rows.items() if row["gaps"]} == with_gaps

    # The counts under each target, from the CLI's packages and from its own summary.
    counts = {t: {s: sum(p["results"][t] == s for p in cli["packages"]) for s in COUNTED} for t in labels}
    assert page["per_target"] == counts
    assert {t: {s: n for s, n in c.items() if s != "wheel"} for t, c in page["per_target"].items()} == cli["summary"][
        "per_target"
    ]

    assert page["packages"] == len(cli["packages"])
    assert page["skipped"] == sorted({s["name"] for s in cli["skipped"]})


@pytest.mark.parametrize("name", LOCKS)
def test_a_lockfile_gets_the_clis_answer(tmp_path, name):
    lock = tmp_path / name
    lock.write_text(LOCKS[name](), encoding="utf-8")
    cli = run_cli(lock, "--offline")
    page = run_driver(lock)
    assert_same_answer(cli, page)
    # The fixture has what it's meant to: every status on some target, a skipped package, and rows left out.
    assert {s for p in cli["packages"] for s in p["results"].values()} == {"wheel", "source", "missing", "n/a"}
    assert page["skipped"] == ["localpkg" if name != "uv.lock" else "demo"]
    assert sorted(r["name"] for r in page["rows"]) == ["fastlib", "nosdist", "winonly"] and page["packages"] == 4
    assert page["looked_up"] is False


# ---- the files checked by looking each package up on PyPI ----


def _entry(filename: str, requires_python: str | None = None) -> dict:
    kind = "sdist" if filename.endswith(".tar.gz") else "bdist_wheel"
    return {"filename": filename, "yanked": False, "requires_python": requires_python, "packagetype": kind}


# pypi.org/pypi/<name>/<version>/json for each pin, and pypi.org/pypi/<name>/json for the range: `ranged` 2.0
# supports only Python 3.14, so `ranged>=1,<3` resolves to 1.0 on 3.13 and 2.0 on 3.14. `gone` isn't on PyPI (a 404).
PINNED = {key: {"info": {}, "urls": [_entry(f) for f in files]} for key, files in FILES.items()}
PROJECTS = {
    "ranged": {
        "info": {"version": "2.0"},
        "urls": [],
        "releases": {
            "1.0": [_entry("ranged-1.0-cp313-cp313-manylinux_2_17_x86_64.whl"), _entry("ranged-1.0.tar.gz")],
            "2.0": [
                _entry("ranged-2.0-cp314-cp314-manylinux_2_17_x86_64.whl", ">=3.14"),
                _entry("ranged-2.0-cp314-cp314-manylinux_2_17_aarch64.whl", ">=3.14"),
            ],
            "3.0": [_entry("ranged-3.0-py3-none-any.whl")],
        },
    }
}


def answer(name: str, version: str | None) -> dict | None:
    """PyPI's JSON for a lookup, or None for a 404."""
    return PINNED.get((name, version)) if version else PROJECTS.get(name)


class _PyPI(BaseHTTPRequestHandler):
    def do_GET(self):
        parts = [unquote(p) for p in self.path.strip("/").split("/")]
        doc = None
        if parts[0] == "pypi" and parts[-1] == "json" and len(parts) in (3, 4):
            doc = answer(parts[1], parts[2] if len(parts) == 4 else None)
        body = json.dumps(doc if doc is not None else {"message": "Not Found"}).encode()
        self.send_response(200 if doc is not None else 404)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


@pytest.fixture(scope="module")
def index():
    """A stand-in for pypi.org's JSON API on this machine, for the CLI's lookups."""
    server = ThreadingHTTPServer(("127.0.0.1", 0), _PyPI)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_address[1]}/pypi"
    server.shutdown()
    server.server_close()


def fetch_lookups(lock: Path, trim: bool = True) -> dict:
    """The answers worker.mjs hands check(): each lookup needs() names, keyed as the worker keys it, null for a 404,
    and trimmed by the worker's own trimProject as the worker trims them. So every looked-up case here compares the
    CLI (which reads PyPI's whole JSON) with the driver on trimmed JSON: when a new wheelgap pin reads a field the
    trimmers drop, a case whose canned JSON has that field fails. `trim=False` gives the untrimmed answers."""
    needed = json.loads(driver.needs(str(lock.parent), lock.name, configs(lock.parent)))
    assert "error" not in needed, needed
    docs = {
        (f"{l['name']}=={l['version']}" if l["version"] else l["name"]): answer(l["name"], l["version"])
        for l in needed["lookups"]
    }
    return trim_json(docs) if trim else docs


REQUIREMENTS = """\
-r base.txt
fastlib==2.0.0
nosdist==1.0
winonly==1.0 ; sys_platform == "win32"
maconly==1.0 ; sys_platform == "darwin"
ranged>=1,<3
gone==9.9
"""

PYPROJECT = """\
[project]
name = "demo"
version = "0.1.0"
requires-python = ">=3.13"
dependencies = [
    "purewheel==3.1",
    "fastlib==2.0.0",
    "nosdist==1.0",
    'winonly==1.0 ; sys_platform == "win32"',
    'maconly==1.0 ; sys_platform == "darwin"',
    "ranged>=1,<3",
    "gone==9.9",
    "demo",
]
"""

PIPFILE_LOCK = {
    "_meta": {
        "pipfile-spec": 6,
        # PyPI alone. With a second source listed, the page looks nothing up (any package could be that index's;
        # see driver._private) where the CLI asks PyPI: they differ on purpose, and test_check_driver.py covers it.
        "sources": [{"name": "pypi", "url": "https://pypi.org/simple", "verify_ssl": True}],
    },
    "default": {
        "purewheel": {"version": "==3.1", "hashes": ["sha256:00"], "index": "pypi"},
        "fastlib": {"version": "==2.0.0", "hashes": ["sha256:00"], "index": "pypi"},
        "nosdist": {"version": "==1.0", "hashes": ["sha256:00"], "index": "pypi"},
        "winonly": {"version": "==1.0", "markers": "sys_platform == 'win32'", "index": "pypi"},
        "maconly": {"version": "==1.0", "markers": "sys_platform == 'darwin'", "index": "pypi"},
        "gone": {"version": "==9.9", "index": "pypi"},
        "corp-utils": {"version": "==2.0", "hashes": ["sha256:00"], "index": "corp"},
        "gitdep": {"git": "https://example.invalid/gitdep.git", "ref": "main"},
    },
    "develop": {"localpkg": {"path": "./localpkg", "editable": True}},
}

_CONDA = "https://conda.anaconda.org/conda-forge"
_PIXI_PLATFORMS = {
    "linux-64": ["fastlib-2.0.0-cp313-cp313-manylinux_2_17_x86_64.manylinux2014_x86_64.whl"],
    "linux-aarch64": ["fastlib-2.0.0.tar.gz"],
    "win-arm64": ["fastlib-2.0.0.tar.gz", "winonly-1.0-cp313-cp313-win_arm64.whl"],
}
_PIXI_EVERYWHERE = ["purewheel-3.1-py3-none-any.whl", "nosdist-1.0-cp313-cp313-manylinux_2_28_x86_64.whl", "gone-9.9.tar.gz"]


def _pixi_lock() -> str:
    out = ["version: 6", "environments:", "  default:", "    channels:", f"    - url: {_CONDA}/"]
    out += ["    indexes:", "    - https://pypi.org/simple", "    packages:"]
    for platform, files in _PIXI_PLATFORMS.items():
        out.append(f"      {platform}:")
        out.append(f"      - conda: {_CONDA}/{platform}/python-3.13.1-h0000000_0_cp313.conda")
        out += [f"      - pypi: {URL}/{f}" for f in (*files, *_PIXI_EVERYWHERE)]
    out.append("packages:")
    for platform in _PIXI_PLATFORMS:
        out.append(f"- conda: {_CONDA}/{platform}/python-3.13.1-h0000000_0_cp313.conda")
        out += [f"  sha256: {'0' * 64}", f"  md5: {'0' * 32}", "  license: Python-2.0", "  size: 1", "  timestamp: 1"]
    for f in dict.fromkeys(f for files in (*_PIXI_PLATFORMS.values(), _PIXI_EVERYWHERE) for f in files):
        name, version = f.removesuffix(".tar.gz").split("-")[:2]
        out += [f"- pypi: {URL}/{f}", f"  name: {name}", f"  version: {version}", f"  sha256: {'0' * 64}"]
    return "\n".join(out) + "\n"


def _write_requirements(folder: Path, filename: str = "requirements.txt") -> None:
    (folder / filename).write_text(REQUIREMENTS, encoding="utf-8")
    (folder / "base.txt").write_text("purewheel==3.1\n", encoding="utf-8")


LOOKED_UP = {
    "requirements.txt": _write_requirements,
    "requirements-dev.txt": lambda folder: _write_requirements(folder, "requirements-dev.txt"),
    "pyproject.toml": lambda folder: (folder / "pyproject.toml").write_text(PYPROJECT, encoding="utf-8"),
    "Pipfile.lock": lambda folder: (folder / "Pipfile.lock").write_text(json.dumps(PIPFILE_LOCK), encoding="utf-8"),
    "pixi.lock": lambda folder: (folder / "pixi.lock").write_text(_pixi_lock(), encoding="utf-8"),
}
# What each file leaves unchecked, and why (the CLI's reasons are compared by name only).
LOOKED_UP_SKIPPED = {
    "requirements.txt": [],
    "requirements-dev.txt": [],
    "pyproject.toml": [],
    "Pipfile.lock": ["corp-utils", "gitdep", "localpkg"],
    "pixi.lock": ["python"],
}


@pytest.mark.parametrize("name", LOOKED_UP)
def test_a_looked_up_file_gets_the_clis_answer(tmp_path, index, name):
    LOOKED_UP[name](tmp_path)
    lock = tmp_path / name
    cli = run_cli(lock, "--no-cache", env={"WHEELGAP_INDEX_URL": index})
    page = run_driver(lock, fetch_lookups(lock))
    assert_same_answer(cli, page)
    # The fixture has what it's meant to: every status, the 404 as unknown (never a gap), and the skipped packages.
    statuses = {s for p in cli["packages"] for s in p["results"].values()}
    assert statuses == {"wheel", "source", "missing", "n/a", "unknown"}
    gone = next(r for r in page["rows"] if r["name"] == "gone")
    assert gone["gaps"] == [] and len(gone["unknown"]) == len(PYTHONS) * len(PLATFORMS)
    assert page["skipped"] == LOOKED_UP_SKIPPED[name] and page["looked_up"] is True
    if "ranged" in lock.read_text(encoding="utf-8"):  # one entry per release the range resolves to
        assert sorted(r["version"] for r in page["rows"] if r["name"] == "ranged") == ["1.0", "2.0"]


@pytest.mark.parametrize("requirement", ["numpy==2.3.4", "numpy>=2.3,<2.4", "numpy"])
def test_trimmed_recorded_project_still_matches_the_cli(tmp_path, index, monkeypatch, requirement):
    project = recorded_project()
    monkeypatch.setitem(PROJECTS, "numpy", project)
    monkeypatch.setitem(PINNED, ("numpy", "2.3.4"), {"info": project["info"], "urls": project["releases"]["2.3.4"]})
    lock = tmp_path / "requirements.txt"
    lock.write_text(requirement + "\n", encoding="utf-8")
    docs = fetch_lookups(lock, trim=False)
    full, trimmed = run_driver(lock, docs), run_driver(lock, trim_json(docs))
    assert full == trimmed
    cli = run_cli(lock, "--no-cache", env={"WHEELGAP_INDEX_URL": index})
    assert_same_answer(cli, trimmed)
