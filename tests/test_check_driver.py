"""site/check/driver.py, the repo checker's Python side, under CPython with the wheelgap release worker.mjs pins.

CI runs this file with `--with wheelgap==<that version>`; without wheelgap (the plain test run) it's skipped.
"""

import json
import re
import sys
from pathlib import Path

import pytest

pytest.importorskip("wheelgap.suggest_targets")
from wheelgap import origin, sources
from pypi_json import recorded_project, trim_json

SITE = Path(__file__).resolve().parent.parent / "site" / "check"
sys.path.insert(0, str(SITE))
import driver


def configs(root: Path) -> str:
    """plan()'s `configs` for a repo whose files are root's, as the worker hands it to needs() and check(): every
    installer settings file it has. The worker fetches the ones beside the checked file or above it first."""
    paths = [p.relative_to(root).as_posix() for p in root.rglob("*") if p.is_file()]
    return json.dumps(json.loads(driver.plan(json.dumps(paths)))["configs"])


@pytest.fixture(autouse=True)
def no_installer_index_settings(monkeypatch):
    """The browser has no PIP_* or UV_* index settings. A developer's shell may, and with one set wheelgap counts no
    looked-up package as PyPI's (origin.index_env), so no row would be marked for a fix lookup."""
    while (name := origin.index_env()) is not None:
        monkeypatch.delenv(name)


LOCK = """\
version = 1
revision = 3
requires-python = ">=3.13"

[[package]]
name = "demo"
version = "0.1.0"
source = { editable = "." }
dependencies = [
    { name = "fastlib" },
    { name = "purewheel" },
    { name = "winonly", marker = "sys_platform == 'win32'" },
]

[[package]]
name = "fastlib"
version = "2.0.0"
source = { registry = "https://pypi.org/simple" }
sdist = { url = "https://files.pythonhosted.org/packages/aa/fastlib-2.0.0.tar.gz", hash = "sha256:00", size = 1 }
wheels = [
    { url = "https://files.pythonhosted.org/packages/aa/fastlib-2.0.0-cp313-cp313-manylinux_2_17_x86_64.manylinux2014_x86_64.whl", hash = "sha256:00", size = 1 },
    { url = "https://files.pythonhosted.org/packages/aa/fastlib-2.0.0-cp313-cp313-musllinux_1_2_aarch64.whl", hash = "sha256:00", size = 1 },
]

[[package]]
name = "purewheel"
version = "3.1"
source = { registry = "https://pypi.org/simple" }
wheels = [
    { url = "https://files.pythonhosted.org/packages/aa/purewheel-3.1-py3-none-any.whl", hash = "sha256:00", size = 1 },
]

[[package]]
name = "winonly"
version = "1.0"
source = { registry = "https://pypi.org/simple" }
wheels = [
    { url = "https://files.pythonhosted.org/packages/aa/winonly-1.0-cp313-cp313-win_amd64.whl", hash = "sha256:00", size = 1 },
]
"""

WORKFLOW = """\
on: push
jobs:
  test:
    runs-on: ubuntu-24.04-arm
    steps:
      - uses: actions/setup-python@v6
        with:
          python-version: "3.13"
"""


@pytest.fixture
def repo(tmp_path):
    (tmp_path / "uv.lock").write_text(LOCK, encoding="utf-8")
    (tmp_path / ".github" / "workflows").mkdir(parents=True)
    (tmp_path / ".github" / "workflows" / "ci.yml").write_text(WORKFLOW, encoding="utf-8")
    (tmp_path / "deploy").mkdir()
    (tmp_path / "deploy" / "Dockerfile").write_text("FROM python:3.13-alpine\n", encoding="utf-8")
    return tmp_path


def test_worker_pins_the_tested_wheelgap():
    import wheelgap

    pin = re.search(r'WHEELGAP = "wheelgap==([^"]+)"', (SITE / "worker.mjs").read_text(encoding="utf-8"))
    assert pin and pin.group(1) == wheelgap.__version__


def test_plan_picks_files_like_suggest_targets():
    paths = [
        "README.md",
        "uv.lock",
        "pixi.lock",
        "pyproject.toml",
        "backend/uv.lock",
        "backend/requirements.txt",
        "backend/pixi.lock",
        "backend/pyproject.toml",
        "node_modules/x/uv.lock",
        ".venv/lib/poetry.lock",
        ".github/workflows/ci.yml",
        ".github/workflows/notes.md",
        ".github/workflows/sub/inner.yml",
        "Dockerfile",
        "deploy/Containerfile",
        "deploy/Dockerfile.j2",
        ".devcontainer/Dockerfile",
        ".python-version",
    ]
    plan = json.loads(driver.plan(json.dumps(paths)))
    assert plan["lockfiles"] == [
        {"path": "uv.lock", "offline": True},
        {"path": "pixi.lock", "offline": False},
        {"path": "pyproject.toml", "offline": False},
        {"path": "backend/uv.lock", "offline": True},
        {"path": "backend/requirements.txt", "offline": False},
        {"path": "backend/pixi.lock", "offline": False},
        {"path": "backend/pyproject.toml", "offline": False},
    ]
    assert plan["workflows"] == [".github/workflows/ci.yml"]
    assert plan["dockerfiles"] == ["Dockerfile", "deploy/Containerfile"]
    assert plan["templates"] == ["deploy/Dockerfile.j2"]
    assert plan["python_version"] == ".python-version"
    # Every installer settings file, wherever it is: the worker fetches the ones beside the checked file or above it.
    assert plan["configs"] == ["backend/pyproject.toml", "pyproject.toml"]


def test_plan_lists_other_requirements_filenames_after_every_exactly_named_file():
    paths = [
        "pyproject.toml",
        "constraints.txt",
        "constraints-dev.txt",
        "requirements/constraints.txt",
        "requirements/README.txt",
        "requirements/docs.txt",
        "requirements/test.txt",
        "requirements/default.txt",
        "requirements/base.txt",
        "requirements/Notes.txt",
        "Requirements-dev.TXT",
        "tests/requirements.txt",
        "tests/requirements/default.txt",
        "requirements-dev.txt",
        "prod-requirements.txt",
        "extra_requirements.txt",
    ]
    plan = json.loads(driver.plan(json.dumps(paths)))
    # No order by how runtime-like a name sounds: autoPick() in check-logic.mjs asks which one when there are several,
    # as `wheelgap DIR` lists them and never picks one.
    assert plan["lockfiles"] == [
        {"path": "pyproject.toml", "offline": False},
        *(
            {"path": p, "offline": False, "other": True}
            for p in [
                "extra_requirements.txt",
                "prod-requirements.txt",
                "requirements-dev.txt",
                "requirements/base.txt",
                "requirements/default.txt",
                "requirements/docs.txt",
                "requirements/test.txt",
            ]
        ),
    ]

    # A root requirements.txt still comes first ahead of other root requirements files.
    with_root = json.loads(driver.plan(json.dumps(["requirements-dev.txt", "requirements.txt", "requirements/default.txt"])))
    assert [entry["path"] for entry in with_root["lockfiles"]] == [
        "requirements.txt",
        "requirements-dev.txt",
        "requirements/default.txt",
    ]
    assert "other" not in with_root["lockfiles"][0]
    # And an exactly-named file deeper in the repo still outranks them, so the page's pick is what it was before.
    nested = json.loads(driver.plan(json.dumps(["requirements-dev.txt", "backend/uv.lock", "backend/pyproject.toml"])))
    assert [entry["path"] for entry in nested["lockfiles"]] == ["backend/uv.lock", "backend/pyproject.toml", "requirements-dev.txt"]


def test_needs_and_check_read_nested_requirements_file_with_include(tmp_path):
    req_dir = tmp_path / "requirements"
    req_dir.mkdir()
    (req_dir / "base.txt").write_text("-r common.txt\npurewheel==3.1\n", encoding="utf-8")
    first = json.loads(driver.needs(str(tmp_path), "requirements/base.txt", configs(tmp_path)))
    assert first["kind"] == "include" and first["path"] == "requirements/common.txt"

    (req_dir / "common.txt").write_text("fastlib==2.0.0\n", encoding="utf-8")
    needed = json.loads(driver.needs(str(tmp_path), "requirements/base.txt", configs(tmp_path)))
    assert {(l["name"], l["version"]) for l in needed["lookups"]} == {("fastlib", "2.0.0"), ("purewheel", "3.1")}

    lookups = {
        "purewheel==3.1": {"info": {"version": "3.1"}, "urls": [{"filename": "purewheel-3.1-py3-none-any.whl"}]},
        "fastlib==2.0.0": {"info": {"version": "2.0.0"}, "urls": [{"filename": "fastlib-2.0.0.tar.gz"}]},
    }
    res = json.loads(driver.check(str(tmp_path), "requirements/base.txt", options(), json.dumps(lookups), configs(tmp_path)))
    assert res["looked_up"] is True
    assert res["packages"] == 2
    assert [row["name"] for row in res["rows"]] == ["fastlib"]


def test_detect_reads_ci_and_dockerfiles(repo):
    d = json.loads(driver.detect(str(repo)))
    assert d["platforms_from"] == "ci" and d["python_from"] == "ci"
    assert set(d["platforms"]) == {"linux-aarch64", "musl-x86_64"}
    assert d["pythons"] == ["3.13"]
    assert {(f["kind"], f["value"], f["path"]) for f in d["found"]} >= {
        ("target", "linux-aarch64", ".github/workflows/ci.yml"),
        ("target", "musl-x86_64", "deploy/Dockerfile"),
    }


def test_detect_falls_back_to_python_version_then_defaults(tmp_path):
    d = json.loads(driver.detect(str(tmp_path)))
    assert d["platforms_from"] == "default" and d["python_from"] == "default"
    assert d["platforms"] == ["linux-x86_64", "linux-aarch64", "macos-arm64", "win-amd64"]
    (tmp_path / ".python-version").write_text("# pinned\n3.12.4\n", encoding="utf-8")
    d = json.loads(driver.detect(str(tmp_path)))
    assert d["pythons"] == ["3.12"] and d["python_from"] == ".python-version"


def options(**kw):
    return json.dumps({"pythons": ["3.13"], "platforms": ["linux-x86_64", "linux-aarch64", "musl-aarch64"]} | kw)


