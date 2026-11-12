"""Offline checks for check_scan.py, update_history.py and build_lookup.py (no network, no real scan)."""

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from scripts import build_lookup, check_scan, update_history

LABELS = ["3.13 linux-x86_64", "3.13 win-arm64"]


def package(rank, name, results=("wheel", "source"), error=None):
    return {
        "rank": rank,
        "name": name,
        "version": "1.0",
        "downloads": 100 - rank,
        "pure": False,
        "error": error,
        "results": list(results),
    }


def scan(packages=None, generated="2026-11-10T03:20:00+00:00", wheels=(3, 1)):
    return {
        "schema": 1,
        "generated": generated,
        "wheelgap": "0.7.0",
        "targets": [{"label": label} for label in LABELS],
        "summary": {label: {"wheel": n} for label, n in zip(LABELS, wheels, strict=True)},
        "packages": packages if packages is not None else [package(i, f"pkg{i}") for i in (1, 2, 3)],
    }


class TempDir(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)

    def write(self, name, value):
        path = self.root / name
        path.write_text(json.dumps(value), encoding="utf-8")
        return path


class UpdateHistoryTests(TempDir):
    def test_new_day_is_appended(self):
        history = self.write("history.json", [{"date": "2026-11-09", "summary": {}}])
        entry = update_history.update(self.write("coverage.json", scan()), history)
        saved = json.loads(history.read_text(encoding="utf-8"))
        self.assertEqual([h["date"] for h in saved], ["2026-11-09", "2026-11-10"])
        self.assertEqual(saved[-1], entry)
        self.assertEqual(entry["packages"], 3)
        self.assertEqual(entry["wheelgap"], "0.7.0")
        self.assertEqual(entry["summary"][LABELS[0]], {"wheel": 3})

    def test_missing_history_file_starts_one(self):
        history = self.root / "history.json"
        update_history.update(self.write("coverage.json", scan()), history)
        self.assertEqual([h["date"] for h in json.loads(history.read_text(encoding="utf-8"))], ["2026-11-10"])

    def test_same_day_is_replaced(self):
        history = self.write("history.json", [{"date": "2026-11-10", "packages": 1, "summary": {}}])
        update_history.update(self.write("coverage.json", scan()), history)
        saved = json.loads(history.read_text(encoding="utf-8"))
        self.assertEqual(len(saved), 1)
        self.assertEqual(saved[0]["packages"], 3)

    def test_entries_stay_sorted_by_date(self):
        history = self.write("history.json", [
            {"date": "2026-11-11", "summary": {}},
            {"date": "2026-11-08", "summary": {}},
        ])
        update_history.update(self.write("coverage.json", scan()), history)
        dates = [h["date"] for h in json.loads(history.read_text(encoding="utf-8"))]
        self.assertEqual(dates, ["2026-11-08", "2026-11-10", "2026-11-11"])


