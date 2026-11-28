"""The Data API (#270): `changes` and `drops` in data/packages/<name>.json (build_lookup.py), and the api/ page."""

import json
import re
from pathlib import Path

import pytest

from scripts import build_api, build_campaigns, build_lookup

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
LABELS = ["3.14 linux-aarch64", "3.14 macos-arm64", "3.14 win-arm64"]
CHANGE_KEYS = {"date", "from", "target", "to", "version"}
DROP_KEYS = {"date", "from", "target", "version"}


def scan(packages, labels=LABELS):
    return {
        "schema": 1,
        "generated": "2026-11-27T03:20:00+00:00",
        "wheelgap": "0.14.0",
        "targets": [{"label": label, "python": label.split()[0], "platform": label.split()[1]} for label in labels],
        "packages": [
            {"rank": i, "name": name, "version": version, "pure": False, "error": None, "results": list(results)}
            for i, (name, version, results) in enumerate(packages, 1)
        ],
    }


def drops_file(drops):
    return {"schema": 1, "wheelgap": "0.14.0", "drops": drops, "waiting": []}


def build(tmp_path, coverage, changes=None, drops=None):
    """Write the inputs (a str is written as is), run build_lookup, and return the data/packages folder."""
    for name, value in (("coverage.json", coverage), ("changes.json", changes), ("drops.json", drops)):
        if value is not None:
            (tmp_path / name).write_text(value if isinstance(value, str) else json.dumps(value), encoding="utf-8")
    out = tmp_path / "_site" / "data" / "packages"
    build_lookup.build(tmp_path / "coverage.json", out, changes_path=tmp_path / "changes.json",
                       drops_path=tmp_path / "drops.json")  # fmt: skip
    return out


def load(path):
    return json.loads(path.read_text(encoding="utf-8"))


def check_logs(entry):
    """The shape of a package file's `changes` and `drops`: at most PER_TARGET per target each, newest first."""
    assert build_lookup.PER_TARGET == 3
    for items in (entry["changes"], entry["drops"]):
        per = {}
        for i in items:
            per[i["target"]] = per.get(i["target"], 0) + 1
        assert all(n <= build_lookup.PER_TARGET for n in per.values())
    assert all(set(c) == CHANGE_KEYS for c in entry["changes"])
    assert all(set(d) == DROP_KEYS for d in entry["drops"])
    for items in (entry["changes"], entry["drops"]):
        dates = [i["date"] for i in items]
        assert dates == sorted(dates, reverse=True)


def assert_kept(kept, logged):
    """Checked without the code under test: for each target, every logged entry is kept when the target has at most
    PER_TARGET, and otherwise exactly PER_TARGET are, none older than one left out."""
    key = lambda i: json.dumps(i, sort_keys=True)  # noqa: E731
    assert {key(i) for i in kept} <= {key(i) for i in logged}
    for target in {i["target"] for i in logged}:
        mine = [i for i in logged if i["target"] == target]
        got = [i for i in kept if i["target"] == target]
        assert len(got) == min(len(mine), build_lookup.PER_TARGET), target
        left_out = [i for i in mine if key(i) not in {key(g) for g in got}]
        if left_out:
            assert min(g["date"] for g in got) >= max(i["date"] for i in left_out), target


@pytest.fixture(scope="module")
def real(tmp_path_factory):
    """data/packages/ built from the repository's own data/, as the publish step does."""
    out = tmp_path_factory.mktemp("site") / "data" / "packages"
    build_lookup.build(DATA / "coverage.json", out, builds_path=DATA / "builds.json",
                       changes_path=DATA / "changes.json", drops_path=DATA / "drops.json")  # fmt: skip
    coverage = load(DATA / "coverage.json")
    return out, {build_campaigns.canonical(p["name"]): p for p in coverage["packages"]}


