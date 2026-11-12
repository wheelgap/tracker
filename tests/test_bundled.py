"""Offline checks for `bundled` (Pyodide ships its own build; wheelgap --pyodide-lock) and the pinned locks (#30)."""

import hashlib
import json
import os
import re
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from scripts import (
    build_badges,
    build_campaigns,
    build_lookup,
    build_pages,
    check_notes,
    check_scan,
    pyodide_locks,
    update_changes,
)

REPO = Path(__file__).resolve().parent.parent
TARGETS = [
    {"label": "3.14 linux-x86_64", "python": "3.14", "platform": "linux-x86_64"},
    {"label": "3.14 musl-aarch64", "python": "3.14", "platform": "musl-aarch64"},
    {"label": "3.14 pyodide", "python": "3.14", "platform": "pyodide"},
]
LABELS = [t["label"] for t in TARGETS]
PYODIDE = 2
LOCK_FILE = "pyodide/314.0.7-pyodide-lock.json"
LOCK_URL = "https://cdn.jsdelivr.net/pyodide/v314.0.7/full/pyodide-lock.json"
LOCK = {
    "info": {"abi_version": "2026_0", "arch": "wasm32", "python": "3.14.2"},
    "packages": {
        "numpy": {"name": "numpy", "version": "2.4.6", "package_type": "package", "file_name": "numpy-2.4.6.whl"},
        "pyyaml": {"name": "PyYAML", "version": "6.0.3", "package_type": "package", "file_name": "pyyaml-6.0.3.whl"},
        "libopenblas": {"name": "libopenblas", "version": "0.3", "package_type": "shared_library"},
        "aiohttp-tests": {"name": "aiohttp-tests", "version": "3.9", "file_name": "aiohttp-tests.zip"},
    },
}


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
        data["pyodide_locks"] = [{"python": "3.14", "pyodide_version": "?", "source": LOCK_FILE}]
    return data


class Repo(unittest.TestCase):
    """A temporary repository root with pyodide/locks.json and one lock file, as scan.yml uses them."""

    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.write_lock(LOCK)

    def write_lock(self, lock, python="3.14", sha=None):
        raw = json.dumps(lock).encode()
        (self.root / "pyodide").mkdir(exist_ok=True)
        (self.root / LOCK_FILE).write_bytes(raw)
        entry = {"python": python, "pyodide": "314.0.7", "file": LOCK_FILE, "url": LOCK_URL,
                 "sha256": sha or hashlib.sha256(raw).hexdigest()}
        (self.root / "pyodide" / "locks.json").write_text(json.dumps({"schema": 1, "locks": [entry]}))

    def problems(self, data, history=()):
        with mock.patch.dict(os.environ):
            os.environ.pop("ALLOW_DROP", None)
            return check_scan.problems(data, list(history), top=len(data["packages"]), root=self.root)


