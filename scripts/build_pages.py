"""Build a static HTML page per package from a wheelgap coverage scan, plus a sitemap.

    python scripts/build_pages.py data/coverage.json _site [notes_dir [gap-wheels.json [status.json]]]

Writes <site>/p/<name>/index.html for each package (PEP 503 canonical name) and
<site>/sitemap.xml listing the main page, the repo checker and every package page, plus a
robots.txt naming the sitemap. Each page shows the package's current notes from notes_dir (default: the repository's notes/) and,
when it has a gap, an "Add a note" link. A `bundled` cell says which version Pyodide ships
(from the committed lock files, see pyodide_locks.py). Runs at publish time; the files are not
committed. The pages are plain HTML (no JavaScript; one JSON-LD data block) and reuse site/style.css, so
search engines can find "does <package> have ARM wheels".
"""

from __future__ import annotations

import html
import json
import re
import sys
from pathlib import Path
from urllib.parse import quote, urlsplit

if __package__:  # imported as scripts.build_pages (the tests)
    from . import (
        build_badges,
        build_campaigns,
        build_gap_index,
        build_lab,
        build_lookup,
        check_notes,
        install_results,
        pyodide_locks,
        update_changes,
    )
else:  # run as python scripts/build_pages.py
    import build_badges
    import build_campaigns
    import build_gap_index
    import build_lab
    import build_lookup
    import check_notes
    import install_results
    import pyodide_locks
    import update_changes

SCAN_SCHEMA = 1
SAFE_NAME = re.compile(r"[a-z0-9]+(-[a-z0-9]+)*")
BASE_URL = "https://wheelgap.github.io/tracker/"
REPO_URL = "https://github.com/wheelgap/tracker"
NOTES_DIR = Path(__file__).resolve().parent.parent / "notes"
BUILDS = Path(__file__).resolve().parent.parent / "data" / "builds.json"
LAB_PILL = {
    "works": "s-wheel",
    "gil": "s-source",
    "pure": "s-none",
    "unsupported": "s-none",
    "partial": "s-source",
    "slow": "s-source",
    "broken": "s-missing",
    "fails": "s-missing",
}
LAB_LABEL = {
    "works": "works",
    "gil": "GIL back on",
    "pure": "pure Python",
    "unsupported": "unsupported",
    "partial": "no compiled part",
    "broken": "won't load",
    "fails": "fails",
    "slow": "too slow",
}
KIND_TEXT = {
    "workaround": "Workaround",
    "alternative": "Alternative",
    "upstream-issue": "Upstream issue",
    "other-source": "Other source",
}
GAP = ("source", "missing")  # not `bundled`: Pyodide's own build installs
SCHEME_RE = re.compile(r"^https://", re.IGNORECASE)  # shown without it; the checker allows only https


