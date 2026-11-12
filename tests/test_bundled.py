"""Offline checks that `bundled` (Pyodide ships its own build; wheelgap --pyodide-lock) counts as installable (#30)."""

import json
import tempfile
import unittest
from pathlib import Path

from scripts import build_badges, build_campaigns, build_pages, check_notes, check_scan, update_changes

TARGETS = [
    {"label": "3.14 linux-x86_64", "python": "3.14", "platform": "linux-x86_64"},
    {"label": "3.14 musl-aarch64", "python": "3.14", "platform": "musl-aarch64"},
    {"label": "3.14 pyodide", "python": "3.14", "platform": "pyodide"},
]
LABELS = [t["label"] for t in TARGETS]
PYODIDE = 2


def package(name, results, rank=1, version="1.0", error=None, pure=False):
    return {
        "rank": rank,
        "name": name,
        "version": version,
        "downloads": 1,
        "pure": pure,
        "error": error,
        "results": list(results),
    }


def scan(packages, wheelgap="0.9.0", generated="2026-11-13T03:20:00+00:00", locks=True):
    data = {
        "schema": 1,
        "generated": generated,
        "wheelgap": wheelgap,
        "targets": TARGETS,
        "summary": {label: {"wheel": 1} for label in LABELS},
        "packages": packages,
    }
    if locks:
        source = "https://cdn.jsdelivr.net/pyodide/v314.0.7/full/pyodide-lock.json"
        data["pyodide_locks"] = [{"python": "3.14", "pyodide_version": "?", "source": source}]
    return data


class CheckScanTests(unittest.TestCase):
    def test_bundled_on_a_pyodide_target_is_accepted(self):
        data = scan([package("numpy", ["wheel", "source", "bundled"])])
        self.assertEqual(check_scan.problems(data, [], top=1), [])

    def test_a_result_the_scripts_dont_know_stops_the_scan(self):
        data = scan([package("numpy", ["wheel", "source", "vendored"])])
        self.assertEqual(
            check_scan.problems(data, [], top=1), ["numpy: unexpected results ['vendored'] (teach the scripts first)"]
        )

    def test_bundled_off_pyodide_stops_the_scan(self):
        data = scan([package("numpy", ["bundled", "source", "wheel"])])
        self.assertEqual(check_scan.problems(data, [], top=1), ["numpy: bundled on a target that isn't pyodide"])


class UpdateChangesTests(unittest.TestCase):
    def test_wheel_and_bundled_either_way_is_no_change(self):
        old = scan([package("a", ["wheel", "wheel", "wheel"]), package("b", ["wheel", "wheel", "bundled"], rank=2)])
        new = scan([package("a", ["wheel", "wheel", "bundled"]), package("b", ["wheel", "wheel", "wheel"], rank=2)])
        self.assertEqual(update_changes.diff(old, new), ([], []))

    def test_bundled_to_a_gap_is_not_a_lost_wheel(self):
        # Pyodide dropping a package isn't a wheel lost on PyPI; neither direction involves `wheel`.
        old = scan([package("a", ["wheel", "wheel", "bundled"])])
        new = scan([package("a", ["wheel", "wheel", "missing"])])
        self.assertEqual(update_changes.diff(old, new), ([], []))

    def test_a_real_lost_wheel_is_still_recorded(self):
        old = scan([package("a", ["wheel", "wheel", "wheel"])])
        new = scan([package("a", ["wheel", "source", "missing"])])
        gained, lost = update_changes.diff(old, new)
        self.assertEqual(gained, [])
        self.assertEqual([(i["target"], i["from"], i["to"]) for i in lost], [
            ("3.14 musl-aarch64", "wheel", "source"),
            ("3.14 pyodide", "wheel", "missing"),
        ])

    def test_same_day_wheel_to_bundled_folds_to_no_change(self):
        earlier = {"date": "2026-11-13", "gained": [], "lost": [
            {"name": "a", "version": "1.0", "target": "3.14 pyodide", "from": "wheel", "to": "missing"},
        ]}
        later = [{"name": "a", "version": "1.1", "target": "3.14 pyodide", "from": "missing", "to": "bundled"}]
        self.assertEqual(update_changes.merge(earlier, [], later), ([], []))

    def run_update(self, old, new):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "previous.json").write_text(json.dumps(old), encoding="utf-8")
            (root / "coverage.json").write_text(json.dumps(new), encoding="utf-8")
            entry = update_changes.update(root / "previous.json", root / "coverage.json", root / "changes.json")
            return entry, json.loads((root / "changes.json").read_text(encoding="utf-8"))

    def test_switchover_from_an_older_wheelgap_is_skipped(self):
        old = scan([package("numpy", ["wheel", "source", "missing"])], wheelgap="0.7.0", locks=False)
        new = scan([package("numpy", ["wheel", "source", "bundled"])])
        entry, changes = self.run_update(old, new)
        self.assertIn("wheelgap 0.7.0 -> 0.9.0", entry)
        self.assertEqual(changes, [])

    def test_switchover_on_the_same_wheelgap_lists_nothing(self):
        # If a 0.9.0 night without --pyodide-lock comes first, the version skip doesn't apply, but the lock
        # only turns `source` and `missing` into `bundled`, which aren't changes to or from `wheel`.
        old = scan([
            package("numpy", ["wheel", "source", "missing"]),
            package("pyyaml", ["wheel", "wheel", "source"], rank=2),
            package("six", ["wheel", "wheel", "wheel"], rank=3, pure=True),
        ], locks=False)
        new = scan([
            package("numpy", ["wheel", "source", "bundled"]),
            package("pyyaml", ["wheel", "wheel", "bundled"], rank=2),
            package("six", ["wheel", "wheel", "wheel"], rank=3, pure=True),
        ])
        entry, changes = self.run_update(old, new)
        self.assertEqual(entry, {"date": "2026-11-13", "gained": [], "lost": []})
        self.assertEqual(changes, [entry])


