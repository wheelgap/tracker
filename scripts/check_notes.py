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
from pathlib import Path
from urllib.parse import urlsplit

KINDS = ("workaround", "alternative", "upstream-issue", "other-source")
MAX_TEXT = 500  # text must be shorter than this
REQUIRED_KEYS = ("platforms", "kind", "text", "links", "updated")
NOTE_KEYS = frozenset(REQUIRED_KEYS) | {"versions"}
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


def problems(notes_dir: Path, coverage: dict) -> list[str]:
    allowed = platform_names(coverage)
    if not allowed:
        return ["coverage has no target platforms"]

    out: list[str] = []
    for path in sorted(notes_dir.iterdir()):
        if path.name == "README.md":
            continue
        if not path.is_file():
            out.append(f"{path.name}: not a file (only notes/<name>.toml belong here)")
            continue
        out.extend(_read_file(path, notes_dir, allowed)[1])
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
    out: dict[str, list[dict]] = {}
    for path in sorted(notes_dir.iterdir()):
        if path.name == "README.md" or not path.is_file():
            continue
        notes, found = _read_file(path, notes_dir, allowed)
        for problem in found:
            warn(f"skipping {path.name}: {problem}")
        if not found:
            out[path.stem] = notes
    return out


def _read_file(path: Path, notes_dir: Path, allowed: set[str]) -> tuple[list[dict], list[str]]:
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
        out.extend(_note_problems(prefix, note, allowed))
    return notes, out


def _release(text: str) -> tuple[int, ...]:
    return tuple(int(part) for part in text.split("."))


def _padded(release: tuple[int, ...], width: int) -> tuple[int, ...]:
    return release + (0,) * (width - len(release))


def version_matches(spec: str, version: object) -> bool:
    """Whether a plain release such as "2.9.1" matches a versions value, as PEP 440 would say.

    Only plain N(.N)* releases are matched. Anything else (a pre-, post- or dev release, a
    local version, or no version) returns False, so a note limited to some versions is
    hidden rather than shown for a release it may not apply to.
    """
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
    every target of every platform it lists (each Python, free-threaded included) has a wheel.
    """
    results = package.get("results") or []
    every = {t.get("platform") for t in targets}
    out = []
    for note in notes:
        if "versions" in note and not version_matches(note["versions"], package.get("version")):
            continue
        platforms = every if note["platforms"] == ["all"] else set(note["platforms"])
        listed = [r for t, r in zip(targets, results) if t.get("platform") in platforms]
        if listed and all(r == "wheel" for r in listed):
            continue
        out.append(note)
    return out


def as_json(note: dict) -> dict:
    """A note with the format's fields, for JSON: versions is null when absent, updated is YYYY-MM-DD."""
    return {
        "platforms": list(note["platforms"]),
        "kind": note["kind"],
        "text": note["text"],
        "links": list(note["links"]),
        "versions": note.get("versions"),
        "updated": note["updated"].isoformat(),
    }


def valid_link(link: object) -> bool:
    """An https:// URL with a host and no whitespace."""
    if not isinstance(link, str) or WHITESPACE_RE.search(link):
        return False
    try:
        parts = urlsplit(link)
        return parts.scheme == "https" and bool(parts.hostname)
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


def _note_problems(prefix: str, note: dict, allowed: set[str]) -> list[str]:
    out: list[str] = []
    unknown = sorted(set(note) - NOTE_KEYS)
    if unknown:
        out.append(f"{prefix}: unknown keys: {', '.join(unknown)}")
    missing = [key for key in REQUIRED_KEYS if key not in note]
    if missing:
        out.append(f"{prefix}: missing required keys: {', '.join(missing)}")

    if "platforms" in note:
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

    if "kind" in note and note["kind"] not in KINDS:
        out.append(f"{prefix}: kind must be one of {', '.join(KINDS)}")

    if "text" in note:
        text = note["text"]
        if not isinstance(text, str) or not text.strip():
            out.append(f"{prefix}: text must be a non-empty string")
        elif "\n" in text or "\r" in text:
            out.append(f"{prefix}: text must be a single line")
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
                f"{prefix}: links must be https URLs with a host and no spaces "
                f"(got {bad[0]!r})"
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