def valid_issues_url(url: object) -> bool:
    """An https:// URL with a host, printable ASCII only, no spaces or control characters, and no user@ part."""
    if not isinstance(url, str) or not url.isascii() or not url.isprintable() or " " in url:
        return False
    try:
        parts = urlsplit(url)
        return parts.scheme == "https" and bool(parts.hostname) and "@" not in parts.netloc
    except ValueError:
        return False


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
INSTALL_STATUS = {
    "wheel": ("wheel", "Wheels for the whole tree"),
    "source": ("source", "Builds from source"),
    "missing": ("missing", "Missing distribution"),
    "not_checked": ("unknown", "Not checked"),
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


DESCRIPTION_LIMIT = 160  # about what search engines show under the link


def describe_install(package: dict, targets: list[dict]) -> str | None:
    """The whole-tree answer (#80's `install`), or None without a checked target.

    It's for install_results.install_python(): the newest non-free-threaded Python with a checked native
    target, which can be older than the newest Python in the scan, as build_badges' tree badge is. A
    `not_checked` target is left out of the count, never reported as working, and Pyodide isn't counted."""
    python = install_results.install_python(package, targets)
    order = list(PLATFORM_NAMES)
    checked = sorted(
        (
            (target["platform"], result)
            for target, result in install_results.native_install(package, targets)
            if target["python"] == python
            and target["platform"] in PLATFORM_NAMES
            and result.get("status") in install_results.CHECKED
        ),
        key=lambda item: order.index(item[0]),
    )
    if not checked:
        return None
    total = len(checked)
    ok = sum(result["status"] == "wheel" for _, result in checked)
    head = f"pip install {package['name']} on Python {python}"
    only = PLATFORM_NAMES[checked[0][0]] if total == 1 else None  # one platform checked: name it, never "all 1"
    if ok == total:
        where = f"on {only}, the only platform checked" if only else f"on all {total} platforms checked"
        lead = f"{head}: wheels for the whole tree {where}"
        text = lead + "."
    else:
        where = f"on {only}, the only platform checked" if only else f"on {ok} of the {total} platforms checked"
        lead = f"{head}: {'no wheels for the whole tree' if only else 'wheels for the whole tree'} {where}"
        text = install_gaps(lead, checked)
    # The limit holds for every name: drop the details before going over it.
    return text if len(text) <= DESCRIPTION_LIMIT else lead + "."


def install_gaps(lead: str, checked: list[tuple[str, dict]]) -> str:
    """The lead clause and what goes wrong where, shortened step by step to fit DESCRIPTION_LIMIT."""

    def what(result: dict, most: int | None) -> str:
        parts = []
        for status, verb in (("source", "builds {} from source"), ("missing", "can't install {}")):
            names = list(dict.fromkeys(b["name"] for b in result["blockers"] if b["status"] == status))
            if most is not None and len(names) > most:
                names = names[:most] + [f"{len(names) - most} more"]
            if names:
                parts.append(verb.format(join_names(names)))
        # A `missing` result with no `missing` blocker left (none listed, or clean_install dropped it): still say
        # it can't install, so a kept `source` blocker alone never reads as "it installs after a build".
        if result["status"] == "missing" and not any(b["status"] == "missing" for b in result["blockers"]):
            parts.append("can't install")
        elif not parts:  # a `source` result without blockers; shouldn't happen, but say the status
            parts.append("builds from source")
        return " and ".join(parts)

    for most in (None, 2, 1):
        grouped: dict[str, list[str]] = {}
        for platform, result in checked:
            if result["status"] != "wheel":
                grouped.setdefault(what(result, most), []).append(PLATFORM_NAMES[platform])
        text = lead + "".join(f"; on {join_names(p)} it {w}" for w, p in grouped.items()) + "."
        if len(text) <= DESCRIPTION_LIMIT:
            return text
    # Still too long: name the platforms only, and failing that, just the count.
    by_status: dict[str, list[str]] = {}
    for platform, result in checked:
        if result["status"] != "wheel":
            by_status.setdefault(result["status"], []).append(PLATFORM_NAMES[platform])
    parts = [lead]
    if source := by_status.get("source"):
        parts.append(f"builds from source on {join_names(source)}")
    if missing := by_status.get("missing"):
        parts.append(f"can't install on {join_names(missing)}")
    text = "; ".join(parts) + "."
    if len(text) <= DESCRIPTION_LIMIT:
        return text

    def platforms(n: int) -> str:
        return f"{n} platform{'s' if n != 1 else ''}"

    counts = [f"builds from source on {platforms(len(source))}"] if source else []
    counts += [f"can't install on {platforms(len(missing))}"] if missing else []
    return f"{lead}; {' and '.join(counts)}."


def describe(package: dict, columns: dict[str, int], python: str, targets: list[dict] | None = None) -> str:
    """One sentence on the package's gaps for the newest Python, for the meta description.

    With checked `install` data and targets, it's the whole-tree answer instead (see describe_install),
    since that's what `pip install` meets."""
    name = f"{package['name']} {package['version'] or ''}".strip()
    if package.get("error"):
        return f"{name}: the latest wheelgap scan couldn't look this package up on PyPI."
    if targets is not None and (whole_tree := describe_install(package, targets)):
        return whole_tree
    if package.get("pure"):
        return f"{name} ships a pure Python wheel."
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


def json_ld(name: str, version: str, url: str, description: str) -> str:
    """A schema.org SoftwareSourceCode block for search engines.

    A `type="application/ld+json"` block is data, not script, so the CSP (no script-src) doesn't apply to it.
    json.dumps escapes for JSON; replacing `<`, `>` and `&` with their \\u escapes keeps a name like
    `</script>` from ending the block, and the JSON still parses to the same text."""
    data = {
        "@context": "https://schema.org",
        "@type": "SoftwareSourceCode",
        "name": name,
        "url": url,
        "sameAs": f"https://pypi.org/project/{canonical(name)}/",
        "programmingLanguage": "Python",
        "description": description,
    }
    if version:
        data["version"] = version
    text = json.dumps(data, ensure_ascii=False, indent=2)
    text = text.replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026")
    return f'  <script type="application/ld+json">\n{text}\n  </script>\n'


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
        if "python" in note:
            meta.append(f"for Python {', '.join(note['python'])}")
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


def clean_way_out(way_out: object) -> tuple[dict[str, dict], int]:
    """The usable `way_out` hints and how many entries were dropped.

    scan_way_out.py writes them after check_scan.py has run, so nothing has checked their shape: a hint is
    kept only when it's an object with a string `release` and a `kind` of "older" or "newer". Anything else,
    including a `way_out` that isn't an object, counts as no hint rather than stopping the deploy."""
    if not isinstance(way_out, dict):
        return {}, 1
    kept = {
        label: hint
        for label, hint in way_out.items()
        if isinstance(hint, dict) and isinstance(hint.get("release"), str) and hint.get("kind") in ("older", "newer")
    }
    return kept, len(way_out) - len(kept)


def clean_install(install: object) -> tuple[dict[str, dict], int]:
    """The usable `install` results and how many entries (targets or blockers) were dropped.

    Like `way_out`, scan_install.py writes them unchecked. A target whose result isn't an object is left out.
    A missing or non-list `blockers` reads as [], and a status that isn't a string as `not_checked`. A blocker
    is kept only when it's an object with a string `name`, a `status` of `source` or `missing` and a `via`
    list of strings; a `version` that isn't a string reads as None."""
    if not isinstance(install, dict):
        return {}, 1
    kept: dict[str, dict] = {}
    dropped = 0
    for target, result in install.items():
        if not isinstance(result, dict):
            dropped += 1
            continue
        status, raw = result.get("status"), result.get("blockers")
        if not isinstance(status, str):
            status, dropped = "not_checked", dropped + 1
        if not isinstance(raw, list):
            raw, dropped = [], dropped + 1
        blockers = []
        for item in raw:
            if (
                isinstance(item, dict)
                and isinstance(item.get("name"), str)
                and item.get("status") in GAP
                and isinstance(item.get("via"), list)
                and all(isinstance(name, str) for name in item["via"])
            ):
                version = item.get("version")
                blockers.append({**item, "version": version if isinstance(version, str) else None})
            else:
                dropped += 1
        kept[target] = {**result, "status": status, "blockers": blockers}
    return kept, dropped


def install_html(install: dict, page_names: set[str] | None = None, own_name: str = "") -> str:
    """Show the install result and dependency blockers for each checked target (see clean_install)."""
    install, _ = clean_install(install)
    if not install:
        return ""
    e = html.escape
    page_names = page_names or set()

    def name_html(name: str, *, is_via: bool = False) -> str:
        slug = canonical(name)
        rendered = e(name)
        if slug in page_names and SAFE_NAME.fullmatch(slug) and not (is_via and slug == own_name):
            return f'<a href="../{e(quote(slug, safe=""))}/">{rendered}</a>'
        return rendered

    rows = []
    for target, result in install.items():
        style, label = INSTALL_STATUS.get(result["status"], INSTALL_STATUS["not_checked"])
        blockers = []
        for item in result["blockers"]:
            requirement = name_html(item["name"])
            if item["version"] is not None:
                requirement += f"=={e(str(item['version']))}"
            reason = "builds from source" if item["status"] == "source" else "has no distribution"
            via = (
                " via <code>" + " → ".join(name_html(name, is_via=True) for name in item["via"]) + "</code>"
                if len(item["via"]) > 1 else ""
            )
            blockers.append(f"<li><code>{requirement}</code> {reason}{via}</li>")
        details = f"<ul>{''.join(blockers)}</ul>" if blockers else ""
        rows.append(
            f'          <tr><th scope="row">{e(target)}</th>'
            f'<td><span class="status s-{style}">{label}</span>{details}</td></tr>\n'
        )
    return (
        '    <section aria-labelledby="install-coverage">\n'
        '      <h2 id="install-coverage">Install coverage with dependencies</h2>\n'
        '      <p>Wheelgap checks the scanned release and its dependencies without extras. A target is marked '
        '“Not checked” if wheelgap could not resolve the full dependency tree or verify its packages.</p>\n'
        f"      {build_campaigns.table_open('Install results with dependencies', 'caption-install')}\n"
        '        <thead><tr><th scope="col">Target</th><th scope="col">Install result</th></tr></thead>\n'
        '        <tbody>\n'
        f'{"".join(rows)}'
        '        </tbody>\n'
        '      </table></div>\n'
        '    </section>\n'
    )


def lab_html(builds: list[dict]) -> str:
    """The Build lab section: verdict, runner, date, and link for each target built for this release."""
    if not builds:
        return ""
    e = html.escape
    rows = []
    for r in sorted(builds, key=lambda b: b.get("target", "")):
        v = build_lab.lab_verdict(r)
        pill = LAB_PILL.get(v["kind"], "s-unknown")
        label = LAB_LABEL.get(v["kind"], v["kind"])
        where = f"{e(str(r.get('runner', '')))}, Python {e(str(r.get('python', '')))}, {e(str(r.get('date', '')))}"
        if (log := r.get("log")) and isinstance(log, str) and log.startswith("https://"):
            where += f' · <a href="{e(log)}">run</a>'
        detail = ""
        if excerpt := r.get("excerpt"):
            detail = f'<details><summary>log</summary><code>{e(excerpt)}</code></details>'
        rows.append(
            f'          <tr><th scope="row">{e(r["target"])}</th>'
            f'<td><span class="status {pill}">{e(label)}</span> {e(v["text"])}</td>'
            f'<td class="lab-where">{where}{detail}</td></tr>\n'
        )
    return (
        '    <section aria-labelledby="build-lab">\n'
        '      <h2 id="build-lab">Build lab</h2>\n'
        '      <p class="sub">A dated observation on GitHub\'s runners, not a promise about your machine: '
        'runners come with compilers and many system libraries preinstalled, so a build that works there '
        'can still need setup on your machine.</p>\n'
        f"      {build_campaigns.table_open('Build lab results', 'caption-lab', 'matrix lab-table')}\n"
        '        <thead><tr><th scope="col">Target</th><th scope="col">From source</th>'
        '<th scope="col">Details</th></tr></thead>\n'
        '        <tbody>\n'
        f'{"".join(rows)}'
        '        </tbody>\n'
        '      </table></div>\n'
        '    </section>\n'
    )


def read_gap_status(path: Path | None) -> dict[tuple[str, str], str] | None:
    """Current status by (canonical project, wheel filename), or None for the legacy fallback."""
    if path is None:
        return None
    try:
        if path.stat().st_size > 10_000_000:
            raise ValueError("too large")
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, RecursionError):
        data = None
    if (
        not isinstance(data, dict)
        or type(data.get("schema")) is not int
        or data["schema"] != 1
        or not isinstance(data.get("wheels"), list)
        or any(
            not isinstance(item, dict)
            or any(not isinstance(item.get(key), str) for key in ("project", "wheel", "status"))
            for item in data["wheels"]
        )
    ):
        print("::warning::status.json is missing or unusable, so package pages use gap-wheels.json alone")
        return None
    return {(canonical(item["project"]), item["wheel"]): item["status"] for item in data["wheels"]}


