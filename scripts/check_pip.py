"""Cross-check the scan against pip, and write the agreement rate (#19).

    uv run --no-project --with "wheelgap==0.9.0" --with pip \\
        python scripts/check_pip.py data/coverage.json data/agreement.json [--top 200] [--target LABEL ...]

For the top packages of the scan (200 by default) and a few targets (3.13 on linux-x86_64, linux-aarch64,
musl-aarch64 and win-arm64 by default), pip is asked whether it would install a wheel of the version the scan
checked: `pip download NAME==VERSION --no-deps --only-binary=:all:` with the target's Python, ABI and every
platform tag it accepts. A download means "wheel"; "No matching distribution found" means "no wheel". The scan's
answer counts as "wheel" only when it is `wheel`.

The platform tags come from wheelgap's own targets.py, in the wheelgap release run with this script (the scan's,
in scan.yml), for the glibc, musl and macOS versions the scan recorded for the target. So this checks everything
wheelgap does with the tags (which wheels a release has, abi3, free-threading, yanked files), not the tag lists
themselves; wheelgap's weekly accuracy check, which writes pip's tags out from its AGENTS.md, covers those.

The result goes to data/agreement.json: the date, the number of checks, how many matched, and every mismatch (name,
version, target, wheelgap's answer and pip's). A cell the scan couldn't look up (`unknown`), and one pip couldn't
answer (a network error, say), isn't a check; they are counted as `skipped` and `errors`. The file is written only
once every check has run, so a failed run leaves the previous one in place. pip's cache is off and each package's
downloads go to one temporary folder that is emptied after every download and deleted after the package.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path

DEFAULT_TOP = 200
DEFAULT_TARGETS = ["3.13 linux-x86_64", "3.13 linux-aarch64", "3.13 musl-aarch64", "3.13 win-arm64"]
INDEX = "https://pypi.org/simple"

# (wheel filename or None, error or None) for one `pip download`.
Answer = tuple[str | None, str | None]


def wheelgap_tags(target: dict) -> list[str]:
    """Every platform tag wheelgap accepts for a scan target, from the installed wheelgap's targets.py."""
    from wheelgap.targets import PLATFORMS, build_targets  # the scan's release, installed by uv run --with

    family = PLATFORMS[target["platform"]][0]
    if family not in ("manylinux", "musllinux", "windows", "macos"):
        raise ValueError(f"pip can't check {target['label']} with `pip download --platform`")
    versions = {key: target[key] for key in ("glibc", "musl", "macos") if key in target}
    return build_targets([target["python"]], [target["platform"]], **versions)[0].platform_tags()


def pip_args(name: str, version: str, target: dict, tags: list[str], dest: str) -> list[str]:
    python = target["python"]
    freethreaded = python.endswith("t")
    major, minor = python.removesuffix("t").split(".")
    return [
        *("download", "--no-deps", "--only-binary=:all:", "--index-url", INDEX, "-d", dest),
        *(arg for tag in tags for arg in ("--platform", tag)),
        *("--python-version", f"{major}.{minor}", "--implementation", "cp"),
        *("--abi", f"cp{major}{minor}{'t' if freethreaded else ''}"),
        f"{name}=={version}",
    ]


def run_pip(args: list[str]) -> Answer:
    dest = Path(args[args.index("-d") + 1])
    # No cache (nothing is kept) and no pip.conf (it could point pip at another index).
    env = os.environ | {"PIP_CONFIG_FILE": os.devnull, "PIP_DISABLE_PIP_VERSION_CHECK": "1", "PIP_NO_INPUT": "1"}
    cmd = [sys.executable, "-m", "pip", *args, "--no-cache-dir", "--progress-bar", "off"]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=300, env=env)
    except subprocess.TimeoutExpired:
        return None, "pip timed out"
    wheels = sorted(p.name for p in dest.glob("*.whl"))
    if proc.returncode == 0 and len(wheels) == 1:
        return wheels[0], None
    if proc.returncode != 0 and "No matching distribution found" in proc.stderr:
        return None, None
    last = (proc.stderr.strip() or proc.stdout.strip() or f"exit {proc.returncode}").splitlines()[-1]
    return None, f"pip: {last}"


