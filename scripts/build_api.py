"""Publish the Data API page (#270): what the per-package JSON files hold, and how to fetch them.

    python scripts/build_api.py _site

Writes <site>/api/index.html, documenting the files build_lookup.py and build_badges.py write (data/packages/<name>.json,
data/packages.json, data/pyodide.json and the badges), and adds it to <site>/sitemap.xml, so run it after
build_pages.py, which writes the sitemap. The page's example of `changes` and `drops` is EXAMPLE, which the tests
check against what build_lookup.py writes and against the README. Runs at publish time; nothing is committed. Plain
HTML, no JavaScript, under the site's usual CSP.
"""

from __future__ import annotations

import html
import json
import sys
from pathlib import Path

if __package__:  # imported as scripts.build_api (the tests)
    from . import build_campaigns, build_lookup, update_changes
else:  # run as python scripts/build_api.py
    import build_campaigns
    import build_lookup
    import update_changes

BASE_URL = build_campaigns.BASE_URL
CSP = build_campaigns.CSP
README_URL = "https://github.com/wheelgap/tracker#api-for-scripts-and-agents"
# A package's `changes` and `drops` after one gained wheel and one dropped platform (the README shows the same).
EXAMPLE = {
    "changes": [{"date": "2026-11-26", "from": "source", "target": "3.14 linux-aarch64", "to": "wheel",
                 "version": "2.1.0"}],
    "drops": [{"date": "2026-11-25", "from": "2.0.4", "target": "3.14 macos-arm64", "version": "2.1.0"}],
}  # fmt: skip
CURL = f"curl -fsS {BASE_URL}data/packages/numpy.json | jq '.results[\"3.13 linux-aarch64\"]'"


def example() -> str:
    return json.dumps(EXAMPLE, indent=2)


def page() -> str:
    e = html.escape
    limit = build_lookup.PER_TARGET
    summary = (
        "One small static JSON file per package: its wheel status on every Python and platform the tracker scans, "
        "its notes and build lab results, and its recent wheel changes and dropped platforms."
    )
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <meta http-equiv="Content-Security-Policy" content="{CSP}">
  <title>Data API · wheelgap tracker</title>
  <meta name="description" content="{e(summary)}">
{build_campaigns.og_tags("Data API · wheelgap tracker", summary, BASE_URL + "api/")}
  <link rel="canonical" href="{e(BASE_URL)}api/">
  <link rel="stylesheet" href="../style.css">
  <link rel="icon" href="../favicon.svg" type="image/svg+xml">
</head>
<body>
  <header class="wrap">
    <p class="eyebrow"><a href="../">wheelgap tracker</a></p>
{build_campaigns.site_nav("../")}
    <h1>Data API</h1>
    <p class="lede">{e(summary)} Static files, rebuilt after every nightly scan, so a script or an agent can ask "does numpy have a wheel for Linux ARM64 on Python 3.13, and has that changed lately?" without downloading the whole scan or reading HTML.</p>
  </header>
  <main class="wrap">
    <section aria-labelledby="data-api">
      <h2 id="data-api">Data API</h2>
      <pre><code>{e(CURL)}</code></pre>
      <p>Use the package's <a href="https://packaging.python.org/en/latest/specifications/name-normalization/">normalized name</a> (lowercase, with runs of <code>-</code>, <code>_</code> and <code>.</code> replaced by <code>-</code>, so <code>Foo_Bar</code> is <code>foo-bar</code>). A 404 means the package isn't in the scan's top 5,000, or the name isn't normalized.</p>
      <ul>
        <li><code>data/packages/&lt;name&gt;.json</code>, one per package (a few KB): <code>schema</code>, <code>name</code>, <code>version</code> (the latest release, the one checked), <code>rank</code>, <code>pure</code>, <code>error</code>, the scan's <code>generated</code> time and <code>wheelgap</code> version, and <code>results</code>, keyed by target label (the same names wheelgap uses, such as <code>"3.14 win-arm64"</code>): <code>wheel</code>, <code>source</code> (builds from source on install), <code>missing</code> (can't install), <code>unknown</code> (the lookup failed) or <code>bundled</code> (Pyodide only: no wheel on PyPI, but Pyodide ships its own build). It also has <code>pyodide</code> and <code>bundled</code> (the Pyodide release behind each <code>bundled</code> result), <code>notes</code>, <code>builds</code> (the build lab's results), <code>changes</code> and <code>drops</code>, and for some packages <code>install</code>, <code>way_out</code> and <code>issues_url</code>.</li>
        <li><a href="../data/packages.json"><code>data/packages.json</code></a>: every package with its version (<code>packages</code>: <code>{{name: version}}</code>), and the <code>targets</code>, with the glibc, musl and macOS baselines each result assumes.</li>
        <li><a href="../data/pyodide.json"><code>data/pyodide.json</code></a>: per Python, the pinned Pyodide release and its lock file (<code>{{"pythons": {{"3.13": {{pyodide, lock}}}}}}</code>).</li>
        <li><code>badge/&lt;name&gt;.json</code> (and <code>badge/&lt;name&gt;-tree.json</code> for the top 500, counting whole dependency trees): <a href="https://shields.io/badges/endpoint-badge">shields.io endpoint</a> badges for a README.</li>
      </ul>
      <h3>Recent changes and drops</h3>
      <p>In a package's file, <code>changes</code> is, for each target, its {limit} newest wheels gained or lost in the nightly scans (the log keeps {update_changes.KEEP_DAYS} days), as <code>{{date, from, target, to, version}}</code>, where <code>from</code> and <code>to</code> are results. <code>drops</code> is, for each target, its {limit} newest <a href="../drops/">dropped platforms</a> (kept for a year), as <code>{{date, from, target, version}}</code>, where <code>from</code> is the release that had the wheel and <code>version</code> the one without it. Both are newest first, and <code>[]</code> when there are none. For example:</p>
      <pre><code>{e(example())}</code></pre>
      <p>Every file carries <code>"schema": 1</code>. New fields are added without changing it; it goes up only if a field changes meaning or goes away. Every field is described in the <a href="{e(README_URL)}">README</a>. The data is CC0.</p>
    </section>
  </main>
{build_campaigns.FOOTER}
</body>
</html>
"""


def build(site: Path) -> None:
    (site / "api").mkdir(parents=True, exist_ok=True)
    (site / "api" / "index.html").write_text(page(), encoding="utf-8")
    build_campaigns.add_to_sitemap(site, [f"{BASE_URL}api/"])


if __name__ == "__main__":
    build(Path(sys.argv[1]))
    print(f"api: wrote {Path(sys.argv[1]) / 'api' / 'index.html'}")