def read_gap_wheels(path: Path | None, status_path: Path | None = None) -> dict[tuple[str, str, str], str]:
    """Status by (canonical name, version, target) of each available gap wheel.
    The file comes from another site, so a missing or unusable one gives no hints (with a
    warning in the deploy log) rather than stopping the pages. build_gap_index.usable checks
    each entry; if that legacy script is retired, move the check here. A usable status.json
    keeps only in-index or yanked wheels; without one, keep the historical list as before."""
    if path is None:
        return {}
    try:
        if path.stat().st_size > 10_000_000:
            raise ValueError("too large")
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        print("::warning::no gap-wheels.json, so package pages show no gap wheel hints")
        return {}
    except (OSError, ValueError, RecursionError):
        data = None
    if not isinstance(data, dict) or data.get("schema") != 1 or not isinstance(data.get("wheels"), list):
        print("::warning::gap-wheels.json is unusable, so package pages show no gap wheel hints")
        return {}
    statuses = read_gap_status(status_path)
    wheels = {}
    for item in data["wheels"]:
        if not (
            build_gap_index.usable(item)
            and isinstance(item.get("version"), str)
            and isinstance(item.get("target"), str)
        ):
            continue
        name = canonical(item["name"])
        status = statuses.get((name, item["wheel"])) if statuses is not None else "in-index"
        if status in ("in-index", "yanked"):
            wheels[(name, item["version"], item["target"])] = status
    return wheels


