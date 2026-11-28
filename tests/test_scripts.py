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
    """A scan whose summary matches its packages, as check_scan requires. Without packages, it has
    at least 3, with wheels[j] of them having a wheel on target j and the rest building from source."""
    if packages is None:
        count = max(3, *wheels)
        packages = [
            package(i, f"pkg{i}", results=["wheel" if i <= n else "source" for n in wheels])
            for i in range(1, count + 1)
        ]
    summary = {label: {} for label in LABELS}
    for p in packages:
        for label, result in zip(LABELS, p.get("results", ()), strict=False):
            summary[label][result] = summary[label].get(result, 0) + 1
    return {
        "schema": 1,
        "generated": generated,
        "wheelgap": "0.7.0",
        "targets": [{"label": label} for label in LABELS],
        "summary": summary,
        "packages": packages,
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

    def test_history_is_capped_at_max_days(self):
        history = self.write("history.json", [
            {"date": f"2026-11-0{i}", "summary": {}} for i in range(1, 6)
        ])
        update_history.update(self.write("coverage.json", scan()), history, max_days=3)
        dates = [h["date"] for h in json.loads(history.read_text(encoding="utf-8"))]
        self.assertEqual(dates, ["2026-11-04", "2026-11-05", "2026-11-10"])


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

    def test_dropped_names_count_towards_the_minimum(self):
        data = scan(packages=[package(i, f"p{i}") for i in range(1, 19)])
        data["dropped_names"] = {"count": 1, "names": ["gone"]}
        self.assertEqual(check_scan.problems(data, [], top=20), [])
        self.assertTrue(check_scan.problems(data, [], top=21))

    def test_malformed_dropped_names_are_refused(self):
        for dropped in (
            None, [], {"count": 1}, {"count": 2, "names": ["gone"]},
            {"count": -1, "names": []}, {"count": True, "names": ["gone"]},
            {"count": 1, "names": [None]}, {"count": 1, "names": [""]},
            {"count": 2, "names": ["gone", "gone"]},
            {"count": 26, "names": [f"gone{i}" for i in range(26)]},
            {"count": 1, "names": ["pkg1"]},
        ):
            with self.subTest(dropped=dropped):
                data = scan()
                data["dropped_names"] = dropped
                self.assertTrue(any("dropped_names" in p for p in check_scan.problems(data, [], top=3)))

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

    def test_summary_that_disagrees_with_the_packages(self):
        data = scan()
        data["summary"][LABELS[1]]["wheel"] = 4
        self.assertEqual(check_scan.problems(data, [], top=3), [
            f"{LABELS[1]}: summary says 4 wheel, the packages have 1"
        ])

    def test_summary_kind_the_packages_do_not_have(self):
        data = scan()
        data["summary"][LABELS[0]]["missing"] = 2
        self.assertEqual(check_scan.problems(data, [], top=3), [
            f"{LABELS[0]}: summary says 2 missing, the packages have 0"
        ])

    def test_result_missing_from_the_summary_counts_as_zero(self):
        data = scan()
        del data["summary"][LABELS[1]]["source"]
        self.assertEqual(check_scan.problems(data, [], top=3), [
            f"{LABELS[1]}: summary says 0 source, the packages have 2"
        ])

    def test_zero_counts_in_the_summary_are_fine(self):
        data = scan()
        data["summary"][LABELS[0]].update(missing=0, unknown=0, bundled=0)
        self.assertEqual(check_scan.problems(data, [], top=3), [])

    def test_duplicate_normalized_name(self):
        data = scan(packages=[package(1, "Foo_Bar"), package(2, "other"), package(3, "foo.bar")])
        self.assertEqual(
            check_scan.problems(data, [], top=3), ["duplicate package: 'Foo_Bar' and 'foo.bar' are both 'foo-bar'"]
        )

    def test_unparseable_generated(self):
        for generated in ("yesterday", "", None, "2026-13-01T00:00:00"):
            with self.subTest(generated=generated):
                found = check_scan.problems(scan(generated=generated), [], top=3)
                self.assertEqual(found, [f"generated {generated!r} isn't an ISO 8601 timestamp"])

    def test_generated_that_does_not_start_with_its_date(self):
        # fromisoformat accepts these, but update_history.py would date the night by their first 10 characters.
        for generated in ("20261110T032000Z", "2026-W46-2T03:20:00"):
            with self.subTest(generated=generated):
                found = check_scan.problems(scan(generated=generated), [], top=3)
                self.assertEqual(found, [f"generated {generated!r} doesn't start with its date as YYYY-MM-DD"])
        for generated in ("2026-11-10T03:20:00Z", "2026-11-10 03:20:00+00:00", "2026-11-10"):
            with self.subTest(generated=generated):
                self.assertEqual(check_scan.problems(scan(generated=generated), [], top=3), [])

    def test_generated_before_the_last_history_entry(self):
        history = [{"date": "2026-11-11", "summary": {}}]
        found = check_scan.problems(scan(generated="2026-11-10T03:20:00+00:00"), history, top=3)
        self.assertEqual(found, ["generated 2026-11-10T03:20:00+00:00 is before the last history entry, 2026-11-11"])

    def test_generated_on_or_after_the_last_history_entry(self):
        for last in ("2026-11-10", "2026-11-09"):
            with self.subTest(last=last):
                history = [{"date": last, "summary": {}}]
                self.assertEqual(check_scan.problems(scan(generated="2026-11-10T23:59:00Z"), history, top=3), [])

    def test_committed_scan_passes(self):
        root = Path(__file__).resolve().parent.parent / "data"
        data = json.loads((root / "coverage.json").read_text(encoding="utf-8"))
        history = json.loads((root / "history.json").read_text(encoding="utf-8"))
        self.assertEqual(check_scan.problems(data, history, top=5000), [])


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
            {
                "schema", "name", "version", "rank", "pure", "error", "generated", "wheelgap", "results", "notes",
                "pyodide", "bundled", "builds", "changes", "drops",
            },
        )
        self.assertEqual((one["pyodide"], one["bundled"]), ({}, {}))  # no pyodide_locks in this scan
        self.assertEqual(one["notes"], [])  # no notes directory given
        self.assertEqual(one["builds"], [])  # no build lab results given
        self.assertEqual((one["changes"], one["drops"]), ([], []))  # no changes.json or drops.json given
        self.assertEqual(one["schema"], 1)
        self.assertEqual(one["name"], "typing-extensions")
        self.assertEqual(one["generated"], data["generated"])
        self.assertEqual(one["results"], {LABELS[0]: "wheel", LABELS[1]: "source"})
        two = json.loads((out / "zope-interface.json").read_text(encoding="utf-8"))
        self.assertEqual(two["error"], "lookup failed")
        self.assertEqual(two["results"], {LABELS[0]: "unknown", LABELS[1]: "n/a"})

    def test_build_lab_results_go_to_their_package(self):
        lab = {
            "schema": 1,
            "updated": "2026-11-15",
            "targets": {},
            "results": [
                {
                    "schema": 1, "name": "Typing_Extensions", "version": "1.0", "target": "3.14t win-arm64",
                    "runner": "windows-11-arm", "python": "3.14.2t", "outcome": "built", "seconds": 87,
                    "wheel": "cp314-cp314t-win_arm64", "extensions": 1, "import": "ok", "gil": "re-enabled",
                    "reason": None, "excerpt": "", "toolchains": ["cl"], "date": "2026-11-15",
                },
            ],
        }
        out = self.root / "packages"
        data = scan(packages=[package(1, "typing-extensions"), package(2, "other")])
        build_lookup.build(self.write("coverage.json", data), out, builds_path=self.write("builds.json", lab))
        one = json.loads((out / "typing-extensions.json").read_text(encoding="utf-8"))
        self.assertEqual(len(one["builds"]), 1)
        self.assertEqual(set(one["builds"][0]), set(build_lookup.BUILD_FIELDS))
        self.assertEqual((one["builds"][0]["gil"], one["builds"][0]["seconds"]), ("re-enabled", 87))
        self.assertEqual(json.loads((out / "other.json").read_text(encoding="utf-8"))["builds"], [])
        # lab.json, for the homepage, has only current releases' results: an older release's result is left out.
        lab["results"].append(dict(lab["results"][0], name="other", version="0.9"))
        build_lookup.build(self.write("coverage.json", data), out, builds_path=self.write("builds.json", lab))
        summary = json.loads((self.root / "lab.json").read_text(encoding="utf-8"))
        self.assertEqual([(r["name"], r["version"]) for r in summary["results"]], [("typing-extensions", "1.0")])

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