class CheckScanTests(unittest.TestCase):
    def setUp(self):
        patcher = mock.patch.dict(os.environ)
        patcher.start()
        self.addCleanup(patcher.stop)
        os.environ.pop("ALLOW_DROP", None)

    def test_good_scan_has_no_problems(self):
        history = [{"date": "2026-11-09", "summary": {LABELS[0]: {"wheel": 3}}}]
        self.assertEqual(check_scan.problems(scan(), history, top=3), [])

    def test_too_few_packages(self):
        found = check_scan.problems(scan(), [], top=100)
        self.assertEqual(found, ["only 3 packages, expected about 100"])

    def test_missing_summary_label(self):
        data = scan()
        del data["summary"][LABELS[1]]
        found = check_scan.problems(data, [], top=3)
        self.assertEqual(len(found), 1)
        self.assertIn("targets without a summary", found[0])
        self.assertIn(LABELS[1], found[0])

    def test_no_targets_at_all(self):
        data = scan()
        data["targets"] = []
        found = check_scan.problems(data, [], top=3)
        self.assertTrue(any("no targets at all" in p for p in found), found)

    def test_results_of_the_wrong_length(self):
        data = scan(packages=[package(1, "a"), package(2, "b", results=("wheel",)), package(3, "c")])
        self.assertEqual(check_scan.problems(data, [], top=3), ["b: 1 results for 2 targets"])

    def test_package_missing_fields(self):
        data = scan()
        del data["packages"][0]["results"]
        self.assertEqual(check_scan.problems(data, [], top=3), ["package entry missing fields: 'pkg1'"])

    def test_wrong_schema(self):
        data = scan()
        data["schema"] = 2
        found = check_scan.problems(data, [], top=3)
        self.assertEqual(len(found), 1)
        self.assertIn("schema 2", found[0])

    def test_too_many_lookup_errors(self):
        data = scan(packages=[package(i, f"p{i}", error="timeout" if i <= 2 else None) for i in range(1, 21)])
        self.assertEqual(check_scan.problems(data, [], top=20), ["2 of 20 lookups failed"])

    def test_lookup_errors_count_from_the_scan_wins(self):
        # Real scans record lookup_errors, which takes precedence over counting packages with an error.
        data = scan(packages=[package(i, f"p{i}") for i in range(1, 21)])
        data["lookup_errors"] = 2
        self.assertEqual(check_scan.problems(data, [], top=20), ["2 of 20 lookups failed"])

    def test_large_drop_in_wheels(self):
        history = [{"date": "2026-11-09", "summary": {LABELS[0]: {"wheel": 100}, LABELS[1]: {"wheel": 1}}}]
        found = check_scan.problems(scan(wheels=(89, 1)), history, top=3)
        self.assertEqual(len(found), 1)
        self.assertIn(f"{LABELS[0]}: wheels fell from 100 to 89", found[0])

    def test_small_drop_is_fine(self):
        history = [{"date": "2026-11-09", "summary": {LABELS[0]: {"wheel": 100}}}]
        self.assertEqual(check_scan.problems(scan(wheels=(90, 1)), history, top=3), [])

    def test_allow_drop_skips_the_drop_check(self):
        os.environ["ALLOW_DROP"] = "true"
        history = [{"date": "2026-11-09", "summary": {LABELS[0]: {"wheel": 100}}}]
        self.assertEqual(check_scan.problems(scan(wheels=(10, 1)), history, top=3), [])


class BuildLookupTests(TempDir):
    def test_one_file_per_package_with_the_promised_fields(self):
        data = scan(packages=[
            package(1, "Typing_Extensions"),
            package(2, "zope.interface", results=("unknown", "n/a"), error="lookup failed"),
        ])
        out = self.root / "site" / "data" / "packages"
        self.assertEqual(build_lookup.build(self.write("coverage.json", data), out), 2)
        self.assertEqual(sorted(p.name for p in out.iterdir()), ["typing-extensions.json", "zope-interface.json"])

        one = json.loads((out / "typing-extensions.json").read_text(encoding="utf-8"))
        # The fields the README's "API for scripts and agents" section promises.
        self.assertEqual(
            set(one),
            {"schema", "name", "version", "rank", "pure", "error", "generated", "wheelgap", "results", "notes"},
        )
        self.assertEqual(one["notes"], [])  # no notes directory given
        self.assertEqual(one["schema"], 1)
        self.assertEqual(one["name"], "typing-extensions")
        self.assertEqual(one["generated"], data["generated"])
        self.assertEqual(one["results"], {LABELS[0]: "wheel", LABELS[1]: "source"})
        two = json.loads((out / "zope-interface.json").read_text(encoding="utf-8"))
        self.assertEqual(two["error"], "lookup failed")
        self.assertEqual(two["results"], {LABELS[0]: "unknown", LABELS[1]: "n/a"})

    def test_index_lists_every_package_and_the_targets(self):
        out = self.root / "packages"
        build_lookup.build(self.write("coverage.json", scan()), out)
        index = json.loads((self.root / "packages.json").read_text(encoding="utf-8"))
        self.assertEqual(index["schema"], 1)
        self.assertEqual(index["packages"], {"pkg1": "1.0", "pkg2": "1.0", "pkg3": "1.0"})
        self.assertEqual(index["targets"], [{"label": label} for label in LABELS])

    def test_unsafe_and_duplicate_names_are_skipped(self):
        data = scan(packages=[package(1, "foo"), package(2, "Foo"), package(3, "../evil")])
        out = self.root / "packages"
        with mock.patch("sys.stderr"):
            self.assertEqual(build_lookup.build(self.write("coverage.json", data), out), 1)
        self.assertEqual([p.name for p in out.iterdir()], ["foo.json"])

    def test_wrong_scan_schema_stops(self):
        data = scan()
        data["schema"] = 2
        with self.assertRaises(SystemExit):
            build_lookup.build(self.write("coverage.json", data), self.root / "packages")


if __name__ == "__main__":
    unittest.main()
