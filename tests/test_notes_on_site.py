"""Offline checks for notes in the per-package JSON and on the package pages (#28)."""

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock
from urllib.parse import parse_qs, urlsplit

from scripts import build_lookup, build_pages, check_notes

TARGETS = [
    {"label": "3.13 linux-x86_64", "python": "3.13", "platform": "linux-x86_64"},
    {"label": "3.13 musl-aarch64", "python": "3.13", "platform": "musl-aarch64"},
    {"label": "3.14t musl-aarch64", "python": "3.14t", "platform": "musl-aarch64"},
    {"label": "3.13 pyodide", "python": "3.13", "platform": "pyodide"},
]


def package(name, results, version="2.9.1", rank=1, error=None):
    return {
        "rank": rank,
        "name": name,
        "version": version,
        "downloads": 1,
        "pure": False,
        "error": error,
        "results": list(results),
    }


def scan(packages):
    return {
        "schema": 1,
        "generated": "2026-11-12T03:20:00+00:00",
        "wheelgap": "0.9.0",
        "targets": TARGETS,
        "summary": {},
        "packages": packages,
    }


def note(platforms='["musl-aarch64"]', kind="alternative", text="Use `pkg-binary` instead.", versions=None,
         links='["https://example.com/docs?a=1&b=2"]'):
    extra = f'versions = "{versions}"\n' if versions else ""
    return (
        "[[note]]\n"
        f"platforms = {platforms}\n"
        f'kind = "{kind}"\n'
        f"text = {json.dumps(text)}\n"
        f"links = {links}\n"
        f"{extra}"
        "updated = 2026-11-11\n"
    )


