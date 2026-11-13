"""Refuse notes that don't match the v0 format, before they reach the knowledge base.

    python3 scripts/check_notes.py [notes_dir] [coverage.json]

Standard library only. Exit 0 when every file in notes_dir (except README.md)
is a valid notes/<normalized-name>.toml. Print one problem per line otherwise.

build_lookup.py and build_pages.py read notes through load() and current() here,
so the site shows only notes this checker accepts.
"""

from __future__ import annotations

import datetime as dt
import json
import re
import sys
import tomllib
import unicodedata
from pathlib import Path
from urllib.parse import urlsplit

KINDS = ("workaround", "alternative", "upstream-issue", "other-source")
MAX_TEXT = 500  # text must be shorter than this
REQUIRED_KEYS = ("platforms", "kind", "text", "links", "updated")
NOTE_KEYS = frozenset(REQUIRED_KEYS) | {"versions", "python"}
NAME_RE = re.compile(r"[-_.]+")
STEM_RE = re.compile(r"[a-z0-9]+(-[a-z0-9]+)*")
# One clause of the v0 versions subset of PEP 440: plain N(.N)* release numbers,
# with a trailing .* only after == or !=. No ~=, ===, epochs, pre/post/dev or local
# versions, so #28 can match it without the packaging library.
SPEC_CLAUSE_RE = re.compile(
    r"\s*(?:(?:==|!=)\s*\d+(?:\.\d+)*(?:\.\*)?|(?:<=|>=|<|>)\s*\d+(?:\.\d+)*)\s*",
    re.ASCII,  # \d is Unicode-aware otherwise: "<٣" would pass
)
# The same clause split into its parts, for matching: operator, release, optional ".*".
SPEC_PARTS_RE = re.compile(r"(==|!=|<=|>=|<|>)\s*(\d+(?:\.\d+)*)(\.\*)?", re.ASCII)
RELEASE_RE = re.compile(r"\d+(?:\.\d+)*", re.ASCII)
WHITESPACE_RE = re.compile(r"\s")


def normalize_name(name: str) -> str:
    """PEP 503 normalization: lowercase, runs of -, _ and . become one -."""
    return NAME_RE.sub("-", name).lower()


def valid_specifier(value: object) -> bool:
    """Comma-separated clauses like "<3" or ">=2.8, <2.10" (see SPEC_CLAUSE_RE)."""
    if not isinstance(value, str) or not value.strip():
        return False
    return all(SPEC_CLAUSE_RE.fullmatch(clause) for clause in value.split(","))


def platform_names(coverage: dict) -> set[str]:
    names: set[str] = set()
    for target in coverage.get("targets", []):
        if platform := target.get("platform"):
            names.add(platform)
    return names


def python_labels(coverage: dict) -> set[str]:
    """The Python labels the scan covers ("3.13", "3.14t", ...), from the `python` of each target."""
    return {t["python"] for t in coverage.get("targets", []) if t.get("python")}


def scanned_pairs(coverage: dict) -> set[tuple[str, str]]:
    """The (platform, Python label) pairs the scan has a target for: pyodide has fewer Pythons than the rest."""
    return {
        (t["platform"], t["python"])
        for t in coverage.get("targets", [])
        if t.get("platform") and t.get("python")
    }


def problems(notes_dir: Path, coverage: dict) -> list[str]:
    allowed = platform_names(coverage)
    if not allowed:
        return ["coverage has no target platforms"]
    pythons = python_labels(coverage)
    pairs = scanned_pairs(coverage)

    out: list[str] = []
    for path in sorted(notes_dir.iterdir()):
        if path.name == "README.md":
            continue
        if not path.is_file():
            out.append(f"{path.name}: not a file (only notes/<name>.toml belong here)")
            continue
        out.extend(_read_file(path, notes_dir, allowed, pythons, pairs)[1])
    return out


