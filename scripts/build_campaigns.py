"""Build static campaign pages from a wheelgap coverage scan.

    python scripts/build_campaigns.py data/coverage.json data/history.json \
        data/changes.json campaigns.toml _site

Writes <site>/campaigns/<id>/index.html for each campaign in the TOML file, an
index of them at <site>/campaigns/index.html, and adds those URLs to
<site>/sitemap.xml (created by build_pages.py, or written here if it is missing).
Runs at publish time; the pages are not committed. The pages are plain HTML
(no JavaScript) and reuse site/style.css.

    python scripts/build_campaigns.py --record-baselines data/coverage.json \
        data/changes.json campaigns.toml data/campaigns

Writes data/campaigns/<id>.json, the campaign's starting to-do list, for each
campaign that doesn't have one yet, once its start has come and every package in
its top N was looked up; an existing file is never rewritten. The
nightly scan runs this and commits the files, so a campaign's Done list and
progress don't depend on changes.json, which only keeps 90 days. The page
build reads them from data/campaigns next to coverage.json (or the directory
given as a sixth argument).

To-do rows are checked against the repository's notes/*.toml, read through
check_notes.py. A package with a current note for the campaign's platform gets a
"has a note" link, and one whose note is an alternative (kind "alternative")
moves out of To do into its own list.
"""

from __future__ import annotations

import datetime
import html
import json
import re
import sys
import tomllib
from pathlib import Path

if __package__:  # imported as scripts.build_campaigns (the tests)
    from . import check_notes
else:  # run as python scripts/build_campaigns.py
    import check_notes

SCAN_SCHEMA = 1
BASELINE_SCHEMA = 1
SAFE_ID = re.compile(r"[a-z0-9]+(-[a-z0-9]+)*")
SAFE_NAME = re.compile(r"[a-z0-9]+(-[a-z0-9]+)*")
BASE_URL = "https://wheelgap.github.io/tracker/"
NOTES_URL = "https://github.com/wheelgap/tracker/blob/main/notes/"
NOTES_DIR = Path(__file__).resolve().parent.parent / "notes"
# Note kinds (notes/README.md) that mean the campaign shouldn't ask upstream for this wheel. Only
# "alternative" says that: a different package or extra has it. None of the kinds records that upstream
# declined; "upstream-issue" is a ticket to follow, so those rows stay in To do with the marker.
SET_ASIDE_KINDS = ("alternative",)
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
STATUS_TEXT = {"wheel": "wheel", "source": "from source", "missing": "can't install", "unknown": "unknown"}
CSP = "default-src 'none'; img-src 'self' data:; style-src 'self' 'unsafe-inline'; base-uri 'none'; form-action 'none'"
ASK_FIRST = (
    "Pick one package, not a list. Search its issue tracker first: someone may have asked already. "
    "If nobody has, open one issue there that links the package's page on this tracker and says which "
    "wheel is missing, and wait for a maintainer to agree before sending a pull request. Never file the "
    "same request across many projects at once, and take \"not now\" for an answer."
)
CHANGES_KEPT_DAYS = 90  # scripts/update_changes.py trims changes.json to this many days


