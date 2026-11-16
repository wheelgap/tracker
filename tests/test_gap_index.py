import json
import tempfile
import unittest
from pathlib import Path

from scripts import build_gap_index

SHA = "c" * 64


def wheel(name="tiktoken", file="tiktoken-0.14.0-cp314-cp314-win_arm64.whl", **over):
    w = {"name": name, "version": "0.14.0", "target": "3.14 win-arm64", "wheel": file, "sha256": SHA,
         "requires_python": ">=3.9", "verified": "2026-11-16"}  # fmt: skip
    w.update(over)
    return w


class Index(unittest.TestCase):
    def build(self, wheels):
        tmp = Path(tempfile.mkdtemp())
        (tmp / "gap-wheels.json").write_text(json.dumps({"schema": 1, "wheels": wheels}))
        self.assertEqual(build_gap_index.build(tmp / "gap-wheels.json", tmp / "site"), 0)
        return tmp / "site"

    def test_pep_503_pages_link_the_wheels_repo_release(self):
        site = self.build([wheel(), wheel(name="Zope.Interface", file="zope_interface-8.7-cp314-cp314-win_arm64.whl")])
        root = (site / "wheels/simple/index.html").read_text()
        self.assertIn('<a href="tiktoken/">tiktoken</a>', root)
        self.assertIn('<a href="zope-interface/">zope-interface</a>', root)  # normalised as PEP 503 says
        page = (site / "wheels/simple/tiktoken/index.html").read_text()
        self.assertIn(
            '<a href="https://github.com/wheelgap/wheels/releases/download/gap-wheels/'
            f'tiktoken-0.14.0-cp314-cp314-win_arm64.whl#sha256={SHA}" data-requires-python="&gt;=3.9">',
            page,
        )
        self.assertNotIn("wheelgap/tracker/releases", page)
        record = json.loads((site / "data/gap-wheels.json").read_text())
        self.assertEqual(len(record["wheels"]), 2)  # the transparency record keeps its old address
        self.assertIn('name="pypi:repository-version"', page)

    def test_entries_that_cant_be_linked_safely_are_left_out(self):
        site = self.build([wheel(file="x.whl\"><script>"), wheel(sha256="nothex"), wheel(name="../evil")])
        self.assertEqual(list((site / "wheels/simple").iterdir()), [site / "wheels/simple/index.html"])

    def test_requires_python_is_escaped(self):
        site = self.build([wheel(requires_python='"><x')])
        project = (site / "wheels/simple/tiktoken/index.html").read_text()
        self.assertIn('data-requires-python="&quot;&gt;&lt;x"', project)

    def test_the_page_says_where_the_wheels_went(self):
        page = (self.build([wheel()]) / "wheels/index.html").read_text()
        self.assertIn('<link rel="canonical" href="https://wheelgap.github.io/wheels/">', page)
        self.assertIn("--extra-index-url https://wheelgap.github.io/wheels/simple/", page)
        self.assertNotIn("<table", page)

    def test_no_file_writes_only_the_moved_page(self):
        tmp = Path(tempfile.mkdtemp())
        self.assertEqual(build_gap_index.build(tmp / "missing.json", tmp / "site"), 1)
        self.assertIn("Gap wheels have moved", (tmp / "site/wheels/index.html").read_text())
        self.assertFalse((tmp / "site/wheels/simple").exists())

    def test_malformed_file_writes_only_the_moved_page(self):
        tmp = Path(tempfile.mkdtemp())
        (tmp / "gap-wheels.json").write_text("{not json")
        self.assertEqual(build_gap_index.build(tmp / "gap-wheels.json", tmp / "site"), 1)
        self.assertFalse((tmp / "site/wheels/simple").exists())

    def test_no_wheels_list_writes_only_the_moved_page(self):
        for body in ('{"schema": 1}', '{"wheels": {}}', "[]"):
            tmp = Path(tempfile.mkdtemp())
            (tmp / "gap-wheels.json").write_text(body)
            self.assertEqual(build_gap_index.build(tmp / "gap-wheels.json", tmp / "site"), 1, body)
            self.assertFalse((tmp / "site/wheels/simple").exists())
            self.assertFalse((tmp / "site/data/gap-wheels.json").exists())
