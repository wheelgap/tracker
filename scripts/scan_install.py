"""Add pinned install-tree coverage to a tracker snapshot using wheelgap's released CLI."""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

if __package__:
    from .build_lab import TARGETS as LAB_TARGETS
else:
    from build_lab import TARGETS as LAB_TARGETS

MAIN_TARGETS = {"3.14 linux-x86_64", "3.14 linux-aarch64", "3.14 macos-arm64", "3.14 macos-x86_64"}
NAME = re.compile(r"[a-zA-Z0-9]+(?:[-_.]+[a-zA-Z0-9]+)*")
VERSION = re.compile(r"[0-9][a-zA-Z0-9.!+_-]*")
ORDER = {"wheel": 0, "source": 1, "missing": 2}
TOP = 500
WORKERS = 4
PACKAGE_TIMEOUT = 25
SCAN_TIMEOUT = 20 * 60


def canonical(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def not_checked() -> dict:
    return {"status": "not_checked", "blockers": []}


def coverage(report: dict, name: str, version: str, label: str) -> dict:
    """A target is checked only when the pinned root and every needed result are known."""
    rows = report.get("packages")
    if not isinstance(rows, list):
        return not_checked()
    # A package or target wheelgap left out (an `ignore` in a config file, a skipped requirement) isn't checked.
    if any(report.get(key) for key in ("ignored", "ignored_targets", "skipped")):
        return not_checked()
    root = False
    status = "wheel"
    blockers = []
    for row in rows:
        package = row.get("name")
        release = row.get("version")
        result = row.get("results", {}).get(label)
        if not isinstance(package, str) or not NAME.fullmatch(package):
            return not_checked()
        if result == "n/a":
            continue
        if result not in ORDER:
            return not_checked()
        if canonical(package) == canonical(name):
            if release != version:
                return not_checked()
            root = True
        if ORDER[result] > ORDER[status]:
            status = result
        if result != "wheel":
            via = row.get("via", {}).get(label, [])
            if not isinstance(via, list) or not all(isinstance(step, str) and NAME.fullmatch(step) for step in via):
                return not_checked()
            blockers.append({"name": package, "version": release, "status": result, "via": via})
    if not root:
        return not_checked()
    return {"status": status, "blockers": blockers}


def check(package: dict, targets: list[dict], deadline: float) -> dict:
    results = {target["label"]: not_checked() for target in targets}
    name, version = package["name"], package["version"]
    if package.get("error") or not NAME.fullmatch(name) or not isinstance(version, str) or not VERSION.fullmatch(version):
        return results
    groups: dict[tuple[str, str, str, str], list[dict]] = {}
    for target in targets:
        key = (target["python"], target["glibc"], target["musl"], target["macos"])
        groups.setdefault(key, []).append(target)
    # From an empty folder, so no pyproject.toml's [tool.wheelgap] settings (an `ignore`, say) apply.
    with tempfile.TemporaryDirectory() as empty:
        for (python, glibc, musl, macos), group in groups.items():
            timeout = min(PACKAGE_TIMEOUT, deadline - time.monotonic())
            if timeout <= 0:
                break
            platforms = ",".join(target["platform"] for target in group)
            try:
                process = subprocess.run(
                    [sys.executable, "-m", "wheelgap", f"{name}=={version}", "--deps", "--all", "--explain", "--json",
                     "--no-notes", "-p", python, "-t", platforms, "--glibc", glibc, "--musl", musl, "--macos", macos],
                    capture_output=True, encoding="utf-8", timeout=timeout, check=False, cwd=empty,
                )
                # 1 is a valid wheelgap report with gaps. 2 includes unsatisfied resolution.
                if process.returncode not in (0, 1):
                    continue
                report = json.loads(process.stdout)
                labels = {t["platform"]: t["label"] for t in report["targets"] if t["python"] == python}
                for target in group:
                    label = labels.get(target["platform"])
                    if label is not None:
                        results[target["label"]] = coverage(report, name, version, label)
            except (OSError, subprocess.TimeoutExpired, ValueError, KeyError, TypeError, AttributeError):
                continue
    return results


def scan(path: Path, top: int) -> int:
    data = json.loads(path.read_text(encoding="utf-8"))
    if data.get("schema") != 1:
        raise ValueError("expected coverage schema 1")
    # Lab targets on the main targets' Python (and its free-threaded build) only: another Python, like the lab's 3.15
    # targets, would be one more wheelgap call per package inside the same deadline, so fewer of the top 500 would be
    # checked. Derived from MAIN_TARGETS, so moving them to a new Python moves the lab targets with them.
    main_pythons = {label.split()[0] for label in MAIN_TARGETS}
    lab = {label for label in LAB_TARGETS if label.split()[0].rstrip("t") in main_pythons}
    targets = [t for t in data["targets"] if t["label"] in lab or t["label"] in MAIN_TARGETS]
    if not targets:
        raise ValueError("no install coverage targets in the snapshot")
    packages = sorted(data["packages"], key=lambda package: package["rank"])[:top]
    deadline = time.monotonic() + SCAN_TIMEOUT
    with ThreadPoolExecutor(max_workers=WORKERS) as pool:
        for package, results in zip(packages, pool.map(lambda p: check(p, targets, deadline), packages), strict=True):
            # Nothing checked (the scan's deadline passed, or every lookup failed): leave the key out, as for a
            # package outside the top, rather than a table of "Not checked" rows.
            if any(r["status"] != "not_checked" for r in results.values()):
                package["install"] = results
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, delete=False) as output:
        temporary = Path(output.name)
        output.write(json.dumps(data, separators=(",", ":")) + "\n")
    try:
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)
    checked = sum(any(r["status"] != "not_checked" for r in p.get("install", {}).values()) for p in packages)
    print(f"install coverage: checked {checked} of {len(packages)} packages")
    return len(packages)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("coverage", type=Path)
    parser.add_argument("--top", type=int, choices=range(1, TOP + 1), default=TOP, metavar=f"1–{TOP}")
    args = parser.parse_args()
    scan(args.coverage, args.top)