class CheckScanTests(Repo):
    def test_bundled_with_its_lock_is_accepted(self):
        self.assertEqual(self.problems(scan([package("numpy", ["wheel", "source", "bundled"])])), [])

    def test_a_scan_without_locks_or_bundled_is_accepted(self):
        self.assertEqual(self.problems(scan([package("numpy", ["wheel", "source", "missing"])], locks=False)), [])

    def test_n_a_is_a_known_result(self):
        self.assertEqual(self.problems(scan([package("numpy", ["wheel", "n/a", "missing"])])), [])

    def test_a_result_the_scripts_dont_know_stops_the_scan(self):
        self.assertEqual(
            self.problems(scan([package("numpy", ["wheel", "source", "vendored"])])),
            ["numpy: unexpected results ['vendored'] (teach the scripts first)"],
        )

    def test_bundled_off_pyodide_stops_the_scan(self):
        self.assertEqual(
            self.problems(scan([package("numpy", ["bundled", "source", "wheel"])])),
            ["numpy: bundled on a target that isn't pyodide"],
        )

    def test_bundled_needs_a_lock_for_its_python(self):
        found = self.problems(scan([package("numpy", ["wheel", "source", "bundled"])], locks=False))
        self.assertEqual(found, ["3.14 pyodide: 1 bundled but no pyodide_locks entry from pyodide/locks.json"])

    def test_bundled_package_must_be_in_the_lock(self):
        # libopenblas is in the lock, but as a shared library: wheelgap couldn't have counted it.
        found = self.problems(scan([
            package("numpy", ["wheel", "source", "bundled"]),
            package("libopenblas", ["wheel", "wheel", "bundled"], rank=2),
        ]))
        self.assertEqual(found, ["3.14 pyodide: bundled but not in Pyodide 314.0.7's lock: libopenblas"])

    def test_a_lock_outside_the_manifest_stops_the_scan(self):
        data = scan([package("numpy", ["wheel", "source", "bundled"])])
        data["pyodide_locks"][0]["source"] = LOCK_URL  # a URL, not the committed file
        found = self.problems(data)
        self.assertIn(f"pyodide_locks: {LOCK_URL} (Python 3.14) isn't in pyodide/locks.json", found)
        self.assertIn("3.14 pyodide: 1 bundled but no pyodide_locks entry from pyodide/locks.json", found)

    def test_a_changed_lock_file_stops_the_scan(self):
        self.write_lock(LOCK, sha="0" * 64)
        found = self.problems(scan([package("numpy", ["wheel", "source", "bundled"])]))
        self.assertIn(f"pyodide_locks: {LOCK_FILE} doesn't match its sha256 in pyodide/locks.json", found)

    def test_a_lock_for_another_python_stops_the_scan(self):
        self.write_lock({**LOCK, "info": {"python": "3.13.2"}})
        found = self.problems(scan([package("numpy", ["wheel", "source", "bundled"])]))
        self.assertIn(f"pyodide_locks: {LOCK_FILE} is for Python 3.13, not 3.14", found)

    def test_malformed_pyodide_locks(self):
        for locks, message in (
            ("x", "pyodide_locks isn't a list"),
            ([{"python": "3.14", "source": LOCK_FILE}], "pyodide_locks entry without string python"),
            ([{"python": "3.14", "pyodide_version": "?", "source": LOCK_FILE}] * 2,
             "pyodide_locks has two entries for Python 3.14"),
        ):
            data = scan([package("numpy", ["wheel", "source", "missing"])])
            data["pyodide_locks"] = locks
            found = self.problems(data)
            self.assertEqual(len(found), 1, found)
            self.assertTrue(found[0].startswith(message), found)

    def test_a_large_drop_in_bundled_is_flagged_like_wheels(self):
        data = scan([package("numpy", ["wheel", "source", "bundled"])])
        data["summary"]["3.14 pyodide"] = {"wheel": 1, "bundled": 80}
        history = [{"date": "2026-11-12", "summary": {"3.14 pyodide": {"wheel": 1, "bundled": 100}}}]
        self.assertEqual(self.problems(data, history), [
            "3.14 pyodide: bundled fell from 100 to 80 (if that's real, run the workflow by hand with allow_drop)"
        ])
        data["summary"]["3.14 pyodide"]["bundled"] = 95
        self.assertEqual(self.problems(data, history), [])


class LockTests(unittest.TestCase):
    def test_lock_packages_uses_wheelgaps_filter_and_names(self):
        self.assertEqual(pyodide_locks.lock_packages(LOCK), {"numpy": "2.4.6", "pyyaml": "6.0.3"})

    def test_compare_with_the_scanned_release(self):
        self.assertEqual(pyodide_locks.compare("2.4", "2.4.0"), "same")
        self.assertEqual(pyodide_locks.compare("2.3.3", "3.1.1"), "older")
        self.assertEqual(pyodide_locks.compare("3.2", "3.1.1"), "different")  # newer than PyPI's latest: not "older"
        self.assertEqual(pyodide_locks.compare("2.0rc1", "2.0"), "different")
        self.assertEqual(pyodide_locks.compare("2.0rc1", "2.0rc1"), "same")
        self.assertEqual(pyodide_locks.compare("2.0", None), "different")

    def test_describe(self):
        self.assertEqual(
            pyodide_locks.describe({"pyodide": "0.29.5", "bundled_version": "2.3.3", "vs_latest": "older"}),
            "Pyodide 0.29.5 ships 2.3.3, older than the latest",
        )
        self.assertEqual(pyodide_locks.describe({"pyodide": None, "bundled_version": None, "vs_latest": None}), "")