def canonical(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def python_key(value: str) -> tuple[int, int]:
    major, minor = value.rstrip("t").split(".", 1)
    return int(major), int(minor)


def newest_non_free_threaded(targets: list[dict]) -> str:
    pys = {t["python"] for t in targets if not str(t["python"]).endswith("t")}
    if not pys:
        sys.exit("build_campaigns: no non-free-threaded Python in coverage.json")
    return max(pys, key=python_key)


def iso_date(value: object) -> str | None:
    """A YYYY-MM-DD string for a TOML date or a quoted date, or None if it isn't a real date."""
    if isinstance(value, datetime.datetime):
        return None
    if isinstance(value, datetime.date):
        return value.isoformat()
    if isinstance(value, str) and re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        try:
            return datetime.date.fromisoformat(value).isoformat()
        except ValueError:
            return None
    return None


def load_campaigns(config_path: Path) -> list[dict]:
    data = tomllib.loads(config_path.read_text(encoding="utf-8"))
    campaigns = data.get("campaign", [])
    if not campaigns:
        sys.exit(f"build_campaigns: no [[campaign]] entries in {config_path}")
    seen: set[str] = set()
    for campaign in campaigns:
        for field in ("id", "title", "platform", "top", "start"):
            if field not in campaign:
                sys.exit(f"build_campaigns: campaign is missing {field!r}")
        ident = campaign["id"]
        if not SAFE_ID.fullmatch(ident):
            sys.exit(f"build_campaigns: invalid campaign id {ident!r}")
        if ident in seen:
            sys.exit(f"build_campaigns: duplicate campaign id {ident!r}")
        seen.add(ident)
        top = campaign["top"]
        if not isinstance(top, int) or isinstance(top, bool) or top < 1:
            sys.exit(f"build_campaigns: campaign {ident!r} has a bad top count")
        start = iso_date(campaign["start"])
        if start is None:
            sys.exit(f"build_campaigns: campaign {ident!r} start must be a date, YYYY-MM-DD")
        campaign["start"] = start
        python = campaign.get("python")
        if python is not None and not (isinstance(python, str) and re.fullmatch(r"3\.\d+", python)):
            sys.exit(f"build_campaigns: campaign {ident!r} python must be like \"3.14\" (not free-threaded)")
        for field in ("title", "platform", "description", "how_to"):
            if field in campaign and not isinstance(campaign[field], str):
                sys.exit(f"build_campaigns: campaign {ident!r} {field} must be a string")
        if not campaign.get("how_to", "https://").startswith("https://"):
            sys.exit(f"build_campaigns: campaign {ident!r} how_to must be an https:// URL")
        sources = campaign.get("sources", [])
        if not isinstance(sources, list) or not all(isinstance(u, str) and u.startswith("https://") for u in sources):
            sys.exit(f"build_campaigns: campaign {ident!r} sources must be a list of https:// URLs")
    return campaigns


def target_index(targets: list[dict], python: str, platform: str) -> int:
    for i, target in enumerate(targets):
        if target["python"] == python and target["platform"] == platform:
            return i
    sys.exit(f"build_campaigns: no {python} {platform} target in coverage.json")


def top_packages(scan: dict, top: int) -> list[dict]:
    return sorted((p for p in scan["packages"] if p["rank"] <= top), key=lambda p: p["rank"])


def package_status(package: dict, index: int) -> str:
    if package.get("error"):
        return "error"
    return package["results"][index]


def has_wheel(package: dict, index: int) -> bool:
    return package_status(package, index) == "wheel"


def coverage_counts(packages: list[dict], index: int) -> tuple[int, int]:
    """Wheels on the target, out of the packages that could be looked up."""
    checked = [p for p in packages if not p.get("error")]
    return sum(1 for p in checked if has_wheel(p, index)), len(checked)


OS_NAMES = {"linux": "Linux", "win": "Windows", "macos": "macOS"}


def os_of(platform: str) -> str:
    """linux for linux-* and musl-* (glibc and musl are both Linux), else the part before the dash."""
    return "linux" if platform.startswith(("linux-", "musl-")) else platform.split("-", 1)[0]


def native_indexes(targets: list[dict], python: str, platform: str) -> list[int]:
    """The campaign Python's targets on the same OS as the campaign's platform.

    A package with no Linux wheel at all (psycopg2 points people at psycopg2-binary) has an OS-wide gap, not
    one for a musllinux campaign to ask about.
    """
    return [
        i for i, t in enumerate(targets) if t["python"] == python and os_of(t["platform"]) == os_of(platform)
    ]


def not_started(package: dict, indexes: list[int]) -> bool:
    """No wheel for this Python on any platform of the campaign's OS: not a gap for this campaign to ask about."""
    return not package.get("error") and not any(package["results"][i] == "wheel" for i in indexes)


def trend(history: list[dict], label: str, start: str) -> list[dict]:
    """Per-day wheel counts for a target label, across the whole nightly scan, from the campaign's start.

    history.json only stores those full-scan totals, not a top-N slice, so the
    trend is labelled as the nightly scan rather than the campaign's top N.
    If no night with this label is on or after start yet, the latest one is returned on its own.
    A row's "upgrade" is its wheelgap version when that differs from the version of the previous
    row for this label (even if that row is before start), else "". A night without the label, or
    without a version, doesn't change what the next row is compared with.
    """
    rows = []
    previous = ""
    for entry in history:
        cell = entry.get("summary", {}).get(label)
        if not cell:
            continue
        version = str(entry.get("wheelgap") or "")
        upgrade = version if previous and version and version != previous else ""
        previous = version or previous
        packages = entry.get("packages") or 0
        wheels = cell.get("wheel", 0)
        rows.append({
            "date": entry["date"],
            "wheels": wheels,
            "packages": packages,
            "percent": (100.0 * wheels / packages) if packages else 0.0,
            "upgrade": upgrade,
        })
    since = [r for r in rows if r["date"] >= start]
    return since or rows[-1:]


def todo_list(packages: list[dict], index: int, indexes: list[int]) -> list[dict]:
    """Packages that lack a wheel on this target but have one for the same Python elsewhere."""
    rows = []
    for package in packages:
        if package.get("error") or has_wheel(package, index) or not_started(package, indexes):
            continue
        status = package_status(package, index)
        rows.append({
            "rank": package["rank"],
            "name": package["name"],
            "version": package["version"] or "",
            "status": status,
            "canonical": canonical(package["name"]),
        })
    return rows


def platform_notes(notes: list[dict], package: dict, targets: list[dict], platform: str) -> list[dict]:
    """The package's current notes (check_notes.current) that cover the campaign's platform."""
    return [
        note for note in check_notes.current(notes, package, targets)
        if note["platforms"] == ["all"] or platform in note["platforms"]
    ]


def gains_since(changes: list[dict], label: str, start: str) -> dict[str, dict]:
    """The first gain of a wheel on the target after start, per canonical name.

    A change dated on the start night is already in that night's scan, so only later nights count.
    """
    first: dict[str, dict] = {}
    for entry in sorted(changes, key=lambda c: c.get("date", "")):
        if entry.get("date", "") <= start:
            continue
        for item in entry.get("gained", []):
            if item.get("target") == label and item.get("to") == "wheel":
                first.setdefault(canonical(item["name"]), item)
    return first


def status_at_start(changes: list[dict], key: str, label: str, start: str, now: str) -> str:
    """A package's result on a target on the start night: the "from" of its first change after start, else now."""
    for entry in sorted(changes, key=lambda c: c.get("date", "")):
        if entry.get("date", "") <= start:
            continue
        for item in entry.get("gained", []) + entry.get("lost", []):
            if item.get("target") == label and canonical(item.get("name", "")) == key:
                return item.get("from") or ""
    return now


def baseline_for(campaign: dict, scan: dict, changes: list[dict], python: str) -> dict:
    """The campaign's starting to-do list, from this scan and the change log.

    It is today's to-do list, plus packages in the top N that gained a wheel on the target after the
    campaign's start and were on the to-do list then: no wheel on the target, and a wheel for the
    campaign's Python on another platform of the same OS (so not "left out"), going by the change log.
    A baseline recorded after its start is a reconstruction; "recorded" says when it was made.
    """
    platform = campaign["platform"]
    targets = scan["targets"]
    index = target_index(targets, python, platform)
    label = targets[index]["label"]
    indexes = native_indexes(targets, python, platform)
    packages = top_packages(scan, campaign["top"])
    rows = {
        canonical(item["name"]): {"name": item["name"], "rank": item["rank"], "status": item["status"]}
        for item in todo_list(packages, index, indexes)
    }
    start = campaign["start"]
    gained = gains_since(changes, label, start)
    for package in packages:
        key = canonical(package["name"])
        if key not in gained or key in rows or package.get("error"):
            continue
        then = {
            i: status_at_start(changes, key, targets[i]["label"], start, package["results"][i]) for i in indexes
        }
        if then[index] == "wheel" or not any(then[i] == "wheel" for i in indexes if i != index):
            continue
        rows[key] = {"name": package["name"], "rank": package["rank"], "status": then[index]}
    return {
        "schema": BASELINE_SCHEMA,
        "campaign": campaign["id"],
        "target": label,
        "top": campaign["top"],
        "start": start,
        "recorded": str(scan["generated"])[:10],
        "wheelgap": str(scan["wheelgap"]),
        "packages": sorted(rows.values(), key=lambda r: r["rank"]),
    }


def unrecordable(campaign: dict, scan: dict, python: str) -> str | None:
    """Why a baseline shouldn't be frozen from this scan, or None if it can be."""
    generated = str(scan["generated"])[:10]
    if campaign["start"] > generated:
        return f"starts on {campaign['start']}, after this scan ({generated})"
    index = target_index(scan["targets"], python, campaign["platform"])
    failed = [
        p["name"] for p in top_packages(scan, campaign["top"])
        if p.get("error") or p["results"][index] == "unknown"
    ]
    if failed:
        return f"{len(failed)} of its top {campaign['top']} couldn't be looked up ({', '.join(failed[:5])})"
    return None


def load_baseline(path: Path) -> dict | None:
    if not path.exists():
        return None
    data = json.loads(path.read_text(encoding="utf-8"))
    if data.get("schema") != BASELINE_SCHEMA:
        sys.exit(f"build_campaigns: {path} schema {data.get('schema')!r}, expected {BASELINE_SCHEMA}")
    packages = data.get("packages")
    if not isinstance(packages, list) or not all(
        isinstance(p, dict) and isinstance(p.get("name"), str) and isinstance(p.get("status", ""), str)
        for p in packages
    ):
        sys.exit(f"build_campaigns: {path} needs a packages list of {{name, status}}")
    for field in ("campaign", "target", "start", "recorded"):
        if not isinstance(data.get(field), str):
            sys.exit(f"build_campaigns: {path} is missing {field!r}")
    if not isinstance(data.get("top"), int) or isinstance(data.get("top"), bool):
        sys.exit(f"build_campaigns: {path} is missing 'top'")
    # A hand edit can repeat a package; count it once.
    seen: set[str] = set()
    data["packages"] = [
        p for p in packages if canonical(p["name"]) not in seen and not seen.add(canonical(p["name"]))
    ]
    return data


def baseline_mismatch(baseline: dict, campaign: dict, label: str) -> list[str]:
    """The settings a recorded baseline doesn't share with campaigns.toml, as "field: baseline value"."""
    expected = {"campaign": campaign["id"], "top": campaign["top"], "start": campaign["start"], "target": label}
    return [f"{field} {baseline.get(field)}" for field, value in expected.items() if baseline.get(field) != value]


def done_list(baseline: dict, packages: list[dict], index: int, label: str, changes: list[dict]) -> list[dict]:
    """Baseline packages that have a wheel on the target in this scan, wherever they now rank.

    packages is the whole scan, not the campaign's top N: a package that gained the wheel and then
    slipped below the top N is still done. Only one that left the scan altogether can't be checked.
    A package that gained the wheel and lost it again isn't done. The date is the latest gain in
    changes.json; once that has aged out of the log (or fell on a night the log skips), it's "before"
    the oldest night still in the log, or "not in the log".
    """
    current = {canonical(p["name"]): p for p in packages}
    latest: dict[str, str] = {}
    for entry in changes:
        for item in entry.get("gained", []):
            if item.get("target") == label and item.get("to") == "wheel":
                key = canonical(item["name"])
                latest[key] = max(latest.get(key, ""), entry.get("date", ""))
    oldest = min((c.get("date", "") for c in changes), default="")
    rows = []
    for item in baseline["packages"]:
        key = canonical(item["name"])
        package = current.get(key)
        if package is None or not has_wheel(package, index):  # an errored package has no "wheel"
            continue
        date = latest.get(key, "")
        if date:
            shown = date
        elif oldest > baseline["start"]:
            shown = f"before {oldest}"
        else:
            shown = "not in the log"
        rows.append({
            "date": shown,
            "sort": date,
            "name": package["name"],
            "version": package["version"] or "",
            "canonical": key,
            "frm": item.get("status") or "",
        })
    rows.sort(key=lambda r: (r["sort"], r["name"]))
    return rows


def source_url(package: dict) -> str | None:
    """Return a homepage or repo URL when coverage.json carries project_urls."""
    urls = package.get("project_urls") or {}
    if not isinstance(urls, dict):
        return None
    for key in ("Source", "source", "Repository", "repository", "Homepage", "homepage", "Home", "home"):
        value = urls.get(key)
        if isinstance(value, str) and value.startswith("https://"):
            return value
    return None


def percent(wheels: int, total: int) -> str:
    if not total:
        return "0%"
    value = 100.0 * wheels / total
    if value == int(value):
        return f"{int(value)}%"
    return f"{value:.1f}%"



def article(name: str) -> str:
    return "an" if name[:1].lower() in "aeiou" else "a"


def rows_html(items: list[dict], *, kind: str, include_source: bool, empty: str = "") -> str:
    e = html.escape
    if not items:
        empty = empty or ("None yet." if kind == "done" else "Every package in the set already has a wheel.")
        return f'        <p class="note">{empty}</p>\n'
    head = "<th>Rank</th><th>Package</th><th>Status</th>"
    if kind == "done":
        head = "<th>Date</th><th>Package</th><th>Was</th>"
    if include_source:
        head += "<th>Source</th>"
    body = []
    for item in items:
        name = item["name"]
        slug = item["canonical"]
        # build_pages.py skips names that aren't safe in a path, so only link pages that exist.
        page_link = f'<a href="../../p/{e(slug)}/">{e(name)}</a>' if SAFE_NAME.fullmatch(slug) else e(name)
        pkg = (
            f"{page_link}"
            f' <span class="ver">{e(item["version"])}</span>'
            f' · <a href="https://pypi.org/project/{e(name)}/">PyPI</a>'
        )
        if item.get("note"):
            pkg += f' · <a href="{e(item["note"])}">has a note</a>'
        if kind == "done":
            was = STATUS_TEXT.get(item["frm"], item["frm"])
            cells = f'<td>{e(item["date"])}</td><td>{pkg}</td><td>{e(was)}</td>'
        else:
            status = item["status"]
            label = "lookup failed" if status == "error" else STATUS_TEXT.get(status, status)
            cells = (
                f'<td>{item["rank"]}</td><td>{pkg}</td>'
                f'<td><span class="status s-{e(status)}">{e(label)}</span></td>'
            )
        if include_source:
            url = item.get("source")
            cells += f'<td><a href="{e(url)}">{e(url)}</a></td>' if url else "<td></td>"
        body.append(f"          <tr>{cells}</tr>")
    return (
        '        <div class="matrix-scroll"><table class="matrix">\n'
        f"          <thead><tr>{head}</tr></thead>\n"
        "          <tbody>\n"
        + "\n".join(body)
        + "\n          </tbody>\n"
        "        </table></div>\n"
    )


def trend_html(rows: list[dict], label: str, start: str) -> str:
    e = html.escape
    if not rows:
        return '        <p class="note">No history has been recorded for this target yet.</p>\n'
    upgrades = any(r["upgrade"] for r in rows)
    body = "\n".join(
        f'          <tr><td>{e(r["date"])}</td>'
        f'<td>{r["wheels"]:,} / {r["packages"]:,}</td>'
        f'<td>{e(percent(r["wheels"], r["packages"]))}</td>'
        + (f'<td>{"wheelgap " + e(r["upgrade"]) if r["upgrade"] else ""}</td>' if upgrades else "")
        + "</tr>"
        for r in rows
    )
    if rows[0]["date"] >= start:
        since = f"since the campaign started on {e(start)}"
    else:
        since = (
            f"on the latest night that has them ({e(rows[0]['date'])}), as no night from the campaign's start "
            f"on {e(start)} has them yet"
        )
    upgrade_note = (
        " A night marked with a wheelgap version is the first scan with that release: its change from the "
        "night before can come from the tool (new targets, or different rules for which wheels count), "
        "not only from packages shipping wheels."
        if upgrades else ""
    )
    return (
        f'        <p class="note">Nightly scan totals for {e(label)} {since}. They count every package in the '
        f"scan, not only this campaign's top N (history.json does not keep a top-N slice).{upgrade_note}</p>\n"
        '        <div class="matrix-scroll"><table class="matrix">\n'
        "          <thead><tr><th>Date</th><th>Wheels</th><th>Share</th>"
        + ("<th>Note</th>" if upgrades else "")
        + "</tr></thead>\n"
        f"          <tbody>\n{body}\n          </tbody>\n"
        "        </table></div>\n"
    )


def page(
    campaign: dict,
    scan: dict,
    history: list,
    changes: list,
    python: str,
    baseline: dict | None = None,
    notes: dict[str, list[dict]] | None = None,
) -> tuple[str, dict]:
    """The campaign's page, and the numbers the campaigns index lists for it.

    notes are check_notes.load()'s notes by canonical name. To-do rows with a current note for the
    platform link it, and those with a SET_ASIDE_KINDS note move to their own list, out of To do.
    """
    e = html.escape
    platform = campaign["platform"]
    top = campaign["top"]
    index = target_index(scan["targets"], python, platform)
    label = scan["targets"][index]["label"]
    packages = top_packages(scan, top)
    indexes = native_indexes(scan["targets"], python, platform)
    os_name = OS_NAMES.get(os_of(platform), os_of(platform))
    wheels, total = coverage_counts(packages, index)
    share = percent(wheels, total)
    compiled = [p for p in packages if not p.get("pure")]
    compiled_wheels, compiled_total = coverage_counts(compiled, index)
    errors = [p for p in packages if p.get("error")]
    waiting = [p for p in packages if not_started(p, indexes)]
    todo = todo_list(packages, index, indexes)
    include_source = any(source_url(p) for p in scan["packages"])
    if include_source:
        for item in todo:
            item["source"] = source_url(next(p for p in packages if p["name"] == item["name"]))
    set_aside = []
    for item in todo:
        package = next(p for p in packages if p["name"] == item["name"])
        found = platform_notes((notes or {}).get(item["canonical"], []), package, scan["targets"], platform)
        if found:
            item["note"] = f"{NOTES_URL}{item['canonical']}.toml"
        if any(note["kind"] in SET_ASIDE_KINDS for note in found):
            set_aside.append(item)
    set_aside_names = {item["name"] for item in set_aside}
    todo = [item for item in todo if item["name"] not in set_aside_names]
    recorded = baseline is not None
    if baseline is None:
        baseline = baseline_for(campaign, scan, changes, python)
    mismatch = baseline_mismatch(baseline, campaign, label) if recorded else []
    done = [] if mismatch else done_list(baseline, scan["packages"], index, label, changes)
    platform_name = PLATFORM_NAMES.get(platform, platform)
    title = campaign["title"]
    url = f"{BASE_URL}campaigns/{campaign['id']}/"
    summary = (
        f"{wheels} of the top {top} packages ship {article(platform_name)} {platform_name} wheel on Python {python} "
        f"({share}). {len(todo)} to ask about."
    )
    lede = campaign.get("description") or (
        f"This page turns the {platform_name} ({platform}) gaps among the most-downloaded packages into a list "
        "people can work through, one package at a time."
    )
    checked_note = f" {len(errors)} couldn't be looked up in this scan and aren't counted." if errors else ""
    waiting_html = ""
    if waiting:
        names = ", ".join(e(p["name"]) for p in waiting)
        waiting_html = (
            f'      <p class="note">Left out: {len(waiting)} {"package has" if len(waiting) == 1 else "packages have"} '
            f"no Python {e(python)} wheel for any {e(os_name)} platform yet ({names}). That's a gap for the project "
            f"as a whole (a new Python, an sdist-only release, or no {e(os_name)} wheels by design), not one this "
            "campaign should ask about.</p>\n"
        )
    read_notes = " A package with a note links it: read the note first." if any(i.get("note") for i in todo) else ""
    set_aside_count = set_aside_html = todo_empty = ""
    if set_aside:
        one = len(set_aside) == 1
        set_aside_count = f" ({len(set_aside)} more with an alternative {'is' if one else 'are'} listed below)"
        not_counted = "It isn't" if one else "They aren't"
        todo_empty = "Nothing to ask about: every package here without the wheel has an alternative, listed below."
        set_aside_html = (
            "    <section>\n"
            "      <h2>Has an alternative</h2>\n"
            f'      <p class="note">{len(set_aside)} of the top {top} packages {"lacks" if one else "lack"} '
            f"{e(article(platform_name))} {e(platform_name)} wheel but {'has' if one else 'have'} a note naming a "
            "different package or extra that has one, so this campaign doesn't ask upstream about "
            f"{'it' if one else 'them'}. {not_counted} counted in To do.</p>\n"
            f"{rows_html(set_aside, kind='todo', include_source=include_source)}"
            "    </section>\n"
        )
    sources_html = ""
    if campaign.get("sources"):
        links = " · ".join(f'<a href="{e(u)}">{e(u.removeprefix("https://"))}</a>' for u in campaign["sources"])
        sources_html = f'    <p class="meta">Sources: {links}</p>\n'
    how_to = campaign.get("how_to")
    how_to_html = f' <a href="{e(how_to)}">How to ask for a missing wheel</a>.' if how_to else ""
    python_why = (
        " (set by the campaign)"
        if campaign.get("python")
        else " (the newest non-free-threaded Python, as the badges use)"
    )
    baseline_file = f"data/campaigns/{e(campaign['id'])}.json"
    start = e(campaign["start"])
    if recorded:
        done_note = f"The starting list is recorded in {baseline_file}."
        if baseline["recorded"] > campaign["start"]:
            done_note = (
                f"The starting list in {baseline_file} was reconstructed on {e(baseline['recorded'])} from that "
                "night's to-do list and the change log since the start."
            )
    else:
        done_note = (
            "The starting list isn't recorded yet (the next nightly scan writes it), so for now it is today's "
            "to-do list plus the packages that gained the wheel since the start."
        )
    count = len(baseline["packages"])
    if mismatch:
        progress = (
            f"No progress to show: {baseline_file} was recorded for other settings "
            f"({e(', '.join(mismatch))}). Delete it to record a new starting list with the next nightly scan."
        )
        done_note = ""
    elif count:
        progress = (
            f'<strong>{len(done):,} of the {count:,}</strong> {"package" if count == 1 else "packages"} on the '
            f"to-do list at the start ({start}) now {'has' if len(done) == 1 else 'have'} "
            f"{e(article(platform_name))} {e(platform_name)} wheel."
        )
    else:
        progress = f"Nothing was on the to-do list at the start ({start})."
    if done_note:
        done_note += (
            " A package that gained the wheel and lost it again isn't counted, and To do can include packages "
            "that weren't on the list at the start (new to the top), which don't count here either. Dates are "
            f"from the nightly change log, which keeps the last {CHANGES_KEPT_DAYS} days and skips nights when "
            "the scan moved to a new wheelgap version. Was is the status at the start."
        )
        if set_aside:
            done_note += (
                " Packages set aside under Has an alternative stay on the starting list, so To do and Done "
                "needn't add up to it."
            )
    generated = str(scan["generated"])[:10]
    listing = {
        "id": campaign["id"],
        "title": title,
        "platform": platform,
        "platform_name": platform_name,
        "python": python,
        "share": share,
        "wheels": wheels,
        "total": total,
        "todo": len(todo),
    }
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
    <p class="eyebrow"><a href="../../">wheelgap tracker</a> · <a href="../">Campaigns</a></p>
    <h1>{e(title)}</h1>
    <p class="lede">{e(lede)}</p>
{sources_html}    <p class="meta">From the wheelgap {e(str(scan["wheelgap"]))} scan of {e(generated)}, using Python {e(python)}{python_why}.</p>
  </header>
  <main class="wrap">
    <section>
      <h2>Coverage</h2>
      <p><strong>{e(share)}</strong> of the top {top} packages have {e(article(platform_name))} {e(platform_name)} wheel today ({wheels:,} of {total:,}).{checked_note}</p>
      <p>Pure-Python packages have a wheel for every platform, so that share flatters it. Of the {compiled_total:,} with compiled code, <strong>{e(percent(compiled_wheels, compiled_total))}</strong> have one ({compiled_wheels:,} of {compiled_total:,}).</p>
{trend_html(trend(history, label, campaign["start"]), label, campaign["start"])}    </section>
    <section>
      <h2>To do</h2>
      <p class="note">{len(todo)} of the top {top} packages have Python {e(python)} wheels for other platforms but not {e(platform_name)}, most-downloaded first{set_aside_count}.{read_notes} {e(ASK_FIRST)}{how_to_html}</p>
{rows_html(todo, kind="todo", include_source=include_source, empty=todo_empty)}{waiting_html}    </section>
{set_aside_html}    <section>
      <h2>Done</h2>
      <p>{progress}</p>
{f'      <p class="note">{done_note}</p>{chr(10)}' if done_note else ""}{"" if mismatch else rows_html(done, kind="done", include_source=False)}    </section>
  </main>
</body>
</html>
""", listing


def index_page(listings: list[dict], generated: str, wheelgap: str) -> str:
    """campaigns/index.html: every campaign in campaigns.toml, in file order."""
    e = html.escape
    rows = "\n".join(
        f'          <tr><td><a href="{e(c["id"])}/">{e(c["title"])}</a></td>'
        f'<td>{e(c["platform_name"])} <span class="ver">{e(c["platform"])}</span></td>'
        f'<td>{e(c["python"])}</td>'
        f'<td><strong>{e(c["share"])}</strong> <span class="ver">{c["wheels"]:,} of {c["total"]:,}</span></td>'
        f'<td>{c["todo"]:,}</td></tr>'
        for c in listings
    )
    count = len(listings)
    summary = f"{count} wheel campaign{'' if count == 1 else 's'}: platform gaps among the most-downloaded PyPI packages, one list per platform."
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <meta http-equiv="Content-Security-Policy" content="{CSP}">
  <title>Campaigns · wheelgap tracker</title>
  <meta name="description" content="{e(summary)}">
  <link rel="canonical" href="{e(BASE_URL)}campaigns/">
  <link rel="stylesheet" href="../style.css">
  <link rel="icon" href="../favicon.svg" type="image/svg+xml">
</head>
<body>
  <header class="wrap">
    <p class="eyebrow"><a href="../">wheelgap tracker</a></p>
    <h1>Campaigns</h1>
    <p class="lede">Each campaign takes one platform and turns the missing wheels among the most-downloaded packages into a to-do list people can work through, one package at a time.</p>
    <p class="meta">From the wheelgap {e(wheelgap)} scan of {e(generated)}.</p>
  </header>
  <main class="wrap">
    <section>
      <h2>All campaigns</h2>
      <p class="note">Coverage is the share of each campaign's top packages with a wheel for its platform and Python. To do counts the packages that have wheels for that Python on other platforms but not this one.</p>
        <div class="matrix-scroll"><table class="matrix">
          <thead><tr><th>Campaign</th><th>Platform</th><th>Python</th><th>Coverage</th><th>To do</th></tr></thead>
          <tbody>
{rows}
          </tbody>
        </table></div>
    </section>
  </main>
</body>
</html>
"""


def add_to_sitemap(site: Path, urls: list[str]) -> None:
    path = site / "sitemap.xml"
    existing: list[str] = []
    if path.exists():
        existing = re.findall(r"<loc>([^<]*)</loc>", path.read_text(encoding="utf-8"))
    seen = set(existing)
    if BASE_URL not in seen:
        existing.insert(0, BASE_URL)
        seen.add(BASE_URL)
    for url in urls:
        if url not in seen:
            existing.append(url)
            seen.add(url)
    body = "".join(f"  <url><loc>{html.escape(u)}</loc></url>\n" for u in existing)
    path.write_text(
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        f'<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n{body}</urlset>\n',
        encoding="utf-8",
    )


def load_scan(coverage_path: Path) -> dict:
    scan = json.loads(coverage_path.read_text(encoding="utf-8"))
    if scan.get("schema") != SCAN_SCHEMA:
        sys.exit(f"build_campaigns: coverage.json schema {scan.get('schema')!r}, expected {SCAN_SCHEMA}")
    return scan


def dump_baseline(baseline: dict) -> str:
    """JSON with one package per line, so the committed file is easy to read and to edit by hand."""
    head = {k: v for k, v in baseline.items() if k != "packages"}
    lines = [f"  {json.dumps(k)}: {json.dumps(v)}," for k, v in head.items()]
    packages = ",\n".join(f"    {json.dumps(p)}" for p in baseline["packages"])
    return "{\n" + "\n".join(lines) + '\n  "packages": [\n' + packages + "\n  ]\n}\n"


def record_baselines(
    coverage_path: Path, changes_path: Path, config_path: Path, out: Path, log=print
) -> list[str]:
    """Write out/<id>.json for each campaign without one; return the ids written.

    A campaign that hasn't started yet, or whose top N had a failed lookup in this scan, is skipped
    and tried again the next night, so a baseline is never frozen from an imperfect scan.
    """
    scan = load_scan(coverage_path)
    changes = json.loads(changes_path.read_text(encoding="utf-8")) if changes_path.exists() else []
    campaigns = load_campaigns(config_path)
    newest = newest_non_free_threaded(scan["targets"])
    written = []
    for campaign in campaigns:
        path = out / f"{campaign['id']}.json"
        if path.exists():
            continue
        python = campaign.get("python") or newest
        reason = unrecordable(campaign, scan, python)
        if reason:
            log(f"campaigns: not recording {campaign['id']} yet: {reason}")
            continue
        baseline = baseline_for(campaign, scan, changes, python)
        out.mkdir(parents=True, exist_ok=True)
        path.write_text(dump_baseline(baseline), encoding="utf-8")
        written.append(campaign["id"])
    return written


def build(
    coverage_path: Path,
    history_path: Path,
    changes_path: Path,
    config_path: Path,
    site: Path,
    baselines: Path | None = None,
    notes_dir: Path | None = None,
) -> int:
    """Write the pages; notes come from notes_dir, or none when it is None."""
    scan = load_scan(coverage_path)
    notes = check_notes.load(notes_dir, scan) if notes_dir is not None else {}
    if baselines is None:
        baselines = coverage_path.parent / "campaigns"
    history = json.loads(history_path.read_text(encoding="utf-8")) if history_path.exists() else []
    changes = json.loads(changes_path.read_text(encoding="utf-8")) if changes_path.exists() else []
    campaigns = load_campaigns(config_path)
    newest = newest_non_free_threaded(scan["targets"])
    urls: list[str] = [f"{BASE_URL}campaigns/"]
    listings: list[dict] = []
    for campaign in campaigns:
        python = campaign.get("python") or newest
        ident = campaign["id"]
        out = site / "campaigns" / ident
        out.mkdir(parents=True, exist_ok=True)
        baseline = load_baseline(baselines / f"{ident}.json")
        text, listing = page(campaign, scan, history, changes, python, baseline, notes)
        (out / "index.html").write_text(text, encoding="utf-8")
        listings.append(listing)
        urls.append(f"{BASE_URL}campaigns/{ident}/")
    generated = str(scan["generated"])[:10]
    (site / "campaigns" / "index.html").write_text(
        index_page(listings, generated, str(scan["wheelgap"])), encoding="utf-8"
    )
    add_to_sitemap(site, urls)
    return len(campaigns)


if __name__ == "__main__":
    if sys.argv[1:2] == ["--record-baselines"]:
        coverage, changes, config, out = (Path(p) for p in sys.argv[2:6])
        written = record_baselines(coverage, changes, config, out)
        print(f"campaigns: recorded {', '.join(written) if written else 'no new'} baseline(s) in {out}")
    else:
        coverage, history, changes, config, site = (Path(p) for p in sys.argv[1:6])
        baselines = Path(sys.argv[6]) if len(sys.argv) > 6 else None
        built = build(coverage, history, changes, config, site, baselines, NOTES_DIR)
        print(f"campaigns: {built} pages in {site / 'campaigns'}")