def test_real_drops_match_drops_json(real):
    out, packages = real
    drops = load(DATA / "drops.json")["drops"]
    dropped = [d for d in drops if build_campaigns.canonical(d["name"]) in packages]
    if not dropped:
        pytest.skip("drops.json has no drop for a package in the current scan")
    name = build_campaigns.canonical(dropped[0]["name"])  # drops.json is newest first
    entry = load(out / f"{name}.json")
    check_logs(entry)
    expected = [{k: d[k] for k in DROP_KEYS} for d in drops if build_campaigns.canonical(d["name"]) == name]
    assert_kept(entry["drops"], expected)
    assert entry["drops"][0]["date"] == dropped[0]["date"]
    assert entry["version"] == packages[name]["version"]


def test_real_changes_match_changes_json(real):
    out, packages = real
    days = load(DATA / "changes.json")
    named = {build_campaigns.canonical(i["name"]) for day in days for kind in ("gained", "lost") for i in day[kind]}
    for name in sorted(named & set(packages))[:5]:
        entry = load(out / f"{name}.json")
        check_logs(entry)
        expected = [
            {"date": day["date"], **{k: i[k] for k in ("from", "target", "to", "version")}}
            for day in days for kind in ("gained", "lost") for i in day[kind]
            if build_campaigns.canonical(i["name"]) == name
        ]  # fmt: skip
        assert_kept(entry["changes"], expected)


def test_real_numpy_and_every_file_have_both_lists(real):
    out, packages = real
    numpy = load(out / "numpy.json")
    check_logs(numpy)
    assert numpy["schema"] == 1
    for path in out.iterdir():
        entry = load(path)
        assert isinstance(entry["changes"], list) and isinstance(entry["drops"], list)


def test_names_in_the_logs_are_normalised(tmp_path):
    coverage = scan([("Foo_Bar", "1.0", ["wheel", "source", "missing"])])
    changes = [{"date": "2026-11-26", "gained": [
        {"name": "foo.bar", "version": "1.0", "target": "3.14 linux-aarch64", "from": "source", "to": "wheel"}
    ], "lost": []}]  # fmt: skip
    drops = [{"date": "2026-11-25", "name": "FOO-bar", "from": "0.9", "version": "1.0", "target": "3.14 win-arm64"}]
    entry = load(build(tmp_path, coverage, changes, drops_file(drops)) / "foo-bar.json")
    assert entry["changes"] == [{"date": "2026-11-26", "from": "source", "target": "3.14 linux-aarch64", "to": "wheel",
                                 "version": "1.0"}]  # fmt: skip
    assert entry["drops"] == [{"date": "2026-11-25", "from": "0.9", "target": "3.14 win-arm64", "version": "1.0"}]


def test_recent_drop_the_limit_and_the_order(tmp_path):
    coverage = scan([("pkg", "2.0", ["wheel", "source", "missing"]), ("other", "1.0", ["wheel"] * 3)])
    changes = [
        {"date": f"2026-11-{day:02d}", "gained": [], "lost": [
            {"name": "pkg", "version": "2.0", "target": label, "from": "wheel", "to": "source"} for label in LABELS
        ]}
        for day in range(1, 11)
    ]  # 30 changes, oldest first as update_changes.py writes them  # fmt: skip
    drops = [{"date": f"2026-11-{day:02d}", "name": "pkg", "from": "1.9", "version": "2.0", "target": label}
             for day in range(28, 0, -1) for label in reversed(LABELS[1:])]  # 56, newest first  # fmt: skip
    out = build(tmp_path, coverage, changes, drops_file(drops))
    entry = load(out / "pkg.json")
    check_logs(entry)
    assert (len(entry["changes"]), len(entry["drops"])) == (9, 6)  # 3 per target: 3 targets changed, 2 dropped
    assert entry["changes"][:2] == [
        {"date": "2026-11-10", "from": "wheel", "target": "3.14 linux-aarch64", "to": "source", "version": "2.0"},
        {"date": "2026-11-10", "from": "wheel", "target": "3.14 macos-arm64", "to": "source", "version": "2.0"},
    ]
    assert entry["changes"][-1]["date"] == "2026-11-08"  # each target's 7 oldest are left out
    assert entry["drops"][:2] == [  # by target within a day, whatever order the log has
        {"date": "2026-11-28", "from": "1.9", "target": "3.14 macos-arm64", "version": "2.0"},
        {"date": "2026-11-28", "from": "1.9", "target": "3.14 win-arm64", "version": "2.0"},
    ]
    assert entry["drops"][-1]["date"] == "2026-11-26"
    other = load(out / "other.json")
    assert (other["changes"], other["drops"]) == ([], [])