class CommittedLockTests(unittest.TestCase):
    """The lock files scan.yml passes are the ones pyodide/locks.json lists, unchanged and for the right Python."""

    def test_manifest_entries_match_their_files(self):
        entries = pyodide_locks.load_manifest(REPO)
        self.assertTrue(entries)
        for entry in entries:
            raw = (REPO / entry["file"]).read_bytes()
            self.assertEqual(hashlib.sha256(raw).hexdigest(), entry["sha256"], entry["file"])
            self.assertEqual(pyodide_locks.lock_python(json.loads(raw)), entry["python"], entry["file"])
            self.assertEqual(entry["file"], f"pyodide/{entry['pyodide']}-pyodide-lock.json")
            url = f"https://cdn.jsdelivr.net/pyodide/v{entry['pyodide']}/full/pyodide-lock.json"
            self.assertEqual(entry["url"], url)

    def test_scan_yml_passes_each_manifest_lock_for_its_python(self):
        workflow = (REPO / ".github" / "workflows" / "scan.yml").read_text(encoding="utf-8")
        env = dict(re.findall(r'^  (PYODIDE_LOCK_\d+): "([^"]+)"$', workflow, re.MULTILINE))
        manifest = {e["file"]: e["python"] for e in pyodide_locks.load_manifest(REPO)}
        self.assertEqual(set(env.values()), set(manifest))
        for name, path in env.items():
            self.assertEqual(name, "PYODIDE_LOCK_" + manifest[path].replace(".", ""))
            self.assertIn(f'--pyodide-lock "${name}"', workflow)


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

    def run_update(self, root, old, new):
        (root / "previous.json").write_text(json.dumps(old), encoding="utf-8")
        (root / "coverage.json").write_text(json.dumps(new), encoding="utf-8")
        entry = update_changes.update(root / "previous.json", root / "coverage.json", root / "changes.json")
        return entry, json.loads((root / "changes.json").read_text(encoding="utf-8"))

    def test_a_wheel_lost_earlier_in_the_day_stays_lost_once_pyodide_covers_it(self):
        # Three scans on one day: wheel, then missing (a lost wheel), then bundled. diff() can't see
        # missing -> bundled, so the day keeps the loss: the PyPI wheel really went, even if Pyodide has it now.
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            first = scan([package("a", ["wheel", "wheel", "wheel"])], generated="2026-11-13T03:20:00+00:00")
            second = scan([package("a", ["wheel", "wheel", "missing"])], generated="2026-11-13T09:00:00+00:00")
            third = scan([package("a", ["wheel", "wheel", "bundled"])], generated="2026-11-13T15:00:00+00:00")
            self.run_update(root, first, second)
            self.assertEqual(update_changes.diff(second, third), ([], []))
            entry, changes = self.run_update(root, second, third)
        lost = [{"name": "a", "version": "1.0", "target": "3.14 pyodide", "from": "wheel", "to": "missing"}]
        self.assertEqual(entry, {"date": "2026-11-13", "gained": [], "lost": lost})
        self.assertEqual(changes, [entry])

    def test_switchover_from_an_older_wheelgap_is_skipped(self):
        with tempfile.TemporaryDirectory() as directory:
            old = scan([package("numpy", ["wheel", "source", "missing"])], wheelgap="0.7.0", locks=False)
            new = scan([package("numpy", ["wheel", "source", "bundled"])])
            entry, changes = self.run_update(Path(directory), old, new)
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
        with tempfile.TemporaryDirectory() as directory:
            entry, changes = self.run_update(Path(directory), old, new)
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


