"""Cross-check the scan against pip, and write the agreement rate (#19).

    uv run --no-project --with "wheelgap==0.9.0" --with "pip==26.3" \\
        python scripts/check_pip.py data/coverage.json agreement/agreement.json [--top 200] [--target LABEL ...]

For the top packages of the scan (200 by default) and a few targets (3.13 on linux-x86_64, linux-aarch64,
musl-aarch64 and win-arm64 by default), pip is asked what it would install of the version the scan checked:
`pip install --dry-run --ignore-installed --no-deps --only-binary=:all: NAME==VERSION` with the target's Python,
ABI and every platform tag it accepts. A dry run picks a file exactly as `pip download` does, but fetches only the
wheel's metadata. A wheel means "wheel"; "No matching distribution found" means "no wheel", but only with positive
evidence: pip runs with -vv, and must have fetched the project's page on PyPI without a retry or a failed fetch,
since pip prints the same message when it can't reach the index. Anything else is an error, not an answer. The
scan's answer counts as "wheel" only when it is `wheel`.

The platform tags come from wheelgap's own targets.py, so both sides use wheelgap's tag lists: the installed wheelgap
must be the release that made the scan (scan.yml installs the same one), and the glibc, musl and macOS versions are
the ones the scan recorded for the target. This checks which wheels wheelgap finds in each release (abi3,
per-Python and free-threaded builds, sdists), not the tag lists themselves; wheelgap's weekly accuracy check, which
writes pip's tags out from its AGENTS.md, covers those. pip is asked about NAME==VERSION, which lets it take a
yanked file when nothing else fits (PEP 592), while the scan looked the release up unpinned and skipped yanked
files: a release whose only fitting wheel is yanked shows up as a mismatch, with the file.

The result goes to the output file: the date, the number of checks, how many matched, every mismatch (name, version,
target, wheelgap's answer and pip's), and the same counts for the packages without a pure-Python wheel. A cell the
scan couldn't look up (`unknown`) isn't a check and is counted as `skipped`; one pip couldn't answer is counted as
`errors`. If errors are more than 5% of the cells pip was asked about (as with the scan's own 5% rule), nothing is
written and the exit code is 1, so the previous file stays. Any other failure exits 2. pip runs isolated from
pip.conf and PIP_* variables, without a cache, and writes only its small report, to a temporary folder deleted
after each run.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import tempfile
import traceback
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import unquote, urlsplit

DEFAULT_TOP = 200
DEFAULT_TARGETS = ["3.13 linux-x86_64", "3.13 linux-aarch64", "3.13 musl-aarch64", "3.13 win-arm64"]
INDEX = "https://pypi.org/simple"
# On every pip run: no pip.conf or PIP_* variables, no cache, no self-check, and -vv so the log shows the index pages.
PIP_OPTIONS = ["--isolated", "--disable-pip-version-check", "--no-input", "--no-cache-dir", "-vv"]
PIP_TIMEOUT = 300
MAX_ERROR_SHARE = 0.05  # scripts/check_agreement.py applies the same limit before the file is committed

# (wheel filename or None, error or None) for one pip run.
Answer = tuple[str | None, str | None]


class TooManyErrors(Exception):
    pass


def wheelgap_tags(target: dict) -> list[str]:
    """Every platform tag wheelgap accepts for a scan target, from the installed wheelgap's targets.py."""
    from wheelgap.targets import PLATFORMS, build_targets  # the scan's release, installed by uv run --with

    family = PLATFORMS[target["platform"]][0]
    if family not in ("manylinux", "musllinux", "windows", "macos"):
        raise ValueError(f"pip can't check {target['label']} with `pip download --platform`")
    versions = {key: target[key] for key in ("glibc", "musl", "macos") if key in target}
    return build_targets([target["python"]], [target["platform"]], **versions)[0].platform_tags()


def pip_args(name: str, version: str, target: dict, tags: list[str]) -> list[str]:
    python = target["python"]
    freethreaded = python.endswith("t")
    major, minor = python.removesuffix("t").split(".")
    return [
        *("install", "--dry-run", "--ignore-installed", "--no-deps", "--only-binary=:all:", "--index-url", INDEX),
        *(arg for tag in tags for arg in ("--platform", tag)),
        *("--python-version", f"{major}.{minor}", "--implementation", "cp"),
        *("--abi", f"cp{major}{minor}{'t' if freethreaded else ''}"),
        f"{name}=={version}",
    ]


