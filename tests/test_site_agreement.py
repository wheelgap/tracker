"""The site's agreement line (renderAgreement in site/app.js), run in node with a stand-in DOM. Skipped without node;
the Check workflow has it."""

import json
import shutil
import subprocess
import unittest
from pathlib import Path

APP = Path(__file__).parents[1] / "site" / "app.js"
NOW = "2026-11-13T00:00:00Z"

HARNESS = r"""
const src = require("fs").readFileSync(process.argv[1], "utf8");
const start = src.indexOf("// data/agreement.json");
const end = src.indexOf("function renderTabs");
const el = { hidden: false, textContent: "stale", kids: [], append(x) { this.kids.push(x); } };
const document = { getElementById: () => el, createElement: (tag) => ({ tag }) };
const $ = (id) => document.getElementById(id);
const fmt = (n) => (typeof n === "number" ? n.toLocaleString("en-GB") : String(n ?? ""));
eval(src.slice(start, end));
const out = [];
for (const a of JSON.parse(process.argv[2])) {
  el.hidden = false; el.textContent = "stale"; el.kids = [];
  renderAgreement(a, Date.parse(process.argv[3]));
  const links = el.kids.map((k) => [k.tag, k.href, k.textContent]);
  out.push({ hidden: el.hidden, text: el.hidden ? null : el.textContent, links });
}
console.log(JSON.stringify(out));
"""


def good(**fields):
    data = {"date": "2026-11-12", "checks": 800, "matched": 799, "skipped": 4, "errors": 1}
    data["compiled"] = {"packages": 36, "checks": 144, "matched": 144}
    return data | fields


@unittest.skipUnless(shutil.which("node"), "node isn't installed")
class RenderAgreementTests(unittest.TestCase):
    def render(self, *cases):
        proc = subprocess.run(
            ["node", "-e", HARNESS, str(APP), json.dumps(cases), NOW], capture_output=True, text=True, check=True
        )
        return json.loads(proc.stdout)

    def test_shown(self):
        [shown] = self.render(good())
        self.assertFalse(shown["hidden"])
        self.assertEqual(
            shown["text"],
            "Cross-checked against pip on 2026-11-12, with wheelgap's own tag lists on both sides: they agree on 99.8% "
            "of 800 checks, and on 100% of the 144 for the 36 packages without a pure-Python wheel (the rest install "
            "everywhere). 4 skipped, 1 without an answer from pip. ",
        )
        self.assertEqual(shown["links"], [["a", "data/agreement.json", "Details"]])

    def test_rounds_down(self):
        [shown] = self.render(good(checks=10000, matched=9999))
        self.assertIn("99.9% of 10,000 checks", shown["text"])  # 99.99 never shows as 100.0

    def test_without_compiled_counts(self):
        [shown] = self.render(good(compiled=None))
        self.assertIn("of 800 checks. 4 skipped", shown["text"])

    def test_markup_is_text(self):
        # The date must be YYYY-MM-DD; anything else hides the line, so no file content reaches the page as HTML.
        [shown] = self.render(good(date="<img src=x onerror=alert(1)>"))
        self.assertTrue(shown["hidden"])

    def test_hidden(self):
        cases = [
            None,
            "800",
            good(checks="800"),
            good(checks=0, matched=0),
            good(matched=801),
            good(errors=-1),
            good(skipped=None),
            good(date="2026-11-09"),  # 4 days before NOW: stale
            good(date="2026-11-20"),  # in the future
        ]
        for case, shown in zip(cases, self.render(*cases), strict=True):
            with self.subTest(case=case):
                self.assertTrue(shown["hidden"])

    def test_still_shown_three_days_on(self):
        [shown] = self.render(good(date="2026-11-10"))
        self.assertFalse(shown["hidden"])


if __name__ == "__main__":
    unittest.main()
