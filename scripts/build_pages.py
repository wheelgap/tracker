"""Build a static HTML page per package from a wheelgap coverage scan, plus a sitemap.

    python scripts/build_pages.py data/coverage.json _site [notes_dir]

Writes <site>/p/<name>/index.html for each package (PEP 503 canonical name) and
<site>/sitemap.xml listing the main page and every package page. Each page shows
the package's current notes from notes_dir (default: the repository's notes/) and,
when it has a gap, an "Add a note" link. A `bundled` cell says which version Pyodide ships
(from the committed lock files, see pyodide_locks.py). Runs at publish time; the files are not
committed. The pages are plain HTML (no JavaScript) and reuse site/style.css, so
search engines can find "does <package> have ARM wheels".
"""

from __future__ import annotations

import html
import json
import re
import sys
from pathlib import Path
from urllib.parse import quote

if __package__:  # imported as scripts.build_pages (the tests)
    from . import check_notes, pyodide_locks
else:  # run as python scripts/build_pages.py
    import check_notes
    import pyodide_locks

SCAN_SCHEMA = 1
SAFE_NAME = re.compile(r"[a-z0-9]+(-[a-z0-9]+)*")
BASE_URL = "https://wheelgap.github.io/tracker/"
REPO_URL = "https://github.com/wheelgap/tracker"
NOTES_DIR = Path(__file__).resolve().parent.parent / "notes"
KIND_TEXT = {
    "workaround": "Workaround",
    "alternative": "Alternative",
    "upstream-issue": "Upstream issue",
    "other-source": "Other source",
}
GAP = ("source", "missing")  # not `bundled`: Pyodide's own build installs
SCHEME_RE = re.compile(r"^https://", re.IGNORECASE)  # shown without it; the checker allows only https

# Same order and names as PLATFORM_NAMES in site/app.js.
PLATFORM_NAMES = {
    "linux-x86_64": "Linux x86_64",
    "linux-aarch64": "Linux ARM64",
    "musl-x86_64": "Alpine x86_64",
    "musl-aarch64": "Alpine ARM64",
    "macos-arm64": "macOS Apple silicon",
    "macos-x86_64": "macOS Intel",
    "win-amd64": "Windows x86_64",
    "win-arm64": "Windows ARM64",
    "pyodide": "Pyodide (browser)",
}
STATUS_TEXT = {
    "wheel": "wheel",
    "bundled": "bundled",
    "source": "from source",
    "missing": "can't install",
    "unknown": "unknown",
}
# Under the table when a result is `bundled` (wheelgap --pyodide-lock); same text as BUNDLED_NOTE in site/logic.js.
BUNDLED_NOTE = (
    "bundled: there's no wheel on PyPI for Pyodide, but Pyodide ships its own build of this package, often an "
    "older version than the one shown here. The cell names the Pyodide release and the version it ships."
)
# No scripts, fetches or frames: only the stylesheet, the favicon and the inline style on the lines under the table.
CSP = "default-src 'none'; img-src 'self' data:; style-src 'self' 'unsafe-inline'; base-uri 'none'; form-action 'none'"