class Temp(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.notes = self.root / "notes"
        self.notes.mkdir()

    def write_note(self, name, body):
        (self.notes / f"{name}.toml").write_text(body, encoding="utf-8")

    def write_scan(self, packages):
        path = self.root / "coverage.json"
        path.write_text(json.dumps(scan(packages)), encoding="utf-8")
        return path


class VersionMatchTests(unittest.TestCase):
    # Each expectation was checked against packaging.specifiers.SpecifierSet.contains.
    CASES = [
        ("<3", "2.9.1", True),
        ("<3", "3", False),
        ("<3", "3.0.0", False),
        ("<3", "10", False),  # numeric, not string, comparison
        (">=2.8, <2.10", "2.9", True),
        (">=2.8, <2.10", "2.10", False),
        (">=2.8, <2.10", "2.8.0", True),
        ("==2.*", "2.9.1", True),
        ("==2.*", "3.0", False),
        ("==2.0.*", "2", True),  # the candidate is padded with zeros
        ("!=2.*", "3.1", True),
        ("!=2.*", "2.1", False),
        ("==2.1", "2.1.0", True),
        ("!=2.1", "2.1.0", False),
        ("<=2.1", "2.1", True),
        (">=2.0", "2", True),  # a short version is padded with zeros
        ("<2.0", "2", False),
        (">2.1", "2.1.0", False),
        (">2.1", "2.1.1", True),
    ]

    def test_plain_releases_match_as_pep_440_says(self):
        for spec, version, expected in self.CASES:
            with self.subTest(spec=spec, version=version):
                self.assertIs(check_notes.version_matches(spec, version), expected)

    def test_a_spec_the_checker_rejects_never_matches(self):
        for spec in ("<2.*", ">=2.*", "~=2.1", "2.1", "", "<2,", "==2.1rc1"):
            with self.subTest(spec=spec):
                self.assertFalse(check_notes.version_matches(spec, "1.0"))
                self.assertFalse(check_notes.version_matches(spec, "2.1"))

    def test_anything_but_a_plain_release_does_not_match(self):
        for version in ("2.9.1rc1", "2.9.1.post1", "2.9.dev0", "2.9+local", "v2.9", "", None):
            with self.subTest(version=version):
                self.assertFalse(check_notes.version_matches("<3", version))


class LoadTests(Temp):
    def test_a_file_with_a_problem_is_left_out_with_a_warning(self):
        self.write_note("good", note())
        self.write_note("bad", note(kind="nonsense"))
        warnings = []
        loaded = check_notes.load(self.notes, scan([]), warn=warnings.append)
        self.assertEqual(list(loaded), ["good"])
        self.assertEqual(len(warnings), 1)
        self.assertIn("bad.toml", warnings[0])

    def test_a_subdirectory_is_warned_about(self):
        (self.notes / "nested").mkdir()
        self.write_note("good", note())
        warnings = []
        self.assertEqual(list(check_notes.load(self.notes, scan([]), warn=warnings.append)), ["good"])
        self.assertEqual(len(warnings), 1)
        self.assertIn("nested: not a file", warnings[0])

    def test_missing_directory_has_no_notes(self):
        self.assertEqual(check_notes.load(self.root / "nowhere", scan([])), {})


class LookupNotesTests(Temp):
    def build(self, packages):
        out = self.root / "packages"
        build_lookup.build(self.write_scan(packages), out, self.notes)
        return {p.stem: json.loads(p.read_text(encoding="utf-8")) for p in out.iterdir()}

    def test_current_note_is_listed_with_the_format_fields(self):
        self.write_note("pkg", note(versions="<3"))
        files = self.build([package("pkg", ["wheel", "source", "wheel", "missing"])])
        self.assertEqual(files["pkg"]["notes"], [{
            "platforms": ["musl-aarch64"],
            "kind": "alternative",
            "text": "Use `pkg-binary` instead.",
            "links": ["https://example.com/docs?a=1&b=2"],
            "versions": "<3",
            "updated": "2026-11-11",
        }])

    def test_note_for_another_version_is_left_out(self):
        self.write_note("pkg", note(versions="<3"))
        files = self.build([package("pkg", ["wheel", "source", "wheel", "missing"], version="3.0")])
        self.assertEqual(files["pkg"]["notes"], [])

    def test_note_is_left_out_once_every_target_of_its_platforms_has_a_wheel(self):
        self.write_note("pkg", note())
        files = self.build([
            package("pkg", ["source", "wheel", "wheel", "missing"]),
            package("other", ["wheel", "wheel", "source", "missing"], rank=2),
        ])
        self.assertEqual(files["pkg"]["notes"], [])  # both musl-aarch64 targets have a wheel
        self.write_note("other", note())
        files = self.build([package("other", ["wheel", "wheel", "source", "missing"], rank=2)])
        self.assertEqual(len(files["other"]["notes"]), 1)  # 3.14t musl-aarch64 still builds from source

    def test_all_platforms_note_stays_while_any_target_lacks_a_wheel(self):
        self.write_note("pkg", note(platforms='["all"]'))
        files = self.build([package("pkg", ["wheel", "wheel", "wheel", "missing"])])
        self.assertEqual(len(files["pkg"]["notes"]), 1)

    def test_all_platforms_note_retires_once_every_target_has_a_wheel(self):
        self.write_note("pkg", note(platforms='["all"]'))
        files = self.build([package("pkg", ["wheel"] * 4)])
        self.assertEqual(files["pkg"]["notes"], [])

    def test_note_on_platforms_the_scan_lacks_is_kept(self):
        # Nothing to judge the note by (no target of its platform in this scan), so it isn't retired.
        pkg = package("pkg", ["wheel"])
        targets = [{"label": "3.13 linux-x86_64", "python": "3.13", "platform": "linux-x86_64"}]
        n = {"platforms": ["musl-aarch64"], "kind": "workaround", "text": "t", "links": ["https://e.x"]}
        self.assertEqual(check_notes.current([n], pkg, targets), [n])

    def test_a_failed_lookup_keeps_its_notes(self):
        self.write_note("pkg", note())
        files = self.build([package("pkg", ["unknown"] * 4, version=None, error="timeout")])
        self.assertEqual(len(files["pkg"]["notes"]), 1)


class PageNotesTests(Temp):
    def build(self, packages):
        site = self.root / "site"
        build_pages.build(self.write_scan(packages), site, self.notes)
        return {p.parent.name: p.read_text(encoding="utf-8") for p in (site / "p").glob("*/index.html")}

    def test_current_note_is_shown_escaped_with_backticks_as_code(self):
        self.write_note("pkg", note(text='Run `pip install "pkg[binary]"` <script>alert(1)</script> & go.'))
        page = self.build([package("pkg", ["wheel", "source", "wheel", "missing"])])["pkg"]
        self.assertIn('<section class="notes">', page)
        self.assertIn(
            "<p>Run <code>pip install &quot;pkg[binary]&quot;</code> "
            "&lt;script&gt;alert(1)&lt;/script&gt; &amp; go.</p>",
            page,
        )
        self.assertNotIn("<script", page)
        self.assertIn('<a href="https://example.com/docs?a=1&amp;b=2">example.com/docs?a=1&amp;b=2</a>', page)
        self.assertIn("Alternative · Alpine ARM64 · checked 2026-11-11", page)
        self.assertIn(f'content="{build_pages.CSP}"', page)

    def test_versions_are_shown_escaped(self):
        self.write_note("pkg", note(versions=">=2.8, <2.10"))
        page = self.build([package("pkg", ["wheel", "source", "wheel", "missing"])])["pkg"]
        self.assertIn("· for versions &gt;=2.8, &lt;2.10 ·", page)

    def test_link_text_drops_the_scheme_whatever_its_case(self):
        self.write_note("pkg", note(links='["HTTPS://Example.com/docs"]'))
        page = self.build([package("pkg", ["wheel", "source", "wheel", "missing"])])["pkg"]
        self.assertIn('<a href="HTTPS://Example.com/docs">Example.com/docs</a>', page)

    def test_an_unpaired_backtick_is_left_as_text(self):
        self.assertEqual(build_pages.note_text("a `b` c `d <"), "a <code>b</code> c `d &lt;")

    def test_stale_note_is_not_shown(self):
        self.write_note("pkg", note(versions="<3"))
        page = self.build([package("pkg", ["wheel", "source", "wheel", "missing"], version="3.1")])["pkg"]
        self.assertNotIn("pkg-binary", page)
        self.assertIn("No notes for pkg yet.", page)

    def test_gap_page_links_to_a_prefilled_new_note(self):
        page = self.build([package("pkg", ["wheel", "source", "wheel", "missing"])])["pkg"]
        start = page.index('">Add a note</a>')
        href = page[page.rindex('href="', 0, start) + 6:start].replace("&amp;", "&")
        url = urlsplit(href)
        self.assertEqual(f"{url.scheme}://{url.netloc}{url.path}", "https://github.com/wheelgap/tracker/new/main")
        query = parse_qs(url.query)
        self.assertEqual(query["filename"], ["notes/pkg.toml"])
        template = query["value"][0]
        self.assertIn('platforms = ["musl-aarch64", "pyodide"]', template)
        self.assertIn("updated = 2026-11-12", template)

    def test_existing_note_file_opens_the_editor_instead(self):
        self.write_note("pkg", note())
        page = self.build([package("pkg", ["wheel", "source", "wheel", "missing"])])["pkg"]
        self.assertIn('<a href="https://github.com/wheelgap/tracker/edit/main/notes/pkg.toml">Add a note</a>', page)

    def test_unknown_results_are_not_a_gap(self):
        page = self.build([package("pkg", ["wheel", "unknown", "wheel", "wheel"])])["pkg"]
        self.assertNotIn("Add a note", page)
        self.assertNotIn('class="notes"', page)

    def test_page_with_a_note_but_no_gap_has_no_add_link(self):
        # Current (3.13 musl-aarch64 is unknown, not a wheel) but nothing builds from source or is missing.
        self.write_note("pkg", note())
        page = self.build([package("pkg", ["wheel", "unknown", "wheel", "wheel"])])["pkg"]
        self.assertIn("pkg-binary", page)
        self.assertNotIn("Add a note", page)

    def test_rejected_note_file_still_gets_the_edit_link(self):
        self.write_note("pkg", note(kind="nonsense"))
        with mock.patch("sys.stderr"):
            page = self.build([package("pkg", ["wheel", "source", "wheel", "missing"])])["pkg"]
        self.assertIn("No notes for pkg yet.", page)
        self.assertIn('<a href="https://github.com/wheelgap/tracker/edit/main/notes/pkg.toml">Add a note</a>', page)

    def test_page_without_a_gap_has_no_notes_section(self):
        self.write_note("pkg", note())  # stale: every musl-aarch64 target has a wheel
        page = self.build([package("pkg", ["wheel"] * 4)])["pkg"]
        self.assertNotIn("Notes", page)
        self.assertNotIn("Add a note", page)

    def test_bad_note_file_is_skipped(self):
        self.write_note("pkg", note(text="<b>x</b>", links='["http://insecure.example"]'))
        with mock.patch("sys.stderr"):
            page = self.build([package("pkg", ["wheel", "source", "wheel", "missing"])])["pkg"]
        self.assertNotIn("insecure", page)
        self.assertIn("No notes for pkg yet.", page)


class RepoNotesTests(unittest.TestCase):
    def test_repo_notes_load_against_the_real_scan(self):
        repo = Path(__file__).resolve().parent.parent
        coverage = json.loads((repo / "data" / "coverage.json").read_text(encoding="utf-8"))
        warnings = []
        loaded = check_notes.load(repo / "notes", coverage, warn=warnings.append)
        self.assertEqual(warnings, [])
        self.assertEqual(set(loaded), {p.stem for p in (repo / "notes").glob("*.toml")})


if __name__ == "__main__":
    unittest.main()