def format_target(label: str) -> str:
    """'3.14 win-arm64' -> 'Windows ARM64, Python 3.14' (a coverage.json target label)."""
    python, platform = label.split(" ", 1)
    return f"{PLATFORM_NAMES.get(platform, platform)}, Python {python}"


def changes_html(changes: list[dict] | None) -> str:
    """The Recent changes section: the package's 10 newest entries from changes_by_package,
    or nothing when it has none."""
    if not changes:
        return ""
    e = html.escape
    items = [
        f"        <li>{e(c['date'])}: {'gained' if c['gained'] else 'lost'} a wheel"
        f" for {e(format_target(c['target']))}</li>\n"
        for c in changes[:10]
    ]
    if len(changes) > 10:
        items.append(f"        <li>and {len(changes) - 10} more</li>\n")
    return (
        '    <section aria-labelledby="recent-changes">\n'
        '      <h2 id="recent-changes">Recent changes</h2>\n'
        "      <p class=\"sub\">Wheels gained and lost in the nightly scans, newest first"
        f" (the log keeps {update_changes.KEEP_DAYS} days).</p>\n"
        "      <ul>\n"
        f"{''.join(items)}"
        "      </ul>\n"
        "    </section>\n"
    )


def changes_by_package(changes: list[dict]) -> dict[str, list[dict]]:
    """Each package's wheel gains and losses from changes.json (the list of days
    update_changes.py writes), keyed by canonical name: newest day first, and within a
    day losses before gains, as on the home page."""
    by_pkg: dict[str, list[dict]] = {}
    for day in sorted(changes, key=lambda d: d["date"], reverse=True):
        for kind in ("lost", "gained"):
            for item in day[kind]:
                by_pkg.setdefault(canonical(item["name"]), []).append(
                    {"date": day["date"], "gained": kind == "gained", "target": item["target"]}
                )
    return by_pkg


