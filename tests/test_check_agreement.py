"""Offline checks for check_agreement.py, which the commit job runs before adding data/agreement.json."""

import copy
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from scripts import check_agreement

GOOD = {
    "schema": 1,
    "date": "2026-11-12",
    "scan": "2026-11-12T03:20:00+00:00",
    "wheelgap": "0.9.0",
    "pip": "26.3",
    "top": 200,
    "targets": ["3.13 linux-x86_64", "3.13 win-arm64"],
    "packages": 200,
    "checks": 400,
    "matched": 399,
    "mismatches": [
        {
            "name": "disputed",
            "version": "1.0",
            "target": "3.13 win-arm64",
            "wheelgap": "source",
            "pip": "wheel",
            "file": "disputed-1.0-cp313-cp313-win_arm64.whl",
        }
    ],
    "compiled": {"packages": 36, "checks": 72, "matched": 71},
    "skipped": 0,
    "errors": 3,
}


def changed(**fields):
    data = copy.deepcopy(GOOD)
    data.update(fields)
    return data


class ProblemsTests(unittest.TestCase):
    def test_good(self):
        self.assertEqual(check_agreement.problems(GOOD), [])

    def test_bad(self):
        mismatch = GOOD["mismatches"][0]
        cases = {
            "not an object": [],
            "extra key": changed(html="<script>"),
            "missing key": {k: v for k, v in GOOD.items() if k != "errors"},
            "schema": changed(schema=2),
            "date": changed(date="12 November"),
            "scan": changed(scan="x" * 41),
            "wheelgap version": changed(wheelgap="<b>1</b>"),
            "pip version": changed(pip=None),
            "count as string": changed(checks="400"),
            "count as bool": changed(skipped=True),
            "negative count": changed(errors=-1),
            "float count": changed(top=200.0),
            "matched above checks": changed(matched=401, mismatches=[]),
            "too many errors": changed(errors=22),
            "targets": changed(targets="3.13 linux-x86_64"),
            "compiled keys": changed(compiled={"packages": 1, "checks": 1}),
            "compiled count": changed(compiled={"packages": 1, "checks": "1", "matched": 1}),
            "compiled above totals": changed(compiled={"packages": 1, "checks": 401, "matched": 1}),
            "mismatch count": changed(mismatches=[]),
            "mismatch extra key": changed(mismatches=[mismatch | {"note": "x"}]),
            "mismatch status": changed(mismatches=[mismatch | {"wheelgap": "<i>"}]),
            "mismatch pip": changed(mismatches=[mismatch | {"pip": "yes"}]),
            "mismatch file on no wheel": changed(mismatches=[mismatch | {"pip": "no wheel"}]),
            "mismatch name not text": changed(mismatches=[mismatch | {"name": ["a"]}]),
            "mismatch control character": changed(mismatches=[mismatch | {"name": "a\nb"}]),
        }
        for case, data in cases.items():
            with self.subTest(case):
                self.assertTrue(check_agreement.problems(data))


class FileTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.path = Path(directory.name) / "agreement.json"

    def run_script(self):
        script = Path(__file__).parents[1] / "scripts" / "check_agreement.py"
        return subprocess.run([sys.executable, str(script), str(self.path)], capture_output=True, text=True)

    def test_good_file_exits_0(self):
        self.path.write_text(json.dumps(GOOD), encoding="utf-8")
        self.assertEqual(self.run_script().returncode, 0)

    def test_bad_files_exit_1(self):
        for content in (b"{not json", b"\xff\xfe", json.dumps(changed(schema=2)).encode()):
            with self.subTest(content=content[:10]):
                self.path.write_bytes(content)
                proc = self.run_script()
                self.assertEqual(proc.returncode, 1)
                self.assertIn("agreement.json:", proc.stderr)

    def test_size_cap(self):
        self.path.write_text(" " * (check_agreement.MAX_BYTES + 1) + json.dumps(GOOD), encoding="utf-8")
        self.assertIn("bytes, more than", check_agreement.check(self.path)[0])


if __name__ == "__main__":
    unittest.main()
