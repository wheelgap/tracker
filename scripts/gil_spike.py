"""Spike: does importing a package's published free-threaded wheel keep the GIL off? (Not for merging.)

    python scripts/gil_spike.py data/coverage.json --top 1000 --target "3.14t linux-x86_64"

For every top-N compiled package whose current release has a wheel for the target, install exactly that release from
wheels only into a fresh uv venv, import its top-level modules as a user would, and report whether the GIL is still
disabled afterwards, and which module CPython's RuntimeWarning names if it isn't. Prints one JSON line per package.
"""

import argparse
import concurrent.futures as cf
import json
import os
import subprocess
import sys
import tempfile

PROBE = r"""
import importlib, importlib.metadata as md, json, re, sys, warnings
name = sys.argv[1]
dist = md.distribution(name)
tops = set()
exts = []
for f in dist.files or []:
    parts = f.parts
    if not parts or parts[0].endswith((".dist-info", ".data")) or parts[0] == "..":
        continue
    s = str(f)
    if re.search(r"\.(cpython-[^/]+|abi3t?|cp3\d+t?-[^/]+)\.so$|\.pyd$", s):
        exts.append(".".join([*parts[:-1], parts[-1].split(".", 1)[0]]))
    top = parts[0]
    if top.endswith(".py"):
        top = top[:-3]
    elif "." in top:
        top = top.split(".", 1)[0]
    if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", top) and not top.startswith("_") and top not in ("tests", "test", "bin"):
        tops.add(top)
out = {"tops": sorted(tops), "extensions": len(set(exts))}
caught_module = None
errors = []
with warnings.catch_warnings(record=True) as caught:
    warnings.simplefilter("always")
    for t in sorted(tops):
        try:
            importlib.import_module(t)
        except BaseException as e:
            errors.append(f"{t}: {type(e).__name__}: {str(e)[:120]}")
for w in caught:
    m = re.search(r"has been enabled to load module '([A-Za-z0-9_.]{1,100})'", str(w.message))
    if m and caught_module is None:
        caught_module = m.group(1)
out["gil_enabled"] = sys._is_gil_enabled()
out["gil_module"] = caught_module
out["import_errors"] = errors[:3]
print("GILSPIKE " + json.dumps(out))
"""


def one(job):
    name, version, rank, py = job
    with tempfile.TemporaryDirectory() as tmp:
        venv = os.path.join(tmp, "v")
        env = dict(os.environ, UV_NO_CACHE="0")
        try:
            subprocess.run(["uv", "venv", "-q", "--python", py, venv], check=True, capture_output=True, timeout=120)
            pybin = os.path.join(venv, "bin", "python")
            inst = subprocess.run(["uv", "pip", "install", "-q", "--python", pybin, "--only-binary", ":all:",
                                   f"{name}=={version}"], capture_output=True, text=True, timeout=600, env=env)
            if inst.returncode != 0:
                return {"name": name, "version": version, "rank": rank, "install": "failed",
                        "error": (inst.stderr or inst.stdout)[-300:]}
            p = subprocess.run([pybin, "-c", PROBE, name], capture_output=True, text=True, timeout=300, cwd=tmp)
            line = next((l for l in reversed(p.stdout.splitlines()) if l.startswith("GILSPIKE ")), None)
            if line is None:
                return {"name": name, "version": version, "rank": rank, "install": "ok", "probe": "crashed",
                        "error": (p.stderr or "")[-300:]}
            return {"name": name, "version": version, "rank": rank, "install": "ok", **json.loads(line[9:])}
        except Exception as e:
            return {"name": name, "version": version, "rank": rank, "install": "error", "error": str(e)[:200]}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("coverage")
    ap.add_argument("--top", type=int, default=1000)
    ap.add_argument("--target", default="3.14t linux-x86_64")
    ap.add_argument("--python", default=sys.executable)
    args = ap.parse_args()
    d = json.load(open(args.coverage))
    i = [t["label"] for t in d["targets"]].index(args.target)
    jobs = [(p["name"], p["version"], p["rank"], args.python) for p in d["packages"]
            if p["rank"] <= args.top and not p["pure"] and not p.get("error") and p["results"][i] == "wheel"]
    print(f"{len(jobs)} packages", file=sys.stderr)
    with cf.ThreadPoolExecutor(8) as ex:
        for r in ex.map(one, jobs):
            print(json.dumps(r), flush=True)


if __name__ == "__main__":
    main()