def test_check_lists_gap_rows_and_counts(repo):
    r = json.loads(driver.check(str(repo), "uv.lock", options()))
    assert r["lockfile"] == "uv.lock" and r["outside_lock"] == [] and r["skipped"] == ["demo"]
    assert r["looked_up"] is False and r["private"] == []  # read from the lock's own files; demo is the project
    assert [row["name"] for row in r["rows"]] == ["fastlib"]
    fast = r["rows"][0]
    assert fast["gaps"] == ["3.13 linux-aarch64"]
    assert fast["keep"] == ["3.13 linux-x86_64", "3.13 musl-aarch64"]
    assert fast["unknown"] == [] and fast["lookup"] is True
    assert fast["statuses"]["3.13 linux-aarch64"] == "source"
    # winonly isn't installed off Windows: n/a, not a gap, and not counted.
    assert r["per_target"]["3.13 linux-x86_64"] == {"wheel": 2, "source": 0, "missing": 0, "unknown": 0}
    assert r["per_target"]["3.13 linux-aarch64"] == {"wheel": 1, "source": 1, "missing": 0, "unknown": 0}


def test_check_doesnt_count_pythons_outside_the_lock(repo):
    (repo / "uv.lock").write_text(LOCK.replace('">=3.13"', '">=3.13, <3.14"'), encoding="utf-8")
    r = json.loads(driver.check(str(repo), "uv.lock", options(pythons=["3.13", "3.14"])))
    assert r["outside_lock"] == ["3.14"]
    assert all(g.startswith("3.13 ") for row in r["rows"] for g in row["gaps"])
    assert r["per_target"]["3.14 linux-x86_64"] == {"wheel": 0, "source": 0, "missing": 0, "unknown": 0}


PYLOCK = """\
lock-version = "1.0"
created-by = "pip"
requires-python = ">=3.13, <3.14"

[[packages]]
name = "fastlib"
version = "2.0.0"
wheels = [
    { name = "fastlib-2.0.0-cp313-cp313-manylinux_2_17_x86_64.manylinux2014_x86_64.whl", url = "https://files.pythonhosted.org/packages/aa/fastlib-2.0.0-cp313-cp313-manylinux_2_17_x86_64.manylinux2014_x86_64.whl", hashes = { sha256 = "00" } },
]
"""


@pytest.mark.parametrize(
    ("name", "text"),
    [
        ("uv-py312.lock", LOCK.replace('">=3.13"', '">=3.13, <3.14"')),
        ("envs/pylock.dev.toml", PYLOCK),
    ],
)
def test_a_lockfile_named_by_its_reader_is_checked_offline(repo, name, text):
    # A link can name a lockfile plan() doesn't list. wheelgap reads it by the reader its name picks (file_kind):
    # checked as a lookups file, the Python its lock leaves out (3.14) would show false gaps, not `outside_lock`.
    (repo / name).parent.mkdir(parents=True, exist_ok=True)
    (repo / name).write_text(text, encoding="utf-8")
    assert json.loads(driver.needs(str(repo), name, configs(repo))) == {"lookups": [], "total": 0}
    r = json.loads(driver.check(str(repo), name, options(pythons=["3.13", "3.14"]), "{}", configs(repo)))
    assert "error" not in r, r
    assert r["looked_up"] is False and r["outside_lock"] == ["3.14"]
    assert all(g.startswith("3.13 ") for row in r["rows"] for g in row["gaps"])
    assert r["per_target"]["3.14 linux-x86_64"] == {"wheel": 0, "source": 0, "missing": 0, "unknown": 0}


def test_offline_follows_the_reader_not_the_exact_name():
    offline = {
        "uv.lock": True, "a/pylock.toml": True, "poetry.lock": True, "pdm.lock": True, "pylock.dev.toml": True,
        "uv-py312.lock": True, "Pipfile.lock": False, "pixi.lock": False, "pyproject.toml": False,
        "requirements.txt": False, "requirements-dev.lock": False, "requirements.uv.lock": False, "dev.in": False,
        "script.py.lock": True, "requirements.py.lock": True, "README.md": False,
    }
    assert {p: driver._reads_offline(p) for p in offline} == offline


def test_check_refuses_lockfiles_that_need_an_index(repo):
    assert json.loads(driver.check(str(repo), "uv.lock", options(platforms=["amiga"])))["kind"] == "targets"
    (repo / "uv.lock").write_text("not toml [", encoding="utf-8")
    bad = json.loads(driver.check(str(repo), "uv.lock", options()))
    assert bad["kind"] == "source" and str(repo) not in bad["error"]


def test_check_reports_a_lockfile_that_needs_the_index_for_its_missing_files(repo):
    # A poetry.lock is offered as checkable, but wheelgap reads it offline only when every package lists its
    # files; one that doesn't would need a PyPI lookup the page can't make offline, so check() says needs-index
    # rather than surfacing wheelgap's API-oriented "set offline=False" error as if the lockfile were unreadable.
    (repo / "poetry.lock").write_text(
        '[[package]]\nname = "demo"\nversion = "0.1.0"\n\n[metadata]\nlock-version = "2.1"\n'
        'python-versions = ">=3.13"\ncontent-hash = "abc123"\n',
        encoding="utf-8",
    )
    r = json.loads(driver.check(str(repo), "poetry.lock", options()))
    assert r["kind"] == "needs-index"
    # A pixi.lock records only the file pixi picked per platform, so it's checked from PyPI lookups instead.
    (repo / "pixi.lock").write_text("version: 7" + chr(10) + "environments:" + chr(10) + "  default: {}" + chr(10) + "packages: []" + chr(10), encoding="utf-8")
    pixi = json.loads(driver.check(str(repo), "pixi.lock", options(), "{}"))
    assert "error" not in pixi and pixi["rows"] == []


def wheel(name, requires_python=None):
    return {"filename": name, "yanked": False, "requires_python": requires_python, "packagetype": "bdist_wheel"}


def test_fixes_names_the_nearest_release_with_a_wheel(repo):
    r = json.loads(driver.check(str(repo), "uv.lock", options()))
    releases = {
        "fastlib": {
            "2.0.0": [wheel("fastlib-2.0.0-cp313-cp313-manylinux_2_17_x86_64.whl")],
            "2.1.0": [wheel("fastlib-2.1.0-cp313-cp313-manylinux_2_17_x86_64.whl")],  # still no aarch64
            "2.2.0": [
                wheel("fastlib-2.2.0-cp313-cp313-manylinux_2_17_x86_64.whl"),
                wheel("fastlib-2.2.0-cp313-cp313-manylinux_2_17_aarch64.whl"),
                wheel("fastlib-2.2.0-cp313-cp313-musllinux_1_2_aarch64.whl"),
            ],
        }
    }
    fx = json.loads(driver.fixes(json.dumps(releases), json.dumps(r["rows"])))
    assert fx == {"fastlib==2.0.0": {"found": {"3.13 linux-aarch64": "2.2.0"}, "loses": []}}
    # A package that wasn't looked up gets no answer at all (the page says "not looked up", not "no fix").
    assert json.loads(driver.fixes("{}", json.dumps(r["rows"]))) == {}


def test_a_private_index_package_is_never_looked_up_on_pypi(repo):
    lock = (
        LOCK.replace('{ name = "purewheel" },', '{ name = "purewheel" },\n    { name = "corp-utils" },')
        + """
[[package]]
name = "corp-utils"
version = "1.0"
source = { registry = "https://pkgs.corp.example/simple" }
sdist = { url = "https://pkgs.corp.example/files/corp-utils-1.0.tar.gz", hash = "sha256:00", size = 1 }
"""
    )
    (repo / "uv.lock").write_text(lock, encoding="utf-8")
    r = json.loads(driver.check(str(repo), "uv.lock", options()))
    corp = next(row for row in r["rows"] if row["name"] == "corp-utils")
    assert corp["lookup"] is False
    squatted = {"corp-utils": {"9.9": [wheel("corp_utils-9.9-py3-none-any.whl")]}}
    assert "corp-utils==1.0" not in json.loads(driver.fixes(json.dumps(squatted), json.dumps(r["rows"])))


def test_fixes_reports_a_release_that_only_trades_one_gap_for_another(repo):
    r = json.loads(driver.check(str(repo), "uv.lock", options()))
    releases = {
        "fastlib": {
            "2.0.0": [wheel("fastlib-2.0.0-cp313-cp313-manylinux_2_17_x86_64.whl")],
            # has the aarch64 wheel the pin lacks, but drops x86_64 and musl: not a fix, yet not "nothing newer"
            "2.1.0": [wheel("fastlib-2.1.0-cp313-cp313-manylinux_2_17_aarch64.whl")],
        }
    }
    fx = json.loads(driver.fixes(json.dumps(releases), json.dumps(r["rows"])))
    assert fx == {"fastlib==2.0.0": {"found": {}, "loses": ["3.13 linux-aarch64"]}}


@pytest.mark.parametrize(
    "case", ["match", "version", "target", "private", "malformed", "requires_python"]
)
def test_gap_wheels_reuse_cli_checks(repo, case):
    # Imported, not importorskip'd: the pinned release has --fix, and a pin without it must fail here.
    from wheelgap import gapwheels

    lockfile = repo / "uv.lock"
    entry = {
        "name": "fastlib",
        "version": "2.0.0",
        "wheel": "fastlib-2.0.0-cp313-cp313-win_amd64.whl",
        "sha256": "a" * 64,
        "requires_python": ">=3.13",
    }
    if case == "private":
        lockfile.write_text(
            LOCK.replace(
                'name = "fastlib"\nversion = "2.0.0"\nsource = { registry = "https://pypi.org/simple" }',
                'name = "fastlib"\nversion = "2.0.0"\nsource = { registry = "https://private.invalid/simple" }',
            ),
            encoding="utf-8",
        )
    elif case == "version":
        entry.update(version="2.0.1", wheel="fastlib-2.0.1-cp313-cp313-win_amd64.whl")
    elif case == "target":
        entry["wheel"] = "fastlib-2.0.0-cp313-cp313-win_arm64.whl"
    elif case == "malformed":
        entry["sha256"] = "bad"
    elif case == "requires_python":
        entry["requires_python"] = ">=3.14"
    options = {"pythons": ["3.13"], "platforms": ["win-amd64"]}
    driver.check(str(repo), lockfile.name, json.dumps(options))
    result = json.loads(
        driver.gap_wheels(json.dumps({"schema": gapwheels.SCHEMA, "wheels": [entry]}))
    )
    if case == "match":
        assert [(cell["name"], cell["target"]) for cell in result["cells"]] == [
            ("fastlib", "3.13 win-amd64")
        ]
        assert "[[tool.uv.index]]" in result["text"]
        assert "fastlib" in result["text"]
        assert result["unofficial"] == gapwheels.UNOFFICIAL
        # No fixes() for this check: no upgrade, so the gap wheel isn't a fallback.
        assert result["upgrades"] is False and [cell["fallback"] for cell in result["cells"]] == [False]
        assert result["text"].startswith(gapwheels.HEAD)
    else:
        assert result == {"cells": [], "text": "", "unofficial": "", "upgrades": False}



