"""Offline checks for the campaign pages and their index."""

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from scripts import build_campaigns, build_pages


CONFIG = """
[[campaign]]
id = "musl-aarch64"
title = "musllinux aarch64 wheels for the top 500"
platform = "musl-aarch64"
top = 5
start = "2026-11-10"
how_to = "https://github.com/wheelgap/tracker/blob/main/docs/adding-wheels-upstream.md"
"""


class CampaignTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.coverage = self.root / "coverage.json"
        self.history = self.root / "history.json"
        self.changes = self.root / "changes.json"
        self.config = self.root / "campaigns.toml"
        self.site = self.root / "_site"
        self.config.write_text(CONFIG, encoding="utf-8")
        self.scan = {
            "schema": 1,
            "generated": "2026-11-11T00:00:00+00:00",
            "wheelgap": "0.8.0",
            "targets": [
                {"label": "3.13 linux-x86_64", "python": "3.13", "platform": "linux-x86_64"},
                {"label": "3.13 musl-aarch64", "python": "3.13", "platform": "musl-aarch64"},
                {"label": "3.14t musl-aarch64", "python": "3.14t", "platform": "musl-aarch64"},
                {"label": "3.15 musl-aarch64", "python": "3.15", "platform": "musl-aarch64"},
                {"label": "3.15 linux-x86_64", "python": "3.15", "platform": "linux-x86_64"},
                {"label": "3.15 pyodide", "python": "3.15", "platform": "pyodide"},
            ],
            # Top 5: NumPy and requests have the wheel; psycopg2 is a real musl-aarch64 gap; newpy has no
            # 3.15 wheel on any platform (only Pyodide doesn't count); failed couldn't be looked up.
            "packages": [
                {"rank": 1, "name": "NumPy", "version": "2.0", "error": None, "pure": False,
                 "results": ["wheel", "source", "source", "wheel", "wheel", "source"]},
                {"rank": 2, "name": "psycopg2", "version": "2.9", "error": None, "pure": False,
                 "results": ["wheel", "source", "source", "source", "wheel", "source"]},
                {"rank": 3, "name": "requests", "version": "2.32", "error": None, "pure": True,
                 "results": ["wheel", "wheel", "wheel", "wheel", "wheel", "wheel"]},
                {"rank": 4, "name": "newpy", "version": "1.0", "error": None, "pure": False,
                 "results": ["wheel", "wheel", "source", "source", "source", "wheel"]},
                {"rank": 5, "name": "failed", "version": None, "error": "lookup failed", "pure": False,
                 "results": ["unknown", "unknown", "unknown", "unknown", "unknown", "unknown"]},
                {"rank": 6, "name": "outside-top", "version": "1.0", "error": None, "pure": False,
                 "results": ["wheel", "missing", "missing", "missing", "wheel", "missing"]},
            ],
        }
        self.history_data = [
            {"date": "2026-11-09", "wheelgap": "0.8.0", "packages": 100,
             "summary": {"3.15 musl-aarch64": {"wheel": 80, "source": 15, "missing": 5, "unknown": 0}}},
            {"date": "2026-11-11", "wheelgap": "0.8.0", "packages": 100,
             "summary": {"3.15 musl-aarch64": {"wheel": 85, "source": 10, "missing": 5, "unknown": 0}}},
        ]
        self.changes_data = [
            {"date": "2026-11-09", "gained": [
                {"name": "NumPy", "version": "1.9", "target": "3.15 musl-aarch64", "from": "source", "to": "wheel"},
            ], "lost": []},
            {"date": "2026-11-11", "gained": [
                {"name": "NumPy", "version": "2.0", "target": "3.15 musl-aarch64", "from": "source", "to": "wheel"},
                {"name": "outside-top", "version": "1.0", "target": "3.15 musl-aarch64", "from": "missing", "to": "wheel"},
                {"name": "psycopg2", "version": "2.9", "target": "3.15 linux-x86_64", "from": "source", "to": "wheel"},
                {"name": "newpy", "version": "1.0", "target": "3.15 musl-aarch64", "from": "missing", "to": "wheel"},
            ], "lost": []},
        ]

    def write_inputs(self):
        self.coverage.write_text(json.dumps(self.scan), encoding="utf-8")
        self.history.write_text(json.dumps(self.history_data), encoding="utf-8")
        self.changes.write_text(json.dumps(self.changes_data), encoding="utf-8")

    def built_page(self) -> str:
        self.write_inputs()
        self.assertEqual(
            build_campaigns.build(self.coverage, self.history, self.changes, self.config, self.site),
            1,
        )
        return (self.site / "campaigns" / "musl-aarch64" / "index.html").read_text(encoding="utf-8")

    def todo(self, page: str) -> str:
        return (
            page.split(">To do</h2>", 1)[1].split("Left out:", 1)[0].split(">Has an alternative</h2>", 1)[0]
            .split(">Done</h2>", 1)[0]
        )

    def set_aside(self, page: str) -> str:
        if ">Has an alternative</h2>" not in page:
            return ""
        return page.split(">Has an alternative</h2>", 1)[1].split(">Done</h2>", 1)[0]

    def page_with_note(self, kind, platforms='["musl-aarch64"]', versions=None) -> str:
        notes = self.root / "notes"
        notes.mkdir(exist_ok=True)
        extra = f'versions = "{versions}"\n' if versions else ""
        (notes / "psycopg2.toml").write_text(
            "[[note]]\n"
            f"platforms = {platforms}\n"
            f'kind = "{kind}"\n'
            'text = "Use psycopg2-binary."\n'
            'links = ["https://www.psycopg.org/docs/install.html"]\n'
            f"{extra}"
            "updated = 2026-11-11\n",
            encoding="utf-8",
        )
        self.write_inputs()
        build_campaigns.build(
            self.coverage, self.history, self.changes, self.config, self.site, notes_dir=notes
        )
        return (self.site / "campaigns" / "musl-aarch64" / "index.html").read_text(encoding="utf-8")

    def test_uses_newest_non_free_threaded_python_and_top_n(self):
        page = self.built_page()
        # 3.15, not 3.14t or 3.13. Of the 4 looked up, NumPy and requests have the wheel: 2 of 4.
        self.assertIn("Python 3.15", page)
        self.assertNotIn("3.14t", page)
        self.assertIn("<strong>50%</strong>", page)
        self.assertIn("(2 of 4)", page)
        self.assertIn("psycopg2", self.todo(page))
        self.assertIn("from source", page)
        self.assertNotIn("outside-top", page)

    def test_headline_also_counts_compiled_packages_only(self):
        page = self.built_page()
        # requests is pure Python; NumPy, psycopg2 and newpy have compiled code, and only NumPy has the wheel.
        self.assertIn("Of the 3 with compiled code, <strong>33.3%</strong> have one (1 of 3)", page)

    def test_alternative_note_moves_the_row_out_of_to_do(self):
        page = self.page_with_note("alternative")
        self.assertNotIn("psycopg2", self.todo(page))
        moved = self.set_aside(page)
        self.assertIn("psycopg2", moved)
        self.assertIn(
            '<a href="https://github.com/wheelgap/tracker/blob/main/notes/psycopg2.toml">has a note</a>', moved
        )
        self.assertIn("0 still need one.", page)  # psycopg2 was the only to-do row
        self.assertIn("(1 more with an alternative is listed below)", page)
        index = (self.site / "campaigns" / "index.html").read_text(encoding="utf-8")
        self.assertIn("<td>0</td></tr>", index)

    def test_all_platforms_alternative_note_also_moves_the_row(self):
        page = self.page_with_note("alternative", platforms='["all"]')
        self.assertNotIn("psycopg2", self.todo(page))
        self.assertIn("psycopg2", self.set_aside(page))

    def test_informational_note_keeps_the_row_with_the_marker(self):
        for kind in ("workaround", "upstream-issue", "other-source"):
            with self.subTest(kind=kind):
                page = self.page_with_note(kind)
                todo = self.todo(page)
                self.assertIn("psycopg2", todo)
                self.assertIn("notes/psycopg2.toml\">has a note</a>", todo)
                self.assertIn("A package with a note links it", page)
                self.assertNotIn("Has an alternative", page)
                self.assertIn("1 still need one.", page)

    def test_note_for_another_platform_or_version_is_ignored(self):
        # psycopg2 lacks a pyodide wheel too, so that note is current, just not for this campaign.
        for platforms, versions in (('["pyodide"]', None), ('["musl-aarch64"]', "<2")):
            with self.subTest(platforms=platforms, versions=versions):
                page = self.page_with_note("alternative", platforms=platforms, versions=versions)
                self.assertIn("psycopg2", self.todo(page))
                self.assertNotIn("has a note", page)
                self.assertNotIn("Has an alternative", page)
                self.assertNotIn("A package with a note links it", page)

    def test_lookup_errors_are_left_out_and_said(self):
        page = self.built_page()
        self.assertIn("1 couldn't be looked up in this scan and aren't counted", page)
        self.assertNotIn("failed", self.todo(page))
        self.assertNotIn("s-error", page)

    def test_packages_with_no_wheels_for_the_python_are_left_out_of_to_do(self):
        page = self.built_page()
        todo = self.todo(page)
        self.assertNotIn("newpy", todo)
        left_out = page.split("Left out:", 1)[1].split("</p>", 1)[0]
        self.assertIn("1 package has no Python 3.15 wheel for any Linux platform yet (newpy)", left_out)

    def test_only_wheels_for_the_same_os_count_as_started(self):
        # A Windows wheel doesn't make a Linux-wide gap a musllinux one.
        self.scan["targets"].append({"label": "3.15 win-amd64", "python": "3.15", "platform": "win-amd64"})
        for package in self.scan["packages"]:
            package["results"].append("wheel")
        page = self.built_page()
        self.assertNotIn("newpy", self.todo(page))
        self.assertIn("(newpy)", page.split("Left out:", 1)[1])

    def test_campaign_python_overrides_the_newest(self):
        self.config.write_text(CONFIG + 'python = "3.13"\n', encoding="utf-8")
        page = self.built_page()
        self.assertIn("using Python 3.13 (set by the campaign)", page)
        # On 3.13 musl-aarch64 only requests and newpy have the wheel, so NumPy (a wheel on 3.15) is to do.
        self.assertIn("(2 of 4)", page)
        self.assertIn("NumPy", self.todo(page))

    def test_bad_campaign_values_are_rejected(self):
        for line in ('python = "3.14t"', "python = 3.14", 'start = "2026-13-45"', "top = true",
                     'how_to = "http://example.com"', "description = 1"):
            with self.subTest(line=line):
                key = line.split(" ", 1)[0]
                config = "\n".join(x for x in CONFIG.splitlines() if not x.startswith(key + " "))
                self.config.write_text(config + "\n" + line + "\n", encoding="utf-8")
                self.write_inputs()
                with self.assertRaises(SystemExit):
                    build_campaigns.build(self.coverage, self.history, self.changes, self.config, self.site)

    def test_bare_toml_date_for_start(self):
        self.config.write_text(CONFIG.replace('start = "2026-11-10"', "start = 2026-11-10"), encoding="utf-8")
        done = self.built_page().split(">Done</h2>", 1)[1]
        self.assertIn("2026-11-11", done)

    def test_names_are_escaped(self):
        self.scan["packages"][1]["name"] = "<script>alert(1)</script>"
        page = self.built_page()
        self.assertNotIn("<script>alert", page)
        self.assertIn("&lt;script&gt;", page)
        # Not a name build_pages.py makes a page for, so no link to one.
        self.assertNotIn('href="../../p/&lt;', page)

    def test_description_replaces_the_default_lede(self):
        self.config.write_text(CONFIG + 'description = "Alpine on Graviton & friends."\n', encoding="utf-8")
        page = self.built_page()
        self.assertIn('<p class="lede">Alpine on Graviton &amp; friends.</p>', page)
        self.assertIn('href="../../p/numpy/"', page)
        self.assertIn('href="https://pypi.org/project/psycopg2/"', page)
        self.assertNotIn("Source</th>", page)

    def trend(self, page: str) -> str:
        return page.split(">Coverage</h2>", 1)[1].split(">To do</h2>", 1)[0]

    def test_trend_uses_history_for_the_badge_python_label(self):
        trend = self.trend(self.built_page())
        self.assertIn("85 / 100", trend)
        self.assertIn("Nightly scan totals for 3.15 musl-aarch64 since the campaign started on 2026-11-10", trend)

    def test_trend_starts_at_the_campaign_start(self):
        trend = self.trend(self.built_page())
        # 9 Nov is before the 10 Nov start.
        self.assertNotIn("2026-11-09", trend)
        self.assertNotIn("80 / 100", trend)
        self.assertIn("<td>2026-11-11</td>", trend)

    def test_trend_shows_the_latest_night_before_any_since_start(self):
        self.config.write_text(CONFIG.replace('start = "2026-11-10"', 'start = "2026-12-01"'), encoding="utf-8")
        trend = self.trend(self.built_page())
        self.assertNotIn("2026-11-09", trend)
        self.assertIn("<td>2026-11-11</td>", trend)
        self.assertIn(
            "on the latest night that has them (2026-11-11), as no night from the campaign's start on 2026-12-01 "
            "has them yet", trend
        )

    def test_trend_start_night_is_included(self):
        self.config.write_text(CONFIG.replace('start = "2026-11-10"', 'start = "2026-11-11"'), encoding="utf-8")
        self.history_data.append({"date": "2026-11-12", "wheelgap": "0.8.0", "packages": 100,
                                  "summary": {"3.15 musl-aarch64": {"wheel": 86}}})
        trend = self.trend(self.built_page())
        # The start night itself is a row, not only the nights after it.
        self.assertIn("<td>2026-11-11</td>", trend)
        self.assertIn("<td>2026-11-12</td>", trend)
        self.assertNotIn("2026-11-09", trend)
        self.assertIn("since the campaign started on 2026-11-11", trend)

    def test_trend_falls_back_when_nights_since_start_lack_the_label(self):
        # 12 Nov is after the start but has no totals for this target.
        self.config.write_text(CONFIG.replace('start = "2026-11-10"', 'start = "2026-11-12"'), encoding="utf-8")
        self.history_data.append({"date": "2026-11-12", "wheelgap": "0.8.0", "packages": 100, "summary": {}})
        trend = self.trend(self.built_page())
        self.assertIn("on the latest night that has them (2026-11-11)", trend)

    def marks(self, history):
        return [(r["date"], r["upgrade"]) for r in build_campaigns.trend(history, "L", "2000-01-01")]

    def test_trend_marker_logic(self):
        def night(date, version, label=True):
            return {"date": date, "wheelgap": version, "packages": 10, "summary": {"L": {"wheel": 1}} if label else {}}
        # No marker on the first row, even with a version.
        self.assertEqual(self.marks([night("d1", "0.7.0")]), [("d1", "")])
        # A blank version isn't marked and doesn't reset what the next row is compared with.
        self.assertEqual(
            self.marks([night("d1", "0.7.0"), night("d2", ""), night("d3", "0.7.0")]),
            [("d1", ""), ("d2", ""), ("d3", "")],
        )
        self.assertEqual(
            self.marks([night("d1", "0.7.0"), night("d2", ""), night("d3", "0.9.0")]),
            [("d1", ""), ("d2", ""), ("d3", "0.9.0")],
        )
        # The upgrade night lacks the label: the next row for the label is still marked.
        self.assertEqual(
            self.marks([night("d1", "0.7.0"), night("d2", "0.9.0", label=False), night("d3", "0.9.0")]),
            [("d1", ""), ("d3", "0.9.0")],
        )
        # A night without the label doesn't count as the previous row.
        self.assertEqual(
            self.marks([night("d1", "0.7.0"), night("d2", "0.9.0", label=False), night("d3", "0.7.0")]),
            [("d1", ""), ("d3", "")],
        )

    def test_trend_date_and_upgrade_are_escaped(self):
        self.history_data[0]["wheelgap"] = "0.7.0"
        self.history_data[1]["date"] = "2026-11-11<b>"
        self.history_data[1]["wheelgap"] = "0.9<i>"
        trend = self.trend(self.built_page())
        self.assertIn("<td>2026-11-11&lt;b&gt;</td>", trend)
        self.assertIn("<td>wheelgap 0.9&lt;i&gt;</td>", trend)
        self.assertNotIn("<b>", trend)
        self.assertNotIn("<i>", trend)

    def test_unsafe_campaign_id_is_rejected(self):
        for ident in ("../x", "x/y", "X", "a--b", ""):
            with self.subTest(ident=ident):
                self.config.write_text(CONFIG.replace('id = "musl-aarch64"', f'id = "{ident}"'), encoding="utf-8")
                self.write_inputs()
                with self.assertRaisesRegex(SystemExit, "invalid campaign id"):
                    build_campaigns.build(self.coverage, self.history, self.changes, self.config, self.site)
        self.assertFalse((self.root / "x").exists())

    def test_title_version_and_left_out_names_are_escaped(self):
        self.config.write_text(CONFIG.replace(
            'title = "musllinux aarch64 wheels for the top 500"', 'title = "Alpine <ARM> & co"'
        ), encoding="utf-8")
        self.scan["packages"][1]["version"] = "2.9<x>"
        self.scan["packages"][3]["name"] = "new<py>"
        page = self.built_page()
        self.assertIn("<h1>Alpine &lt;ARM&gt; &amp; co</h1>", page)
        self.assertIn('<span class="ver">2.9&lt;x&gt;</span>', self.todo(page))
        self.assertIn("(new&lt;py&gt;)", page.split("Left out:", 1)[1])
        for raw in ("<ARM>", "<x>", "new<py>"):
            self.assertNotIn(raw, page)

    def test_trend_marks_a_wheelgap_version_change(self):
        self.history_data[1]["wheelgap"] = "0.9.0"
        self.history_data.append(
            {"date": "2026-11-12", "wheelgap": "0.9.0", "packages": 100,
             "summary": {"3.15 musl-aarch64": {"wheel": 86, "source": 9, "missing": 5, "unknown": 0}}}
        )
        trend = self.trend(self.built_page())
        self.assertIn("<th>Note</th>", trend)
        # 11 Nov is the first 0.9.0 night (9 Nov, before start, was 0.8.0); 12 Nov is unchanged.
        self.assertIn("<tr><td>2026-11-11</td><td>85 / 100</td><td>85%</td><td>wheelgap 0.9.0</td></tr>", trend)
        self.assertIn("<tr><td>2026-11-12</td><td>86 / 100</td><td>86%</td><td></td></tr>", trend)
        self.assertIn("can come from the tool", trend)

    def test_trend_has_no_note_column_without_a_version_change(self):
        trend = self.trend(self.built_page())
        self.assertNotIn("<th>Note</th>", trend)
        self.assertNotIn("wheelgap 0.8.0", trend)
        self.assertNotIn("can come from the tool", trend)

    def test_done_is_gains_on_or_after_start_in_the_current_top_n(self):
        page = self.built_page()
        # 11 Nov gain for NumPy is in; 9 Nov is before start; outside-top is rank 6; linux-x86_64 is wrong target.
        done = page.split(">Done</h2>", 1)[1]
        self.assertIn("NumPy", done)
        self.assertIn("2026-11-11", done)
        self.assertNotIn("2026-11-09", done)
        self.assertNotIn("outside-top", done)
        self.assertNotIn("psycopg2", done)
        # newpy gained the wheel on 11 Nov but no longer has it: not done.
        self.assertNotIn("newpy", done)
        self.assertIn("<td>from source</td>", done)
        self.assertIn("keeps the last 90 days", done)

    def write_baseline(self, **fields):
        # Matches CONFIG: same campaign, top, start and target.
        baseline = {
            "schema": 1, "campaign": "musl-aarch64", "target": "3.15 musl-aarch64", "top": 5,
            "start": "2026-11-10", "recorded": "2026-11-10", "wheelgap": "0.8.0",
            "packages": [
                {"name": "NumPy", "rank": 1, "status": "source"},
                {"name": "psycopg2", "rank": 2, "status": "source"},
                {"name": "newpy", "rank": 4, "status": "missing"},
            ],
        }
        baseline.update(fields)
        (self.root / "campaigns").mkdir(exist_ok=True)
        (self.root / "campaigns" / "musl-aarch64.json").write_text(json.dumps(baseline), encoding="utf-8")

    def done(self, page: str) -> str:
        return page.split(">Done</h2>", 1)[1]

    def set_start(self, start: str):
        self.config.write_text(CONFIG.replace('start = "2026-11-10"', f'start = "{start}"'), encoding="utf-8")

    def recordable(self, extra: str = ""):
        # Top 4 leaves out "failed", whose lookup failed, so a baseline can be recorded.
        self.config.write_text(CONFIG.replace("top = 5", "top = 4") + extra, encoding="utf-8")
        self.write_inputs()

    def record(self, **kwargs):
        return build_campaigns.record_baselines(
            self.coverage, self.changes, self.config, self.root / "campaigns", **kwargs
        )

    def test_done_comes_from_the_recorded_baseline(self):
        self.write_baseline()
        done = self.done(self.built_page())
        # NumPy has the wheel now; its latest gain (11 Nov) is still in the change log.
        self.assertIn("<td>2026-11-11</td>", done)
        self.assertIn("NumPy", done)
        # psycopg2 never gained it; newpy gained it on 11 Nov and lost it again.
        self.assertNotIn("psycopg2", done)
        self.assertNotIn("newpy", done)
        self.assertIn(
            "<strong>1 of the 3</strong> packages on the to-do list at the start (2026-11-10) now has an", done
        )
        self.assertIn("The starting list is recorded in data/campaigns/musl-aarch64.json.", done)
        self.assertNotIn("reconstructed", done)
        self.assertIn("new to the top", done)
        self.assertIn("<td>from source</td>", done)

    def test_done_counts_a_package_that_left_the_top_n(self):
        # outside-top (rank 6) was on the list at the start, then gained the wheel and slipped out of the top 5.
        self.scan["packages"][5]["results"][3] = "wheel"
        self.write_baseline(packages=[{"name": "outside-top", "rank": 5, "status": "missing"}])
        done = self.done(self.built_page())
        self.assertIn("<strong>1 of the 1</strong> package on the to-do list", done)
        self.assertIn("outside-top", done)

    def test_done_ignores_errored_packages(self):
        self.scan["packages"][4]["results"][3] = "wheel"  # "failed" has an error; its results don't count
        self.write_baseline(packages=[{"name": "failed", "rank": 5, "status": "source"}])
        done = self.done(self.built_page())
        self.assertIn("<strong>0 of the 1</strong> package on the to-do list at the start (2026-11-10) now have", done)
        self.assertNotIn("failed", done.split("</p>", 2)[2])

    def test_done_dates_come_from_the_campaign_target_only(self):
        # psycopg2 has a wheel now; its only logged gain is on 3.15 linux-x86_64, not this target.
        self.scan["packages"][1]["results"][3] = "wheel"
        self.write_baseline(packages=[{"name": "psycopg2", "rank": 2, "status": "source"}])
        done = self.done(self.built_page())
        self.assertIn("<td>not in the log</td>", done)
        self.assertNotIn("2026-11-11", done)

    def test_gain_older_than_the_change_log_still_counts_as_done(self):
        self.set_start("2026-08-01")
        self.write_baseline(start="2026-08-01", recorded="2026-08-01")
        # NumPy's gain has aged out of the 90-day log: it's still done, dated before the oldest night kept.
        for entry in self.changes_data:
            entry["gained"] = [g for g in entry["gained"] if g["name"] != "NumPy"]
        done = self.done(self.built_page())
        self.assertIn("NumPy", done)
        self.assertIn("<td>before 2026-11-09</td>", done)
        self.assertIn("<strong>1 of the 3</strong>", done)

    def test_gain_on_a_night_the_log_skipped(self):
        # The log covers the whole campaign but has no gain for NumPy (a wheelgap upgrade night).
        self.write_baseline()
        for entry in self.changes_data:
            entry["gained"] = [g for g in entry["gained"] if g["name"] != "NumPy"]
        done = self.done(self.built_page())
        self.assertIn("<td>not in the log</td>", done)

    def test_baseline_for_other_settings_shows_no_count(self):
        for fields, said in (({"target": "3.14 <musl>"}, "target 3.14 &lt;musl&gt;"),
                             ({"start": "2026-11-01<s>"}, "start 2026-11-01&lt;s&gt;"),
                             ({"top": 500}, "top 500"),
                             ({"campaign": "other"}, "campaign other")):
            with self.subTest(fields=fields):
                self.write_baseline(**fields)
                done = self.done(self.built_page())
                self.assertIn("No progress to show: data/campaigns/musl-aarch64.json was recorded for other settings", done)
                self.assertIn(said, done)
                self.assertIn("Delete it to record a new starting list", done)
                self.assertNotIn("<strong>", done)
                self.assertNotIn("NumPy", done)
                self.assertNotIn("<s>", done)
                self.assertNotIn("<musl>", done)

    def test_bad_baseline_is_rejected(self):
        for fields in ({"schema": 2}, {"packages": [{"rank": 1}]}, {"start": None}, {"campaign": None},
                       {"top": "5"}, {"top": True}):
            with self.subTest(fields=fields):
                self.write_baseline(**fields)
                self.write_inputs()
                with self.assertRaises(SystemExit):
                    build_campaigns.build(self.coverage, self.history, self.changes, self.config, self.site)

    def test_empty_baseline(self):
        self.write_baseline(packages=[])
        done = self.done(self.built_page())
        self.assertIn("Nothing was on the to-do list at the start (2026-11-10).", done)
        self.assertIn("None yet.", done)

    def test_hand_edited_duplicates_count_once(self):
        self.write_baseline(packages=[{"name": "NumPy", "status": "source"}, {"name": "numpy", "status": "source"},
                                      {"name": "psycopg2", "status": "source"}])
        done = self.done(self.built_page())
        self.assertIn("<strong>1 of the 2</strong> packages", done)
        self.assertEqual(done.count('href="../../p/numpy/"'), 1)

    def test_baseline_values_in_the_done_table_are_escaped(self):
        self.write_baseline(recorded="2026-11-12<r>", packages=[{"name": "NumPy", "status": "<s>"}])
        self.changes_data[1]["date"] = "2026-11-11<d>"
        done = self.done(self.built_page())
        self.assertIn("reconstructed on 2026-11-12&lt;r&gt;", done)
        self.assertIn("<td>&lt;s&gt;</td>", done)
        self.assertIn("<td>2026-11-11&lt;d&gt;</td>", done)
        for raw in ("<r>", "<s>", "<d>"):
            self.assertNotIn(raw, done)

    def test_without_a_baseline_the_page_says_so(self):
        done = self.done(self.built_page())
        self.assertIn("isn't recorded yet", done)
        # Today's to-do (psycopg2) plus NumPy, which gained the wheel after the start. newpy also gained it,
        # but had no Python 3.15 Linux wheel at all at the start (it was left out), so it isn't added.
        self.assertIn("<strong>1 of the 2</strong>", done)

    def test_gains_since(self):
        changes = [
            {"date": "2026-11-10", "gained": [{"name": "a", "target": "L", "from": "source", "to": "wheel"}]},
            {"date": "2026-11-11", "gained": [{"name": "b", "target": "L", "from": "missing", "to": "wheel"},
                                              {"name": "c", "target": "other", "from": "source", "to": "wheel"}]},
        ]
        # The start night's own changes are already in its scan; only later nights and this label count.
        self.assertEqual(set(build_campaigns.gains_since(changes, "L", "2026-11-10")), {"b"})
        self.assertEqual(set(build_campaigns.gains_since(changes, "L", "2026-11-09")), {"a", "b"})

    def test_record_baselines_writes_each_campaign_once(self):
        self.recordable()
        self.assertEqual(self.record(), ["musl-aarch64"])
        path = self.root / "campaigns" / "musl-aarch64.json"
        baseline = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(
            {k: baseline[k] for k in ("campaign", "target", "top", "start", "recorded")},
            {"campaign": "musl-aarch64", "target": "3.15 musl-aarch64", "top": 4, "start": "2026-11-10",
             "recorded": "2026-11-11"},
        )
        # psycopg2 is to do today; NumPy gained the wheel after the 10 Nov start (its 9 Nov gain is before
        # it). newpy gained it too but was left out at the start; outside-top is rank 6.
        self.assertEqual(
            baseline["packages"],
            [{"name": "NumPy", "rank": 1, "status": "source"}, {"name": "psycopg2", "rank": 2, "status": "source"}],
        )
        # Never rewritten, even when the scan changes.
        before = path.read_text(encoding="utf-8")
        self.scan["packages"][1]["results"][3] = "wheel"
        self.write_inputs()
        self.assertEqual(self.record(), [])
        self.assertEqual(path.read_text(encoding="utf-8"), before)
        # The page build picks it up from next to coverage.json, and says it was reconstructed after the start.
        self.write_inputs()
        build_campaigns.build(self.coverage, self.history, self.changes, self.config, self.site)
        done = self.done((self.site / "campaigns" / "musl-aarch64" / "index.html").read_text(encoding="utf-8"))
        self.assertIn("was reconstructed on 2026-11-11", done)
        self.assertIn("<strong>2 of the 2</strong> packages on the to-do list at the start (2026-11-10) now have", done)

    def test_recorder_uses_the_campaign_python(self):
        self.recordable('python = "3.13"\n')
        self.assertEqual(self.record(), ["musl-aarch64"])
        baseline = json.loads((self.root / "campaigns" / "musl-aarch64.json").read_text(encoding="utf-8"))
        self.assertEqual(baseline["target"], "3.13 musl-aarch64")
        # On 3.13 musl-aarch64, NumPy and psycopg2 lack the wheel and have one on 3.13 linux-x86_64.
        self.assertEqual([p["name"] for p in baseline["packages"]], ["NumPy", "psycopg2"])

    def test_recorder_waits_for_the_start(self):
        self.recordable()
        self.config.write_text(self.config.read_text(encoding="utf-8").replace("2026-11-10", "2026-11-12"),
                               encoding="utf-8")
        said = []
        self.assertEqual(self.record(log=said.append), [])
        self.assertFalse((self.root / "campaigns" / "musl-aarch64.json").exists())
        self.assertIn("starts on 2026-11-12, after this scan (2026-11-11)", said[0])
        # The start night itself is fine.
        self.config.write_text(self.config.read_text(encoding="utf-8").replace("2026-11-12", "2026-11-11"),
                               encoding="utf-8")
        self.assertEqual(self.record(), ["musl-aarch64"])

    def test_recorder_skips_a_scan_with_failed_lookups(self):
        # Top 5 includes "failed", whose lookup failed even though its stale results look complete.
        self.scan["packages"][4]["results"] = ["source"] * 6
        self.write_inputs()
        said = []
        self.assertEqual(self.record(log=said.append), [])
        self.assertFalse((self.root / "campaigns" / "musl-aarch64.json").exists())
        self.assertIn("1 of its top 5 couldn't be looked up (failed)", said[0])
        # An "unknown" result on the target counts as a failed lookup too.
        self.recordable()
        self.scan["packages"][1]["results"][3] = "unknown"
        self.write_inputs()
        self.assertEqual(self.record(log=said.append), [])
        self.assertIn("(psycopg2)", said[1])

    def test_cli_records_baselines(self):
        self.recordable()
        out = self.root / "baselines"
        result = subprocess.run(
            [sys.executable, str(Path(build_campaigns.__file__)), "--record-baselines",
             str(self.coverage), str(self.changes), str(self.config), str(out)],
            capture_output=True, text=True, check=True,
        )
        self.assertIn("recorded musl-aarch64 baseline", result.stdout)
        self.assertTrue((out / "musl-aarch64.json").exists())

    def test_ask_upstream_and_how_to_link_and_no_javascript(self):
        page = self.built_page()
        self.assertIn("Pick one package, not a list", page)
        self.assertIn("Never file the same request across many projects at once", page)
        self.assertIn("https://github.com/wheelgap/tracker/blob/main/docs/adding-wheels-upstream.md", page)
        self.assertNotIn("<script", page)
        self.assertIn('href="../../style.css"', page)

    def test_appends_campaign_url_to_existing_sitemap(self):
        self.write_inputs()
        build_pages.build(self.coverage, self.site)
        build_campaigns.build(self.coverage, self.history, self.changes, self.config, self.site)
        sitemap = (self.site / "sitemap.xml").read_text(encoding="utf-8")
        self.assertIn("https://wheelgap.github.io/tracker/</loc>", sitemap)
        self.assertIn("https://wheelgap.github.io/tracker/p/numpy/</loc>", sitemap)
        self.assertIn("https://wheelgap.github.io/tracker/campaigns/musl-aarch64/</loc>", sitemap)

    def test_cli_writes_the_page(self):
        self.write_inputs()
        result = subprocess.run(
            [sys.executable, str(Path(build_campaigns.__file__)),
             str(self.coverage), str(self.history), str(self.changes),
             str(self.config), str(self.site)],
            capture_output=True, text=True, check=True,
        )
        self.assertIn("1 pages", result.stdout)
        self.assertTrue((self.site / "campaigns" / "musl-aarch64" / "index.html").exists())

    def test_index_lists_every_campaign_with_escaped_titles(self):
        self.config.write_text(CONFIG + """
[[campaign]]
id = "win-arm64"
title = "Windows <ARM> & friends"
platform = "musl-aarch64"
top = 3
start = "2026-11-11"
python = "3.13"
""", encoding="utf-8")
        self.write_inputs()
        self.assertEqual(
            build_campaigns.build(self.coverage, self.history, self.changes, self.config, self.site), 2
        )
        index = (self.site / "campaigns" / "index.html").read_text(encoding="utf-8")
        rows = index.split("<tbody>", 1)[1].split("</tbody>", 1)[0]
        first, second = [line for line in rows.splitlines() if "<tr>" in line]
        # Config order, each linked to its own page, with platform, coverage share and to-do count.
        self.assertIn('<a href="musl-aarch64/">musllinux aarch64 wheels for the top 500</a>', first)
        self.assertIn("Alpine ARM64", first)
        self.assertIn("<strong>50%</strong>", first)
        self.assertIn("<td>1</td>", first)  # psycopg2
        self.assertIn('<a href="win-arm64/">Windows &lt;ARM&gt; &amp; friends</a>', second)
        self.assertNotIn("<ARM>", index)
        # Top 3 on 3.13: requests has the wheel, NumPy and psycopg2 don't (1 of 3), both to do.
        self.assertIn("1 of 3", second)
        self.assertIn("<td>2</td>", second)
        self.assertNotIn("<script", index)
        self.assertIn('href="../style.css"', index)
        self.assertIn(build_campaigns.CSP, index)
        # Each campaign page links back to the index.
        page = (self.site / "campaigns" / "win-arm64" / "index.html").read_text(encoding="utf-8")
        self.assertIn('<a href="../">Campaigns</a>', page)

    def test_index_is_in_the_sitemap(self):
        self.write_inputs()
        build_pages.build(self.coverage, self.site)
        build_campaigns.build(self.coverage, self.history, self.changes, self.config, self.site)
        sitemap = (self.site / "sitemap.xml").read_text(encoding="utf-8")
        self.assertIn("<loc>https://wheelgap.github.io/tracker/campaigns/</loc>", sitemap)

    def test_home_page_footer_links_the_campaigns_index(self):
        home = (Path(__file__).resolve().parents[1] / "site" / "index.html").read_text(encoding="utf-8")
        footer = home.split("<footer", 1)[1]
        self.assertIn('<a href="campaigns/">Campaigns</a>', footer)

    def test_sources_are_linked_and_escaped(self):
        self.config.write_text(
            CONFIG + 'sources = ["https://example.com/a?b=1&c=<2>", "https://example.org/"]\n', encoding="utf-8"
        )
        page = self.built_page()
        self.assertIn(
            '<p class="meta">Sources: <a href="https://example.com/a?b=1&amp;c=&lt;2&gt;">example.com/a?b=1&amp;c=&lt;2&gt;</a>'
            ' · <a href="https://example.org/">example.org/</a></p>',
            page,
        )
        for line in ('sources = ["http://example.com/"]', 'sources = "https://example.com/"', "sources = [1]"):
            with self.subTest(line=line):
                self.config.write_text(CONFIG + line + "\n", encoding="utf-8")
                with self.assertRaises(SystemExit):
                    build_campaigns.build(self.coverage, self.history, self.changes, self.config, self.site)

    def test_repo_config_has_the_win_arm64_campaign(self):
        root = Path(__file__).resolve().parents[1]
        campaigns = {c["id"]: c for c in build_campaigns.load_campaigns(root / "campaigns.toml")}
        win = campaigns["win-arm64"]
        self.assertEqual((win["platform"], win["top"], win["python"]), ("win-arm64", 500, "3.14"))
        self.assertTrue(win["description"])
        self.assertTrue(win["sources"])
        # Its starting to-do list is recorded, for the same target.
        baseline = build_campaigns.load_baseline(root / "data" / "campaigns" / "win-arm64.json")
        self.assertEqual(baseline["target"], "3.14 win-arm64")
        self.assertEqual(baseline["start"], win["start"])

    def test_rejects_unknown_scan_schema(self):
        self.scan["schema"] = 2
        self.write_inputs()
        with self.assertRaisesRegex(SystemExit, "schema 2.*expected 1"):
            build_campaigns.build(self.coverage, self.history, self.changes, self.config, self.site)
        self.assertFalse((self.site / "campaigns").exists())


if __name__ == "__main__":
    unittest.main()