def _planned(report: Path) -> list[str] | None:
    """The filenames in pip's --report, or None if it wrote none (it doesn't when it fails)."""
    try:
        data = json.loads(report.read_text(encoding="utf-8"))
        return [unquote(urlsplit(item["download_info"]["url"]).path.rsplit("/", 1)[-1]) for item in data["install"]]
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        return None


def _key(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()  # the normalized name pip asks the index for


def classify(name: str, returncode: int, log: str, planned: list[str] | None) -> Answer:
    """pip's answer, trusting "no wheel" only when pip fetched the project's page with no retry or failed fetch."""
    if returncode == 0:
        if planned is not None and len(planned) == 1 and planned[0].endswith(".whl"):
            return planned[0], None
        return None, f"pip succeeded but would install {planned}"
    lines = log.splitlines()
    if trouble := [line.strip() for line in lines if "Retrying (" in line or "Could not fetch URL" in line]:
        return None, f"pip: {trouble[0]}"
    fetched = any(line.startswith(f"Fetched page {INDEX}/{_key(name)}/ ") for line in lines)
    if fetched and any("No matching distribution found" in line for line in lines):
        return None, None
    last = next((line.strip() for line in reversed(lines) if line.strip()), f"exit {returncode}")
    return None, (f"pip: {last}" if fetched else f"pip didn't fetch {INDEX}/{_key(name)}/: {last}")


def run_pip(name: str, args: list[str]) -> Answer:
    """Run pip with `args` (from pip_args) and read its answer from its report and log."""
    env = {key: value for key, value in os.environ.items() if not key.upper().startswith("PIP_")}
    with tempfile.TemporaryDirectory(prefix="tracker-pip-") as tmp:
        report = Path(tmp) / "report.json"
        cmd = [sys.executable, "-m", "pip", *args, *PIP_OPTIONS, "--report", str(report)]
        try:
            proc = subprocess.run(cmd, capture_output=True, text=True, timeout=PIP_TIMEOUT, env=env)
        except subprocess.TimeoutExpired:
            return None, f"pip timed out after {PIP_TIMEOUT} s"
        return classify(name, proc.returncode, f"{proc.stdout}\n{proc.stderr}", _planned(report))


def check_package(
    pkg: dict, columns: list[tuple[int, dict, list[str]]], pip: Callable[[str, list[str]], Answer]
) -> list[dict]:
    """One row per target: the scan's answer and pip's."""
    rows = []
    for index, target, tags in columns:
        ours = pkg["results"][index]
        row = {"name": pkg["name"], "version": pkg["version"], "target": target["label"], "wheelgap": ours}
        if ours == "unknown":
            rows.append(row | {"skipped": True})
            continue
        wheel, error = pip(pkg["name"], pip_args(pkg["name"], pkg["version"], target, tags))
        rows.append(row | ({"error": error} if error else {"pip": "wheel" if wheel else "no wheel", "file": wheel}))
    return rows


def _counts(checks: list[dict]) -> tuple[int, list[dict]]:
    mismatches = [
        {key: r[key] for key in ("name", "version", "target", "wheelgap", "pip", "file")}
        for r in checks
        if (r["wheelgap"] == "wheel") != (r["pip"] == "wheel")
    ]
    return len(checks) - len(mismatches), mismatches


def agreement(
    scan: dict,
    top: int = DEFAULT_TOP,
    labels: list[str] = DEFAULT_TARGETS,
    pip: Callable[[str, list[str]], Answer] = run_pip,
    tags: Callable[[dict], list[str]] = wheelgap_tags,
    workers: int = 8,
) -> dict:
    by_label = {t["label"]: (i, t) for i, t in enumerate(scan["targets"])}
    unknown = [label for label in labels if label not in by_label]
    if unknown:
        raise ValueError(f"targets not in the scan: {', '.join(unknown)}")
    columns = [(*by_label[label], tags(by_label[label][1])) for label in labels]
    sample = sorted(scan["packages"], key=lambda p: p["rank"])[:top]
    looked_up = [p for p in sample if not p.get("error") and p.get("version")]
    with ThreadPoolExecutor(max_workers=workers) as pool:
        rows = [row for found in pool.map(lambda p: check_package(p, columns, pip), looked_up) for row in found]
    checks = [r for r in rows if "pip" in r]
    errors = [r for r in rows if "error" in r]
    if len(errors) > MAX_ERROR_SHARE * (len(checks) + len(errors)):
        examples = "; ".join(f"{r['name']} on {r['target']}: {r['error']}" for r in errors[:3])
        raise TooManyErrors(f"pip couldn't answer {len(errors)} of {len(checks) + len(errors)} checks ({examples})")
    matched, mismatches = _counts(checks)
    compiled = {p["name"] for p in looked_up if not p.get("pure")}
    compiled_checks = [r for r in checks if r["name"] in compiled]
    compiled_matched, _ = _counts(compiled_checks)
    return {
        "schema": 1,
        "date": datetime.now(UTC).strftime("%Y-%m-%d"),
        "scan": scan.get("generated"),
        "wheelgap": scan.get("wheelgap"),
        "pip": pip_version(),
        "top": top,
        "targets": labels,
        "packages": len(looked_up),
        "checks": len(checks),
        "matched": matched,
        "mismatches": mismatches,
        # Packages without a pure-Python wheel, where the answer depends on the platform.
        "compiled": {"packages": len(compiled), "checks": len(compiled_checks), "matched": compiled_matched},
        # Not checks: the scan couldn't look the cell up, or pip couldn't answer.
        "skipped": sum(1 for r in rows if r.get("skipped")) + (len(sample) - len(looked_up)) * len(labels),
        "errors": len(errors),
    }


def pip_version() -> str | None:
    proc = subprocess.run([sys.executable, "-m", "pip", "--version"], capture_output=True, text=True)
    return proc.stdout.split()[1] if proc.returncode == 0 and proc.stdout.startswith("pip ") else None


def write(result: dict, path: Path) -> None:
    """Write the whole file or nothing: a half-written agreement.json would break the site."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    try:
        tmp.write_text(json.dumps(result, indent=1) + "\n", encoding="utf-8")
        tmp.replace(path)
    finally:
        tmp.unlink(missing_ok=True)


def installed_wheelgap() -> str | None:
    try:
        import wheelgap
    except ImportError:
        return None
    return wheelgap.__version__


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=(__doc__ or "").split("\n\n")[0])
    parser.add_argument("coverage", type=Path)
    parser.add_argument("out", type=Path)
    parser.add_argument("--top", type=int, default=DEFAULT_TOP, help=f"packages to check (default {DEFAULT_TOP})")
    parser.add_argument("--target", action="append", help="a target label from the scan (default: the four above)")
    parser.add_argument("--workers", type=int, default=8)
    args = parser.parse_args(argv)
    scan = json.loads(args.coverage.read_text(encoding="utf-8"))
    installed = installed_wheelgap()
    if installed is None:
        parser.error('wheelgap isn\'t installed; run it with uv run --no-project --with "wheelgap==..." --with pip')
    if installed != scan.get("wheelgap"):
        parser.error(f"the scan was made with wheelgap {scan.get('wheelgap')}, but {installed} is installed")
    if pip_version() is None:
        parser.error("pip isn't installed; run it with uv run --no-project --with ... --with pip")
    try:
        result = agreement(scan, args.top, args.target or DEFAULT_TARGETS, workers=args.workers)
    except TooManyErrors as e:
        print(f"agreement: not written, {e}", file=sys.stderr)
        return 1
    write(result, args.out)
    c = result["compiled"]
    print(
        f"agreement: wheelgap matches pip on {result['matched']} of {result['checks']} checks, and on "
        f"{c['matched']} of {c['checks']} for the {c['packages']} packages without a pure-Python wheel "
        f"({len(result['mismatches'])} mismatches, {result['skipped']} skipped, {result['errors']} errors)"
    )
    for m in result["mismatches"]:
        print(
            f"  {m['name']} {m['version']} on {m['target']}: "
            f"wheelgap says {m['wheelgap']}, pip installs {m['file'] or 'no wheel'}"
        )
    return 0


def run(argv: list[str] | None = None) -> int:
    """main(), but a crash exits 2 rather than Python's 1."""
    try:
        return main(argv)
    except Exception:
        traceback.print_exc()
        return 2


if __name__ == "__main__":
    sys.exit(run())
