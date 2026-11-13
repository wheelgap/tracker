"""Offline checks for scripts/check_notes.py (no network)."""

import datetime as dt
import json
import tempfile
import unittest
from pathlib import Path

from scripts import check_notes

LABELS = [
    {"label": "3.13 linux-x86_64", "platform": "linux-x86_64"},
    {"label": "3.13 musl-aarch64", "platform": "musl-aarch64"},
]
# Targets with a Python each, as data/coverage.json has them.
PYTHON_TARGETS = [
    {"label": "3.13 musl-aarch64", "python": "3.13", "platform": "musl-aarch64"},
    {"label": "3.14t musl-aarch64", "python": "3.14t", "platform": "musl-aarch64"},
    {"label": "3.13 pyodide", "python": "3.13", "platform": "pyodide"},
]


def coverage(targets=None):
    return {"targets": targets if targets is not None else LABELS}


def note_toml(
    platforms='["musl-aarch64"]',
    kind="alternative",
    text="Use the binary extra.",
    links='["https://example.com/docs"]',
    versions=None,
    updated="2026-11-11",
    python=None,
):
    extra = f'versions = "{versions}"\n' if versions is not None else ""
    extra += f"python = {python}\n" if python is not None else ""
    return (
        f"[[note]]\n"
        f"platforms = {platforms}\n"
        f'kind = "{kind}"\n'
        f'text = "{text}"\n'
        f"links = {links}\n"
        f"{extra}"
        f"updated = {updated}\n"
    )


class CheckNotesTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.notes = self.root / "notes"
        self.notes.mkdir()

    def write(self, name, body):
        path = self.notes / name
        path.write_text(body, encoding="utf-8")
        return path

    def problems(self, scan=None):
        return check_notes.problems(
            self.notes, scan if scan is not None else coverage()
        )

    def test_valid_note_is_clean(self):
        self.write("psycopg2.toml", note_toml())
        self.assertEqual(self.problems(), [])

    def test_all_platforms_is_accepted(self):
        self.write("psycopg2.toml", note_toml(platforms='["all"]'))
        self.assertEqual(self.problems(), [])

    def test_readme_is_ignored(self):
        self.write("README.md", "not toml")
        self.assertEqual(self.problems(), [])

    def test_filename_must_be_normalized(self):
        self.write("Py_Crypto.toml", note_toml())
        found = self.problems()
        self.assertTrue(any("normalized" in p for p in found), found)

    def test_unreadable_toml_is_reported(self):
        self.write("broken.toml", "[[note]\n")
        found = self.problems()
        self.assertTrue(found and "broken.toml" in found[0], found)

    def test_missing_note_table_is_reported(self):
        self.write("empty.toml", 'kind = "alternative"\n')
        found = self.problems()
        self.assertTrue(any("at least one" in p for p in found), found)

    def test_unknown_kind_is_reported(self):
        self.write("psycopg2.toml", note_toml(kind="tip"))
        found = self.problems()
        self.assertTrue(any("kind" in p for p in found), found)

    def test_unknown_platform_is_reported(self):
        self.write("psycopg2.toml", note_toml(platforms='["alpine"]'))
        found = self.problems()
        self.assertTrue(any("unknown platform" in p for p in found), found)

    def test_all_mixed_with_a_name_is_reported(self):
        self.write("psycopg2.toml", note_toml(platforms='["all", "musl-aarch64"]'))
        found = self.problems()
        self.assertTrue(
            any('"all" cannot be combined with others' in p for p in found), found
        )
        self.assertFalse(any("unknown platform" in p for p in found), found)

    def test_empty_text_is_reported(self):
        self.write("psycopg2.toml", note_toml(text="   "))
        found = self.problems()
        self.assertTrue(any("text" in p for p in found), found)

    def test_text_over_500_characters_is_reported(self):
        self.write("psycopg2.toml", note_toml(text="x" * 501))
        found = self.problems()
        self.assertTrue(any("500" in p for p in found), found)

    def test_http_link_is_rejected(self):
        self.write("psycopg2.toml", note_toml(links='["http://example.com/docs"]'))
        found = self.problems()
        self.assertTrue(any("https" in p for p in found), found)

    def test_updated_must_be_a_date(self):
        self.write("psycopg2.toml", note_toml(updated='"2026-11-11"'))
        found = self.problems()
        self.assertTrue(any("updated must be a date" in p for p in found), found)

    def test_versions_if_present_must_be_a_string(self):
        self.write("psycopg2.toml", note_toml() + "versions = 3\n")
        found = self.problems()
        self.assertTrue(any("versions" in p for p in found), found)

    def test_optional_versions_string_is_accepted(self):
        self.write("psycopg2.toml", note_toml(versions="<3"))
        self.assertEqual(self.problems(), [])

    def test_filename_with_a_doubled_separator_is_reported(self):
        self.write("py--crypto.toml", note_toml())
        found = self.problems()
        self.assertTrue(any("normalized" in p for p in found), found)

    def test_unknown_note_key_is_reported(self):
        self.write("psycopg2.toml", note_toml() + 'colour = "red"\n')
        found = self.problems()
        self.assertTrue(any("unknown keys: colour" in p for p in found), found)

    def test_unknown_top_level_key_is_reported(self):
        self.write("psycopg2.toml", 'title = "psycopg2"\n' + note_toml())
        found = self.problems()
        self.assertTrue(any("unknown top-level keys: title" in p for p in found), found)

    def test_bad_versions_specifier_is_reported(self):
        bad_specifiers = (
            "3.x or later",
            "<3,",
            "=>3",
            "3",
            "< 3 or > 4",
            " ",
            "~=2.9",
            "===2.9.13",
            "<3.*",
            ">=2.*",
            "==1!2.0",
            "==2.9+local",
            "<3.0rc1",
            ">=2.9.post1",
            "==2.9.dev0",
            "==v2",
            "==2.",
        )
        for bad in bad_specifiers:
            with self.subTest(versions=bad):
                self.write("psycopg2.toml", note_toml(versions=bad))
                found = self.problems()
                self.assertTrue(any("versions" in p for p in found), found)

    def test_multi_clause_versions_specifier_is_accepted(self):
        for good in (">=2.8, <2.10", "!=2.9.1", "==2.*", "!=2.9.*", "<=3", ">1,<2"):
            with self.subTest(versions=good):
                self.write("psycopg2.toml", note_toml(versions=good))
                self.assertEqual(self.problems(), [])

    def test_offset_datetime_is_not_a_date(self):
        self.write("psycopg2.toml", note_toml(updated="2026-11-11T09:30:00+01:00"))
        found = self.problems()
        self.assertTrue(any("updated must be a date" in p for p in found), found)

    def test_local_datetime_is_not_a_date(self):
        self.write("psycopg2.toml", note_toml(updated="2026-11-11T09:30:00"))
        found = self.problems()
        self.assertTrue(any("updated must be a date" in p for p in found), found)

    def test_non_utf8_file_is_reported(self):
        (self.notes / "psycopg2.toml").write_bytes(
            note_toml().encode("utf-8") + b"# caf\xe9\n"
        )
        found = self.problems()
        self.assertEqual(found, ["psycopg2.toml: not valid UTF-8"])

    def test_empty_file_is_reported(self):
        self.write("psycopg2.toml", "")
        found = self.problems()
        self.assertTrue(any("at least one" in p for p in found), found)

    def test_wrong_types_are_reported(self):
        self.write(
            "psycopg2.toml",
            "[[note]]\n"
            'platforms = "musl-aarch64"\n'
            "kind = 3\n"
            "text = 5\n"
            'links = "https://example.com/docs"\n'
            "versions = 3\n"
            "updated = 20261111\n",
        )
        found = self.problems()
        for field in ("platforms", "kind", "text", "links", "versions", "updated"):
            self.assertTrue(any(f": {field} " in p for p in found), (field, found))

    def test_note_that_is_not_a_table_is_reported(self):
        self.write("psycopg2.toml", "note = [1]\n")
        found = self.problems()
        self.assertTrue(any("expected a table" in p for p in found), found)

    def test_text_of_499_characters_is_accepted(self):
        self.write("psycopg2.toml", note_toml(text="x" * 499))
        self.assertEqual(self.problems(), [])

    def test_text_of_500_characters_is_reported(self):
        self.write("psycopg2.toml", note_toml(text="x" * 500))
        found = self.problems()
        self.assertTrue(any("under 500" in p for p in found), found)

    def test_multi_line_text_is_reported(self):
        self.write("psycopg2.toml", note_toml(text="First line.\\nSecond line."))
        found = self.problems()
        self.assertTrue(any("single line" in p for p in found), found)

    def test_text_with_control_or_format_characters_is_reported(self):
        # TOML escapes, so the file holds the real characters: a bidi override, tab, ESC, zero-width space.
        for text, code in (
            ("run \\u202egnp.exe", "U+202E"),
            ("pip\\tinstall", "U+0009"),
            ("\\u001b[31mred", "U+001B"),
            ("zero\\u200bwidth", "U+200B"),
        ):
            with self.subTest(text=text):
                self.write("psycopg2.toml", note_toml(text=text))
                found = self.problems()
                self.assertTrue(any(code in p for p in found), found)

    def test_text_with_plain_non_ascii_is_accepted(self):
        self.write("psycopg2.toml", note_toml(text="café — naïve"))
        self.assertEqual(self.problems(), [])

    def test_empty_platforms_are_reported(self):
        self.write("psycopg2.toml", note_toml(platforms="[]"))
        found = self.problems()
        self.assertTrue(any("platforms must be a non-empty list" in p for p in found))

    def test_empty_links_are_reported(self):
        self.write("psycopg2.toml", note_toml(links="[]"))
        found = self.problems()
        self.assertTrue(any("links must be a non-empty list" in p for p in found))

    def test_missing_required_keys_are_reported(self):
        self.write("psycopg2.toml", '[[note]]\nkind = "alternative"\n')
        found = self.problems()
        self.assertTrue(
            any(
                "missing required keys: platforms, text, links, updated" in p
                for p in found
            ),
            found,
        )

    def test_python_list_of_scanned_pythons_is_accepted(self):
        self.write("psycopg2.toml", note_toml(python='["3.13", "3.14t"]'))
        self.assertEqual(self.problems(coverage(PYTHON_TARGETS)), [])

    def test_unknown_python_is_reported(self):
        self.write("psycopg2.toml", note_toml(python='["3.12"]'))
        found = self.problems(coverage(PYTHON_TARGETS))
        self.assertTrue(any("unknown python '3.12'" in p for p in found), found)

    def test_python_without_the_t_suffix_is_not_the_free_threaded_label(self):
        # The scan has 3.14t but no 3.14.
        self.write("psycopg2.toml", note_toml(python='["3.14"]'))
        found = self.problems(coverage(PYTHON_TARGETS))
        self.assertTrue(any("unknown python '3.14'" in p for p in found), found)

    def test_python_is_unknown_when_the_scan_has_no_pythons(self):
        self.write("psycopg2.toml", note_toml(python='["3.13"]'))
        found = self.problems()  # LABELS carry no python
        self.assertTrue(any("unknown python '3.13'" in p for p in found), found)

    def test_empty_python_list_is_reported(self):
        self.write("psycopg2.toml", note_toml(python="[]"))
        found = self.problems(coverage(PYTHON_TARGETS))
        self.assertTrue(any("python must be a non-empty list" in p for p in found), found)

    def test_python_must_be_a_list(self):
        for value in ('"3.13"', "3.13", "true"):
            with self.subTest(value=value):
                self.write("psycopg2.toml", note_toml(python=value))
                found = self.problems(coverage(PYTHON_TARGETS))
                self.assertTrue(any("python must be a non-empty list" in p for p in found), found)

    def test_python_entries_must_be_strings(self):
        self.write("psycopg2.toml", note_toml(python="[3.13]"))
        found = self.problems(coverage(PYTHON_TARGETS))
        self.assertTrue(any("unknown python" in p for p in found), found)

    def test_duplicate_python_is_reported(self):
        self.write("psycopg2.toml", note_toml(python='["3.13", "3.13"]'))
        found = self.problems(coverage(PYTHON_TARGETS))
        self.assertTrue(any("duplicate python: 3.13" in p for p in found), found)

    def test_python_no_listed_platform_has_is_reported(self):
        # 3.14t is scanned, but not on pyodide, so the note could never retire.
        self.write("psycopg2.toml", note_toml(platforms='["pyodide"]', python='["3.14t"]'))
        found = self.problems(coverage(PYTHON_TARGETS))
        self.assertTrue(
            any("the scan has no target for platforms pyodide on python 3.14t" in p for p in found), found
        )
        notes = check_notes.load(self.notes, coverage(PYTHON_TARGETS), warn=lambda message: None)
        self.assertEqual(notes, {})

    def test_python_one_listed_platform_has_is_accepted(self):
        for platforms in ('["pyodide", "musl-aarch64"]', '["all"]'):
            with self.subTest(platforms=platforms):
                self.write("psycopg2.toml", note_toml(platforms=platforms, python='["3.14t"]'))
                self.assertEqual(self.problems(coverage(PYTHON_TARGETS)), [])

    def test_note_without_python_still_passes_when_the_scan_has_no_pythons(self):
        self.write("psycopg2.toml", note_toml())
        self.assertEqual(self.problems(), [])

    def test_duplicate_platforms_are_reported(self):
        self.write(
            "psycopg2.toml", note_toml(platforms='["musl-aarch64", "musl-aarch64"]')
        )
        found = self.problems()
        self.assertTrue(any("duplicate platforms" in p for p in found), found)

    def test_duplicate_links_are_reported(self):
        self.write(
            "psycopg2.toml",
            note_toml(links='["https://example.com/a", "https://example.com/a"]'),
        )
        found = self.problems()
        self.assertTrue(any("duplicate links" in p for p in found), found)

    def test_links_without_a_host_or_with_spaces_are_rejected(self):
        for bad in (
            "https://",
            "https:///path",
            "https://exa mple.com/",
            "https://example.com/a b",
            "https://example.com/\\t",
            "ftp://example.com/",
            "https://[::1/",
        ):
            with self.subTest(link=bad):
                self.write("psycopg2.toml", note_toml(links=f'["{bad}"]'))
                found = self.problems()
                self.assertTrue(any("links must be https" in p for p in found), found)

    def test_links_with_control_characters_or_user_info_are_rejected(self):
        # TOML escapes, so the file holds the real characters: NUL, DEL, a zero-width space, a bidi override.
        for bad in (
            "https://example.com/\\u0000",
            "https://example.com/\\u007f",
            "https://example.com/\\u200b",
            "https://example.com/\\u202egnp.exe",
            "https://docs.python.org@evil.example/",
            "https://user:pass@example.com/",
        ):
            with self.subTest(link=bad):
                self.write("psycopg2.toml", note_toml(links=f'["{bad}"]'))
                found = self.problems()
                self.assertTrue(any("links must be https" in p for p in found), found)

    def test_link_with_an_at_sign_in_the_path_is_accepted(self):
        self.write("psycopg2.toml", note_toml(links='["https://example.com/@user/pkg"]'))
        self.assertEqual(self.problems(), [])

    def test_future_updated_date_is_reported(self):
        self.write("psycopg2.toml", note_toml(updated="2999-01-01"))
        found = self.problems()
        self.assertTrue(any("in the future" in p for p in found), found)

    def test_today_is_not_in_the_future(self):
        self.write("psycopg2.toml", note_toml(updated=dt.date.today().isoformat()))
        self.assertEqual(self.problems(), [])

    def test_non_toml_file_is_reported(self):
        self.write("psycopg2.txt", note_toml())
        found = self.problems()
        self.assertEqual(found, ["psycopg2.txt: not a .toml file"])

    def test_subdirectory_is_reported(self):
        (self.notes / "extra").mkdir()
        found = self.problems()
        self.assertTrue(found and found[0].startswith("extra: not a file"), found)

    def test_filename_error_suggests_the_normalized_name(self):
        self.write("Py_Crypto.toml", note_toml())
        found = self.problems()
        self.assertTrue(any("expected 'py-crypto'" in p for p in found), found)

    def test_real_notes_pass_against_coverage(self):
        repo = Path(__file__).resolve().parent.parent
        scan = json.loads((repo / "data" / "coverage.json").read_text(encoding="utf-8"))
        self.assertEqual(check_notes.problems(repo / "notes", scan), [])

    def test_cli_exits_zero_on_the_repo_notes(self):
        repo = Path(__file__).resolve().parent.parent
        self.assertEqual(
            check_notes.main(
                [str(repo / "notes"), str(repo / "data" / "coverage.json")]
            ),
            0,
        )

    def test_cli_exits_nonzero_when_a_note_is_bad(self):
        self.write("psycopg2.toml", note_toml(kind="tip"))
        coverage_path = self.root / "coverage.json"
        coverage_path.write_text(json.dumps(coverage()), encoding="utf-8")
        self.assertEqual(check_notes.main([str(self.notes), str(coverage_path)]), 1)

    def test_normalize_name(self):
        self.assertEqual(check_notes.normalize_name("Py_Crypto"), "py-crypto")
        self.assertEqual(check_notes.normalize_name("psycopg2"), "psycopg2")