@pytest.mark.parametrize("upgrade", [True, False])
def test_gap_wheels_come_after_the_upgrade_fixes_found(repo, upgrade):
    # wheelgap 0.14.0's --fix leads with an upgrade to an official release; a gap wheel for a cell an upgrade fixes
    # too is the fallback ("if you can't upgrade"), and the section says so. The page follows it once fixes() ran.
    from wheelgap import gapwheels

    entry = {
        "name": "fastlib",
        "version": "2.0.0",
        "wheel": "fastlib-2.0.0-cp313-cp313-win_amd64.whl",
        "sha256": "a" * 64,
        "requires_python": ">=3.13",
    }
    r = json.loads(driver.check(str(repo), "uv.lock", json.dumps({"pythons": ["3.13"], "platforms": ["win-amd64"]})))
    newer = [wheel("fastlib-2.1.0-cp313-cp313-win_amd64.whl")] if upgrade else []
    releases = {"fastlib": {"2.0.0": [wheel("fastlib-2.0.0.tar.gz")], "2.1.0": [wheel("fastlib-2.1.0.tar.gz"), *newer]}}
    fx = json.loads(driver.fixes(json.dumps(releases), json.dumps(r["rows"])))
    assert fx["fastlib==2.0.0"]["found"] == ({"3.13 win-amd64": "2.1.0"} if upgrade else {})
    result = json.loads(driver.gap_wheels(json.dumps({"schema": gapwheels.SCHEMA, "wheels": [entry]})))
    assert result["upgrades"] is upgrade
    assert [(c["name"], c["target"], c["fallback"]) for c in result["cells"]] == [("fastlib", "3.13 win-amd64", upgrade)]
    assert result["text"].startswith(gapwheels.FALLBACK_HEAD if upgrade else gapwheels.HEAD)
    assert ("fastlib 2.0.0: 3.13 win-amd64 (if you can't upgrade fastlib)" in result["text"]) is upgrade
    # A new check forgets the last one's upgrades.
    driver.check(str(repo), "uv.lock", json.dumps({"pythons": ["3.13"], "platforms": ["win-amd64"]}))
    again = json.loads(driver.gap_wheels(json.dumps({"schema": gapwheels.SCHEMA, "wheels": [entry]})))
    assert again["upgrades"] is False and again["text"].startswith(gapwheels.HEAD)


def test_the_mirrored_rules_are_the_pinned_wheelgaps():
    # check-logic.mjs can't call wheelgap, so it copies these from the release worker.mjs pins: they must match it.
    logic = (SITE / "check-logic.mjs").read_text(encoding="utf-8")
    words = re.search(r"^const SIDE_WORDS = new Set\(\[(.*?)\]\);$", logic, re.M | re.S)
    assert words and set(re.findall(r'"([^"]+)"', words.group(1))) == set(sources._SIDE_WORDS)
    depth = re.search(r"^export const NESTED_DEPTH = (\d+);$", logic, re.M)
    assert depth and int(depth.group(1)) == sources.NESTED_DEPTH
    assert driver._NOT_REQUIREMENTS == sources._NOT_REQUIREMENTS

def test_plan_lists_a_pyproject_after_the_lockfiles_in_its_folder():
    plan = json.loads(driver.plan(json.dumps(["pyproject.toml", "uv.lock", "sub/pyproject.toml", "sub/uv.lock"])))
    assert [l["path"] for l in plan["lockfiles"]] == ["uv.lock", "pyproject.toml", "sub/uv.lock", "sub/pyproject.toml"]
    assert [l["offline"] for l in plan["lockfiles"]] == [True, False, True, False]


def test_plan_says_whether_the_root_is_a_project_and_whether_it_listed_every_file():
    # #238: autoPick() never takes a subfolder's only input for the project's beside a root setup.py or setup.cfg,
    # or when plan() left files out, as `wheelgap DIR` doesn't (sources._unread_project, and its search's limit).
    plan = json.loads(driver.plan(json.dumps(["setup.py", "setup.cfg", "tools/requirements.txt", "a/setup.py"])))
    assert plan["root_project_files"] == ["setup.py", "setup.cfg"]
    assert plan["more_lockfiles"] == 0
    assert json.loads(driver.plan(json.dumps(["backend/uv.lock", "backend/setup.cfg"])))["root_project_files"] == []
    assert plan["unlisted_inputs"] == []
    many = [f"pkg{i}/uv.lock" for i in range(driver.MAX_LOCKFILES + 4)]
    assert json.loads(driver.plan(json.dumps(many)))["more_lockfiles"] == 4


def test_plan_names_the_inputs_wheelgap_dir_finds_in_folders_it_skips():
    # sources._nested_inputs() on wheelgap main searches every folder up to 3 down but hidden ones, node_modules,
    # site-packages and __pycache__; plan() also skips build/, dist/, tests/... (suggest_targets._SKIP_DIRS). The page
    # can't list those, so autoPick() never takes the one input it lists for the only one beside them.
    paths = [
        "backend/uv.lock",
        "build/requirements.txt",
        "dist/pkg/uv.lock",
        "target/a/b/pyproject.toml",
        "tests/requirements.txt",
        "build/a/b/c/uv.lock",  # 4 folders down
        "build/requirements-dev.txt",  # not a name a folder's input has
        ".venv/uv.lock",
        "build/.cache/uv.lock",
        "node_modules/x/uv.lock",
        "a/site-packages/pyproject.toml",
        "uv.lock",  # the root's: plan() lists it
    ]
    plan = json.loads(driver.plan(json.dumps(paths)))
    assert plan["unlisted_inputs"] == [
        "build/requirements.txt", "dist/pkg/uv.lock", "target/a/b/pyproject.toml", "tests/requirements.txt",
    ]
    assert [l["path"] for l in plan["lockfiles"]] == ["uv.lock", "backend/uv.lock"]


@pytest.mark.parametrize(
    ("text", "project"),
    [
        ("[tool.ruff]\nline-length = 100\n", False),  # tool settings only (transformers, beside its setup.py)
        ('[tool.poetry.dependencies]\npython = "^3.12"\n', False),  # nothing but python
        ('[build-system]\nrequires = ["setuptools"]\n', True),
        ('[project]\nname = "x"\ndynamic = ["dependencies"]\n', True),
        ('[tool.poetry.dependencies]\npython = "^3.12"\nrequests = "*"\n', True),
        ('[tool.flit.metadata]\nmodule = "x"\n', True),
        ('[tool.pdm]\ndependencies = ["requests"]\n', True),
        ("[project\n", True),  # not TOML: `wheelgap DIR` takes it for the project's, as _unread_project does
    ],
)
def test_check_says_whether_a_pyproject_it_couldnt_read_is_a_project(tmp_path, text, project):
    (tmp_path / "pyproject.toml").write_text(text, encoding="utf-8")
    bad = json.loads(driver.check(str(tmp_path), "pyproject.toml", options(), "{}", configs(tmp_path)))
    assert bad["kind"] == "source" and bad["project"] is project
    # The page reads `project` for the step to a subfolder only: other files' errors don't carry it.
    (tmp_path / "uv.lock").write_text("not toml [", encoding="utf-8")
    assert "project" not in json.loads(driver.check(str(tmp_path), "uv.lock", options(), "{}", configs(tmp_path)))


def test_needs_lists_the_lookups_a_requirements_file_needs(tmp_path):
    (tmp_path / "requirements.txt").write_text("numpy==2.3.4\nfastlib>=2,<3\n\n# notes\n", encoding="utf-8")
    n = json.loads(driver.needs(str(tmp_path), "requirements.txt", configs(tmp_path)))
    assert n["lookups"] == [{"name": "numpy", "version": "2.3.4"}, {"name": "fastlib", "version": None}]


def test_needs_skips_private_index_packages(tmp_path):
    (tmp_path / "requirements.txt").write_text(
        "--index-url https://example.invalid/simple\nnumpy==2.3.4\n", encoding="utf-8"
    )
    assert json.loads(driver.needs(str(tmp_path), "requirements.txt", configs(tmp_path)))["lookups"] == []
    r = json.loads(driver.check(str(tmp_path), "requirements.txt", options(), "{}", configs(tmp_path)))
    assert r["skipped"] == ["numpy"] and r["rows"] == []
    # The page says why nothing was checked, and never "all 0 packages have a wheel": `private` names them.
    assert r["private"] == ["numpy"] and r["packages"] == 0 and r["looked_up"] is True
    # An extra index is another index too: pip may take any of the file's packages from it.
    (tmp_path / "requirements.txt").write_text(
        "--extra-index-url https://download.example.invalid/whl\nnumpy==2.3.4\nfastlib>=2\n", encoding="utf-8"
    )
    assert json.loads(driver.needs(str(tmp_path), "requirements.txt", configs(tmp_path)))["lookups"] == []
    # Even handed an answer for one of them, check() fills nothing in.
    r = json.loads(
        driver.check(str(tmp_path), "requirements.txt", options(), json.dumps({"numpy==2.3.4": PYPI_DOC(numpy_urls)}), configs(tmp_path))
    )
    assert r["private"] == ["fastlib", "numpy"] and r["packages"] == 0 and r["rows"] == []


TORCH_LINE = "--extra-index-url https://download.pytorch.org/whl/cu128\n"
NO_CONFIGS = "[]"  # plan()'s `configs` for a repo with no uv.toml, pyproject.toml or pdm.toml