def load(notes_dir: Path, coverage: dict, warn=None) -> dict[str, list[dict]]:
    """The notes in notes_dir by normalized package name, from the files this checker accepts.

    A file with any problem is left out whole, and each problem is passed to warn (a GitHub
    Actions warning on stderr by default), so a bad note never reaches the site.
    """
    if warn is None:
        def warn(message: str) -> None:
            print(f"::warning::notes: {message}", file=sys.stderr)

    if not notes_dir.is_dir():
        return {}
    allowed = platform_names(coverage)
    if not allowed:
        warn("coverage has no target platforms, so no notes are shown")
        return {}
    pythons = python_labels(coverage)
    pairs = scanned_pairs(coverage)
    out: dict[str, list[dict]] = {}
    for path in sorted(notes_dir.iterdir()):
        if path.name == "README.md":
            continue
        if not path.is_file():
            warn(f"skipping {path.name}: not a file (only notes/<name>.toml belong here)")
            continue
        notes, found = _read_file(path, notes_dir, allowed, pythons, pairs)
        for problem in found:
            warn(f"skipping {path.name}: {problem}")
        if not found:
            out[path.stem] = notes
    return out


def _read_file(
    path: Path,
    notes_dir: Path,
    allowed: set[str],
    pythons: set[str] = frozenset(),
    pairs: set[tuple[str, str]] | None = None,
) -> tuple[list[dict], list[str]]:
    """The file's notes and its problems; the notes are only usable when there are no problems."""
    rel = (
        path.relative_to(notes_dir).as_posix()
        if path.is_relative_to(notes_dir)
        else path.name
    )
    if path.suffix != ".toml":
        return [], [f"{rel}: not a .toml file"]
    stem = path.stem
    if not STEM_RE.fullmatch(stem):
        expected = normalize_name(stem)
        message = f"filename must be the normalized package name (got {stem!r}, expected {expected!r})"
        return [], [f"{rel}: {message}"]

    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except UnicodeDecodeError:
        return [], [f"{rel}: not valid UTF-8"]
    except (OSError, tomllib.TOMLDecodeError) as e:
        return [], [f"{rel}: {e}"]

    out: list[str] = []
    unknown = sorted(set(data) - {"note"})
    if unknown:
        out.append(f"{rel}: unknown top-level keys: {', '.join(unknown)}")

    notes = data.get("note")
    if not isinstance(notes, list) or not notes:
        out.append(f"{rel}: needs at least one [[note]]")
        return [], out

    for i, note in enumerate(notes, start=1):
        prefix = f"{rel} note {i}"
        if not isinstance(note, dict):
            out.append(f"{prefix}: expected a table")
            continue
        out.extend(_note_problems(prefix, note, allowed, pythons, pairs))
    return notes, out


def _release(text: str) -> tuple[int, ...]:
    return tuple(int(part) for part in text.split("."))


def _padded(release: tuple[int, ...], width: int) -> tuple[int, ...]:
    return release + (0,) * (width - len(release))


def version_matches(spec: str, version: object) -> bool:
    """Whether a plain release such as "2.9.1" matches a versions value, as PEP 440 would say.

    Only plain N(.N)* releases are matched. Anything else (a pre-, post- or dev release, a
    local version, or no version) returns False, so a note limited to some versions is
    hidden rather than shown for a release it may not apply to. So does a spec the checker
    rejects (valid_specifier), such as "<2.*".
    """
    if not valid_specifier(spec):
        return False
    if not isinstance(version, str) or not RELEASE_RE.fullmatch(version):
        return False
    have = _release(version)
    for clause in spec.split(","):
        parts = SPEC_PARTS_RE.fullmatch(clause.strip())
        if not parts:
            return False
        op, release, star = parts.groups()
        want = _release(release)
        if star:  # prefix match; the candidate is padded with zeros (PEP 440)
            hit = _padded(have, len(want))[: len(want)] == want
            ok = hit if op == "==" else not hit
        else:
            width = max(len(have), len(want))
            a, b = _padded(have, width), _padded(want, width)
            ok = {"==": a == b, "!=": a != b, "<=": a <= b, ">=": a >= b, "<": a < b, ">": a > b}[op]
        if not ok:
            return False
    return True


