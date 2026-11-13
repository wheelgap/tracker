"""Check an agreement.json from scripts/check_pip.py before the commit job adds it to data/ (#19).

    python3 scripts/check_agreement.py agreement/agreement.json

It comes from a job that ran third-party code (wheelgap and pip from PyPI), so it's committed and published only if
it has exactly the shape check_pip.py writes: the expected keys and no others, integers where counts go, short
strings, real wheel/no-wheel disagreements on sampled targets, one entry per mismatch, at most 5% of pip's answers
missing, and a size cap. Prints the problems and exits 1 if there are any. Standard library only, so the commit job
runs no third-party code.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

MAX_BYTES = 1_000_000
MAX_ERROR_SHARE = 0.05  # the same limit as check_pip.py
KEYS = {
    *("schema", "date", "scan", "wheelgap", "pip", "top", "targets", "packages", "checks", "matched"),
    *("mismatches", "compiled", "skipped", "errors"),
}
COUNTS = ("top", "packages", "checks", "matched", "skipped", "errors")
MISMATCH_KEYS = {"name", "version", "target", "wheelgap", "pip", "file"}
STATUSES = {"wheel", "source", "missing", "n/a", "bundled"}
_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_VERSION = re.compile(r"^[0-9A-Za-z.+!_-]{1,40}$")


def _count(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


def _text(value: object, limit: int = 200) -> bool:
    return isinstance(value, str) and 0 < len(value) <= limit and value.isprintable()


def problems(data: object) -> list[str]:
    if not isinstance(data, dict):
        return ["not a JSON object"]
    out = []
    if set(data) != KEYS:
        out.append(f"keys {sorted(set(data) ^ KEYS)} missing or unexpected")
        return out
    if data["schema"] != 1:
        out.append(f"schema {data['schema']!r}, expected 1")
    if not (isinstance(data["date"], str) and _DATE.match(data["date"])):
        out.append("date isn't YYYY-MM-DD")
    if not _text(data["scan"], 40):
        out.append("scan isn't a short string")
    for key in ("wheelgap", "pip"):
        if not (isinstance(data[key], str) and _VERSION.match(data[key])):
            out.append(f"{key} isn't a version")
    bad = [key for key in COUNTS if not _count(data[key])]
    if bad:
        return out + [f"not non-negative integers: {', '.join(bad)}"]
    targets = data["targets"]
    if not (isinstance(targets, list) and 0 < len(targets) <= 50 and all(_text(t, 60) for t in targets)):
        out.append("targets isn't a short list of labels")
    if data["matched"] > data["checks"]:
        out.append("more matched than checks")
    if data["errors"] > MAX_ERROR_SHARE * (data["checks"] + data["errors"]):
        out.append(f"{data['errors']} errors in {data['checks'] + data['errors']} attempts")
    compiled = data["compiled"]
    if not (isinstance(compiled, dict) and set(compiled) == {"packages", "checks", "matched"}):
        out.append("compiled isn't {packages, checks, matched}")
    elif not all(_count(v) for v in compiled.values()):
        out.append("compiled counts aren't non-negative integers")
    elif not (compiled["matched"] <= compiled["checks"] <= data["checks"] and compiled["packages"] <= data["packages"]):
        out.append("compiled counts are larger than the totals")
    mismatches = data["mismatches"]
    if not isinstance(mismatches, list) or len(mismatches) != data["checks"] - data["matched"]:
        out.append("mismatches doesn't list checks - matched entries")
    else:
        for m in mismatches:
            if not (
                isinstance(m, dict)
                and set(m) == MISMATCH_KEYS
                and all(_text(m[key]) for key in ("name", "version", "target"))
                and m["wheelgap"] in STATUSES
                and m["pip"] in ("wheel", "no wheel")
                and (m["file"] is None if m["pip"] == "no wheel" else _text(m["file"], 300))
            ):
                out.append(f"bad mismatch entry: {str(m)[:200]}")
                break
            if (m["wheelgap"] == "wheel") == (m["pip"] == "wheel"):
                out.append("mismatch answers agree")
            if isinstance(targets, list) and m["target"] not in targets:
                out.append("mismatch target isn't in targets")
    return out


def check(path: Path) -> list[str]:
    size = path.stat().st_size
    if size > MAX_BYTES:
        return [f"{size} bytes, more than {MAX_BYTES}"]
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (UnicodeDecodeError, ValueError) as e:
        return [f"not valid JSON: {e}"]
    return problems(data)


if __name__ == "__main__":
    found = check(Path(sys.argv[1]))
    for problem in found:
        print(f"agreement.json: {problem}", file=sys.stderr)
    sys.exit(1 if found else 0)