def canonical(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def python_key(value: str) -> tuple[int, int]:
    major, minor = value.rstrip("t").split(".", 1)
    return int(major), int(minor)


def newest_non_free_threaded(targets: list[dict]) -> str:
    pys = {t["python"] for t in targets if not str(t["python"]).endswith("t")}
    if not pys:
        sys.exit("build_pages: no non-free-threaded Python in coverage.json")
    return max(pys, key=python_key)


def join_names(names: list[str]) -> str:
    return names[0] if len(names) == 1 else ", ".join(names[:-1]) + " and " + names[-1]


def describe(package: dict, columns: dict[str, int], python: str) -> str:
    """One sentence on the package's gaps for the newest Python, for the meta description."""
    name = f"{package['name']} {package['version'] or ''}".strip()
    if package.get("error"):
        return f"{name}: the latest wheelgap scan couldn't look this package up on PyPI."
    if package.get("pure"):
        return f"{name} is pure Python: it installs on Linux, Alpine, macOS, Windows, ARM and Pyodide."
    by_status: dict[str, list[str]] = {}
    for platform, index in columns.items():
        by_status.setdefault(package["results"][index], []).append(PLATFORM_NAMES[platform])
    wheel = len(by_status.get("wheel", []))
    if wheel == len(columns):
        return f"{name} ships a prebuilt wheel for all {len(columns)} platforms checked on Python {python}."
    parts = [f"{name} has a prebuilt wheel for {wheel} of {len(columns)} platforms on Python {python}"]
    if source := by_status.get("source"):
        parts.append(f"builds from source on {join_names(source)}")
    if missing := by_status.get("missing"):
        parts.append(f"can't install on {join_names(missing)}")
    if unknown := by_status.get("unknown"):
        parts.append(f"is unknown on {join_names(unknown)}")
    return "; ".join(parts) + "."


def note_text(text: str) -> str:
    """Escaped note text; a pair of backticks becomes <code>, and nothing else is markup."""
    parts = text.split("`")
    if len(parts) % 2 == 0:  # an unpaired backtick stays as it is
        parts[-2:] = ["`".join(parts[-2:])]
    return "".join(
        f"<code>{html.escape(part)}</code>" if i % 2 else html.escape(part) for i, part in enumerate(parts)
    )


def gap_platforms(package: dict, targets: list[dict]) -> list[str]:
    """Platforms where some target builds from source or can't install, in PLATFORM_NAMES order."""
    if package.get("error"):
        return []
    found = {t["platform"] for t, r in zip(targets, package["results"]) if r in GAP}
    return [pl for pl in PLATFORM_NAMES if pl in found]


def add_note_url(name: str, platforms: list[str], today: str, exists: bool) -> str:
    """GitHub's new-file page for notes/<name>.toml with a template, or its editor if the file exists."""
    if exists:  # GitHub can't create a file that's already there, and can't pre-fill the editor
        return f"{REPO_URL}/edit/main/{quote(f'notes/{name}.toml')}"
    listed = ", ".join(f'"{pl}"' for pl in platforms)
    template = (
        f"# The format: {REPO_URL}/blob/main/notes/README.md\n"
        "[[note]]\n"
        f"platforms = [{listed}]  # the platforms without a wheel: keep the ones this note is about\n"
        'kind = ""  # workaround | alternative | upstream-issue | other-source\n'
        'text = ""  # one or two sentences on what someone should do; `backticks` for commands\n'
        'links = [""]  # https:// links that back the claim\n'
        f"updated = {today}  # the day you checked\n"
    )
    return f"{REPO_URL}/new/main?filename={quote(f'notes/{name}.toml')}&value={quote(template)}"


def notes_html(notes: list[dict], name: str, gaps: list[str], today: str, exists: bool) -> str:
    """The Notes section: one card per current note, then the "Add a note" link if the package has a gap."""
    if not notes and not gaps:
        return ""
    e = html.escape
    cards = []
    for note in notes:
        platforms = note["platforms"]
        where = "All platforms" if platforms == ["all"] else ", ".join(PLATFORM_NAMES.get(p, p) for p in platforms)
        meta = [KIND_TEXT.get(note["kind"], note["kind"]), where]
        if "versions" in note:
            meta.append(f"for versions {note['versions']}")
        meta.append(f"checked {note['updated'].isoformat()}")
        links = " · ".join(f'<a href="{e(u)}">{e(SCHEME_RE.sub("", u))}</a>' for u in note["links"])
        cards.append(
            '      <div class="card">\n'
            f"        <p>{note_text(note['text'])}</p>\n"
            f'        <p class="sub">{" · ".join(e(m) for m in meta)}</p>\n'
            f'        <p class="sub">{links}</p>\n'
            "      </div>\n"
        )
    intro = "" if notes else f'      <p class="note">No notes for {e(name)} yet.</p>\n'
    add = ""
    if gaps:
        url = add_note_url(name, gaps, today, exists)
        add = (
            f'      <p class="note">Know a workaround, an alternative package, a conda-forge build or an upstream '
            f'issue for {e(name)}? <a href="{e(url)}">Add a note</a> (a pull request on GitHub; the format is in '
            f'<a href="{e(REPO_URL)}/blob/main/notes/README.md">notes/README.md</a>).</p>\n'
        )
    return f'    <section class="notes">\n      <h2>Notes</h2>\n{intro}{"".join(cards)}{add}    </section>\n'


def page(
    package: dict,
    scan: dict,
    python: str,
    notes: list[dict] | None = None,
    note_file: bool = False,
    found: dict[str, dict] | None = None,
) -> str:
    """The package's page. notes are its notes from notes/ (stale ones are left out here);
    note_file says whether notes/<name>.toml exists, so "Add a note" opens the editor instead.
    found is pyodide_locks.releases(scan): the Pyodide release behind each Python's bundled cells."""
    found = found or {}
    bundled = pyodide_locks.bundled(package, scan["targets"], found)
    targets = scan["targets"]
    pys = list(dict.fromkeys(t["python"] for t in targets))
    platforms = [pl for pl in PLATFORM_NAMES if any(t["platform"] == pl for t in targets)]
    index = {(t["python"], t["platform"]): i for i, t in enumerate(targets)}
    # The native platforms, like the badge, so the description and the badge give the same count.
    columns = {pl: index[(python, pl)] for pl in platforms if pl != "pyodide" and (python, pl) in index}
    e = html.escape
    name, version = package["name"], package["version"] or ""
    title = f"{name} wheel coverage: Linux, Alpine, macOS, Windows, ARM, Pyodide"
    url = f"{BASE_URL}p/{canonical(name)}/"
    summary = describe(package, columns, python)

    def cell(py: str, pl: str) -> str:
        i = index.get((py, pl))
        if i is None:
            return '<td><span class="status s-none">n/a</span></td>'
        s = package["results"][i]
        detail = pyodide_locks.describe(bundled.get(targets[i]["label"])) if s == "bundled" else ""
        small = f'<small class="bver">{e(detail)}</small>' if detail else ""
        return f'<td><span class="status s-{e(s)}">{e(STATUS_TEXT.get(s, s))}</span>{small}</td>'

    rows = "\n".join(
        f'          <tr><th scope="row">{e(PLATFORM_NAMES[pl])}</th>{"".join(cell(py, pl) for py in pys)}</tr>'
        for pl in platforms
    )
    sub = f"#{package['rank']} by downloads"
    if package.get("pure"):
        sub += " · pure Python: installs everywhere"
    if package.get("error"):
        sub += f" · lookup failed: {e(package['error'])}"
    sub += f' · <a href="https://pypi.org/project/{e(name)}/">PyPI</a>'
    explain = f"uvx wheelgap {name}=={version} -p {python} -t all --explain" if version else ""
    if explain and python in found:
        # `-t all` is the native platforms only, and wheelgap refuses a lock no pyodide target uses: add pyodide,
        # with the lock, so its answer counts Pyodide's own builds as the table does.
        lock = found[python]["url"]
        explain = f"uvx wheelgap {name}=={version} -p {python} -t all,pyodide --explain --pyodide-lock {lock}"
    why = f'\n      <p class="sub" style="margin:12px 0 0">Why? <code>{e(explain)}</code></p>' if explain else ""
    if not package.get("error") and "bundled" in package["results"]:
        why = f'\n      <p class="sub" style="margin:12px 0 0">{e(BUNDLED_NOTE)}</p>{why}'
    shown = check_notes.current(notes or [], package, targets)
    notes_section = notes_html(
        shown, canonical(name), gap_platforms(package, targets), str(scan["generated"])[:10], note_file
    )
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <meta http-equiv="Content-Security-Policy" content="{CSP}">
  <title>{e(title)}</title>
  <meta name="description" content="{e(summary)}">
  <link rel="canonical" href="{e(url)}">
  <link rel="stylesheet" href="../../style.css">
  <link rel="icon" href="../../favicon.svg" type="image/svg+xml">
</head>
<body>
  <header class="wrap">
    <p class="eyebrow"><a href="../../">wheelgap tracker</a></p>
    <h1>{e(name)} wheel coverage</h1>
    <p>{e(summary)}</p>
    <p class="meta">From the wheelgap {e(str(scan["wheelgap"]))} scan of {e(str(scan["generated"])[:10])}.</p>
  </header>
  <main class="wrap">
    <div class="card">
      <h3>{e(name)} <span class="ver">{e(version)}</span></h3>
      <p class="sub">{sub}</p>
      <div class="matrix-scroll"><table class="matrix">
        <thead><tr><th>Platform</th>{"".join(f"<th>{e(py)}</th>" for py in pys)}</tr></thead>
        <tbody>
{rows}
        </tbody>
      </table></div>{why}
    </div>
{notes_section}    <p class="sub"><a href="../../#package={e(name)}">All {len(scan["packages"]):,} packages</a></p>
  </main>
</body>
</html>
"""


def build(
    coverage_path: Path, site: Path, notes_dir: Path | None = None, root: Path = pyodide_locks.ROOT
) -> int:
    """Write the pages and the sitemap; notes come from notes_dir, or none when it is None.
    root holds pyodide/locks.json and the lock files it names."""
    scan = json.loads(coverage_path.read_text(encoding="utf-8"))
    if scan.get("schema") != SCAN_SCHEMA:
        sys.exit(f"build_pages: coverage.json schema {scan.get('schema')!r}, expected {SCAN_SCHEMA}")
    notes = check_notes.load(notes_dir, scan) if notes_dir is not None else {}
    python = newest_non_free_threaded(scan["targets"])
    found = pyodide_locks.releases(scan, root)
    names: list[str] = []
    seen: set[str] = set()
    for p in scan["packages"]:
        name = canonical(p["name"])
        if not SAFE_NAME.fullmatch(name) or name in seen:
            print(f"::warning::build_pages: skipping {p['name']!r}", file=sys.stderr)
            continue
        names.append(name)
        seen.add(name)
        out = site / "p" / name
        out.mkdir(parents=True, exist_ok=True)
        note_file = notes_dir is not None and (notes_dir / f"{name}.toml").exists()
        text = page(p, scan, python, notes.get(name), note_file, found)
        (out / "index.html").write_text(text, encoding="utf-8")
    urls = [BASE_URL] + [f"{BASE_URL}p/{name}/" for name in names]
    sitemap = "".join(f"  <url><loc>{html.escape(u)}</loc></url>\n" for u in urls)
    (site / "sitemap.xml").write_text(
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        f'<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n{sitemap}</urlset>\n',
        encoding="utf-8",
    )
    return len(names)


if __name__ == "__main__":
    coverage, site = (Path(p) for p in sys.argv[1:3])
    notes_dir = Path(sys.argv[3]) if len(sys.argv) > 3 else NOTES_DIR
    print(f"pages: {build(coverage, site, notes_dir)} packages in {site / 'p'}")