def test_pytorch_extra_index_checks_pypi_packages_and_marks_torch_unknown(tmp_path):
    (tmp_path / "requirements.txt").write_text(
        TORCH_LINE + "torch==2.6.0\ntorchvision==0.21.0\npurewheel==3.1\n",
        encoding="utf-8",
    )
    needed = json.loads(driver.needs(str(tmp_path), "requirements.txt", NO_CONFIGS))
    assert needed["lookups"] == [{"name": "purewheel", "version": "3.1"}]

    lookups = {
        "purewheel==3.1": {"info": {"version": "3.1"}, "urls": [{"filename": "purewheel-3.1-py3-none-any.whl"}]},
    }
    res = json.loads(driver.check(str(tmp_path), "requirements.txt", options(), json.dumps(lookups), NO_CONFIGS))
    assert res["private"] == []
    assert res["extra_indexes"] == ["download.pytorch.org"] and res["reader"] == "requirements"
    assert res["packages"] == 3
    by_name = {row["name"]: row for row in res["rows"]}
    assert set(by_name) == {"torch", "torchvision"}
    assert by_name["torch"]["gaps"] == []
    assert len(by_name["torch"]["unknown"]) == 3
    assert by_name["torch"]["reason"] == "may be installed from download.pytorch.org, which this page doesn't read"
    # Written with "=", and with a comment after it, pip reads the same one extra index.
    for line in ("--extra-index-url=https://download.pytorch.org/whl/cpu\n", TORCH_LINE.strip() + "  # CUDA 12.8\n"):
        (tmp_path / "requirements.txt").write_text(line + "purewheel==3.1\n", encoding="utf-8")
        assert json.loads(driver.needs(str(tmp_path), "requirements.txt", NO_CONFIGS))["total"] == 1, line

    # Adding --index-url alongside PyTorch's --extra-index-url keeps the whole file private.
    (tmp_path / "requirements.txt").write_text(
        TORCH_LINE + "--index-url https://private.example/simple\npurewheel==3.1\n",
        encoding="utf-8",
    )
    assert json.loads(driver.needs(str(tmp_path), "requirements.txt", NO_CONFIGS))["lookups"] == []
    priv = json.loads(driver.check(str(tmp_path), "requirements.txt", options(), "{}", NO_CONFIGS))
    assert priv["private"] == ["purewheel"] and priv["extra_indexes"] == []

    # Credentials in a listed host's URL keep the file private, and the URL never appears in the result.
    secret_url = "https://user:s3cr3t@download.pytorch.org/whl/cu128"
    (tmp_path / "requirements.txt").write_text(f"--extra-index-url {secret_url}\npurewheel==3.1\n", encoding="utf-8")
    assert json.loads(driver.needs(str(tmp_path), "requirements.txt", NO_CONFIGS))["lookups"] == []
    raw_res = driver.check(str(tmp_path), "requirements.txt", options(), "{}", NO_CONFIGS)
    assert "s3cr3t" not in raw_res and secret_url not in raw_res
    assert json.loads(raw_res)["private"] == ["purewheel"]

    # An --extra-index-url in an included file is recognized too.
    (tmp_path / "torch.txt").write_text(
        "--extra-index-url https://download.pytorch.org/whl/cpu\ntorch==2.6.0\n", encoding="utf-8"
    )
    (tmp_path / "requirements.txt").write_text("-r torch.txt\npurewheel==3.1\n", encoding="utf-8")
    inc_needed = json.loads(driver.needs(str(tmp_path), "requirements.txt", NO_CONFIGS))
    assert inc_needed["lookups"] == [{"name": "purewheel", "version": "3.1"}]
    inc_res = json.loads(driver.check(str(tmp_path), "requirements.txt", options(), json.dumps(lookups), NO_CONFIGS))
    assert inc_res["extra_indexes"] == ["download.pytorch.org"] and inc_res["private"] == []


@pytest.mark.parametrize(
    "lines",
    [
        # Each is read by pip 25.3 as another index, a find-links folder, no index, or an include that names one
        # (checked with pip's own requirements parser), so with the PyTorch line the file must stay private.
        "-ihttps://corp.example/simple\n",
        "-f./wheels\n",
        "--index https://corp.example/simple\n",
        "--extra https://corp.example/simple\n",
        "--no-i\n",
        "-rcorp.txt\n",
        "--requirem corp.txt\n",
        "-c https://corp.example/constraints.txt\n",
        "--extra-index-url https://pypi.example.org/simple\n",  # another host
        "--extra-index-url https://download.pytorch.org.evil.example/whl\n",
        "--extra-index-url https://download.pytorch.org:8443/whl/cu128\n",
        "--extra-index-url ${TORCH_INDEX}\n",
        '--extra-index-url "https://download.pytorch.org/whl/cu128"\n',
        "--extra-index-url https://download.pytorch.org/whl/cu128 --pre\n",
        "--extra-index-url https://download.pytorch.org/whl/cu128 \\\n --index-url https://corp.example/simple\n",
    ],
)
def test_any_index_option_but_a_whole_pytorch_line_keeps_the_file_private(tmp_path, lines):
    (tmp_path / "corp.txt").write_text("--index-url https://corp.example/simple\n", encoding="utf-8")
    (tmp_path / "requirements.txt").write_text(TORCH_LINE + lines + "purewheel==3.1\n", encoding="utf-8")
    assert json.loads(driver.needs(str(tmp_path), "requirements.txt", NO_CONFIGS))["lookups"] == []
    r = json.loads(driver.check(str(tmp_path), "requirements.txt", options(), "{}", NO_CONFIGS))
    assert "error" not in r, r
    assert "purewheel" in r["private"] and r["extra_indexes"] == []


@pytest.mark.parametrize(
    "line",
    [
        # pip 25.3 shlex-splits an option line and expands ${VAR}: each of these is an index option to it (checked with
        # its own requirements parser; ${OPT} with OPT=i), though wheelgap's collector, splitting on whitespace, misses it.
        "-'i' https://corp.example/simple",
        '--"index-url" https://corp.example/simple',
        "--ext\\ra-index-url https://corp.example/simple",
        "--no-ind''ex",
        "-${OPT} https://corp.example/simple",
    ],
)
def test_an_option_line_pip_reads_differently_keeps_an_extra_index_file_private(tmp_path, line):
    (tmp_path / "requirements.txt").write_text(TORCH_LINE + line + "\npurewheel==3.1\n", encoding="utf-8")
    assert json.loads(driver.needs(str(tmp_path), "requirements.txt", NO_CONFIGS))["lookups"] == []
    r = json.loads(driver.check(str(tmp_path), "requirements.txt", options(), "{}", NO_CONFIGS))
    assert "error" not in r, r
    assert r["private"] == ["purewheel"] and r["extra_indexes"] == []
    # The same in an include (-r, and -c) the file names.
    (tmp_path / "inc.txt").write_text(line + "\n", encoding="utf-8")
    for include in ("-r inc.txt", "-c inc.txt"):
        (tmp_path / "requirements.txt").write_text(TORCH_LINE + include + "\npurewheel==3.1\n", encoding="utf-8")
        assert json.loads(driver.needs(str(tmp_path), "requirements.txt", NO_CONFIGS))["lookups"] == [], include


def test_quotes_outside_option_lines_dont_stop_an_extra_index_file(tmp_path):
    # A marker's quotes are on a requirement line, which pip never reads an index option from.
    (tmp_path / "requirements.txt").write_text(
        TORCH_LINE + 'purewheel==3.1; python_version >= "3.13"  # pinned "for now"\n', encoding="utf-8"
    )
    assert json.loads(driver.needs(str(tmp_path), "requirements.txt", NO_CONFIGS))["total"] == 1


def test_a_byte_order_mark_doesnt_hide_a_first_line_index(tmp_path):
    # wheelgap reads requirements files as utf-8-sig, as pip decodes them; read as plain utf-8, the first line would
    # start with U+FEFF and not read as an option at all.
    (tmp_path / "requirements.txt").write_bytes(
        b"\xef\xbb\xbf--index-url https://corp.example/simple\n" + TORCH_LINE.encode() + b"purewheel==3.1\n"
    )
    assert json.loads(driver.needs(str(tmp_path), "requirements.txt", NO_CONFIGS))["lookups"] == []
    assert json.loads(driver.check(str(tmp_path), "requirements.txt", options(), "{}", NO_CONFIGS))["private"] == [
        "purewheel"
    ]


@pytest.mark.parametrize(
    "configs",
    [
        None,  # the worker didn't say which settings files the repo has: nothing is certain
        '["pyproject.toml"]',  # beside the file in the repo, but never fetched, so its [tool.uv] can't be read
        '["uv.toml"]',
        '["app/pdm.toml", "other/pyproject.toml"]',
        "{}",
    ],
)
def test_an_installer_settings_file_the_page_didnt_read_keeps_the_file_private(tmp_path, configs):
    (tmp_path / "requirements.txt").write_text(TORCH_LINE + "purewheel==3.1\n", encoding="utf-8")
    private = configs != '["app/pdm.toml", "other/pyproject.toml"]'
    n = json.loads(driver.needs(str(tmp_path), "requirements.txt", configs))
    assert n["lookups"] == ([] if private else [{"name": "purewheel", "version": "3.1"}]), configs
    # One in a subfolder isn't above the file, so it doesn't count; one above a nested file does.
    (tmp_path / "app").mkdir()
    (tmp_path / "app" / "requirements.txt").write_text(TORCH_LINE + "purewheel==3.1\n", encoding="utf-8")
    nested = json.loads(driver.needs(str(tmp_path), "app/requirements.txt", '["pyproject.toml", "app/x/uv.toml"]'))
    assert nested["lookups"] == []
    assert json.loads(driver.needs(str(tmp_path), "app/requirements.txt", '["app/x/uv.toml"]'))["total"] == 1


def test_an_installer_settings_file_on_disk_that_names_an_index_keeps_the_file_private(tmp_path):
    (tmp_path / "requirements.txt").write_text(TORCH_LINE + "purewheel==3.1\n", encoding="utf-8")
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname = "x"\n[[tool.uv.index]]\nurl = "https://corp.example/simple"\n', encoding="utf-8"
    )
    assert json.loads(driver.needs(str(tmp_path), "requirements.txt", NO_CONFIGS))["lookups"] == []


# A pyproject.toml whose [tool.uv] names a private index, beside a requirements file that installs from it.
CORP_INDEX = 'name = "corp"\nurl = "https://pypi.corp.example/simple"\n'
CORP_PYPROJECT = f'[project]\nname = "app"\nversion = "1.0"\ndependencies = ["numpy==2.3.4"]\n\n[[tool.uv.index]]\n{CORP_INDEX}'


