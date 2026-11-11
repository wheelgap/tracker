"""Offline checks for the musllinux aarch64 campaign page."""

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
        return page.split(">To do</h2>", 1)[1].split("Left out:", 1)[0].split(">Done</h2>", 1)[0]

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

    def test_trend_uses_history_for_the_badge_python_label(self):
        page = self.built_page()
        self.assertIn("2026-11-09", page)
        self.assertIn("80 / 100", page)
        self.assertIn("85 / 100", page)
        self.assertIn("nightly scan", page.lower())

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

    def test_rejects_unknown_scan_schema(self):
        self.scan["schema"] = 2
        self.write_inputs()
        with self.assertRaisesRegex(SystemExit, "schema 2.*expected 1"):
            build_campaigns.build(self.coverage, self.history, self.changes, self.config, self.site)
        self.assertFalse((self.site / "campaigns").exists())


if __name__ == "__main__":
    unittest.main()
