import json
import tempfile
import unittest
from pathlib import Path

from scripts import build_gap_index

SHA = "c" * 64


def wheel(name="tiktoken", file="tiktoken-0.14.0-cp314-cp314-win_arm64.whl", **over):
    w = {"name": name, "version": "0.14.0", "target": "3.14 win-arm64", "wheel": file, "sha256": SHA,
         "requires_python": ">=3.9", "verified": "2026-11-16",
         "builds": ["https://github.com/wheelgap/tracker/actions/runs/1", "javascript:alert(1)"]}  # fmt: skip
    w.update(over)
    return w


class Index(unittest.TestCase):
    def build(self, wheels):
        tmp = Path(tempfile.mkdtemp())
        (tmp / "gap-wheels.json").write_text(json.dumps({"schema": 1, "wheels": wheels}))
        build_gap_index.build(tmp / "gap-wheels.json", tmp / "site")
        return tmp / "site"

    def test_pep_503_pages_with_hashes(self):
        site = self.build([wheel(), wheel(name="Zope.Interface", file="zope_interface-8.7-cp314-cp314-win_arm64.whl")])
        root = (site / "wheels/simple/index.html").read_text()
        self.assertIn('<a href="tiktoken/">tiktoken</a>', root)
        self.assertIn('<a href="zope-interface/">zope-interface</a>', root)  # normalised as PEP 503 says
        page = (site / "wheels/simple/tiktoken/index.html").read_text()
        self.assertIn(
            '<a href="https://github.com/wheelgap/tracker/releases/download/gap-wheels/'
            f'tiktoken-0.14.0-cp314-cp314-win_arm64.whl#sha256={SHA}" data-requires-python="&gt;=3.9">',
            page,
        )
        self.assertIn('name="pypi:repository-version"', page)

    def test_entries_that_cant_be_linked_safely_are_left_out(self):
        site = self.build([wheel(file="x.whl\"><script>"), wheel(sha256="nothex"), wheel(name="../evil")])
        self.assertEqual(list((site / "wheels/simple").iterdir()), [site / "wheels/simple/index.html"])

    def test_the_human_page_escapes_and_links_only_github_builds(self):
        site = self.build([wheel(version="<b>1</b>", requires_python='"><x')])
        page = (site / "wheels/index.html").read_text()
        self.assertIn("&lt;b&gt;1&lt;/b&gt;", page)
        self.assertNotIn("javascript:", page)
        self.assertIn("https://github.com/wheelgap/tracker/actions/runs/1", page)
        self.assertIn("pip only, for now", page)
        self.assertNotIn("unsafe-best-match", page)
        project = (site / "wheels/simple/tiktoken/index.html").read_text()
        self.assertIn('data-requires-python="&quot;&gt;&lt;x"', project)

    def test_no_file_yet(self):
        tmp = Path(tempfile.mkdtemp())
        build_gap_index.build(tmp / "missing.json", tmp / "site")
        self.assertIn("No wheels published yet", (tmp / "site/wheels/index.html").read_text())
