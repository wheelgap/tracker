import json
import subprocess
from pathlib import Path
from unittest.mock import Mock

from scripts import scan_install


def target(python, platform):
    return {
        "python": python, "platform": platform, "label": f"{python} {platform}",
        "glibc": "2.36", "musl": "1.2", "macos": "14.0",
    }


def test_check_includes_transitive_blockers_and_unknown(monkeypatch):
    targets = [target("3.14", "win-arm64"), target("3.14", "macos-arm64"), target("3.14", "linux-x86_64")]
    invoked = []

    def run(args, **kwargs):
        invoked.append(args)
        return subprocess.CompletedProcess(args, 1, json.dumps({
            "targets": [{**t, "label": t["platform"]} for t in targets],
            "packages": [
                {"name": "sample", "version": "1.0", "results": dict.fromkeys([t["platform"] for t in targets], "wheel")},
                {"name": "conditional", "version": "2.0",
                 "results": {"win-arm64": "source", "macos-arm64": "unknown", "linux-x86_64": "n/a"},
                 "via": {"win-arm64": ["sample", "middle", "conditional"]}},
            ],
        }))

    monkeypatch.setattr(scan_install.subprocess, "run", run)
    results = scan_install.check({"name": "sample", "version": "1.0"}, targets, float("inf"))
    assert results == {
        "3.14 win-arm64": {"status": "source", "blockers": [{
            "name": "conditional", "version": "2.0", "status": "source", "via": ["sample", "middle", "conditional"],
        }]},
        "3.14 macos-arm64": {"status": "not_checked", "blockers": []},
        "3.14 linux-x86_64": {"status": "wheel", "blockers": []},
    }
    assert len(invoked) == 1
    assert invoked[0][3] == "sample==1.0"
    assert invoked[0][invoked[0].index("--macos") + 1] == "14.0"


def test_check_handles_timeout_and_refused_resolution(monkeypatch):
    targets = [target("3.14", "win-arm64"), target("3.14t", "win-arm64")]
    failures = [subprocess.TimeoutExpired("wheelgap", 1), subprocess.CompletedProcess([], 2, '{"error": {}}')]
    monkeypatch.setattr(scan_install.subprocess, "run", Mock(side_effect=failures))
    assert scan_install.check({"name": "sample", "version": "1.0"}, targets, float("inf")) == {
        t["label"]: {"status": "not_checked", "blockers": []} for t in targets
    }


def test_root_must_be_checked_at_snapshot_version():
    assert scan_install.coverage({"packages": [
        {"name": "sample", "version": "2.0", "results": {"win-arm64": "wheel"}},
    ]}, "sample", "1.0", "win-arm64")["status"] == "not_checked"
    assert scan_install.coverage({"packages": []}, "sample", "1.0", "win-arm64")["status"] == "not_checked"


def test_scan_adds_only_ranked_packages_and_chosen_targets(tmp_path, monkeypatch):
    path = tmp_path / "coverage.json"
    scan = {
        "schema": 1, "targets": [target("3.14", "win-arm64"), target("3.15", "win-arm64"), target("3.14t", "linux-x86_64")],
        "packages": [{"rank": 2, "name": "second"}, {"rank": 1, "name": "first"}], "summary": {"foo": {}},
    }
    path.write_text(json.dumps(scan), encoding="utf-8")
    monkeypatch.setattr(scan_install, "check", lambda package, targets, _deadline: {
        t["label"]: {"status": "wheel", "blockers": []} for t in targets
    })
    assert scan_install.scan(path, 1) == 1
    result = json.loads(path.read_text(encoding="utf-8"))
    assert result["summary"] == scan["summary"]
    assert "install" not in result["packages"][0]
    assert set(result["packages"][1]["install"]) == {"3.14 win-arm64", "3.14t linux-x86_64"}


def test_scan_leaves_out_packages_with_nothing_checked(tmp_path, monkeypatch):
    path = tmp_path / "coverage.json"
    scan = {"schema": 1, "targets": [target("3.14", "win-arm64")], "packages": [{"rank": 1, "name": "late"}]}
    path.write_text(json.dumps(scan), encoding="utf-8")
    monkeypatch.setattr(scan_install, "check", lambda package, targets, _deadline: {
        t["label"]: scan_install.not_checked() for t in targets
    })
    assert scan_install.scan(path, 1) == 1
    assert "install" not in json.loads(path.read_text(encoding="utf-8"))["packages"][0]


def test_a_left_out_package_or_odd_path_is_not_checked():
    rows = [{"name": "sample", "version": "1.0", "results": {"win-arm64": "wheel"}}]
    for key in ("ignored", "ignored_targets", "skipped"):
        report = {"packages": rows, key: ["something"]}
        assert scan_install.coverage(report, "sample", "1.0", "win-arm64")["status"] == "not_checked", key
    for via in ("sample", [1], ["sample", "<b>"]):
        report = {"packages": rows + [
            {"name": "dep", "version": "2.0", "results": {"win-arm64": "source"}, "via": {"win-arm64": via}},
        ]}
        assert scan_install.coverage(report, "sample", "1.0", "win-arm64")["status"] == "not_checked", via


def test_check_runs_wheelgap_outside_the_repo(monkeypatch):
    seen = []

    def run(args, **kwargs):
        seen.append(kwargs.get("cwd"))
        return subprocess.CompletedProcess(args, 2, "")

    monkeypatch.setattr(scan_install.subprocess, "run", run)
    scan_install.check({"name": "sample", "version": "1.0"}, [target("3.14", "win-arm64")], float("inf"))
    assert seen and seen[0] is not None and not (Path(seen[0]) / "pyproject.toml").exists()


def test_scan_checks_only_the_314_lab_targets(tmp_path, monkeypatch):
    path = tmp_path / "coverage.json"
    labels = ["3.14 win-arm64", "3.15 linux-x86_64", "3.15 win-amd64", "3.14 linux-x86_64"]
    scan = {"schema": 1, "targets": [target(*label.split()) for label in labels], "packages": [{"rank": 1, "name": "a"}]}
    path.write_text(json.dumps(scan), encoding="utf-8")
    seen = []
    monkeypatch.setattr(scan_install, "check", lambda package, targets, _deadline: seen.extend(t["label"] for t in targets) or {})
    scan_install.scan(path, 1)
    assert seen == ["3.14 win-arm64", "3.14 linux-x86_64"]