class BadgeTests(unittest.TestCase):
    def test_bundled_counts_as_installable(self):
        badge = build_badges.badge_for(package("numpy", ["wheel", "wheel", "bundled"]), [0, 1, 2], "3.14")
        self.assertEqual((badge["message"], badge["color"]), ("3/3 platforms", "brightgreen"))


class NotesTests(unittest.TestCase):
    NOTE = {"platforms": ["pyodide"], "kind": "workaround", "text": "t", "links": ["https://e.x"]}

    def test_pyodide_note_retires_once_pyodide_bundles_the_package(self):
        self.assertEqual(check_notes.current([self.NOTE], package("pkg", ["wheel", "source", "bundled"]), TARGETS), [])

    def test_pyodide_note_stays_while_pyodide_has_neither(self):
        pkg = package("pkg", ["wheel", "source", "missing"])
        self.assertEqual(check_notes.current([self.NOTE], pkg, TARGETS), [self.NOTE])


class PageTests(unittest.TestCase):
    def build(self, packages):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "coverage.json").write_text(json.dumps(scan(packages)), encoding="utf-8")
            build_pages.build(root / "coverage.json", root / "site")
            return {p.parent.name: p.read_text(encoding="utf-8") for p in (root / "site" / "p").glob("*/index.html")}

    def test_bundled_is_shown_and_explained_and_not_a_gap(self):
        pages = self.build([
            package("numpy", ["wheel", "wheel", "bundled"]),
            package("plain", ["wheel", "wheel", "wheel"], rank=2),
        ])
        self.assertIn('<span class="status s-bundled">bundled</span>', pages["numpy"])
        self.assertIn(build_pages.BUNDLED_NOTE, pages["numpy"])
        self.assertNotIn("Add a note", pages["numpy"])  # nothing left to fix
        self.assertNotIn(build_pages.BUNDLED_NOTE, pages["plain"])


class CampaignTests(unittest.TestCase):
    """Campaigns are on native platforms today, where `bundled` never appears; on a pyodide target it counts as done."""

    def test_bundled_counts_as_having_the_wheel(self):
        packages = [
            package("numpy", ["wheel", "wheel", "bundled"]),
            package("gap", ["wheel", "wheel", "missing"], rank=2),
        ]
        self.assertTrue(build_campaigns.has_wheel(packages[0], PYODIDE))
        self.assertEqual(build_campaigns.coverage_counts(packages, PYODIDE), (1, 2))
        self.assertFalse(build_campaigns.not_started(packages[0], [PYODIDE]))

    def test_a_bundled_package_on_the_starting_list_is_done(self):
        baseline = {"start": "2026-11-10", "packages": [{"name": "numpy", "status": "missing"}, {"name": "gap"}]}
        packages = [
            package("numpy", ["wheel", "wheel", "bundled"]),
            package("gap", ["wheel", "wheel", "missing"], rank=2),
        ]
        done = build_campaigns.done_list(baseline, packages, PYODIDE, "3.14 pyodide", [])
        self.assertEqual([(r["name"], r["frm"]) for r in done], [("numpy", "missing")])


if __name__ == "__main__":
    unittest.main()