def test_one_release_changing_every_target_is_kept_whole(tmp_path):
    # Review of #272: fonttools 4.69.0 lost 40 targets in one scan; a cap per package cut that day short.
    labels = [f"3.{py} {plat}" for py in (13, 14) for plat in (f"plat-{n:02d}" for n in range(21))]  # 42 targets
    coverage = scan([("pkg", "2.0", ["wheel"] * len(labels))], labels=labels)
    lost = {"date": "2026-11-25", "gained": [], "lost": [
        {"name": "pkg", "version": "1.9", "target": t, "from": "wheel", "to": "source"} for t in labels]}
    gained = {"date": "2026-11-26", "gained": [
        {"name": "pkg", "version": "2.0", "target": t, "from": "source", "to": "wheel"} for t in labels], "lost": []}
    entry = load(build(tmp_path, coverage, [lost, gained]) / "pkg.json")
    assert len(entry["changes"]) == 84
    assert {c["target"] for c in entry["changes"] if c["date"] == "2026-11-25"} == set(labels)
    assert [c["target"] for c in entry["changes"][:42]] == sorted(labels)  # newest day first, by target


def test_within_a_day_entries_are_ordered_by_target_whatever_else_differs(tmp_path):
    coverage = scan([("pkg", "2.0", ["wheel"] * 3)])
    drops = [{"date": "2026-11-20", "name": "pkg", "from": "1.0", "version": "2.0", "target": "3.14 win-arm64"},
             {"date": "2026-11-20", "name": "pkg", "from": "1.1", "version": "2.0", "target": "3.14 linux-aarch64"}]  # fmt: skip
    entry = load(build(tmp_path, coverage, drops=drops_file(drops)) / "pkg.json")
    assert [d["target"] for d in entry["drops"]] == ["3.14 linux-aarch64", "3.14 win-arm64"]


def test_missing_or_malformed_logs_warn_and_carry_on(tmp_path, capsys):
    coverage = scan([("pkg", "1.0", ["wheel"] * 3)])
    entry = load(build(tmp_path, coverage) / "pkg.json")  # neither log exists: no warning
    assert (entry["changes"], entry["drops"]) == ([], [])
    assert "::warning::" not in capsys.readouterr().out
    entry = load(build(tmp_path, coverage, changes={"not": "a list"}, drops="{not json") / "pkg.json")
    assert (entry["changes"], entry["drops"]) == ([], [])
    out = capsys.readouterr().out
    assert "changes.json: isn't a JSON list" in out and "drops.json: isn't readable JSON" in out
    build(tmp_path, coverage, drops={"schema": 2, "drops": []})
    assert "isn't a schema 1 drops file" in capsys.readouterr().out
    drops = [{"date": "2026-11-25", "name": "pkg", "from": "0.9", "version": "1.0", "target": "3.14 win-arm64"},
             {"date": "soon", "name": "pkg", "from": "0.9", "version": "1.0", "target": "3.14 win-arm64"},
             {"name": "pkg"}, "junk"]  # fmt: skip
    changes = [{"date": "2026-11-26", "gained": [
        {"name": "pkg", "version": "1.0", "target": "3.14 win-arm64", "from": "source", "to": "wheel"},
        {"name": "pkg", "version": "1.0", "target": "3.14 win-arm64", "from": None, "to": "wheel"},
    ], "lost": "nope"}]  # fmt: skip
    entry = load(build(tmp_path, coverage, changes, drops_file(drops)) / "pkg.json")
    assert len(entry["drops"]) == 1
    assert entry["changes"] == []  # the day isn't well formed, so read_changes leaves it out
    assert "left out 3 malformed drop(s)" in capsys.readouterr().out
    changes[0]["lost"] = []
    entry = load(build(tmp_path, coverage, changes, drops_file(drops)) / "pkg.json")
    assert [c["from"] for c in entry["changes"]] == ["source"]  # the item with a null `from` is left out