class LookupTests(Repo):
    def build(self, packages, locks=True):
        coverage = self.root / "coverage.json"
        coverage.write_text(json.dumps(scan(packages, locks=locks)), encoding="utf-8")
        out = self.root / "site" / "data" / "packages"
        build_lookup.build(coverage, out, root=self.root)
        files = {p.stem: json.loads(p.read_text(encoding="utf-8")) for p in out.glob("*.json")}
        return files, json.loads((out.parent / "pyodide.json").read_text(encoding="utf-8"))

    def test_bundled_cells_carry_pyodides_version_and_release(self):
        files, releases = self.build([
            package("NumPy", ["wheel", "wheel", "bundled"], version="2.5.0"),
            package("PyYAML", ["wheel", "wheel", "bundled"], rank=2, version="6.0.3"),
            package("plain", ["wheel", "wheel", "wheel"], rank=3),
        ])
        pyodide = {"3.14 pyodide": {"pyodide": "314.0.7", "lock": LOCK_URL}}
        self.assertEqual(files["numpy"]["pyodide"], pyodide)
        self.assertEqual(files["numpy"]["bundled"], {
            "3.14 pyodide": {"pyodide": "314.0.7", "bundled_version": "2.4.6", "vs_latest": "older"},
        })
        self.assertEqual(files["pyyaml"]["bundled"]["3.14 pyodide"]["vs_latest"], "same")
        self.assertEqual((files["plain"]["pyodide"], files["plain"]["bundled"]), (pyodide, {}))
        self.assertEqual(releases["pythons"], {"3.14": {"pyodide": "314.0.7", "lock": LOCK_URL}})

    def test_a_lock_that_doesnt_match_gives_no_version_rather_than_a_guess(self):
        self.write_lock(LOCK, sha="0" * 64)
        with mock.patch("sys.stderr"):
            files, releases = self.build([package("numpy", ["wheel", "wheel", "bundled"])])
        self.assertEqual(files["numpy"]["pyodide"], {})
        self.assertEqual(files["numpy"]["bundled"], {
            "3.14 pyodide": {"pyodide": None, "bundled_version": None, "vs_latest": None},
        })
        self.assertEqual(releases["pythons"], {})


class PageTests(Repo):
    def build(self, packages):
        coverage = self.root / "coverage.json"
        coverage.write_text(json.dumps(scan(packages)), encoding="utf-8")
        build_pages.build(coverage, self.root / "site", root=self.root)
        return {p.parent.name: p.read_text(encoding="utf-8") for p in (self.root / "site" / "p").glob("*/index.html")}

    def test_bundled_cell_names_pyodides_version_and_isnt_a_gap(self):
        pages = self.build([
            package("numpy", ["wheel", "wheel", "bundled"], version="2.5.0"),
            package("plain", ["wheel", "wheel", "wheel"], rank=2),
        ])
        self.assertIn(
            '<span class="status s-bundled">bundled</span>'
            '<small class="bver">Pyodide 314.0.7 ships 2.4.6, older than the latest</small>',
            pages["numpy"],
        )
        self.assertIn(build_pages.BUNDLED_NOTE.replace("'", "&#x27;"), pages["numpy"])
        self.assertNotIn("Add a note", pages["numpy"])  # nothing left to fix
        self.assertNotIn("bundled:", pages["plain"])

    def test_why_command_passes_the_pinned_lock(self):
        pages = self.build([package("numpy", ["wheel", "wheel", "bundled"])])
        self.assertIn(f"uvx wheelgap numpy==1.0 -p 3.14 -t all --explain --pyodide-lock {LOCK_URL}", pages["numpy"])

    def test_a_failed_lookup_gets_no_bundled_line(self):
        pages = self.build([package("numpy", ["unknown", "unknown", "bundled"], version=None, error="timeout")])
        self.assertNotIn("bundled:", pages["numpy"])
        self.assertNotIn('class="bver"', pages["numpy"])


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

    def test_trend_counts_bundled_like_the_coverage(self):
        history = [
            {"date": "2026-11-12", "wheelgap": "0.9.0", "packages": 100,
             "summary": {"3.14 pyodide": {"wheel": 80, "missing": 20}}},
            {"date": "2026-11-13", "wheelgap": "0.9.0", "packages": 100,
             "summary": {"3.14 pyodide": {"wheel": 80, "bundled": 5, "missing": 15}}},
        ]
        rows = build_campaigns.trend(history, "3.14 pyodide", "2026-11-12")
        self.assertEqual([(r["wheels"], r["percent"]) for r in rows], [(80, 80.0), (85, 85.0)])


if __name__ == "__main__":
    unittest.main()
