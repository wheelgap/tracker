"""Publish the gap wheels index: a PEP 503 simple index and a page explaining it, from data/gap-wheels.json.

    python3 scripts/build_gap_index.py data/gap-wheels.json _site

Writes <site>/wheels/simple/index.html (every project), <site>/wheels/simple/<project>/index.html (its wheels, each
linking to the file on the `gap-wheels` release with its sha256, which pip checks) and <site>/wheels/index.html (what
the index is, how to use it, why the wheels can be trusted, and how a project opts out). gap_wheels.py publish writes
the JSON; it is also published as data/gap-wheels.json, the transparency record. Runs at publish time; nothing is
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
RELEASE_URL = "https://github.com/wheelgap/tracker/releases/download/gap-wheels/"
ASSET = re.compile(r"[A-Za-z0-9._-]{1,200}\.whl", re.ASCII)
SHA256 = re.compile(r"[0-9a-f]{64}", re.ASCII)
PROJECT = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*", re.ASCII)
OPT_OUT_URL = (
    "https://github.com/wheelgap/tracker/issues/new?title=Gap%20wheels%20opt-out%3A%20PROJECT"
    "&body=Please%20stop%20publishing%20gap%20wheels%20for%20PROJECT."
)


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


def human_page(by_project: dict[str, list[dict]]) -> str:
    e = html.escape
    rows = []
    for project, wheels in by_project.items():
        for w in wheels:
            builds = [u for u in w.get("builds") or [] if isinstance(u, str) and u.startswith("https://github.com/")]
            links = ", ".join(f'<a href="{e(u)}">build {n}</a>' for n, u in enumerate(builds, 1))
            rows.append(
                f'          <tr><td><a href="../p/{e(project)}/">{e(w["name"])}</a></td>'
                f"<td>{e(str(w.get('version', '')))}</td><td>{e(str(w.get('target', '')))}</td>"
                f"<td>{e(str(w.get('verified') or ''))}</td><td>{links}</td>"
                f'<td><a href="{e(RELEASE_URL + w["wheel"])}">wheel</a></td></tr>'
            )
    count = sum(len(v) for v in by_project.values())
    table = (
        '        <div class="matrix-scroll"><table class="matrix">\n'
        "          <thead><tr><th>Package</th><th>Version</th><th>Target</th><th>Verified</th><th>Builds</th>"
        "<th>File</th></tr></thead>\n"
        "          <tbody>\n" + "\n".join(rows) + "\n          </tbody>\n"
        "        </table></div>\n"
        if rows
        else '        <p class="note">No wheels published yet.</p>\n'
    )
    summary = "Wheels PyPI is missing for Windows on ARM and new Pythons, built twice from the sdist and published only when both builds match."
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <meta http-equiv="Content-Security-Policy" content="{CSP}">
  <title>Gap wheels · wheelgap tracker</title>
  <meta name="description" content="{e(summary)}">
  <link rel="canonical" href="{e(BASE_URL)}wheels/">
  <link rel="stylesheet" href="../style.css">
  <link rel="icon" href="../favicon.svg" type="image/svg+xml">
</head>
<body>
  <header class="wrap">
    <p class="eyebrow"><a href="../">wheelgap tracker</a></p>
    <h1>Gap wheels</h1>
    <p class="lede">Wheels PyPI doesn't have yet, for popular packages on platforms people are moving to. Without one, <code>pip install</code> compiles the package, and often fails. {count} wheel{'' if count == 1 else 's'} so far.</p>
  </header>
  <main class="wrap">
    <section>
      <h2>Use it</h2>
      <p>Add the index next to PyPI. pip looks at both and takes a wheel from here where it fits your platform and PyPI has none:</p>
      <pre><code>pip install --extra-index-url {e(INDEX_URL)} PACKAGE</code></pre>
      <p>Each wheel is for one release. If PyPI has a newer release with no wheel for your platform, pip builds that one from source; add <code>--prefer-binary</code> to take the wheel here instead.</p>
      <p>pip only, for now: uv ranks an extra index above PyPI and, for a version found here, would see only this one Windows wheel and none of PyPI's files.</p>
    </section>
    <section>
      <h2>What is checked</h2>
      <ul>
        <li>It is built from the sdist PyPI publishes for that release (its sha256 is checked against PyPI's). Build tools come from PyPI too, and a build may download more (Rust crates, for example).</li>
        <li>It is built <strong>twice</strong>, in two separate GitHub Actions runs, and published only when both builds are byte-identical. A build that isn't reproducible, or that something changed in one run only, is never published.</li>
        <li>It carries exactly one tag, the Python and platform it was built and tested on, and every DLL its extension modules import is either beside them in the wheel or one every Windows machine with that Python has.</li>
        <li>It is installed into a fresh environment on a fresh runner of its platform and imported; on free-threaded Python the GIL must stay off. That checks the wheel works; it isn't a security check.</li>
        <li>The index carries each file's sha256, which pip checks. Each file has an attestation that the tracker's gap-wheels workflow published it: <code>gh attestation verify FILE.whl -R wheelgap/tracker</code>.</li>
        <li>Every published wheel, with its sdist's sha256 and links to both build runs, is in <a href="../data/gap-wheels.json">gap-wheels.json</a>.</li>
      </ul>
      <p>What it can't promise: a wheel is exactly as trustworthy as the sdist it was built from, since building the package yourself runs the same code. Only releases whose PyPI metadata names an OSI-approved licence are published, and for now only Windows wheels: a Linux wheel needs its libraries bundled and a manylinux or musllinux tag first.</p>
    </section>
    <section>
      <h2>Maintainers</h2>
      <p>These wheels stand in until your release ships its own; when it does, tell us in an issue and ours come down. If you'd rather we didn't publish wheels of your project at all, <a href="{e(OPT_OUT_URL)}">open an opt-out issue</a>: we take them down by hand, and the weekly batch keeps them out. Want the build that worked here? Each row links to both build runs.</p>
    </section>
    <section>
      <h2>Published wheels</h2>
{table}    </section>
  </main>
</body>
</html>
"""


def build(index_path: Path, site: Path) -> int:
    data = json.loads(index_path.read_text(encoding="utf-8")) if index_path.is_file() else {"wheels": []}
    wheels = data.get("wheels") if isinstance(data, dict) and isinstance(data.get("wheels"), list) else []
    by_project = projects(wheels)
    simple = site / "wheels" / "simple"
    simple.mkdir(parents=True, exist_ok=True)
    root_links = [f'<a href="{p}/">{p}</a>' for p in by_project]
    (simple / "index.html").write_text(simple_page("Simple index", root_links), encoding="utf-8")
    for project, files in by_project.items():
        (simple / project).mkdir(exist_ok=True)
        (simple / project / "index.html").write_text(
            simple_page(f"Links for {project}", project_links(files)), encoding="utf-8"
        )
    (site / "wheels" / "index.html").write_text(human_page(by_project), encoding="utf-8")
    build_campaigns.add_to_sitemap(site, [f"{BASE_URL}wheels/"])
    print(f"gap wheels: {sum(len(v) for v in by_project.values())} wheels of {len(by_project)} projects")
    return 0


if __name__ == "__main__":
    sys.exit(build(Path(sys.argv[1]), Path(sys.argv[2])))
