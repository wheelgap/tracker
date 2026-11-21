"""The post-scan filter never needs a live PyPI lookup."""

import json
import subprocess
import sys
from pathlib import Path

import pytest

from scripts import check_scan, drop_missing_names

ROOT = Path(__file__).resolve().parent.parent


def scan(missing=1):
    packages = [
        {"rank": i, "name": f"pkg{i}", "version": "1.0", "pure": False, "error": None,
         "results": ["wheel", "source"]}
        for i in range(1, 21)
    ]
    packages += [
        {"rank": 21 + i, "name": f"gone{i}", "version": None, "pure": False,
         "error": "not on index", "results": ["unknown", "unknown"]}
        for i in range(missing)
    ]
    return {
        "schema": 1, "generated": "2026-11-20T03:20:00Z", "wheelgap": "0.9.0",
        "targets": [{"label": "3.13 linux-x86_64"}, {"label": "3.13 win-arm64"}],
        "summary": {"3.13 linux-x86_64": {"wheel": 20, "unknown": missing},
                    "3.13 win-arm64": {"source": 20, "unknown": missing}},
        "packages": packages, "lookup_errors": missing,
    }


def write(tmp_path, data):
    path = tmp_path / "coverage.json"
    path.write_text(json.dumps(data, indent=2) + "\n\n", encoding="utf-8")
    return path


def run(path):
    return subprocess.run([sys.executable, str(ROOT / "scripts/drop_missing_names.py"), str(path)],
                          capture_output=True, text=True)


def test_missing_name_is_removed_from_every_summary_and_the_scan_still_passes(tmp_path):
    data = scan()
    # Removing a name in the middle of the ranking must not renumber the other packages.
    data["packages"][10]["error"] = "not on index"
    data["packages"][10]["results"] = ["unknown", "unknown"]
    data["packages"][-1].update(rank=22, name="kept", version="1.0", error=None, results=["wheel", "source"])
    original_packages = [p for p in data["packages"] if p["name"] != "pkg11"]
    path = write(tmp_path, data)
    result = run(path)
    assert result.returncode == 0, result.stderr
    saved = json.loads(path.read_text())
    assert saved["packages"] == original_packages
    assert saved["dropped_names"] == {"count": 1, "names": ["pkg11"]}
    assert saved["lookup_errors"] == 0
    assert saved["summary"] == {"3.13 linux-x86_64": {"wheel": 20, "unknown": 0},
                                "3.13 win-arm64": {"source": 20, "unknown": 0}}
    assert check_scan.problems(saved, [], top=21) == []
    validated = subprocess.run([sys.executable, str(ROOT / "scripts/check_scan.py"), str(path),
                                str(tmp_path / "no-history.json"), "21"], capture_output=True, text=True)
    assert validated.returncode == 0, validated.stderr
    once = path.read_bytes()
    assert drop_missing_names.drop(path) == 0
    assert path.read_bytes() == once


@pytest.mark.parametrize("error", ["timeout", "HTTP 503", "not on index: temporary error"])
def test_other_lookup_errors_are_kept(tmp_path, error):
    data = scan(2)
    data["packages"][-1]["error"] = error
    path = write(tmp_path, data)
    assert drop_missing_names.drop(path) == 1
    saved = json.loads(path.read_text())
    assert saved["packages"][-1] == data["packages"][-1]
    assert saved["lookup_errors"] == 1
    assert all(summary["unknown"] == 1 for summary in saved["summary"].values())


def test_lookup_errors_can_be_counted_when_the_field_is_absent(tmp_path):
    data = scan()
    del data["lookup_errors"]
    path = write(tmp_path, data)
    assert drop_missing_names.drop(path) == 1
    assert json.loads(path.read_text())["lookup_errors"] == 0


def test_more_than_the_limit_is_refused_without_rewriting_the_scan(tmp_path):
    path = write(tmp_path, scan(26))
    original = path.read_bytes()
    result = run(path)
    assert result.returncode == 1
    assert "refusing to drop 26 names (limit 25)" in result.stderr
    assert path.read_bytes() == original


def test_the_limit_is_allowed(tmp_path):
    path = write(tmp_path, scan(25))
    assert drop_missing_names.drop(path) == 25
    saved = json.loads(path.read_text())
    assert saved["dropped_names"]["count"] == 25
    assert check_scan.problems(saved, [], top=45) == []


def test_scan_with_nothing_to_drop_is_byte_for_byte_unchanged(tmp_path):
    data = scan(1)
    data["packages"][-1]["error"] = "timeout"
    path = write(tmp_path, data)
    original = path.read_bytes()
    assert run(path).returncode == 0
    assert path.read_bytes() == original
    assert "dropped_names" not in json.loads(path.read_text())