def page(
    package: dict,
    scan: dict,
    python: str,
    notes: list[dict] | None = None,
    note_file: bool = False,
    found: dict[str, dict] | None = None,
    campaigns: list[tuple[str, dict]] | None = None,
    tree_badge: bool = False,
    builds: list[dict] | None = None,
    changes: list[dict] | None = None,
    page_names: set[str] | None = None,
    gap_wheels: dict[tuple[str, str, str], str] | None = None,
    dropped: list[int] | None = None,
) -> str:
    """The package's page. notes are its notes from notes/ (stale ones are left out here);
    note_file says whether notes/<name>.toml exists, so "Add a note" opens the editor instead.
    found is pyodide_locks.releases(scan): the Pyodide release behind each Python's bundled cells.
    campaigns pairs each membership status with its loaded campaign. tree_badge says whether
    build_badges writes badge/<name>-tree.json, so the page shows its snippet too.
    builds holds the package's build lab results; changes is its entry from changes_by_package.
    gap_wheels maps (canonical name, version, target) to each available wheel's status (read_gap_wheels).
    dropped, when given, collects how many malformed `way_out` and `install` entries the page left out."""
    found = found or {}
    bundled = pyodide_locks.bundled(package, scan["targets"], found)
    way_out, bad = clean_way_out(package.get("way_out", {}))
    if "install" in package:
        # Cleaned once here, so the summary (describe_install) and the install table read the same results.
        install, bad_install = clean_install(package["install"])
        package = {**package, "install": install}
        bad += bad_install
    if dropped is not None and bad:
        dropped.append(bad)
    targets = scan["targets"]
    pys = list(dict.fromkeys(t["python"] for t in targets))
    platforms = [pl for pl in PLATFORM_NAMES if any(t["platform"] == pl for t in targets)]
    index = {(t["python"], t["platform"]): i for i, t in enumerate(targets)}
    # The native platforms, like the badge (the install-led description counts only the platforms it checked).
    columns = {pl: index[(python, pl)] for pl in platforms if pl != "pyodide" and (python, pl) in index}
    e = html.escape
    name, version = package["name"], package["version"] or ""
    current_builds = [r for r in (builds or []) if version and r.get("version") == version]
    lab_by_target = {r["target"]: r for r in current_builds if "target" in r}
    gap_targets = {
        target["label"]
        for target, status in zip(targets, package["results"])
        if status in GAP and (canonical(name), version, target["label"]) in (gap_wheels or {})
    }
    yanked_note = (
        " PyPI has yanked this release; pip and uv take it only with an exact == pin."
        if any((gap_wheels or {}).get((canonical(name), version, target)) == "yanked" for target in gap_targets)
        else ""
    )
    title = f"{name} wheel coverage: Linux, Alpine, macOS, Windows, ARM, Pyodide"
    url = f"{BASE_URL}p/{canonical(name)}/"
    summary = describe(package, columns, python, targets)
    campaign_links = []
    for status, campaign in campaigns or []:
        link = f'<a href="../../campaigns/{e(campaign["id"])}/">{e(campaign["title"])}</a>'
        prefix = {"todo": "On the to-do list of", "done": "Done in", "set_aside": "Set aside in"}[status]
        suffix = " (has an alternative)" if status == "set_aside" else ""
        campaign_links.append(f"{prefix} {link}{suffix}")
    campaign_line = (
        f'      <p class="sub">Campaigns: {" · ".join(campaign_links)}.</p>\n' if campaign_links else ""
    )
    slug = quote(canonical(name), safe="")
    badge_md = (
        f"[![wheels](https://img.shields.io/endpoint?url={BASE_URL}badge/{slug}.json)]"
        f"({BASE_URL}#package={quote(name, safe='')})"
    )
    badge_code = f"<code>{e(badge_md)}</code>"
    if tree_badge:
        tree_md = (
            f"[![wheels with dependencies](https://img.shields.io/endpoint?url={BASE_URL}badge/{slug}"
            f"{build_badges.TREE_SUFFIX}.json)]({BASE_URL}p/{slug}/)"
        )
        badge_code += f"\n<code>{e(tree_md)}</code>"

    def cell(py: str, pl: str) -> str:
        i = index.get((py, pl))
        if i is None:
            return '<td><span class="status s-none">n/a</span></td>'
        s = package["results"][i]
        detail = pyodide_locks.describe(bundled.get(targets[i]["label"])) if s == "bundled" else ""
        if s in GAP and (hint := way_out.get(targets[i]["label"])):
            if hint["kind"] == "older":
                # A fact, not advice: that release may lack wheels the current one has on other platforms.
                detail = f"{hint['release']} is the last release with a wheel here."
            elif hint["kind"] == "newer":
                detail = f"Upgrade to {hint['release']} for a wheel here."
        if s == "source" and (r := lab_by_target.get(targets[i]["label"])):
            v = build_lab.lab_verdict(r)
            detail = f"{v['text']}. {detail}" if detail else v["text"]
        if targets[i]["label"] in gap_targets:
            detail = f"{detail} · Gap wheel available (see below)" if detail else "Gap wheel available (see below)"
        small = f'<small class="bver">{e(detail)}</small>' if detail else ""
        return f'<td><span class="status s-{e(s)}">{e(STATUS_TEXT.get(s, s))}</span>{small}</td>'

    rows = "\n".join(
        f'          <tr><th scope="row"><a href="../../platforms/{e(pl)}/">{e(PLATFORM_NAMES[pl])}</a></th>{"".join(cell(py, pl) for py in pys)}</tr>'
        for pl in platforms
    )
    sub = f"#{package['rank']} by downloads"
    if package.get("pure"):
        sub += " · pure Python wheel"
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
    ask = ""
    if (
        not package.get("error")
        and any(r in GAP for r in package.get("results", []))
        and (issues_url := package.get("issues_url"))
        and valid_issues_url(issues_url)
    ):
        ask = f'\n      <p class="sub" style="margin:12px 0 0">No wheel for your platform? Ask the maintainers: <a href="{e(issues_url)}">{e(issues_url)}</a></p>'
    shown = check_notes.current(notes or [], package, targets)
    notes_section = notes_html(
        shown, canonical(name), gap_platforms(package, targets), str(scan["generated"])[:10], note_file
    )
    install_section = install_html(package.get("install", {}), page_names, canonical(name))
    gap_section = (
        '    <section class="notes"><h2>Gap wheels</h2>\n'
        f"      <p>wheelgap publishes an unofficial wheel of {e(name)} {e(version)} for "
        f"{e(', '.join(sorted(gap_targets)))}, built from the PyPI sdist. It isn't from the "
        f"package's maintainers.{yanked_note}</p>\n"
        "      <pre><code>pip install --extra-index-url https://wheelgap.github.io/wheels/simple/ "
        f"{e(canonical(name))}=={e(version)}</code></pre>\n"
        '      <p><a href="https://wheelgap.github.io/wheels/">About the gap wheels</a></p>\n'
        "    </section>\n"
        if gap_targets
        else ""
    )
    # Only where the cell still says source: a wheel uploaded since the lab ran (or a target the scan dropped) makes
    # that result old news, not something to show.
    source_labels = {t["label"] for t, st in zip(targets, package["results"]) if st == "source"}
    lab_section = lab_html([r for r in current_builds if r.get("target") in source_labels])
    changes_section = changes_html(changes)
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <meta http-equiv="Content-Security-Policy" content="{CSP}">
  <title>{e(title)}</title>
  <meta name="description" content="{e(summary)}">
  <meta property="og:type" content="website">
  <meta property="og:title" content="{e(title)}">
  <meta property="og:description" content="{e(summary)}">
  <meta property="og:url" content="{e(url)}">
  <meta property="og:image" content="{e(BASE_URL)}og.png">
  <meta property="og:image:width" content="1280">
  <meta property="og:image:height" content="640">
  <meta property="og:image:alt" content="wheelgap tracker: wheel coverage for the 5,000 most downloaded PyPI packages, across 42 platforms and Pythons.">
  <meta name="twitter:card" content="summary_large_image">
  <link rel="canonical" href="{e(url)}">
  <link rel="stylesheet" href="../../style.css">
  <link rel="icon" href="../../favicon.svg" type="image/svg+xml">
