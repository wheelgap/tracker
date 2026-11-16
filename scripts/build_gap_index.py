"""Keep the tracker's old gap wheels index working: the wheels moved to wheelgap/wheels on 2026-11-16.

    python3 scripts/build_gap_index.py gap-wheels.json _site

Reads wheelgap/wheels' data/gap-wheels.json (scan.yml downloads it from wheelgap.github.io/wheels/data/) and writes
<site>/wheels/simple/index.html and <site>/wheels/simple/<project>/index.html, a PEP 503 index linking each wheel on
wheelgap/wheels' `gap-wheels` release with its sha256, so `--extra-index-url .../tracker/wheels/simple/` keeps
working, and copies the JSON to <site>/data/gap-wheels.json, its old address. <site>/wheels/index.html says where
the wheels went. Without a usable JSON only that page is written and it exits 1. Runs at publish time; nothing is
committed. Plain HTML, no JavaScript, under the site's usual CSP.
"""

from __future__ import annotations

import html
import json
import re
import sys
from pathlib import Path

if __package__:  # imported as scripts.build_gap_index (the tests)
    from . import build_campaigns
else:
    import build_campaigns

BASE_URL = build_campaigns.BASE_URL
CSP = build_campaigns.CSP
INDEX_URL = f"{BASE_URL}wheels/simple/"
NEW_HOME = "https://wheelgap.github.io/wheels/"
NEW_INDEX = f"{NEW_HOME}simple/"
RELEASE_URL = "https://github.com/wheelgap/wheels/releases/download/gap-wheels/"
ASSET = re.compile(r"[A-Za-z0-9._-]{1,200}\.whl", re.ASCII)
SHA256 = re.compile(r"[0-9a-f]{64}", re.ASCII)
PROJECT = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*", re.ASCII)


def canonical(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def usable(w: object) -> bool:
    """An entry the index can link safely: everything that goes into a URL or a page has the expected shape."""
    return (
        isinstance(w, dict)
        and isinstance(w.get("name"), str)
        and PROJECT.fullmatch(canonical(w["name"])) is not None
        and isinstance(w.get("wheel"), str)
        and ASSET.fullmatch(w["wheel"]) is not None
        and isinstance(w.get("sha256"), str)
        and SHA256.fullmatch(w["sha256"]) is not None
    )


def projects(wheels: list) -> dict[str, list[dict]]:
    out: dict[str, list[dict]] = {}
    for w in wheels:
        if usable(w):
            out.setdefault(canonical(w["name"]), []).append(w)
    return {k: sorted(v, key=lambda w: w["wheel"]) for k, v in sorted(out.items())}


def simple_page(title: str, links: list[str]) -> str:
    body = "\n".join(f"    {link}<br>" for link in links)
    return f"""<!doctype html>
<html>
  <head>
    <meta name="pypi:repository-version" content="1.0">
    <title>{html.escape(title)}</title>
  </head>
  <body>
{body}
  </body>
</html>
"""


def project_links(wheels: list[dict]) -> list[str]:
    e = html.escape
    links = []
    for w in wheels:
        rp = w.get("requires_python")
        attr = f' data-requires-python="{e(rp)}"' if isinstance(rp, str) and rp.strip() else ""
        links.append(f'<a href="{e(RELEASE_URL + w["wheel"])}#sha256={w["sha256"]}"{attr}>{e(w["wheel"])}</a>')
    return links


def moved_page() -> str:
    e = html.escape
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <meta http-equiv="Content-Security-Policy" content="{CSP}">
  <title>Gap wheels have moved · wheelgap tracker</title>
  <meta name="description" content="The gap wheels now live at {e(NEW_HOME)}">
  <link rel="canonical" href="{e(NEW_HOME)}">
  <link rel="stylesheet" href="../style.css">
  <link rel="icon" href="../favicon.svg" type="image/svg+xml">
</head>
<body>
  <header class="wrap">
    <p class="eyebrow"><a href="../">wheelgap tracker</a></p>
    <h1>Gap wheels have moved</h1>
    <p class="lede">The gap wheels now have their own repo, <a href="https://github.com/wheelgap/wheels">wheelgap/wheels</a>, and their own page: <a href="{e(NEW_HOME)}">{e(NEW_HOME)}</a>.</p>
  </header>
  <main class="wrap">
    <section>
      <p>Use the new index:</p>
      <pre><code>pip install --extra-index-url {e(NEW_INDEX)} PACKAGE</code></pre>
      <p>The old index, <code>{e(INDEX_URL)}</code>, still works for now and serves the same files.</p>
    </section>
  </main>
</body>
</html>
"""


def build(index_path: Path, site: Path) -> int:
    (site / "wheels").mkdir(parents=True, exist_ok=True)
    (site / "wheels" / "index.html").write_text(moved_page(), encoding="utf-8")
    try:
        data = json.loads(index_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        print(f"gap wheels: no index, {index_path} can't be read: {exc}", file=sys.stderr)
        return 1
    if not isinstance(data, dict) or not isinstance(data.get("wheels"), list):
        print(f"gap wheels: no index, {index_path} has no wheels list", file=sys.stderr)
        return 1
    by_project = projects(data["wheels"])
    simple = site / "wheels" / "simple"
    simple.mkdir(parents=True, exist_ok=True)
    root_links = [f'<a href="{p}/">{p}</a>' for p in by_project]
    (simple / "index.html").write_text(simple_page("Simple index", root_links), encoding="utf-8")
    for project, files in by_project.items():
        (simple / project).mkdir(exist_ok=True)
        (simple / project / "index.html").write_text(
            simple_page(f"Links for {project}", project_links(files)), encoding="utf-8"
        )
    # The transparency record stays at its old address too.
    (site / "data").mkdir(exist_ok=True)
    (site / "data" / "gap-wheels.json").write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    print(f"gap wheels: {sum(len(v) for v in by_project.values())} wheels of {len(by_project)} projects")
    return 0


if __name__ == "__main__":
    sys.exit(build(Path(sys.argv[1]), Path(sys.argv[2])))