def kept_private(root: Path, lockfile: str, listing: str | None) -> dict:
    """check()'s result for a file the page must look nothing up for, after asserting that needs() names nothing."""
    assert json.loads(driver.needs(str(root), lockfile, listing)) == {"lookups": [], "total": 0}
    r = json.loads(driver.check(str(root), lockfile, options(), "{}", listing))
    assert "error" not in r, r
    assert r["private"] and r["rows"] == [] and r["packages"] == 0, r
    return r


def test_a_pyproject_beside_a_requirements_file_that_names_an_index_keeps_it_private(tmp_path):
    # The reviewer's repro: `uv export` writes no index lines, and uv applies the pyproject.toml's [tool.uv] index to
    # `uv pip install -r requirements.txt`, so wheelgap reads it from disk and doesn't call the file PyPI's.
    (tmp_path / "requirements.txt").write_text("corp-secret-lib==1.0\n", encoding="utf-8")
    (tmp_path / "pyproject.toml").write_text(CORP_PYPROJECT, encoding="utf-8")
    assert sources.load_file(tmp_path / "requirements.txt").pypi_only is False  # what wheelgap says on a disk
    # The worker fetches it before the check, since plan() lists it beside the file.
    assert json.loads(driver.configs_for("requirements.txt", configs(tmp_path))) == ["pyproject.toml"]
    r = kept_private(tmp_path, "requirements.txt", configs(tmp_path))
    assert r["private"] == ["corp-secret-lib"]
    assert (r["configs"], r["configs_unread"], r["configs_unknown"]) == (["pyproject.toml"], [], False)
    # It's still requirements.txt that's checked, not the pyproject.toml's own dependencies.
    assert r["lockfile"] == "requirements.txt" and r["listed"] == 1 and r["skipped"] == ["corp-secret-lib"]


@pytest.mark.parametrize(
    ("name", "text"),
    [
        ("pyproject.toml", '[tool.uv]\nindex-url = "https://pypi.corp.example/simple"\n'),
        ("pyproject.toml", '[tool.uv.pip]\nextra-index-url = ["https://pypi.corp.example/simple"]\n'),
        ("pyproject.toml", '[tool.uv.sources]\ncorp-secret-lib = { index = "corp" }\n'),
        ("pyproject.toml", f"[[tool.poetry.source]]\n{CORP_INDEX}"),
        ("pyproject.toml", f"[[tool.pdm.source]]\n{CORP_INDEX}"),
        ("pyproject.toml", '[tool.pixi.pypi-options]\nindex-url = "https://pypi.corp.example/simple"\n'),
        ("pyproject.toml", "[tool.uv\n"),  # one wheelgap can't read counts as naming an index
        ("uv.toml", f"[[index]]\n{CORP_INDEX}"),
        ("uv.toml", "no-index = true\n"),
        ("pdm.toml", '[pypi]\nurl = "https://pypi.corp.example/simple"\n'),
    ],
)
@pytest.mark.parametrize("lockfile", ["requirements.txt", "pyproject.toml", "Pipfile.lock"])
def test_every_index_setting_wheelgap_reads_nearby_keeps_the_file_private(tmp_path, name, text, lockfile):
    files = {
        "requirements.txt": "corp-secret-lib==1.0\n",
        "pyproject.toml": '[project]\nname = "app"\nversion = "1.0"\ndependencies = ["corp-secret-lib==1.0"]\n',
        "Pipfile.lock": json.dumps(
            {
                "_meta": {"sources": [{"url": "https://pypi.org/simple", "name": "pypi"}]},
                "default": {"corp-secret-lib": {"version": "==1.0"}},
            }
        ),
    }
    (tmp_path / "app").mkdir()
    (tmp_path / "app" / lockfile).write_text(files[lockfile], encoding="utf-8")
    # The same file with nothing nearby is looked up on PyPI...
    assert json.loads(driver.needs(str(tmp_path), f"app/{lockfile}", configs(tmp_path)))["total"] == 1
    # ...and with the setting in the repo's root, above it, it isn't.
    (tmp_path / name).write_text(text, encoding="utf-8")
    assert sources.load_file(tmp_path / "app" / lockfile).pypi_only is False
    assert name in json.loads(driver.configs_for(f"app/{lockfile}", configs(tmp_path)))  # (a pyproject.toml: itself too)
    assert kept_private(tmp_path, f"app/{lockfile}", configs(tmp_path))["private"] == ["corp-secret-lib"]


def test_a_uv_toml_above_a_requirements_file_is_read_and_one_beside_it_elsewhere_isnt(tmp_path):
    (tmp_path / "services" / "api").mkdir(parents=True)
    (tmp_path / "services" / "api" / "requirements.txt").write_text("corp-secret-lib==1.0\n", encoding="utf-8")
    (tmp_path / "services" / "uv.toml").write_text(f"[[index]]\n{CORP_INDEX}", encoding="utf-8")
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "uv.toml").write_text(f"[[index]]\n{CORP_INDEX}", encoding="utf-8")
    listing = configs(tmp_path)
    assert json.loads(listing) == ["docs/uv.toml", "services/uv.toml"]
    # Only the one in a folder above the file: docs/ isn't, so it's never fetched for this check.
    assert json.loads(driver.configs_for("services/api/requirements.txt", listing)) == ["services/uv.toml"]
    r = kept_private(tmp_path, "services/api/requirements.txt", listing)
    assert r["configs"] == ["services/uv.toml"]
    # A uv.toml above it that names no index changes nothing: the file is looked up on PyPI.
    (tmp_path / "services" / "uv.toml").write_text('python-preference = "managed"\n', encoding="utf-8")
    n = json.loads(driver.needs(str(tmp_path), "services/api/requirements.txt", listing))
    assert n["lookups"] == [{"name": "corp-secret-lib", "version": "1.0"}]


def test_a_settings_file_the_repo_lists_but_the_worker_couldnt_fetch_keeps_the_file_private(tmp_path):
    # pyproject.toml is in the repo's file list, beside the file, but its fetch failed, so it isn't on disk: what it
    # says about an index is unknown, and wheelgap, not finding it, would take the file as PyPI's.
    (tmp_path / "requirements.txt").write_text("corp-secret-lib==1.0\n", encoding="utf-8")
    assert sources.load_file(tmp_path / "requirements.txt").pypi_only is True
    r = kept_private(tmp_path, "requirements.txt", json.dumps(["pyproject.toml"]))
    assert (r["configs"], r["configs_unread"], r["configs_unknown"]) == ([], ["pyproject.toml"], False)
    # Above a nested file, or beside a pyproject.toml that's checked, the same.
    (tmp_path / "app").mkdir()
    (tmp_path / "app" / "pyproject.toml").write_text(
        '[project]\nname = "app"\nversion = "1.0"\ndependencies = ["corp-secret-lib==1.0"]\n', encoding="utf-8"
    )
    listing = json.dumps(["app/pyproject.toml", "app/uv.toml"])
    assert kept_private(tmp_path, "app/pyproject.toml", listing)["configs_unread"] == ["app/uv.toml"]
    # Once it's fetched (and names no index), the file is looked up.
    (tmp_path / "app" / "uv.toml").write_text("", encoding="utf-8")
    assert json.loads(driver.needs(str(tmp_path), "app/pyproject.toml", listing))["total"] == 1


def test_a_settings_file_named_in_another_case_keeps_the_file_private(tmp_path):
    # A checkout on macOS or Windows reads Pyproject.toml as pyproject.toml; the browser's disk never would, so it's
    # never fetched, and the file stays private as if it couldn't be.
    (tmp_path / "requirements.txt").write_text("corp-secret-lib==1.0\n", encoding="utf-8")
    listing = json.loads(driver.plan(json.dumps(["requirements.txt", "Pyproject.toml", "docs/UV.TOML"])))["configs"]
    assert listing == ["Pyproject.toml", "docs/UV.TOML"]
    assert json.loads(driver.configs_for("requirements.txt", json.dumps(listing))) == []
    r = kept_private(tmp_path, "requirements.txt", json.dumps(listing))
    assert r["configs_unread"] == ["Pyproject.toml"]  # docs/ isn't above the file
    # Even written to disk under that name, it's never one this page reads.
    (tmp_path / "Pyproject.toml").write_text("[project]\n", encoding="utf-8")
    assert kept_private(tmp_path, "requirements.txt", json.dumps(listing))["configs_unread"] == ["Pyproject.toml"]


@pytest.mark.parametrize("listing", ["null", None, "{}", '["pyproject.toml", 1]'])
def test_without_the_repos_list_of_settings_files_nothing_is_looked_up(repo, listing):
    # GitHub cut the file list short (the worker passes null), or the list is missing or malformed: a settings file
    # naming an index may be missing from it, so the file stays private, and the result says why.
    (repo / "requirements.txt").write_text("numpy==2.3.4\n", encoding="utf-8")
    r = kept_private(repo, "requirements.txt", listing)
    assert (r["configs"], r["configs_unread"], r["configs_unknown"]) == ([], [], True)
    assert json.loads(driver.configs_for("requirements.txt", listing)) == []  # nothing to fetch for it either
    # A lockfile that lists its files reads none of them, so it's checked as before.
    assert json.loads(driver.check(str(repo), "uv.lock", options(), "{}", listing))["rows"] == json.loads(
        driver.check(str(repo), "uv.lock", options())
    )["rows"]


def test_a_requirements_file_with_no_settings_file_nearby_is_still_looked_up_on_pypi(tmp_path):
    (tmp_path / "app").mkdir()
    (tmp_path / "app" / "requirements.txt").write_text("numpy==2.3.4\n", encoding="utf-8")
    # One elsewhere in the repo, naming an index, isn't read for it (nor fetched), so it changes nothing.
    (tmp_path / "tools").mkdir()
    (tmp_path / "tools" / "pyproject.toml").write_text(CORP_PYPROJECT, encoding="utf-8")
    listing = configs(tmp_path)
    assert json.loads(driver.configs_for("app/requirements.txt", listing)) == []
    n = json.loads(driver.needs(str(tmp_path), "app/requirements.txt", listing))
    assert n == {"lookups": [{"name": "numpy", "version": "2.3.4"}], "total": 1}
    docs = {"numpy==2.3.4": PYPI_DOC(numpy_urls)}
    r = json.loads(driver.check(str(tmp_path), "app/requirements.txt", options(), json.dumps(docs), listing))
    assert r["private"] == [] and [row["name"] for row in r["rows"]] == ["numpy"]
    assert (r["configs"], r["configs_unread"], r["configs_unknown"]) == ([], [], False)
    # Nor does one beside it that names no index: it's read, and the requirements file is still the one checked.
    (tmp_path / "app" / "pyproject.toml").write_text(
        '[project]\nname = "app"\nversion = "1.0"\ndependencies = ["fastlib>=2"]\n', encoding="utf-8"
    )
    listing = configs(tmp_path)
    assert json.loads(driver.needs(str(tmp_path), "app/requirements.txt", listing))["lookups"] == n["lookups"]
    r = json.loads(driver.check(str(tmp_path), "app/requirements.txt", options(), json.dumps(docs), listing))
    assert r["lockfile"] == "app/requirements.txt" and r["listed"] == 1 and r["configs"] == ["app/pyproject.toml"]
    assert r["private"] == [] and [row["name"] for row in r["rows"]] == ["numpy"]