def current(notes: list[dict], package: dict, targets: list[dict]) -> list[dict]:
    """The notes still worth showing for a package in this scan.

    A note is stale, and left out, when its versions don't match the scanned version, or when
    every target of every platform it lists (each Python, free-threaded included) has a wheel, or on
    Pyodide is `bundled` (Pyodide ships its own build). A note with a `python` list only counts the
    targets of those Pythons, so it retires once they have wheels even if another Python still lacks one.
    """
    results = package.get("results") or []
    every = {t.get("platform") for t in targets}
    out = []
    for note in notes:
        if "versions" in note and not version_matches(note["versions"], package.get("version")):
            continue
        platforms = every if note["platforms"] == ["all"] else set(note["platforms"])
        listed = [
            r for t, r in zip(targets, results)
            if t.get("platform") in platforms and covers_python(note, t.get("python"))
        ]
        if listed and all(r in ("wheel", "bundled") for r in listed):
            continue
        out.append(note)
    return out


def covers_python(note: dict, python: str | None) -> bool:
    """Whether a note applies to a scanned Python label such as "3.14t".

    A note without a `python` list applies to every Python. With one, it applies only to the
    listed Pythons, so an unknown Python (None) is not covered.
    """
    return "python" not in note or python in note["python"]


def as_json(note: dict) -> dict:
    """A note with the format's fields, for JSON: versions is null when absent, updated is YYYY-MM-DD.

    python is only present when the note has one. wheelgap reads these notes and rejects any whose keys
    aren't exactly the ones it knows, so a `"python": null` on every note would make a client that doesn't
    know the field drop all of them; leaving it out keeps the existing notes byte-for-byte the same.
    """
    out = {
        "platforms": list(note["platforms"]),
        "kind": note["kind"],
        "text": note["text"],
        "links": list(note["links"]),
        "versions": note.get("versions"),
        "updated": note["updated"].isoformat(),
    }
    if "python" in note:
        out["python"] = list(note["python"])
    return out


def valid_link(link: object) -> bool:
    """An https:// URL with a host, and no whitespace, control or format characters or user info.

    The site shows the link itself as its text, so it mustn't be able to hide or disguise its target
    (a bidi override, or https://docs.python.org@evil.example/).
    """
    if not isinstance(link, str) or WHITESPACE_RE.search(link):
        return False
    if any(unicodedata.category(c) in ("Cc", "Cf") for c in link):
        return False
    try:
        parts = urlsplit(link)
        return parts.scheme == "https" and bool(parts.hostname) and "@" not in parts.netloc
    except ValueError:  # e.g. a malformed IPv6 host or port
        return False


def _duplicates(items: list) -> list:
    seen: list = []
    dupes: list = []
    for item in items:
        if item in seen and item not in dupes:
            dupes.append(item)
        seen.append(item)
    return dupes