def check_package(
    pkg: dict, columns: list[tuple[int, dict, list[str]]], pip: Callable[[list[str]], Answer]
) -> list[dict]:
    """One row per target: the scan's answer and pip's, in one temporary folder emptied after each download."""
    rows = []
    tmp = tempfile.mkdtemp(prefix="tracker-pip-")
    try:
        for index, target, tags in columns:
            ours = pkg["results"][index]
            row = {"name": pkg["name"], "version": pkg["version"], "target": target["label"], "wheelgap": ours}
            if ours == "unknown":
                rows.append(row | {"skipped": True})
                continue
            wheel, error = pip(pip_args(pkg["name"], pkg["version"], target, tags, tmp))
            for leftover in Path(tmp).iterdir():
                leftover.unlink()
            rows.append(row | ({"error": error} if error else {"pip": "wheel" if wheel else "no wheel", "file": wheel}))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    return rows


def agreement(
    scan: dict,
    top: int = DEFAULT_TOP,
    labels: list[str] = DEFAULT_TARGETS,
    pip: Callable[[list[str]], Answer] = run_pip,
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
    mismatches = [
        {key: r[key] for key in ("name", "version", "target", "wheelgap", "pip", "file")}
        for r in checks
        if (r["wheelgap"] == "wheel") != (r["pip"] == "wheel")
    ]
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
        "matched": len(checks) - len(mismatches),
        "mismatches": mismatches,
        # Not checks: the scan couldn't look the cell up, or pip couldn't answer.
        "skipped": sum(1 for r in rows if r.get("skipped")) + (len(sample) - len(looked_up)) * len(labels),
        "errors": sum(1 for r in rows if "error" in r),
    }


def pip_version() -> str | None:
    proc = subprocess.run([sys.executable, "-m", "pip", "--version"], capture_output=True, text=True)
    return proc.stdout.split()[1] if proc.returncode == 0 and proc.stdout.startswith("pip ") else None


def write(result: dict, path: Path) -> None:
    """Write the whole file or nothing: a half-written agreement.json would break the site."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(result, indent=1) + "\n", encoding="utf-8")
    tmp.replace(path)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("coverage", type=Path)
    parser.add_argument("out", type=Path)
    parser.add_argument("--top", type=int, default=DEFAULT_TOP, help=f"packages to check (default {DEFAULT_TOP})")
    parser.add_argument("--target", action="append", help="a target label from the scan (default: the four above)")
    parser.add_argument("--workers", type=int, default=8)
    args = parser.parse_args(argv)
    scan = json.loads(args.coverage.read_text(encoding="utf-8"))
    try:
        import wheelgap

        if wheelgap.__version__ != scan.get("wheelgap"):
            print(f"note: the scan used wheelgap {scan.get('wheelgap')}; tags come from {wheelgap.__version__}")
    except ImportError:
        parser.error('wheelgap isn\'t installed; run it with uv run --no-project --with "wheelgap==..." --with pip')
    if pip_version() is None:
        parser.error("pip isn't installed; run it with uv run --no-project --with ... --with pip")
    result = agreement(scan, args.top, args.target or DEFAULT_TARGETS, workers=args.workers)
    write(result, args.out)
    print(
        f"agreement: wheelgap matches pip on {result['matched']} of {result['checks']} checks "
        f"({len(result['mismatches'])} mismatches, {result['skipped']} skipped, {result['errors']} errors)"
    )
    for m in result["mismatches"]:
        print(
            f"  {m['name']} {m['version']} on {m['target']}: "
            f"wheelgap says {m['wheelgap']}, pip downloads {m['file'] or 'no wheel'}"
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