def test_the_settings_files_are_the_ones_the_pinned_wheelgap_reads():
    # When the pin moves, these fail if wheelgap reads other files near the input, or from other readers.
    import inspect

    assert driver.CONFIG_NAMES == tuple(sources._CONFIG_FILES)
    # Read from the input's folder and every folder above it, up to the first with a .git: what configs_for() names.
    walk = inspect.getsource(sources._nearby_config_names_an_index)
    assert "for directory in (folder, *folder.parents):" in walk and '(directory / ".git").exists()' in walk
    # The readers that call it, directly or through another function of sources.py (parse_requirements and
    # _project_source do, since 0.14.0).
    funcs = {n: f for n, f in vars(sources).items() if inspect.isfunction(f) and f.__module__ == sources.__name__}
    reach = {"_nearby_config_names_an_index"}
    while more := {
        n for n, f in funcs.items() if n not in reach and any(re.search(rf"\b{r}\(", inspect.getsource(f)) for r in reach)
    }:
        reach |= more
    callers = {kind for kind, read in sources.READERS.items() if read.__name__ in reach}
    assert callers == set(driver.NEARBY_CONFIG_READERS)
    # The worker drops the ones another check fetched by the same names.
    names = re.search(r"^const CONFIG_NAMES = (\[.*\]);$", (SITE / "worker.mjs").read_text(encoding="utf-8"), re.M)
    assert names and tuple(json.loads(names.group(1))) == driver.CONFIG_NAMES
    # A pip.conf or pip.ini isn't one: this release doesn't read them (they'd be a reason to keep a file private).
    assert "pip.conf" in inspect.getsource(sources._nearby_config_names_an_index)  # named as not read
    assert not {"pip.conf", "pip.ini"} & set(sources._CONFIG_FILES)


@pytest.mark.parametrize("listing", ['["pyproject.toml"]', "null", None])
def test_an_extra_index_file_whose_nearby_settings_werent_read_is_never_looked_up(tmp_path, listing):
    # The PyTorch line alone would make the file public, but a pyproject.toml beside it that the worker couldn't fetch
    # (listed, not on disk), or a file list GitHub cut short (null), leaves what it says about an index unknown:
    # _public_extra_hosts() refuses first, so _private()'s early return for a public file never skips that check.
    (tmp_path / "requirements.txt").write_text(TORCH_LINE + "torch==2.6.0\npurewheel==3.1\n", encoding="utf-8")
    r = kept_private(tmp_path, "requirements.txt", listing)
    assert r["extra_indexes"] == [] and r["private"] == ["purewheel", "torch"]
    assert (r["configs_unread"], r["configs_unknown"]) == ((["pyproject.toml"], False) if listing == '["pyproject.toml"]' else ([], True))


def test_an_extra_index_file_beside_a_read_settings_file_stays_private_even_without_an_index_in_it(tmp_path):
    # Fetched and read, naming no index: wheelgap would call a plain requirements file PyPI's then, but the extra-index
    # allowlist only vouches for a requirements file with no installer settings of its own nearby.
    (tmp_path / "requirements.txt").write_text(TORCH_LINE + "purewheel==3.1\n", encoding="utf-8")
    (tmp_path / "uv.toml").write_text('python-preference = "managed"\n', encoding="utf-8")
    r = kept_private(tmp_path, "requirements.txt", configs(tmp_path))
    assert r["extra_indexes"] == [] and r["configs"] == ["uv.toml"] and r["configs_unread"] == []


def test_needs_reads_a_pyprojects_direct_requirements(tmp_path):
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname = "demo"\nversion = "0.1.0"\ndependencies = ["numpy==2.3.4", "demo"]\n',
        encoding="utf-8",
    )
    n = json.loads(driver.needs(str(tmp_path), "pyproject.toml", configs(tmp_path)))
    assert n["lookups"] == [{"name": "numpy", "version": "2.3.4"}]


@pytest.mark.parametrize("settings", [
    '[tool.uv.sources]\nmylib = { workspace = true }\n',
    '[[tool.uv.index]]\nurl = "https://example.invalid/simple"\n',
])
def test_pyproject_with_other_sources_reports_every_private_dependency(tmp_path, settings):
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname = "myapp"\ndependencies = ["fastapi", "numpy", "mylib"]\n' + settings,
        encoding="utf-8",
    )
    assert json.loads(driver.needs(str(tmp_path), "pyproject.toml", configs(tmp_path)))["lookups"] == []
    r = json.loads(driver.check(str(tmp_path), "pyproject.toml", options(), "{}", configs(tmp_path)))
    assert (r["listed"], r["packages"], r["private_count"]) == (3, 0, 3)
    assert r["private"] == r["skipped"] == ["fastapi", "mylib", "numpy"]
    assert r["looked_up"] is True and r["rows"] == []


def test_private_count_counts_entries_not_distinct_names(tmp_path):
    (tmp_path / "pyproject.toml").write_text(
        '''[project]
name = "demo"
dependencies = ['numpy; python_version < "3.14"', 'numpy; python_version >= "3.14"']
[tool.uv.sources]
demo = { workspace = true }
''', encoding="utf-8",
    )
    assert json.loads(driver.needs(str(tmp_path), "pyproject.toml", configs(tmp_path)))["lookups"] == []
    r = json.loads(driver.check(str(tmp_path), "pyproject.toml", options(), "{}", configs(tmp_path)))
    assert (r["listed"], r["packages"], r["private_count"]) == (2, 0, 2)
    assert r["private"] == r["skipped"] == ["numpy"]


def test_private_count_does_not_include_other_skipped_packages(tmp_path):
    (tmp_path / "pyproject.toml").write_text(
        '''[project]
name = "demo"
dependencies = ["numpy", "localpkg @ file:///localpkg"]
[tool.uv.sources]
demo = { workspace = true }
''', encoding="utf-8",
    )
    r = json.loads(driver.check(str(tmp_path), "pyproject.toml", options(), "{}", configs(tmp_path)))
    assert (r["listed"], r["packages"], r["private_count"]) == (2, 0, 1)
    assert r["private"] == ["numpy"] and r["skipped"] == ["localpkg", "numpy"]


def test_needs_names_a_missing_include_and_works_once_its_fetched(tmp_path):
    (tmp_path / "requirements.txt").write_text("-r dev.txt\nnumpy==2.3.4\n", encoding="utf-8")
    n = json.loads(driver.needs(str(tmp_path), "requirements.txt", configs(tmp_path)))
    assert n["kind"] == "include" and n["path"] == "dev.txt" and str(tmp_path) not in n["error"]
    (tmp_path / "dev.txt").write_text("fastlib>=2\n", encoding="utf-8")
    assert [l["name"] for l in json.loads(driver.needs(str(tmp_path), "requirements.txt", configs(tmp_path)))["lookups"]] == [
        "fastlib",
        "numpy",
    ]


def test_needs_names_a_missing_include_under_a_root_reached_through_a_link(tmp_path):
    # wheelgap makes an include's path absolute without following links, so a root whose own path has one (macOS's
    # /var -> /private/var) must still contain it: compared with Path.resolve(), it never would.
    real = tmp_path / "real"
    real.mkdir()
    (tmp_path / "link").symlink_to(real, target_is_directory=True)
    root = tmp_path / "link"
    (real / "requirements.txt").write_text("-r sub/dev.txt\nnumpy==2.3.4\n", encoding="utf-8")
    n = json.loads(driver.needs(str(root), "requirements.txt", configs(root)))
    assert n == {"error": "sub/dev.txt (an include) isn't fetched yet", "kind": "include", "path": "sub/dev.txt"}


def test_needs_refuses_an_include_outside_the_repo(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "requirements.txt").write_text("-r ../shared.txt\nnumpy==2.3.4\n", encoding="utf-8")
    n = json.loads(driver.needs(str(repo), "requirements.txt", configs(repo)))
    # No path for the worker to fetch: it would be read from outside the repo's folder.
    assert n["kind"] == "include" and n["path"] is None
    assert "outside the repository" in n["error"] and str(tmp_path) not in n["error"]


def test_needs_lists_each_lookup_once_and_leaves_other_errors_to_check(tmp_path):
    (tmp_path / "requirements.txt").write_text(
        "numpy==2.3.4\nNumPy==2.3.4 ; python_version >= '3.13'\nfastlib\n", encoding="utf-8"
    )
    n = json.loads(driver.needs(str(tmp_path), "requirements.txt", configs(tmp_path)))
    assert n["lookups"] == [{"name": "numpy", "version": "2.3.4"}, {"name": "fastlib", "version": None}]
    # A pyproject.toml with only tool settings: nothing to look up, and check() says why it can't be read.
    (tmp_path / "pyproject.toml").write_text("[tool.ruff]\nline-length = 100\n", encoding="utf-8")
    assert json.loads(driver.needs(str(tmp_path), "pyproject.toml", configs(tmp_path)))["kind"] == "source"
    bad = json.loads(driver.check(str(tmp_path), "pyproject.toml", options(), "{}", configs(tmp_path)))
    assert bad["kind"] == "source" and "no [project] table" in bad["error"] and str(tmp_path) not in bad["error"]


def test_a_lockfile_that_lists_its_files_needs_no_lookups(repo):
    assert json.loads(driver.needs(str(repo), "uv.lock")) == {"lookups": [], "total": 0}