# Keep a cheap check that the date type the checker wants is what tomllib emits.
class PythonScopedNoteTests(unittest.TestCase):
    """A note's optional `python` list limits which Pythons it applies to and retires on."""

    def note(self, **extra):
        return {
            "platforms": ["musl-aarch64"], "kind": "workaround", "text": "t",
            "links": ["https://e.x"], **extra,
        }

    def package(self, *results):
        # One result per PYTHON_TARGETS entry: 3.13 musl, 3.14t musl, 3.13 pyodide.
        return {"name": "pkg", "version": "1.0", "results": list(results)}

    def test_note_without_python_covers_every_python(self):
        for python in ("3.13", "3.14t", None):
            self.assertTrue(check_notes.covers_python(self.note(), python), python)

    def test_note_with_python_covers_only_the_listed_ones(self):
        n = self.note(python=["3.13"])
        self.assertTrue(check_notes.covers_python(n, "3.13"))
        self.assertFalse(check_notes.covers_python(n, "3.14t"))
        self.assertFalse(check_notes.covers_python(n, "3.14"))  # "3.14t" is a different label
        self.assertFalse(check_notes.covers_python(n, None))

    def test_note_without_python_stays_while_a_free_threaded_wheel_is_missing(self):
        # The existing behaviour: only 3.14t is left, and the note still shows.
        n = self.note()
        pkg = self.package("wheel", "missing", "missing")
        self.assertEqual(check_notes.current([n], pkg, PYTHON_TARGETS), [n])

    def test_note_with_python_retires_once_its_pythons_have_wheels(self):
        n = self.note(python=["3.13"])
        pkg = self.package("wheel", "missing", "missing")  # 3.13 musl has a wheel; only 3.14t lacks one
        self.assertEqual(check_notes.current([n], pkg, PYTHON_TARGETS), [])

    def test_note_with_python_stays_while_a_listed_python_lacks_a_wheel(self):
        n = self.note(python=["3.13", "3.14t"])
        pkg = self.package("wheel", "source", "missing")
        self.assertEqual(check_notes.current([n], pkg, PYTHON_TARGETS), [n])

    def test_note_for_the_free_threaded_python_ignores_a_3_13_gap(self):
        n = self.note(python=["3.14t"])
        pkg = self.package("source", "wheel", "missing")  # 3.13 musl lacks a wheel, 3.14t has one
        self.assertEqual(check_notes.current([n], pkg, PYTHON_TARGETS), [])

    def test_python_list_applies_to_every_platform_of_an_all_note(self):
        n = self.note(platforms=["all"], python=["3.13"])
        # 3.13 musl and 3.13 pyodide: pyodide still lacks a wheel, so the note stays.
        self.assertEqual(check_notes.current([n], self.package("wheel", "missing", "missing"), PYTHON_TARGETS), [n])
        self.assertEqual(check_notes.current([n], self.package("wheel", "missing", "bundled"), PYTHON_TARGETS), [])

    def test_note_with_python_the_scan_lacks_is_kept(self):
        # No target has this Python, so there's nothing to judge the note by (as for a missing platform). The
        # checker refuses such a note (test_python_no_listed_platform_has_is_reported); this is only a fallback.
        n = self.note(python=["3.12"])
        pkg = self.package("wheel", "wheel", "wheel")
        self.assertEqual(check_notes.current([n], pkg, PYTHON_TARGETS), [n])


class TomlDateTests(unittest.TestCase):
    def test_bare_toml_date_is_datetime_date(self):
        import tomllib

        data = tomllib.loads("updated = 2026-11-11\n")
        self.assertIsInstance(data["updated"], dt.date)
        self.assertNotIsInstance(data["updated"], dt.datetime)