def test_same_data_gives_the_same_bytes(tmp_path):
    coverage = scan([("pkg", "1.0", ["wheel", "source", "missing"])])
    changes = [{"date": "2026-11-26", "gained": [
        {"name": "pkg", "version": "1.0", "target": label, "from": "source", "to": "wheel"} for label in LABELS
    ], "lost": []}]  # fmt: skip
    first = (build(tmp_path, coverage, changes) / "pkg.json").read_bytes()
    changes[0]["gained"].reverse()  # the log's order within a day doesn't matter
    second = (build(tmp_path, coverage, changes) / "pkg.json").read_bytes()
    assert first == second
    for item in json.loads(first)["changes"]:
        assert list(item) == sorted(item)  # each entry's keys are sorted


def test_readme_and_page_example_is_what_the_build_writes(tmp_path):
    coverage = scan([("example-pkg", "2.1.0", ["wheel", "source", "missing"])])
    changes = [{"date": "2026-11-26", "gained": [{"name": "example-pkg", **build_api.EXAMPLE["changes"][0]}],
                "lost": []}]  # fmt: skip
    drops = [{"name": "example-pkg", **build_api.EXAMPLE["drops"][0]}]
    entry = load(build(tmp_path, coverage, changes, drops_file(drops)) / "example-pkg.json")
    assert {k: entry[k] for k in ("changes", "drops")} == build_api.EXAMPLE
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    section = readme.split("## API for scripts and agents\n", 1)[1].split("\n## ", 1)[0]
    block = re.search(r"```json\n(.*?)```", section, re.S).group(1)
    assert json.loads(block) == build_api.EXAMPLE
    assert "`changes`: for each target, the package's 3 newest" in section
    assert "`drops`: for each target, its 3 newest" in section
    assert "(https://wheelgap.github.io/tracker/api/)" in section
    assert "## Data API" not in readme  # one section documents the files


def test_docs_page_and_sitemap(tmp_path):
    site = tmp_path / "_site"
    site.mkdir()
    (site / "sitemap.xml").write_text(
        '<?xml version="1.0" encoding="UTF-8"?>\n<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
        "  <url><loc>https://wheelgap.github.io/tracker/</loc></url>\n</urlset>\n", encoding="utf-8")  # fmt: skip
    build_api.build(site)
    page = (site / "api/index.html").read_text(encoding="utf-8")
    assert '<h2 id="data-api">Data API</h2>' in page
    assert page.count("curl ") == 1
    assert "curl -fsS https://wheelgap.github.io/tracker/data/packages/numpy.json" in page
    for endpoint in ("data/packages/&lt;name&gt;.json", "data/packages.json", "data/pyodide.json", "badge/&lt;name&gt;.json"):
        assert endpoint in page
    assert "<code>changes</code>" in page and "<code>drops</code>" in page and "for each target, its 3 newest" in page
    assert '<link rel="canonical" href="https://wheelgap.github.io/tracker/api/">' in page
    assert build_campaigns.FOOTER in page
    assert "<script" not in page
    assert "api/v1" not in page
    sitemap = (site / "sitemap.xml").read_text(encoding="utf-8")
    assert sitemap.count("<loc>https://wheelgap.github.io/tracker/api/</loc>") == 1
    build_api.build(site)  # a second run doesn't list it twice
    assert (site / "sitemap.xml").read_text(encoding="utf-8").count("tracker/api/<") == 1


def test_publish_step_builds_the_page_after_the_package_pages():
    workflow = (ROOT / ".github/workflows/scan.yml").read_text(encoding="utf-8")
    step = workflow.split("- name: Assemble the site", 1)[1].split("\n      - ", 1)[0]
    assert step.index("scripts/build_pages.py") < step.index("if ! python3 scripts/build_api.py _site; then\n")
    assert "::warning::couldn't build the Data API page" in step  # it never stops the deploy
    assert "python3 scripts/build_lookup.py data/coverage.json _site/data/packages\n" in step
    assert (build_lookup.CHANGES, build_lookup.DROPS) == (DATA / "changes.json", DATA / "drops.json")
    assert '<a href="api/">Data API</a>' in (ROOT / "site/index.html").read_text(encoding="utf-8")