def test_needs_and_check_read_a_pipfile_lock(tmp_path):
    pipfile = {
        "_meta": {
            "sources": [{"url": "https://pypi.org/simple", "name": "pypi", "verify_ssl": True}],
            "hash": {"sha256": "x"},
        },
        "default": {
            "numpy": {"version": "==2.3.4", "hashes": ["sha256:x"]},
            "corp-utils": {"version": "==2.0", "hashes": ["sha256:z"], "index": "corp"},
        },
        "develop": {},
    }
    (tmp_path / "Pipfile.lock").write_text(json.dumps(pipfile), encoding="utf-8")
    n = json.loads(driver.needs(str(tmp_path), "Pipfile.lock", configs(tmp_path)))
    assert n["lookups"] == [{"name": "numpy", "version": "2.3.4"}]  # corp-utils is bound to another index
    docs = {"numpy==2.3.4": PYPI_DOC(numpy_urls)}
    r = json.loads(driver.check(str(tmp_path), "Pipfile.lock", options(), json.dumps(docs), configs(tmp_path)))
    assert r["skipped"] == ["corp-utils"]
    assert [row["name"] for row in r["rows"]] == ["numpy"]
    assert r["rows"][0]["gaps"] == ["3.13 linux-aarch64", "3.13 musl-aarch64"]


numpy_urls = [
    wheel("numpy-2.3.4-cp313-cp313-manylinux_2_17_x86_64.manylinux2014_x86_64.whl"),
    wheel("numpy-2.3.4.tar.gz"),
]
PYPI_DOC = lambda urls: {"info": {}, "releases": {}, "urls": urls}


def test_check_fills_requirements_from_canned_pypi_json(tmp_path):
    (tmp_path / "requirements.txt").write_text("numpy==2.3.4\nfastlib>=2,<3\nmissing==1.0\n", encoding="utf-8")
    docs = {
        "numpy==2.3.4": PYPI_DOC(numpy_urls),
        # fastlib 2.2.0 only supports Python 3.14, so the range resolves to 2.0.0 on 3.13 and 2.2.0 on 3.14.
        "fastlib": {
            "info": {},
            "urls": [],
            "releases": {
                "2.0.0": [wheel("fastlib-2.0.0-cp313-cp313-manylinux_2_17_x86_64.whl"), wheel("fastlib-2.0.0.tar.gz")],
                "2.2.0": [
                    wheel("fastlib-2.2.0-cp314-cp314-manylinux_2_17_x86_64.whl", requires_python=">=3.14"),
                    wheel("fastlib-2.2.0-cp314-cp314-manylinux_2_17_aarch64.whl", requires_python=">=3.14"),
                    wheel("fastlib-2.2.0.tar.gz", requires_python=">=3.14"),
                ],
            },
        },
        "missing==1.0": None,  # PyPI answered 404: unknown, never a gap
    }
    r = json.loads(driver.check(str(tmp_path), "requirements.txt", options(pythons=["3.13", "3.14"]), json.dumps(docs), configs(tmp_path)))
    assert r["skipped"] == [] and r["packages"] == 4  # fastlib resolves to one row per Python
    numpy = next(row for row in r["rows"] if row["name"] == "numpy")
    assert numpy["statuses"]["3.13 linux-x86_64"] == "wheel"  # the cp313 x86_64 wheel
    assert len(numpy["gaps"]) == 5 and "3.13 linux-x86_64" not in numpy["gaps"]
    assert numpy["lookup"] is True
    assert numpy["statuses"]["3.13 linux-x86_64"] == "wheel" and numpy["lookup"] is True
    fast = [row for row in r["rows"] if row["name"] == "fastlib"]
    assert {(row["version"], tuple(row["gaps"])) for row in fast} == {
        ("2.0.0", ("3.13 linux-aarch64", "3.13 musl-aarch64")),
        ("2.2.0", ("3.14 musl-aarch64",)),
    }
    gone = next(row for row in r["rows"] if row["name"] == "missing")
    assert gone["gaps"] == [] and gone["lookup"] is False and len(gone["unknown"]) == 6  # unknown on every target
    assert gone["gaps"] == [] and gone["lookup"] is False
    # fixes() reads the same releases mapping, so the range's 2.0.0 row gets an answer like a lockfile's.
    fx = json.loads(driver.fixes(json.dumps({"fastlib": docs["fastlib"]["releases"]}), json.dumps(fast)))
    assert set(fx) == {"fastlib==2.0.0", "fastlib==2.2.0"} and fx["fastlib==2.0.0"]["found"] == {}


def test_check_fills_a_pyproject_from_canned_pypi_json(tmp_path):
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname = "demo"\nversion = "0.1.0"\ndependencies = ["numpy==2.3.4", "demo"]\n', encoding="utf-8"
    )
    r = json.loads(driver.check(str(tmp_path), "pyproject.toml", options(), json.dumps({"numpy==2.3.4": PYPI_DOC(numpy_urls)}), configs(tmp_path)))
    assert r["skipped"] == [] and r["packages"] == 1  # demo is the project itself: not a row
    assert [row["name"] for row in r["rows"]] == ["numpy"]
    assert r["rows"][0]["gaps"] == ["3.13 linux-aarch64", "3.13 musl-aarch64"]


def test_a_free_threaded_python_is_resolved_as_its_major_minor(tmp_path):
    # Requires-Python is compared with a version, and "3.14t" isn't one. Handed over as the page writes it, every
    # file that has a Requires-Python is ruled out, so a pin read as "can't install" on a free-threaded target.
    (tmp_path / "requirements.txt").write_text("numpy==2.3.4\nfastlib>=2\n", encoding="utf-8")
    docs = {
        "numpy==2.3.4": PYPI_DOC(
            [
                wheel("numpy-2.3.4-cp314-cp314t-manylinux_2_17_x86_64.manylinux2014_x86_64.whl", ">=3.11"),
                wheel("numpy-2.3.4.tar.gz", ">=3.11"),
            ]
        ),
        "fastlib": {
            "info": {},
            "urls": [],
            "releases": {"2.0.0": [wheel("fastlib-2.0.0-cp314-cp314t-manylinux_2_17_x86_64.whl", ">=3.13")]},
        },
    }
    for pythons in (["3.14t"], ["3.13", "3.14t"]):
        opts = options(pythons=pythons, platforms=["linux-x86_64"])
        r = json.loads(driver.check(str(tmp_path), "requirements.txt", opts, json.dumps(docs), configs(tmp_path)))
        assert r["per_target"]["3.14t linux-x86_64"] == {"wheel": 2, "source": 0, "missing": 0, "unknown": 0}, pythons
        assert not [row for row in r["rows"] if "3.14t linux-x86_64" in row["gaps"] + row["unknown"]], pythons


def test_a_pyproject_is_checked_on_pythons_outside_its_requires_python(tmp_path):
    # `uvx wheelgap pyproject.toml -p 3.13` checks 3.13 whatever requires-python says ("checked anyway because you
    # asked for them"): PyPI has the release's files for every Python, unlike a uv.lock.
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname = "demo"\nversion = "0.1.0"\nrequires-python = ">=3.14"\ndependencies = ["numpy==2.3.4"]\n',
        encoding="utf-8",
    )
    docs = json.dumps({"numpy==2.3.4": PYPI_DOC(numpy_urls)})
    r = json.loads(driver.check(str(tmp_path), "pyproject.toml", options(), docs, configs(tmp_path)))
    assert r["outside_lock"] == [] and r["looked_up"] is True
    assert r["per_target"]["3.13 linux-x86_64"] == {"wheel": 1, "source": 0, "missing": 0, "unknown": 0}
    assert [row["gaps"] for row in r["rows"]] == [["3.13 linux-aarch64", "3.13 musl-aarch64"]]


def test_a_requirement_no_target_installs_is_not_a_package_of_the_result(tmp_path):
    # As the CLI counts them: winonly's marker rules it out on every target here, so it's no row and not in the total.
    (tmp_path / "requirements.txt").write_text(
        'numpy==2.3.4\nwinonly==1.0 ; sys_platform == "win32"\n', encoding="utf-8"
    )
    docs = {
        "numpy==2.3.4": PYPI_DOC(numpy_urls),
        "winonly==1.0": PYPI_DOC([wheel("winonly-1.0-cp313-cp313-win_amd64.whl")]),
    }
    r = json.loads(driver.check(str(tmp_path), "requirements.txt", options(), json.dumps(docs), configs(tmp_path)))
    assert r["packages"] == 1 and [row["name"] for row in r["rows"]] == ["numpy"]


PIXI = """\
version: 6
environments:
  default:
    channels:
    - url: https://conda.anaconda.org/conda-forge/
    indexes:
    - https://pypi.org/simple
    packages:
      linux-64:
      - conda: https://conda.anaconda.org/conda-forge/linux-64/python-3.13.1-h9a34f23_5_cp313.conda
      - pypi: https://files.pythonhosted.org/packages/aa/bb/fastlib-2.0.0-cp313-cp313-manylinux_2_17_x86_64.manylinux2014_x86_64.whl
packages:
- conda: https://conda.anaconda.org/conda-forge/linux-64/python-3.13.1-h9a34f23_5_cp313.conda
  sha256: 0000000000000000000000000000000000000000000000000000000000000000
  md5: 00000000000000000000000000000000
  license: Python-2.0
  size: 1
  timestamp: 1
- pypi: https://files.pythonhosted.org/packages/aa/bb/fastlib-2.0.0-cp313-cp313-manylinux_2_17_x86_64.manylinux2014_x86_64.whl
  name: fastlib
  version: 2.0.0
  sha256: 0000000000000000000000000000000000000000000000000000000000000000
"""


def test_a_pixi_lock_is_looked_up_package_by_package(tmp_path):
    # A pixi.lock says where each file came from (here PyPI's own file host), so its packages are looked up as the
    # CLI does. It never says the whole input is PyPI-only, which must not get every package skipped.
    (tmp_path / "pixi.lock").write_text(PIXI, encoding="utf-8")
    n = json.loads(driver.needs(str(tmp_path), "pixi.lock"))
    assert n["lookups"] == [{"name": "fastlib", "version": "2.0.0"}]
    docs = {
        "fastlib==2.0.0": PYPI_DOC(
            [
                wheel("fastlib-2.0.0-cp313-cp313-manylinux_2_17_x86_64.manylinux2014_x86_64.whl"),
                wheel("fastlib-2.0.0.tar.gz"),
            ]
        )
    }
    opts = options(platforms=["linux-x86_64", "linux-aarch64"])
    r = json.loads(driver.check(str(tmp_path), "pixi.lock", opts, json.dumps(docs)))
    assert r["packages"] == 1 and r["skipped"] == ["python"] and r["private"] == []  # python is the conda package
    assert [(row["name"], row["gaps"]) for row in r["rows"]] == [("fastlib", ["3.13 linux-aarch64"])]
    # As with --suggest: the lock doesn't record the index as PyPI, so no fix is looked up for it.
    assert r["rows"][0]["lookup"] is False
    # With no answer from PyPI it's unknown on every target, never judged on the one file the lock recorded.
    r = json.loads(driver.check(str(tmp_path), "pixi.lock", opts, "{}"))
    assert [(row["gaps"], len(row["unknown"])) for row in r["rows"]] == [([], 2)]