def _note_problems(
    prefix: str,
    note: dict,
    allowed: set[str],
    pythons: set[str] = frozenset(),
    pairs: set[tuple[str, str]] | None = None,
) -> list[str]:
    out: list[str] = []
    unknown = sorted(set(note) - NOTE_KEYS)
    if unknown:
        out.append(f"{prefix}: unknown keys: {', '.join(unknown)}")
    missing = [key for key in REQUIRED_KEYS if key not in note]
    if missing:
        out.append(f"{prefix}: missing required keys: {', '.join(missing)}")

    if "platforms" in note:
        before = len(out)
        platforms = note["platforms"]
        if not isinstance(platforms, list) or not platforms:
            out.append(f"{prefix}: platforms must be a non-empty list")
        elif "all" in platforms and len(platforms) > 1:
            out.append(f'{prefix}: platforms "all" cannot be combined with others')
        elif platforms != ["all"]:
            bad = [p for p in platforms if not isinstance(p, str) or p not in allowed]
            if bad:
                out.append(f"{prefix}: unknown platform {bad[0]!r}")
            elif dupes := _duplicates(platforms):
                out.append(f"{prefix}: duplicate platforms: {', '.join(dupes)}")
        scope_ok = len(out) == before  # valid platforms, so python's overlap with them can be checked below
    else:
        scope_ok = False

    if "python" in note:
        before = len(out)
        listed = note["python"]
        if not isinstance(listed, list) or not listed:
            out.append(f"{prefix}: python must be a non-empty list")
        elif bad := [p for p in listed if not isinstance(p, str) or p not in pythons]:
            out.append(f"{prefix}: unknown python {bad[0]!r}")
        elif dupes := _duplicates(listed):
            out.append(f"{prefix}: duplicate python: {', '.join(dupes)}")
        # Every label is scanned somewhere, but a note must also have a target to retire by: pyodide, say,
        # has no free-threaded Python, so platforms = ["pyodide"] with python = ["3.14t"] would never retire.
        if scope_ok and pairs is not None and len(out) == before:
            platforms = allowed if note["platforms"] == ["all"] else set(note["platforms"])
            if not any((p, py) in pairs for p in platforms for py in listed):
                out.append(
                    f"{prefix}: the scan has no target for platforms {', '.join(note['platforms'])} "
                    f"on python {', '.join(listed)}"
                )

    if "kind" in note and note["kind"] not in KINDS:
        out.append(f"{prefix}: kind must be one of {', '.join(KINDS)}")

    if "text" in note:
        text = note["text"]
        if not isinstance(text, str) or not text.strip():
            out.append(f"{prefix}: text must be a non-empty string")
        elif "\n" in text or "\r" in text:
            out.append(f"{prefix}: text must be a single line")
        elif bad := [c for c in text if unicodedata.category(c) in ("Cc", "Cf")]:
            out.append(f"{prefix}: text contains U+{ord(bad[0]):04X}")
        elif len(text) >= MAX_TEXT:
            out.append(
                f"{prefix}: text is {len(text)} characters, must be under {MAX_TEXT}"
            )

    if "links" in note:
        links = note["links"]
        if not isinstance(links, list) or not links:
            out.append(f"{prefix}: links must be a non-empty list")
        elif bad := [link for link in links if not valid_link(link)]:
            out.append(
                f"{prefix}: links must be https URLs with a host, no spaces or control "
                f"characters and no user@ part (got {bad[0]!r})"
            )
        elif dupes := _duplicates(links):
            out.append(f"{prefix}: duplicate links: {', '.join(dupes)}")

    if "versions" in note and not valid_specifier(note["versions"]):
        out.append(
            f'{prefix}: versions must be clauses like "<3" or ">=2.8, <2.10" '
            f"(==, !=, <=, >=, <, > and N.N versions only; got {note['versions']!r})"
        )

    if "updated" in note:
        updated = note["updated"]
        # A TOML datetime is a dt.datetime, which subclasses dt.date: require a date.
        if type(updated) is not dt.date:
            out.append(
                f"{prefix}: updated must be a date like 2026-11-11, "
                "not a string or datetime"
            )
        # One day of slack, so a note dated in a timezone ahead of UTC still passes.
        elif updated > dt.datetime.now(dt.UTC).date() + dt.timedelta(days=1):
            out.append(f"{prefix}: updated {updated} is in the future")

    return out


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    root = Path(__file__).resolve().parent.parent
    notes_dir = Path(args[0]) if args else root / "notes"
    coverage_path = Path(args[1]) if len(args) > 1 else root / "data" / "coverage.json"
    coverage = json.loads(coverage_path.read_text(encoding="utf-8"))
    found = problems(notes_dir, coverage)
    if found:
        print("\n".join(found), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