</head>
<body>
  <header class="wrap">
    <p class="eyebrow"><a href="../../">wheelgap tracker</a></p>
{build_campaigns.site_nav("../../")}
    <h1>{e(name)} wheel coverage</h1>
    <p>{e(summary)}</p>
    <p class="meta">From the wheelgap {e(str(scan["wheelgap"]))} scan of {e(str(scan["generated"])[:10])}.</p>
  </header>
  <main class="wrap">
    <div class="card">
      <h2 class="card-title">{e(name)} <span class="ver">{e(version)}</span></h2>
      <p class="sub">{sub}</p>
{campaign_line}      {build_campaigns.table_open(f"{name} {version} by platform and Python", "caption-coverage")}
        <thead><tr><th scope="col">Platform</th>{"".join(f'<th scope="col">{e(py)}</th>' for py in pys)}</tr></thead>
        <tbody>
{rows}
        </tbody>
      </table></div>{ask}{why}
    </div>
{changes_section}{install_section}{gap_section}{lab_section}{notes_section}    <section>
      <h2>Badge for your README</h2>
      <pre class="badge-md">{badge_code}</pre>
      <p class="sub"><a href="../../data/packages/{e(slug)}.json">Package JSON</a></p>
    </section>
    <p class="sub"><a href="../../#package={e(name)}">All {len(scan["packages"]):,} packages</a></p>
  </main>
{build_campaigns.FOOTER}
{json_ld(name, version, url, summary)}</body>
</html>
"""


def build(
    coverage_path: Path,
    site: Path,
    notes_dir: Path | None = None,
    root: Path = pyodide_locks.ROOT,
    config_path: Path | None = None,
    builds_path: Path | None = None,
    changes_path: Path | None = None,
    gap_wheels_path: Path | None = None,
    gap_status_path: Path | None = None,
) -> int:
    """Write the pages and the sitemap; notes come from notes_dir, or none when it is None.
    root holds pyodide/locks.json and the lock files it names.
    config_path defaults to campaigns.toml beside the scan's data directory.
    builds_path is the build lab's data/builds.json (None: no lab results).
    changes_path defaults to changes.json beside coverage.json.
    gap_wheels_path is wheelgap/wheels' gap-wheels.json (None or unusable: no gap wheel hints).
    gap_status_path is its current status.json (None or unusable: use the historical list)."""
    scan = json.loads(coverage_path.read_text(encoding="utf-8"))
    if scan.get("schema") != SCAN_SCHEMA:
        sys.exit(f"build_pages: coverage.json schema {scan.get('schema')!r}, expected {SCAN_SCHEMA}")
    notes = check_notes.load(notes_dir, scan) if notes_dir is not None else {}
    lab = build_lookup.lab_results(builds_path)
    gap_wheels = read_gap_wheels(gap_wheels_path, gap_status_path)
    python = newest_non_free_threaded(scan["targets"])
    found = pyodide_locks.releases(scan, root)
    changes_path = changes_path or coverage_path.parent / "changes.json"
    changes = update_changes.read_changes(changes_path)
    pkg_changes = changes_by_package(changes)
    memberships: dict[str, list[tuple[str, dict]]] = {}
    config_path = config_path or coverage_path.parent.parent / "campaigns.toml"
    if config_path.exists():
        for campaign in build_campaigns.load_campaigns(config_path):
            campaign_python = campaign.get("python") or python
            platform = campaign["platform"]
            index = build_campaigns.target_index(scan["targets"], campaign_python, platform)
            label = scan["targets"][index]["label"]
            todo, set_aside = build_campaigns.todo_lists(
                build_campaigns.top_packages(scan, campaign["top"]),
                scan,
                campaign_python,
                platform,
                notes,
                campaign.get("compare", "platform"),
            )
            baseline = build_campaigns.load_baseline(
                coverage_path.parent / "campaigns" / f"{campaign['id']}.json"
            )
            if baseline is None:
                baseline = build_campaigns.baseline_for(campaign, scan, changes, campaign_python)
            done = [] if build_campaigns.baseline_mismatch(baseline, campaign, label) else (
                build_campaigns.done_list(baseline, scan["packages"], index, label, changes)
            )
            for status, rows in (("todo", todo), ("set_aside", set_aside), ("done", done)):
                for item in rows:
                    memberships.setdefault(item["canonical"], []).append((status, campaign))
    trees = build_badges.tree_badges(scan)
    names: list[str] = []
    seen: set[str] = set()
    dropped: list[int] = []
    valid_packages: list[tuple[str, dict]] = []
    for p in scan["packages"]:
        name = canonical(p["name"])
        if not SAFE_NAME.fullmatch(name) or name in seen:
            print(f"::warning::build_pages: skipping {p['name']!r}", file=sys.stderr)
            continue
        names.append(name)
        seen.add(name)
        valid_packages.append((name, p))
    for name, p in valid_packages:
        out = site / "p" / name
        out.mkdir(parents=True, exist_ok=True)
        note_file = notes_dir is not None and (notes_dir / f"{name}.toml").exists()
        text = page(
            p,
            scan,
            python,
            notes.get(name),
            note_file,
            found,
            memberships.get(name),
            name in trees,
            builds=lab.get(name, []),
            changes=pkg_changes.get(name, []),
            page_names=seen,
            gap_wheels=gap_wheels,
            dropped=dropped,
        )
        (out / "index.html").write_text(text, encoding="utf-8")
    if dropped:
        print(
            f"::warning::build_pages: left out {sum(dropped)} malformed way_out or install "
            f"{'entry' if sum(dropped) == 1 else 'entries'} on {len(dropped)} package "
            f"page{'s' if len(dropped) != 1 else ''}"
        )
    urls = [BASE_URL, f"{BASE_URL}check/"] + [f"{BASE_URL}p/{name}/" for name in names]
    sitemap = "".join(f"  <url><loc>{html.escape(u)}</loc></url>\n" for u in urls)
    (site / "sitemap.xml").write_text(
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        f'<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n{sitemap}</urlset>\n',
        encoding="utf-8",
    )
    (site / "robots.txt").write_text(robots_txt(), encoding="utf-8")
    return len(names)


def robots_txt() -> str:
    """Allow everything and name the sitemap.

    Crawlers read robots.txt only at a host's root, so under github.io's /tracker/ this file is
    ignored; it starts working if the site moves to its own domain or root. Until then the sitemap
    is found through the links on the pages, or by submitting it to a search engine by hand."""
    return f"User-agent: *\nAllow: /\n\nSitemap: {BASE_URL}sitemap.xml\n"


if __name__ == "__main__":
    coverage, site = (Path(p) for p in sys.argv[1:3])
    notes_dir = Path(sys.argv[3]) if len(sys.argv) > 3 else NOTES_DIR
    gap_path = Path(sys.argv[4]) if len(sys.argv) > 4 else None
    status_path = Path(sys.argv[5]) if len(sys.argv) > 5 else None
    count = build(coverage, site, notes_dir, builds_path=BUILDS, gap_wheels_path=gap_path, gap_status_path=status_path)
    print(f"pages: {count} packages in {site / 'p'}")