def test_an_answer_with_no_file_list_is_unknown_never_a_gap(tmp_path):
    # A 200 that isn't PyPI's JSON (a proxy's page, say). wheelgap reads a missing `urls` as a release with no files,
    # which is "can't install": a gap nobody has. A range without `releases` already says "unknown" itself.
    (tmp_path / "requirements.txt").write_text("numpy==2.3.4\nfastlib\nranged>=2\n", encoding="utf-8")
    docs = {
        "numpy==2.3.4": {"message": "not PyPI's JSON"},
        "fastlib": {"info": {"version": "2.0.0"}},
        "ranged": {"info": {}},
    }
    r = json.loads(driver.check(str(tmp_path), "requirements.txt", options(), json.dumps(docs), configs(tmp_path)))
    assert r["packages"] == 3 and all(sum(c.values()) == c["unknown"] == 3 for c in r["per_target"].values())
    assert [(row["name"], row["gaps"], len(row["unknown"]), row["lookup"]) for row in r["rows"]] == [
        ("numpy", [], 3, False),
        ("fastlib", [], 3, False),
        ("ranged", [], 3, False),
    ]


@pytest.mark.parametrize("requirement", ["numpy==2.3.4", "numpy>=2.3,<2.4", "numpy"])
def test_trimmed_recorded_json_keeps_check_and_fixes_answers(tmp_path, requirement):
    project = recorded_project()
    pinned = requirement == "numpy==2.3.4"
    doc = {"info": project["info"], "urls": project["releases"]["2.3.4"]} if pinned else project
    docs = {"numpy==2.3.4" if pinned else "numpy": doc}
    releases = {"numpy": project["releases"]}
    trimmed, trimmed_releases = trim_json(docs), trim_json(releases, releases=True)
    assert len(json.dumps(trimmed)) < len(json.dumps(docs)) / 2
    assert len(json.dumps(trimmed_releases)) < len(json.dumps(releases)) / 2
    (tmp_path / "requirements.txt").write_text(requirement + "\n", encoding="utf-8")
    answers = []
    for lookups, rel in [(docs, releases), (trimmed, trimmed_releases)]:
        result = json.loads(driver.check(str(tmp_path), "requirements.txt", options(), json.dumps(lookups), configs(tmp_path)))
        fixes = json.loads(driver.fixes(json.dumps(rel), json.dumps(result["rows"])))
        answers.append((result, fixes))
    assert answers[0] == answers[1]
    result, fixes = answers[1]
    assert result["packages"] == 1 and result["rows"][0]["lookup"] is True
    if requirement != "numpy":
        # The recorded 2.4.0 files are yanked: they must not hide the next usable fix.
        assert fixes["numpy==2.3.4"]["found"] == {"3.13 linux-aarch64": "2.5.5"}
    else:
        assert fixes == {"numpy==2.5.5": {"found": {}, "loses": []}}


@pytest.mark.parametrize("case", ["hash", "exclude-newer"])
def test_trimming_keeps_the_hashes_and_upload_times_wheelgap_reads(tmp_path, case):
    # Since 0.14.0, a requirement's --hash options keep only the files whose digests.sha256 they list, and a uv
    # exclude-newer drops the files uploaded after it (upload_time_iso_8601): the trimmed JSON must keep both.
    project = recorded_project()
    if case == "hash":
        sha = project["releases"]["2.3.4"][0]["digests"]["sha256"]
        lockfile, text = "requirements.txt", f"numpy==2.3.4 --hash=sha256:{sha}\n"
        docs = {"numpy==2.3.4": {"info": project["info"], "urls": project["releases"]["2.3.4"]}}
    else:
        lockfile = "pyproject.toml"
        text = '[project]\nname = "demo"\nversion = "0.1"\ndependencies = ["numpy>=2.3"]\n\n[tool.uv]\nexclude-newer = "2025-11-01T00:00:00Z"\n'
        docs = {"numpy": project}
    (tmp_path / lockfile).write_text(text, encoding="utf-8")
    full, trimmed = (
        json.loads(driver.check(str(tmp_path), lockfile, options(), json.dumps(d), configs(tmp_path)))
        for d in (docs, trim_json(docs))
    )
    assert full == trimmed
    [row] = trimmed["rows"]
    if case == "hash":  # the listed hash is the x86_64 wheel's: the sdist's isn't, so the others are gaps
        assert (row["version"], row["keep"], row["lookup"]) == ("2.3.4", ["3.13 linux-x86_64"], True)
    else:  # 2.3.4 is the newest release uploaded before the cutoff
        assert row["version"] == "2.3.4"

@pytest.mark.parametrize("requirement", ["fastlib==1.0", "fastlib>=1", "fastlib"])
def test_trimming_preserves_yanks_python_constraints_and_invalid_releases(tmp_path, requirement):
    old = [wheel("fastlib-1.0-cp313-cp313-manylinux_2_17_x86_64.whl", ">=3.13,<3.14"),
           wheel("fastlib-1.0.tar.gz", ">=3.13,<3.14")]
    newer = [wheel("fastlib-2.0-cp314-cp314-manylinux_2_17_x86_64.whl", ">=3.14"),
             wheel("fastlib-2.0-cp314-cp314-manylinux_2_17_aarch64.whl", ">=3.14")]
    releases = {"fastlib": {
        "1.0": old, "2.0": newer,
        "3.0": [{**wheel("fastlib-3.0-py3-none-any.whl"), "yanked": True}],
        "4.0": [wheel("fastlib-4.0-py3-none-any.whl"), {}],  # reject the whole list, never filter the bad entry out
    }}
    project = {"info": {"version": "1.0"}, "urls": old, "releases": releases["fastlib"]}
    docs = {"fastlib==1.0" if "==" in requirement else "fastlib": project}
    (tmp_path / "requirements.txt").write_text(requirement + "\n", encoding="utf-8")
    answers = []
    for lookups, rel in [(docs, releases), (trim_json(docs), trim_json(releases, releases=True))]:
        result = json.loads(driver.check(str(tmp_path), "requirements.txt", options(pythons=["3.13", "3.14"]), json.dumps(lookups), configs(tmp_path)))
        answers.append((result, json.loads(driver.fixes(json.dumps(rel), json.dumps(result["rows"])))))
    assert answers[0] == answers[1]
    if requirement == "fastlib>=1":
        assert {r["version"] for r in answers[1][0]["rows"]} == {"1.0", "2.0"}


@pytest.mark.parametrize("requirement", ["fastlib==1.0", "fastlib>=1", "fastlib"])
@pytest.mark.parametrize("doc", [None, [], {}, {"info": {"version": "1.0"}}, {"urls": None},
    {"urls": "bad"}, {"urls": [None]}, {"urls": [{}]}, {"urls": [{"filename": None}]},
    {"releases": {"1.0": [{}]}}])
def test_trimming_bad_json_keeps_it_unknown(tmp_path, requirement, doc):
    (tmp_path / "requirements.txt").write_text(requirement + "\n", encoding="utf-8")
    docs = {"fastlib==1.0" if "==" in requirement else "fastlib": doc}
    before = json.loads(driver.check(str(tmp_path), "requirements.txt", options(), json.dumps(docs), configs(tmp_path)))
    after = json.loads(driver.check(str(tmp_path), "requirements.txt", options(), json.dumps(trim_json(docs)), configs(tmp_path)))
    assert before == after
    assert after["rows"][0]["gaps"] == [] and len(after["rows"][0]["unknown"]) == 3
    assert after["rows"][0]["lookup"] is False


@pytest.mark.parametrize("releases", [None, [], {}, {"1.0": None}, {"1.0": []}])
def test_trimming_preserves_empty_lists_and_range_fallback(tmp_path, releases):
    (tmp_path / "requirements.txt").write_text("fastlib>=1\n", encoding="utf-8")
    docs = {"fastlib": {"info": {"version": "1.0"}, "urls": [wheel("fastlib-1.0.tar.gz")], "releases": releases}}
    before = json.loads(driver.check(str(tmp_path), "requirements.txt", options(), json.dumps(docs), configs(tmp_path)))
    after = json.loads(driver.check(str(tmp_path), "requirements.txt", options(), json.dumps(trim_json(docs)), configs(tmp_path)))
    assert before == after
    # Non-mapping releases falls back to info.version + urls; an empty mapping does not.
    assert bool(after["rows"][0]["gaps"]) == (not isinstance(releases, dict))
    raw_releases = {"fastlib": releases}
    assert driver.fixes(json.dumps(raw_releases), json.dumps(after["rows"])) == driver.fixes(
        json.dumps(trim_json(raw_releases, releases=True)), json.dumps(after["rows"]))


def test_the_result_counts_the_packages_a_file_lists(tmp_path):
    # No dependencies is different from dependencies that aren't installed on the targets picked, which is an answer.
    (tmp_path / "pyproject.toml").write_text('[project]\nname = "demo"\nversion = "0.1.0"\n', encoding="utf-8")
    r = json.loads(driver.check(str(tmp_path), "pyproject.toml", options(), "{}", configs(tmp_path)))
    assert "error" not in r and (r["listed"], r["packages"], r["skipped"], r["rows"]) == (0, 0, [], [])
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname = "demo"\nversion = "0.1.0"\ndependencies = [\'winonly==1.0 ; sys_platform == "win32"\']\n',
        encoding="utf-8",
    )
    r = json.loads(driver.check(str(tmp_path), "pyproject.toml", options(), "{}", configs(tmp_path)))
    assert (r["listed"], r["packages"], r["skipped"], r["rows"]) == (1, 0, [], [])
    assert r["private_count"] == 0
