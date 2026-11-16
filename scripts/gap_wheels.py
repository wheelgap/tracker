"""Gap wheels: verify which of the build lab's clean builds are reproducible and install, and publish the Windows ones.

    python3 scripts/gap_wheels.py plan data/builds.json [--max N] [--only a,b] --github-output FILE
    python scripts/gap_wheels.py build --name NAME --version VERSION --target TARGET --attempt 1|2 --runner R --out FILE
    python3 scripts/gap_wheels.py dispatch --plan PLAN_JSON --batch RUNID-ATTEMPT
    python3 scripts/gap_wheels.py collect --plan PLAN_JSON --batch B --repo R --sha SHA --since TIME --results DIR
    python3 scripts/gap_wheels.py merge RESULTS_DIR --plan PLAN_JSON --out MANIFEST [--summary FILE]
    python3 scripts/gap_wheels.py stage --manifest MANIFEST --staged DIR --repo R [--github-output FILE]
    python scripts/gap_wheels.py install-test --staged DIR --target TARGET --out FILE
    python3 scripts/gap_wheels.py publish --staged-json JSON --files DIR --tested DIR --index data/gap-wheels.json \
        --release-dir DIR

A *pair* is a release and lab target the build lab built cleanly (data/builds.json: built, imported, a compiled
extension module, and on free-threaded Python the GIL kept off), so PyPI has no wheel for it there. Each pair is built
twice, in separate jobs on separate runners:

- from the sdist alone (`pip download --no-binary NAME`: build tools still come as wheels, as pip installs them),
- unpacked into a fixed folder, so the build path baked into a binary is the same both times,
- with SOURCE_DATE_EPOCH fixed, and on Windows with `-Brepro` for MSVC's compiler and linker and `codegen-units=1`
  for Rust (spelt `-Brepro`, since Git Bash rewrites a leading `/` into a Windows path).

A wheel is *verified* when both builds give byte-identical wheels from the sdist PyPI publishes (merge checks its
sha256 against PyPI's), and pip installs that wheel file, with `--only-binary :all:` (compiling nothing), into a fresh
virtual environment on the first build's runner. The merge job writes a manifest of the verified wheels: filename,
sha256, the sdist's name and sha256, and the run that built them.

Build jobs run each package's own build code with the runner's admin rights, so they're untrusted: they get a
read-only token and no secrets, and merge accepts a result only if it is exactly what `build` writes, for the pair
and attempt the plan gave that job. Two matching builds catch a build that isn't reproducible; they don't stop a
malicious package from shipping a malicious wheel *of itself* (its sdist would do the same on install).

What they must not be able to do is speak for another package. Build code with admin rights can read its job's
artifact token, and that token can write any artifact name in its own workflow run. So each build runs in a
workflow run of its own (`dispatch` starts one run of gap-wheel-build.yml per build, titled with its batch, pair and
attempt), and `collect` takes each result only from the one run with that exact title, started from main at the
coordinator's commit by workflow_dispatch after the batch began; two runs with the same title mean neither counts.
A build can then forge nothing but its own result. Each build run also keeps its wheel as its own `wheel` artifact;
stage takes a file from it only if its sha256 is the one both builds reported, so a build can't substitute bytes
either. See the stage/install-test/publish section below for what is made public, and how.
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import io
import json
import os
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import time
import urllib.request
import zipfile
import zlib
from collections.abc import Callable
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import build_lab  # TARGETS, interpreter_problem, the name and version rules

SCHEMA = 1
SDIST = re.compile(r"[A-Za-z0-9._-]{1,120}\.(?:tar\.gz|zip)", re.ASCII)
WHEEL = re.compile(r"[A-Za-z0-9._+!-]{1,200}\.whl", re.ASCII)
SHA256 = re.compile(r"[0-9a-f]{64}", re.ASCII)
RESULT_KEYS = frozenset(
    {"schema", "name", "version", "target", "attempt", "runner", "sdist", "sdist_sha256", "wheel", "sha256", "installs"}
)
MAX_RESULT_BYTES = 10_000
BUILD_TIMEOUT = build_lab.BUILD_TIMEOUT
INSTALL_TIMEOUT = build_lab.INSTALL_TIMEOUT


def clean_pairs(builds: dict) -> list[dict]:
    """The lab's clean builds: (name, version, target) with a compiled module that imported, GIL off where it applies."""
    out = []
    for r in builds.get("results", []):
        if (
            r.get("outcome") == "built"
            and r.get("import") == "ok"
            and isinstance(r.get("extensions"), int)
            and r["extensions"] > 0
            and r.get("gil") != "re-enabled"
            and r.get("target") in build_lab.TARGETS
            and build_lab.NAME.fullmatch(str(r.get("name")))
            and build_lab.VERSION.fullmatch(str(r.get("version")))
        ):
            out.append({"name": r["name"], "version": r["version"], "target": r["target"]})
    return sorted(out, key=lambda p: (build_lab.canonical(p["name"]), p["target"]))


def plan(builds: dict, limit: int, only: set[str] | None = None, start: int = 0) -> list[dict]:
    """Two build jobs per pair, at most `limit` pairs, each with the runner (and Docker image) of its target. `start`
    rotates the (sorted) pairs first, so a weekly batch capped at `limit` reaches the end of the list too."""
    jobs = []
    pairs = [p for p in clean_pairs(builds) if only is None or build_lab.canonical(p["name"]) in only]
    if pairs:
        offset = start % len(pairs)
        pairs = pairs[offset:] + pairs[:offset]
    pairs = pairs[:limit]
    for i, pair in enumerate(pairs):
        spec = build_lab.TARGETS[pair["target"]]
        for attempt in (1, 2):
            job = {**pair, "id": str(i), "attempt": attempt, "runner": spec["runner"], "python": spec["python"]}
            if "image" in spec:
                job["image"] = spec["image"]
            jobs.append(job)
    return jobs


def cmd_plan(args: argparse.Namespace) -> int:
    builds = json.loads(Path(args.builds).read_text(encoding="utf-8"))
    only = {build_lab.canonical(n.strip()) for n in args.only.split(",") if n.strip()} if args.only else None
    # --rotate: start each week `--max` pairs further on, so a capped weekly batch covers every pair in turn. Weeks are
    # counted without resetting (an ISO week number jumps back to 1 each January).
    start = dt.date.today().toordinal() // 7 * args.max if args.rotate else 0
    jobs = plan(builds, args.max, only, start)
    hosted = [j for j in jobs if "image" not in j]
    docker = [j for j in jobs if "image" in j]
    print(f"{len(jobs) // 2} pairs, {len(jobs)} builds", file=sys.stderr)
    if args.github_output:
        with open(args.github_output, "a", encoding="utf-8") as f:
            f.write(f"hosted={json.dumps(hosted)}\nhosted_count={len(hosted)}\n")
            f.write(f"docker={json.dumps(docker)}\ndocker_count={len(docker)}\n")
            f.write(f"all={json.dumps(jobs)}\n")
    else:
        print(json.dumps(jobs, indent=1))
    return 0


# --- build (inside the untrusted job) ------------------------------------------------------------------------------


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def unpack(sdist: Path, dest: Path) -> Path:
    """Unpack an sdist into `dest` (emptied first) and return its one top-level folder. tarfile's `data` filter refuses
    absolute paths, `..`, links out of the folder and device files; a zip member that would land outside is refused."""
    shutil.rmtree(dest, ignore_errors=True)
    dest.mkdir(parents=True)
    if sdist.name.endswith(".zip"):
        with zipfile.ZipFile(sdist) as z:
            for member in z.namelist():
                if not (dest / member).resolve().is_relative_to(dest.resolve()):
                    raise ValueError(f"{member}: outside the sdist's folder")
            z.extractall(dest)
    else:
        with tarfile.open(sdist) as t:
            t.extractall(dest, filter="data")
    tops = list(dest.iterdir())
    if len(tops) != 1 or not tops[0].is_dir():
        raise ValueError("the sdist doesn't unpack into one folder")
    return tops[0]


def build_env() -> dict[str, str]:
    env = dict(os.environ, PIP_DISABLE_PIP_VERSION_CHECK="1", PIP_NO_INPUT="1", SOURCE_DATE_EPOCH="1700000000",
               PYTHONHASHSEED="0")  # fmt: skip
    if os.name == "nt":
        env.update(CL="-Brepro", LINK="-Brepro", RUSTFLAGS="-C link-arg=-Brepro -C codegen-units=1")
    return env


def wheel_members(wheel: Path) -> dict[str, str]:
    """Each file in the wheel, by name: its sha256 (streamed), or {} if the wheel can't be read. For saying *which*
    files differ when two builds don't agree; a wheel that isn't a readable zip still gets its result."""
    try:
        with zipfile.ZipFile(wheel) as z:
            return {
                i.filename: hashlib.file_digest(z.open(i), "sha256").hexdigest() for i in z.infolist() if not i.is_dir()
            }
    except Exception as e:  # noqa: BLE001  (diagnostics only: never cost the build its result)
        print(f"gap_wheels: couldn't list the wheel's files: {e}", file=sys.stderr)
        return {}


def run_build(
    name: str, version: str, attempt: int, work: Path, members: dict[str, str] | None = None, keep: Path | None = None
) -> dict:
    """Download the sdist, build the wheel from a fixed folder, hash it, and (first attempt) install it from a local
    folder into a fresh venv with nothing compiled. Fields left None mean that step didn't happen."""
    fields: dict = {"sdist": None, "sdist_sha256": None, "wheel": None, "sha256": None, "installs": None}
    env = dict(os.environ, PIP_DISABLE_PIP_VERSION_CHECK="1", PIP_NO_INPUT="1")
    sdists = work / "sdist"
    pip = [sys.executable, "-m", "pip"]
    subprocess.run([*pip, "download", "--no-deps", "--no-binary", name, "--no-cache-dir", "-d", str(sdists),
                    f"{name}=={version}"], check=False, env=env, timeout=BUILD_TIMEOUT)  # fmt: skip
    found = [p for p in sdists.glob("*") if SDIST.fullmatch(p.name)] if sdists.is_dir() else []
    if len(found) != 1:
        print(f"gap_wheels: expected one sdist, found {len(found)}", file=sys.stderr)
        return fields
    fields.update(sdist=found[0].name, sdist_sha256=sha256(found[0]))
    fixed = Path("C:/gw" if os.name == "nt" else "/tmp/gw")
    try:
        src = unpack(found[0], fixed)
    except (ValueError, tarfile.TarError, zipfile.BadZipFile, OSError) as e:
        print(f"gap_wheels: can't unpack the sdist: {e}", file=sys.stderr)
        return fields
    out = work / "out"
    try:
        p = subprocess.run([*pip, "wheel", "--no-deps", "--no-cache-dir", "-w", str(out), str(src)], check=False,
                           env=build_env(), capture_output=True, text=True, errors="replace",
                           timeout=BUILD_TIMEOUT)  # fmt: skip
        print((p.stdout + p.stderr)[-6000:])
    except subprocess.TimeoutExpired:
        print("gap_wheels: the build timed out", file=sys.stderr)
        return fields
    wheels = sorted(out.glob("*.whl")) if out.is_dir() else []
    if len(wheels) != 1 or not WHEEL.fullmatch(wheels[0].name):
        print(f"gap_wheels: expected one wheel, found {len(wheels)}", file=sys.stderr)
        return fields
    fields.update(wheel=wheels[0].name, sha256=sha256(wheels[0]))
    if keep is not None:  # uploaded as this run's `wheel` artifact; stage takes it only if its sha256 is verified
        keep.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(wheels[0], keep / wheels[0].name)
    if members is not None:
        members.update(wheel_members(wheels[0]))
    if attempt == 1:
        venv = work / "fresh"
        subprocess.run([sys.executable, "-m", "venv", str(venv)], check=True, timeout=INSTALL_TIMEOUT)
        py = venv / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
        try:
            # The file itself, not NAME==VERSION: PyPI may have a wheel of its own by now, which pip could prefer.
            i = subprocess.run([str(py), "-m", "pip", "install", "--only-binary", ":all:", "--no-cache-dir",
                                str(wheels[0])], check=False, env=env,
                               capture_output=True, text=True, errors="replace",
                               timeout=INSTALL_TIMEOUT)  # fmt: skip
            print((i.stdout + i.stderr)[-3000:])
            fields["installs"] = i.returncode == 0
        except subprocess.TimeoutExpired:
            fields["installs"] = False
    return fields


def cmd_build(args: argparse.Namespace) -> int:
    target = args.target
    if (
        target not in build_lab.TARGETS
        or not build_lab.NAME.fullmatch(args.name)
        or not build_lab.VERSION.fullmatch(args.version)
        or args.attempt not in (1, 2)
    ):
        print(f"gap_wheels: not a gap-wheels job: {args.name} {args.version} {target}", file=sys.stderr)
        return 2
    if (why := build_lab.interpreter_problem(target)) is not None:
        print(f"gap_wheels: this Python isn't {target}'s: {why}", file=sys.stderr)
        return 3
    members: dict[str, str] = {}
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        keep = Path(args.wheel_dir) if args.wheel_dir else None
        fields = run_build(args.name, args.version, args.attempt, Path(tmp), members, keep)
    result = {"schema": SCHEMA, "name": args.name, "version": args.version, "target": target,
              "attempt": args.attempt, "runner": args.runner, **fields}  # fmt: skip
    Path(args.out).write_text(json.dumps(result) + "\n", encoding="utf-8")
    # Beside the result, only to say which files differ when two builds don't agree; never part of a verdict.
    text = json.dumps(members, sort_keys=True) + "\n"
    if len(members) > MAX_MEMBERS or len(text) > MAX_MEMBERS_BYTES:
        text = "{}\n"  # too big to list: no diff listing, but the result itself must still arrive
    Path(args.out).with_name("members.json").write_text(text, encoding="utf-8")
    print(json.dumps(result, indent=1))
    return 0


# --- merge (trusted) -----------------------------------------------------------------------------------------------


def wheel_problem(wheel: object, job: dict) -> str | None:
    """Why a result's wheel filename can't be this pair's (None if it can): the project, version and platform tag."""
    if not isinstance(wheel, str) or not WHEEL.fullmatch(wheel):
        return "wheel"
    parts = wheel[: -len(".whl")].split("-")
    if len(parts) not in (5, 6):
        return "wheel name"
    name, version, platform = parts[0], parts[1], parts[-1]
    if build_lab.canonical(name) != build_lab.canonical(job["name"]):
        return "wheel is for another project"
    if version.lower() not in {job["version"].lower(), job["version"].lower().replace("-", "_")}:
        return "wheel is for another version"
    tag = build_lab.TARGETS[job["target"]]["wheel"]
    if not any(re.fullmatch(tag, p, re.ASCII) for p in platform.split(".")):  # a compressed tag set: one must match
        return "wheel isn't for the target"
    return None


def problems(result: object, job: dict) -> list[str]:
    """What's wrong with an untrusted result for `job` (empty when it's exactly what `build` writes for that job)."""
    if not isinstance(result, dict):
        return ["not an object"]
    if set(result) != RESULT_KEYS:
        return ["keys"]
    out = []
    if result["schema"] != SCHEMA:
        out.append("schema")
    for key in ("name", "version", "target", "attempt", "runner"):
        if result[key] != job[key]:
            out.append(f"{key} isn't the planned one")
    if result["sdist"] is not None and not (isinstance(result["sdist"], str) and SDIST.fullmatch(result["sdist"])):
        out.append("sdist")
    for key in ("sdist_sha256", "sha256"):
        if result[key] is not None and not (isinstance(result[key], str) and SHA256.fullmatch(result[key])):
            out.append(key)
    if result["wheel"] is not None and (why := wheel_problem(result["wheel"], job)):
        out.append(why)
    if (result["wheel"] is None) != (result["sha256"] is None):
        out.append("wheel and sha256 go together")
    if result["wheel"] is not None and (result["sdist"] is None or result["sdist_sha256"] is None):
        out.append("a wheel needs its sdist and sdist_sha256")
    if result["installs"] is not None and not isinstance(result["installs"], bool):
        out.append("installs")
    if job["attempt"] == 2 and result["installs"] is not None:
        out.append("only the first build installs")
    return out


MEMBER = re.compile(r"[A-Za-z0-9._+/-]{1,200}", re.ASCII)
MAX_MEMBERS = 10_000
MAX_MEMBERS_BYTES = 2_000_000


def differing(first: object, second: object, shown: int = 5) -> str:
    """", in NAME, NAME (+N more)": the wheel files whose sha256 differs between two untrusted members.json, or ""
    when either is missing or malformed. Diagnostic text only: it never changes a verdict."""
    def ok(m: object) -> bool:
        return (isinstance(m, dict) and len(m) <= MAX_MEMBERS
                and all(isinstance(k, str) and MEMBER.fullmatch(k) and isinstance(v, str) and SHA256.fullmatch(v)
                        for k, v in m.items()))  # fmt: skip

    if not (ok(first) and ok(second)):
        return ""
    names = sorted(k for k in first.keys() | second.keys() if first.get(k) != second.get(k))
    if not names:
        return ""
    more = f" (+{len(names) - shown} more)" if len(names) > shown else ""
    return ", in " + ", ".join(f"`{n}`" for n in names[:shown]) + more


def verify(
    jobs: list[dict],
    results: dict[tuple[str, int], object],
    published: Callable[[str, str], dict[str, str] | None],
    members: dict[tuple[str, int], object] | None = None,
) -> tuple[list[dict], list[dict]]:
    """(verified wheels, every pair with its verdict) from the plan's jobs, the results by (id, attempt), and a lookup
    of PyPI's sdists for a release, {filename: sha256} or None if PyPI couldn't be asked. It's only asked about pairs
    whose two builds agree."""
    members = members or {}
    verified, report = [], []
    by_id: dict[str, dict[int, dict]] = {}
    for job in jobs:
        by_id.setdefault(job["id"], {})[job["attempt"]] = job
    for pid, attempts in sorted(by_id.items(), key=lambda kv: int(kv[0])):
        job = attempts[1]
        row = {"name": job["name"], "version": job["version"], "target": job["target"]}
        got = {a: results.get((pid, a)) for a in (1, 2)}
        bad = [f"build {a}: {p}" for a in (1, 2) if got[a] is not None for p in problems(got[a], attempts[a])]
        first, second = got[1], got[2]
        if bad:
            verdict = "rejected: " + "; ".join(bad)
        elif first is None or second is None:
            verdict = "a build has no result"
        elif first["sha256"] is None or second["sha256"] is None:
            verdict = "a build made no wheel"
        elif (first["wheel"], first["sha256"], first["sdist_sha256"]) != (
            second["wheel"],
            second["sha256"],
            second["sdist_sha256"],
        ):
            verdict = "the two builds differ" + differing(members.get((pid, 1)), members.get((pid, 2)))
        elif (sdists := published(job["name"], job["version"])) is None:
            verdict = "couldn't check the sdist against PyPI"
        elif sdists.get(first["sdist"]) != first["sdist_sha256"]:
            verdict = "the sdist isn't the one PyPI publishes"
        elif first["installs"] is not True:
            verdict = "pip couldn't install it without compiling"
        else:
            verdict = "verified"
            verified.append({**row, "wheel": first["wheel"], "sha256": first["sha256"], "sdist": first["sdist"],
                             "sdist_sha256": first["sdist_sha256"]})  # fmt: skip
        report.append({**row, "verdict": verdict})
    return verified, report


def pypi_sdists(name: str, version: str) -> dict[str, str] | None:
    """The release's sdists on PyPI, {filename: sha256}, or None if PyPI couldn't be asked."""
    url = f"https://pypi.org/pypi/{name}/{version}/json"
    try:
        with urllib.request.urlopen(url, timeout=10) as r:
            data = json.load(r)
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict) or not isinstance(data.get("urls"), list):
        return None
    return {
        u["filename"]: u.get("digests", {}).get("sha256")
        for u in data["urls"]
        if isinstance(u, dict) and u.get("packagetype") == "sdist" and isinstance(u.get("filename"), str)
    }


def read_result(path: Path) -> object:
    if path.stat().st_size > MAX_RESULT_BYTES:
        return "too big"
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (UnicodeDecodeError, ValueError):
        return "not JSON"


def cmd_merge(args: argparse.Namespace) -> int:
    jobs = json.loads(Path(args.plan).read_text(encoding="utf-8"))
    results: dict[tuple[str, int], object] = {}
    members: dict[tuple[str, int], object] = {}
    for job in jobs:
        path = Path(args.results) / f"result-{job['id']}-{job['attempt']}" / "result.json"
        if path.is_file():
            results[(job["id"], job["attempt"])] = read_result(path)
        mpath = path.with_name("members.json")
        if mpath.is_file() and mpath.stat().st_size <= MAX_MEMBERS_BYTES:
            try:
                members[(job["id"], job["attempt"])] = json.loads(mpath.read_text(encoding="utf-8"))
            except Exception:  # noqa: BLE001,S110  (untrusted diagnostics, e.g. nesting deep enough for RecursionError)
                pass
    verified, report = verify(jobs, results, pypi_sdists, members)
    runs_file = Path(args.results) / "runs.json"
    if runs_file.is_file():  # written by collect, which is trusted
        urls = json.loads(runs_file.read_text(encoding="utf-8"))
        ids = {(j["name"], j["version"], j["target"]): j["id"] for j in jobs}
        for wheel in verified:
            pid = ids[(wheel["name"], wheel["version"], wheel["target"])]
            wheel["builds"] = [urls.get(f"{pid}-1"), urls.get(f"{pid}-2")]
    run = ""
    if all(os.environ.get(k) for k in ("GITHUB_SERVER_URL", "GITHUB_REPOSITORY", "GITHUB_RUN_ID")):
        run = f"{os.environ['GITHUB_SERVER_URL']}/{os.environ['GITHUB_REPOSITORY']}/actions/runs/{os.environ['GITHUB_RUN_ID']}"
    date = dt.datetime.now(dt.UTC).date().isoformat()
    manifest = {"schema": SCHEMA, "date": date, "run": run, "wheels": verified}
    Path(args.out).write_text(json.dumps(manifest, indent=1) + "\n", encoding="utf-8")
    head = f"**{len(verified)} verified** of {len(report)} pairs (byte-identical twice, and pip installs it with nothing compiled)"
    lines = [head, "", "| package | version | target | verdict |", "|---|---|---|---|"]
    lines += [f"| {r['name']} | {r['version']} | {r['target']} | {r['verdict']} |" for r in report]
    text = "\n".join(lines) + "\n"
    print(text)
    if args.summary:
        with open(args.summary, "a", encoding="utf-8") as f:
            f.write(text)
    return 0


# --- dispatch and collect: one workflow run per build (trusted) ------------------------------------------------------

BUILD_WORKFLOW = "gap-wheel-build.yml"
BATCH = re.compile(r"[0-9]{1,20}-[0-9]{1,4}", re.ASCII)
SINCE = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z", re.ASCII)  # as the API writes created_at
REPO = re.compile(r"[A-Za-z0-9-]{1,39}/[A-Za-z0-9._-]{1,100}", re.ASCII)
SHA = re.compile(r"[0-9a-f]{40}", re.ASCII)
MAX_ARTIFACT_BYTES = 3_000_000


def title(batch: str, job: dict) -> str:
    """The build run's title; gap-wheel-build.yml's run-name gives the same string from its inputs."""
    return f"gap-wheel {batch} {job['name']} {job['version']} {job['target']} build {job['attempt']}"


def gh(args: list[str]) -> bytes:
    return subprocess.run(["gh", *args], check=True, capture_output=True, timeout=120).stdout


def cmd_dispatch(args: argparse.Namespace) -> int:
    check_args(args)
    jobs = json.loads(Path(args.plan).read_text(encoding="utf-8"))
    for n, job in enumerate(jobs):
        # At most --parallel of this batch's builds at once, so the batch doesn't hold every runner the account has.
        while sum(r.get("status") != "completed" for r in this_batch(retry(lambda: list_runs(args.repo, args.since)),
                                                                      args.batch)) >= args.parallel:  # fmt: skip
            time.sleep(60)
        # A commit on main mid-batch would start the rest at another commit, which collect rightly refuses: stop loudly.
        # Checked last thing before starting the build, so the window is seconds, not the wait above.
        if (tip := retry(lambda: gh(["api", f"repos/{args.repo}/commits/main", "--jq", ".sha"])).decode().strip()) != args.sha:
            raise SystemExit(f"gap_wheels: main moved to {tip} after {n} of {len(jobs)} builds started; run the batch again")
        spec = build_lab.TARGETS[job["target"]]  # the plan's own values, checked again: they become a run's inputs
        if not (build_lab.NAME.fullmatch(job["name"]) and build_lab.VERSION.fullmatch(job["version"])):
            raise SystemExit(f"gap_wheels: not a plan job: {job!r}")
        fields = {"batch": args.batch, "name": job["name"], "version": job["version"], "target": job["target"],
                  "attempt": str(job["attempt"]), "runner": spec["runner"], "python": spec["python"],
                  "image": spec.get("image", "")}  # fmt: skip
        command = ["workflow", "run", BUILD_WORKFLOW, "--repo", args.repo, "--ref", "main",
                   *[x for k, v in fields.items() for x in ("-f", f"{k}={v}")]]  # fmt: skip
        start(command, title(args.batch, job), args)
        print(f"started {title(args.batch, job)}", flush=True)
        time.sleep(2)  # well under GitHub's limit on write requests
    return 0


def start(command: list[str], name: str, args: argparse.Namespace, attempts: int = 4) -> None:
    """Start one build run. Starting is a POST, so a failed attempt may still have made the run: look for it before
    trying again, since a second run with the same title would make collect refuse both."""
    for attempt in range(attempts):
        try:
            gh(command)
            return
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
            time.sleep(30 * (attempt + 1))
            if any(r.get("display_title") == name for r in retry(lambda: list_runs(args.repo, args.since))):
                return
    raise SystemExit(f"gap_wheels: couldn't start {name}")


def retry[T](call: Callable[[], T], attempts: int = 4) -> T:
    """`call`, retried with backoff on a failed gh command (GitHub's API has the odd 5xx and secondary rate limit)."""
    for attempt in range(attempts):
        try:
            return call()
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
            if attempt == attempts - 1:
                raise
            time.sleep(30 * (attempt + 1))
    raise AssertionError("unreachable")


def this_batch(runs: list[dict], batch: str) -> list[dict]:
    prefix = f"gap-wheel {batch} "
    return [r for r in runs if isinstance(r, dict) and str(r.get("display_title", "")).startswith(prefix)]


def check_args(args: argparse.Namespace) -> None:
    if not (BATCH.fullmatch(args.batch) and SINCE.fullmatch(args.since) and REPO.fullmatch(args.repo)
            and SHA.fullmatch(args.sha)):  # fmt: skip
        raise SystemExit("gap_wheels: bad --batch, --since, --repo or --sha")


def pick_runs(jobs: list[dict], runs: list[dict], batch: str, sha: str, since: str) -> dict[tuple[str, int], dict | str]:
    """By (id, attempt): the one build run that can speak for that job, or why there isn't one."""
    by_title: dict[str, list[dict]] = {}
    for run in runs:
        if isinstance(run, dict) and isinstance(run.get("display_title"), str):
            by_title.setdefault(run["display_title"], []).append(run)
    out: dict[tuple[str, int], dict | str] = {}
    for job in jobs:
        found = by_title.get(title(batch, job), [])
        if not found:
            out[(job["id"], job["attempt"])] = "no build run"
        elif len(found) > 1:
            out[(job["id"], job["attempt"])] = "more than one build run with its title"
        elif not (
            found[0].get("event") == "workflow_dispatch"
            and found[0].get("head_branch") == "main"
            and found[0].get("head_sha") == sha
            and found[0].get("path") == f".github/workflows/{BUILD_WORKFLOW}"
            and str(found[0].get("created_at", "")) >= since
        ):
            out[(job["id"], job["attempt"])] = "the build run isn't one this batch started"
        elif found[0].get("status") != "completed":
            out[(job["id"], job["attempt"])] = "the build run hasn't finished"
        else:
            out[(job["id"], job["attempt"])] = found[0]
    return out


def list_runs(repo: str, since: str) -> list[dict]:
    query = f"event=workflow_dispatch&branch=main&created=%3E%3D{since}&per_page=100"
    pages = gh(["api", "--paginate", "--slurp", f"repos/{repo}/actions/workflows/{BUILD_WORKFLOW}/runs?{query}"])
    return [run for page in json.loads(pages) for run in page.get("workflow_runs", [])]


def fetch_result(repo: str, run: dict) -> tuple[bytes, bytes | None] | None:
    """The run's `result` artifact's result.json and members.json (None if it has none), or None."""
    listing = json.loads(gh(["api", f"repos/{repo}/actions/runs/{int(run['id'])}/artifacts?name=result"]))
    artifacts = [a for a in listing.get("artifacts", []) if a.get("name") == "result" and not a.get("expired")]
    if len(artifacts) != 1 or int(artifacts[0].get("size_in_bytes", 0)) > MAX_ARTIFACT_BYTES:
        return None
    archive = gh(["api", f"repos/{repo}/actions/artifacts/{int(artifacts[0]['id'])}/zip"])
    try:
        with zipfile.ZipFile(io.BytesIO(archive)) as z:
            result = bounded(z, "result.json", MAX_RESULT_BYTES)
            if result is None:
                return None
            try:
                members = bounded(z, "members.json", MAX_MEMBERS_BYTES) if "members.json" in z.namelist() else None
            except Exception:  # noqa: BLE001  (a bad members.json never costs the result)
                members = None
            return result, members
    except (zipfile.BadZipFile, KeyError, zlib.error, EOFError, RuntimeError, NotImplementedError, OSError):
        return None


def bounded(z: zipfile.ZipFile, name: str, limit: int) -> bytes | None:
    """The member's bytes, reading at most limit + 1 whatever size it declares (a zip bomb stops there), or None
    if it's bigger than limit."""
    with z.open(name) as f:
        data = f.read(limit + 1)
    return None if len(data) > limit else data


def cmd_collect(args: argparse.Namespace) -> int:
    """Wait for the batch's build runs, then put each result where merge looks: RESULTS/result-ID-ATTEMPT/result.json,
    and RESULTS/runs.json: by "ID-ATTEMPT", the build run's URL."""
    check_args(args)
    jobs = json.loads(Path(args.plan).read_text(encoding="utf-8"))
    deadline = time.monotonic() + args.wait * 60
    while True:
        picked = pick_runs(jobs, retry(lambda: list_runs(args.repo, args.since)), args.batch, args.sha, args.since)
        waiting = [k for k, v in picked.items() if v in ("no build run", "the build run hasn't finished")]
        print(f"{len(picked) - len(waiting)} of {len(picked)} builds done", file=sys.stderr)
        if not waiting or time.monotonic() > deadline:
            break
        time.sleep(60)
    out = Path(args.results)
    urls = {}
    for (pid, attempt), run in picked.items():
        if isinstance(run, str):
            print(f"build {pid}-{attempt}: {run}", file=sys.stderr)
            continue
        urls[f"{pid}-{attempt}"] = f"https://github.com/{args.repo}/actions/runs/{int(run['id'])}"
        if (got := retry(lambda run=run: fetch_result(args.repo, run))) is not None:
            (out / f"result-{pid}-{attempt}").mkdir(parents=True, exist_ok=True)
            (out / f"result-{pid}-{attempt}" / "result.json").write_bytes(got[0])
            if got[1] is not None:
                (out / f"result-{pid}-{attempt}" / "members.json").write_bytes(got[1])
    out.mkdir(parents=True, exist_ok=True)
    (out / "runs.json").write_text(json.dumps(urls) + "\n", encoding="utf-8")
    return 0


# --- stage, install-test and publish: making verified wheels public ---------------------------------------------------
#
# stage (trusted, in the coordinator): for each verified wheel, take the file from its build run's `wheel` artifact and
#   keep it only if its sha256 is the one both builds reported, it is for a Windows target, PyPI names an
#   OSI-approved licence for the release, and the project hasn't opted out.
# install-test (untrusted: it imports package code): on a fresh runner of the target, install each staged wheel
#   into a new venv and import it as the build lab does. Its answers only decide whether a byte-verified wheel is
#   worth publishing; a package's code could lie about them, but never change which bytes are published.
# publish (trusted, never runs package code): re-check every file against the sha256 stage passed it, then add it to
#   data/gap-wheels.json; the workflow attests the files, uploads them to the `gap-wheels` release and deploys the
#   index the site builds from that file (scripts/build_gap_index.py).

# The one tag a published wheel may carry for its target: the Python and platform its builds and install test ran on,
# nothing wider. Not abi3 or py3-none (other Pythons would take it untested), not a compressed tag set (win_arm64.
# win_amd64 would put an ARM64 .pyd on x64), and not Linux: `pip wheel` tags it linux_*, which pip installs on any
# libc and version, so those need auditwheel's manylinux/musllinux tag first.
PUBLISH_TAGS = {
    "3.14 win-arm64": "cp314-cp314-win_arm64",
    "3.14t win-arm64": "cp314-cp314t-win_arm64",
    "3.15 win-amd64": "cp315-cp315-win_amd64",
}
# DLLs every Windows machine with that CPython has: the interpreter's, the VC++ runtime Python's installer ships
# (vcruntime140 only, not msvcp140 or vcomp140), the Universal CRT and core system DLLs. An extension importing any
# other DLL that the wheel doesn't carry works on GitHub's images (they have the full VC++ redistributable) and fails
# on a user's machine with "DLL load failed", so it isn't published. Bundling (delvewheel) would change the bytes.
SYSTEM_DLLS = frozenset({
    "python3.dll", "vcruntime140.dll", "vcruntime140_1.dll", "ucrtbase.dll", "kernel32.dll", "kernelbase.dll",
    "ntdll.dll", "user32.dll", "gdi32.dll", "advapi32.dll", "shell32.dll", "shlwapi.dll", "ole32.dll",
    "oleaut32.dll", "ws2_32.dll", "mswsock.dll", "bcrypt.dll", "bcryptprimitives.dll", "ncrypt.dll", "crypt32.dll",
    "secur32.dll", "sspicli.dll", "userenv.dll", "iphlpapi.dll", "dnsapi.dll", "dbghelp.dll", "psapi.dll",
    "version.dll", "winmm.dll", "rpcrt4.dll", "comctl32.dll", "comdlg32.dll", "setupapi.dll", "cfgmgr32.dll",
    "powrprof.dll", "netapi32.dll", "wldap32.dll", "winhttp.dll", "wininet.dll", "normaliz.dll", "msvcrt.dll",
    "imm32.dll", "propsys.dll", "dwmapi.dll", "uxtheme.dll", "opengl32.dll", "d3d11.dll", "dxgi.dll",
})  # fmt: skip
SYSTEM_DLL = re.compile(r"api-ms-win-[a-z0-9-]+\.dll|ext-ms-win-[a-z0-9-]+\.dll|python3[0-9]{1,2}t?\.dll", re.ASCII)
# OSI-approved licences by SPDX id (the common ones on PyPI); an expression passes only if every id is one of these.
OSI = frozenset(s.lower() for s in (
    "0BSD", "AFL-3.0", "AGPL-3.0-only", "AGPL-3.0-or-later", "Apache-1.1", "Apache-2.0", "APSL-2.0", "Artistic-2.0",
    "BSD-1-Clause", "BSD-2-Clause", "BSD-2-Clause-Patent", "BSD-3-Clause", "BSL-1.0", "CDDL-1.0", "CECILL-2.1",
    "ECL-2.0", "EFL-2.0", "EPL-1.0", "EPL-2.0", "EUPL-1.2", "GPL-2.0-only", "GPL-2.0-or-later", "GPL-3.0-only",
    "GPL-3.0-or-later", "HPND", "ISC", "LGPL-2.0-only", "LGPL-2.0-or-later", "LGPL-2.1-only", "LGPL-2.1-or-later",
    "LGPL-3.0-only", "LGPL-3.0-or-later", "LPL-1.02", "MIT", "MIT-0", "MPL-1.1", "MPL-2.0", "MS-PL", "MS-RL", "MulanPSL-2.0",
    "NCSA", "OFL-1.1", "OSL-3.0", "PostgreSQL", "Python-2.0", "UPL-1.0", "Unicode-3.0", "Unicode-DFS-2016",
    "Unlicense", "W3C", "Zlib", "ZPL-2.0", "ZPL-2.1",
    # Deprecated SPDX ids still common on PyPI ("GPL-2.0+" is GPL-2.0-or-later).
    "AGPL-3.0", "GPL-2.0", "GPL-3.0", "LGPL-2.0", "LGPL-2.1", "LGPL-3.0",
))  # fmt: skip
ASSET = re.compile(r"[A-Za-z0-9._-]{1,200}\.whl", re.ASCII)  # release asset names: GitHub rewrites other characters
RUN_URL = re.compile(r"https://github\.com/[A-Za-z0-9-]{1,39}/[A-Za-z0-9._-]{1,100}/actions/runs/([0-9]{1,20})")
MAX_WHEEL_BYTES = 500_000_000
MAX_TESTED_BYTES = 200_000
RELEASE = "gap-wheels"
INDEX_SCHEMA = 1


# Standard SPDX exceptions that only add permissions (what may follow WITH).
SPDX_EXCEPTIONS = frozenset(s.lower() for s in (
    "Autoconf-exception-2.0", "Autoconf-exception-3.0", "Bison-exception-2.2", "Classpath-exception-2.0",
    "Font-exception-2.0", "GCC-exception-2.0", "GCC-exception-3.1", "LGPL-3.0-linking-exception",
    "Linux-syscall-note", "LLVM-exception", "Qt-LGPL-exception-1.1", "Swift-exception",
    "Universal-FOSS-exception-1.0", "WxWindows-exception-3.1",
))  # fmt: skip


def osi_expression(expr: str) -> bool:
    """A well-formed SPDX expression in which every licence is OSI-approved and every WITH names a standard
    exception that only adds permissions. Grammar: or := and (OR and)*; and := one (AND one)*;
    one := "(" or ")" | ID["+"] [WITH EXCEPTION]."""
    tokens = re.findall(r"[A-Za-z0-9.+-]+|[()]", expr)
    if not tokens or "".join(tokens) != re.sub(r"\s+", "", expr) or len(tokens) > 100:
        return False
    pos = 0

    def peek() -> str | None:
        return tokens[pos] if pos < len(tokens) else None

    def one() -> bool:
        nonlocal pos
        t = peek()
        if t is None or t.upper() in ("AND", "OR", "WITH") or t == ")":
            return False
        pos += 1
        if t == "(":
            if not either() or peek() != ")":
                return False
            pos += 1
            return True
        if t.lower().removesuffix("+") not in OSI:
            return False
        if (w := peek()) is not None and w.upper() == "WITH":
            pos += 1
            exc = peek()
            if exc is None or exc.lower() not in SPDX_EXCEPTIONS:
                return False
            pos += 1
        return True

    def joined(part, op: str) -> bool:
        nonlocal pos
        if not part():
            return False
        while (t := peek()) is not None and t.upper() == op:
            pos += 1
            if not part():
                return False
        return True

    def either() -> bool:
        return joined(lambda: joined(one, "AND"), "OR")

    return either() and pos == len(tokens)


def licence(info: object) -> str | None:
    """The release's licence when PyPI's metadata says it's OSI-approved: a PEP 639 SPDX expression whose every id is
    on the list, or OSI-approved classifiers. None otherwise (free text isn't read: refuse rather than guess)."""
    if not isinstance(info, dict):
        return None
    expr = info.get("license_expression")
    if isinstance(expr, str) and expr.strip():
        expr = expr.strip()
        return expr if len(expr) <= 200 and osi_expression(expr) else None
    classifiers = info.get("classifiers") if isinstance(info.get("classifiers"), list) else []
    osi = [c.removeprefix("License :: OSI Approved :: ") for c in classifiers
           if isinstance(c, str) and c.startswith("License :: OSI Approved :: ")]  # fmt: skip
    return "; ".join(osi)[:200] if osi else None


def pypi_info(name: str, version: str) -> dict | None:
    """PyPI's `info` for the release, or None if PyPI couldn't be asked."""
    try:
        with urllib.request.urlopen(f"https://pypi.org/pypi/{name}/{version}/json", timeout=10) as r:
            data = json.load(r)
    except (OSError, ValueError):
        return None
    return data.get("info") if isinstance(data, dict) and isinstance(data.get("info"), dict) else None


def tag_ok(wheel: str, target: str) -> bool:
    """The wheel carries exactly its target's one publishable tag."""
    tag = PUBLISH_TAGS.get(target)
    return tag is not None and "-".join(wheel[: -len(".whl")].split("-")[-3:]) == tag


def pe_imports(data: bytes) -> set[str] | None:
    """The DLLs a PE file (.pyd/.dll) imports, normal and delay-loaded, lowercased; None if it can't be read. Reads
    untrusted bytes, so every offset is bounds-checked and anything odd is None (the wheel isn't published)."""

    def u16(o: int) -> int:
        if o < 0 or o + 2 > len(data):
            raise ValueError
        return int.from_bytes(data[o : o + 2], "little")

    def u32(o: int) -> int:
        if o < 0 or o + 4 > len(data):
            raise ValueError
        return int.from_bytes(data[o : o + 4], "little")

    try:
        if data[:2] != b"MZ":
            return None
        pe = u32(0x3C)
        if data[pe : pe + 4] != b"PE\0\0":
            return None
        sections, opt_size = u16(pe + 6), u16(pe + 20)
        opt = pe + 24
        magic = u16(opt)
        if magic not in (0x10B, 0x20B):
            return None
        dirs = opt + (96 if magic == 0x10B else 112)
        count = u32(dirs - 4)
        table = opt + opt_size
        spans = [(u32(table + 40 * i + 12), max(u32(table + 40 * i + 8), u32(table + 40 * i + 16)),
                  u32(table + 40 * i + 20)) for i in range(min(sections, 96))]  # fmt: skip

        def offset(rva: int) -> int:
            for va, size, raw in spans:
                if va <= rva < va + size:
                    return raw + rva - va
            raise ValueError

        def name_at(rva: int) -> str:
            o = offset(rva)
            end = data.find(b"\0", o, o + 256)
            if end < 0:
                raise ValueError
            return data[o:end].decode("ascii").lower()

        names: set[str] = set()
        for index, step, name_field in ((1, 20, 12), (13, 32, 4)):  # import table; delay-load (name RVA at +4)
            if index >= count:
                continue
            rva = u32(dirs + 8 * index)
            if not rva:
                continue
            o = offset(rva)
            for n in range(4096):
                entry = data[o + n * step : o + (n + 1) * step]
                if len(entry) < step:
                    raise ValueError
                if not any(entry):
                    break
                names.add(name_at(u32(o + n * step + name_field)))
            else:
                raise ValueError
        return names
    except (ValueError, UnicodeDecodeError):
        return None


def missing_dlls(wheel: bytes) -> list[str] | None:
    """The DLLs the wheel's .pyd/.dll files import that a user's machine may not have and that the wheel doesn't carry
    beside the importing file (Python looks in that file's own folder, not elsewhere in the wheel); empty when it's
    self-contained, None if a file can't be read or there are too many to check."""
    with zipfile.ZipFile(io.BytesIO(wheel)) as z:
        binaries = [i for i in z.infolist() if i.filename.lower().endswith((".pyd", ".dll"))]
        if len(binaries) > 500:
            return None
        beside: dict[str, set[str]] = {}
        for i in binaries:
            folder, _, name = i.filename.lower().rpartition("/")
            beside.setdefault(folder, set()).add(name)
        missing: set[str] = set()
        for info in binaries:
            data = bounded(z, info.filename, 200_000_000)
            imports = pe_imports(data) if data is not None else None
            if imports is None:
                return None
            here = beside[info.filename.lower().rpartition("/")[0]]
            missing |= {n for n in imports if n not in here and n not in SYSTEM_DLLS and not SYSTEM_DLL.fullmatch(n)}
    return sorted(missing)


def unwrap_wheel(archive: bytes, filename: str, digest: str) -> bytes | None:
    """The wheel from a `wheel` artifact zip: exactly one member, named `filename`, whose sha256 is `digest`."""
    try:
        with zipfile.ZipFile(io.BytesIO(archive)) as z:
            if z.namelist() != [filename]:
                return None
            data = bounded(z, filename, MAX_WHEEL_BYTES)
    except (zipfile.BadZipFile, KeyError, zlib.error, EOFError, RuntimeError, NotImplementedError, OSError):
        return None
    return data if data is not None and hashlib.sha256(data).hexdigest() == digest else None


def fetch_wheel(repo: str, run_id: int) -> bytes | None:
    """The zip of a build run's `wheel` artifact, or None."""
    listing = json.loads(gh(["api", f"repos/{repo}/actions/runs/{run_id}/artifacts?name=wheel"]))
    artifacts = [a for a in listing.get("artifacts", []) if a.get("name") == "wheel" and not a.get("expired")]
    if len(artifacts) != 1 or int(artifacts[0].get("size_in_bytes", 0)) > MAX_WHEEL_BYTES:
        return None
    return subprocess.run(["gh", "api", f"repos/{repo}/actions/artifacts/{int(artifacts[0]['id'])}/zip"],
                          check=True, capture_output=True, timeout=600).stdout  # fmt: skip


def opted_out(path: Path) -> set[str]:
    """Canonical project names from the opt-out file (one per line, # comments)."""
    if not path.is_file():
        return set()
    names = (line.split("#", 1)[0].strip() for line in path.read_text(encoding="utf-8").splitlines())
    return {build_lab.canonical(n) for n in names if n}


def stage_reason(wheel: dict, info: dict | None, optout: set[str]) -> str | None:
    """Why a verified wheel isn't staged (None: stage it)."""
    if not ASSET.fullmatch(wheel["wheel"]):
        return "its filename can't be a release asset name"
    if not tag_ok(wheel["wheel"], wheel["target"]):
        return f"not tagged exactly {PUBLISH_TAGS.get(wheel['target'], 'a publishable tag')} (a linux_* wheel needs auditwheel first)"
    if build_lab.canonical(wheel["name"]) in optout:
        return "the project opted out"
    if info is None:
        return "couldn't ask PyPI for the licence"
    if licence(info) is None:
        return "PyPI names no OSI-approved licence for it"
    return None


def take_wheel(repo: str, wheel: dict) -> tuple[bytes | None, str | None]:
    """(the verified bytes, None) from build 1's run, else build 2's (both reported this sha256), or (None, why not)."""
    ids = [int(m.group(1)) for u in wheel.get("builds") or [] if isinstance(u, str) and (m := RUN_URL.fullmatch(u))]
    data = None
    for run_id in ids:
        archive = retry(lambda run_id=run_id: fetch_wheel(repo, run_id))
        if archive is not None and (data := unwrap_wheel(archive, wheel["wheel"], wheel["sha256"])):
            break
    if data is None:
        return None, "no build run kept a wheel with the verified sha256"
    if (missing := missing_dlls(data)) is None:
        return None, "couldn't read its extension modules' imports"
    if missing:
        return None, f"needs DLLs a user's machine may not have: {', '.join(missing[:5])}"
    return data, None


def cmd_stage(args: argparse.Namespace) -> int:
    """Write STAGED/<wheel> and STAGED/staged.json for the verified wheels that may be published, and (for the
    workflow) `staged`, the same list, and `tests`, the targets to install-test, as GitHub outputs."""
    manifest = json.loads(Path(args.manifest).read_text(encoding="utf-8"))
    out = Path(args.staged)
    out.mkdir(parents=True, exist_ok=True)
    optout = opted_out(Path(args.optout))
    staged, lines = [], []
    for wheel in manifest["wheels"]:
        info = pypi_info(wheel["name"], wheel["version"])
        reason = stage_reason(wheel, info, optout)
        data = None
        if reason is None:
            try:
                data, reason = take_wheel(args.repo, wheel)
            except Exception as e:  # noqa: BLE001  (one unreadable wheel costs only itself, not the week's batch)
                data, reason = None, f"couldn't read it ({type(e).__name__})"
        lines.append(f"| {wheel['name']} | {wheel['version']} | {wheel['target']} | {reason or 'staged'} |")
        if reason is not None:
            continue
        (out / wheel["wheel"]).write_bytes(data)
        staged.append({**{k: wheel[k] for k in ("name", "version", "target", "wheel", "sha256", "sdist",
                                                "sdist_sha256", "builds")},
                       "licence": licence(info), "requires_python": info.get("requires_python") or None,
                       "verified": manifest.get("date"), "batch": manifest.get("run")})  # fmt: skip
    (out / "staged.json").write_text(json.dumps(staged, indent=1) + "\n", encoding="utf-8")
    tests = sorted({w["target"] for w in staged})
    matrix = [{"target": t, "runner": build_lab.TARGETS[t]["runner"], "python": build_lab.TARGETS[t]["python"],
               "slug": t.replace(" ", "-")} for t in tests]  # fmt: skip
    text = "\n".join([f"**{len(staged)} staged** of {len(manifest['wheels'])} verified", "",
                      "| package | version | target | |", "|---|---|---|---|", *lines]) + "\n"  # fmt: skip
    print(text)
    if args.summary:
        with open(args.summary, "a", encoding="utf-8") as f:
            f.write(text)
    if args.github_output:
        with open(args.github_output, "a", encoding="utf-8") as f:
            # The publish job trusts only this output for what to publish; the artifact is just the bytes.
            f.write(f"staged={json.dumps(staged, separators=(',', ':'))}\n")
            f.write(f"tests={json.dumps(matrix, separators=(',', ':'))}\n")
    return 0


def install_and_import(wheel: Path, target: str, work: Path) -> str | None:
    """Install the wheel into a fresh venv (dependencies as pip resolves them) and import it as the build lab does;
    on free-threaded Python the GIL must stay off. None if it works, else why not."""
    venv = work / "venv"
    shutil.rmtree(venv, ignore_errors=True)
    subprocess.run([sys.executable, "-m", "venv", str(venv)], check=True, timeout=INSTALL_TIMEOUT)
    py = str(venv / ("Scripts/python.exe" if os.name == "nt" else "bin/python"))
    env = dict(os.environ, PIP_DISABLE_PIP_VERSION_CHECK="1", PIP_NO_INPUT="1")
    try:
        i = subprocess.run([py, "-m", "pip", "install", "--no-cache-dir", str(wheel)], check=False, env=env,
                           capture_output=True, text=True, errors="replace", timeout=INSTALL_TIMEOUT)  # fmt: skip
    except subprocess.TimeoutExpired:
        return "install timed out"
    print((i.stdout + i.stderr)[-3000:])
    if i.returncode != 0:
        return "pip couldn't install it"
    modules, extension_modules, extensions = build_lab.wheel_modules(wheel)
    ours = work / "wheel-modules.json"
    ours.write_text(json.dumps(build_lab.wheel_names(wheel)), encoding="utf-8")
    answers = []
    for mode, names in (("top", modules), ("extensions", extension_modules)):
        if mode == "extensions" and not extensions:
            break
        check = build_lab._import_check(work, env, mode, ours, names, python=py)
        if check is None or check[0] != 0 or check[1] is None or not check[1]["loaded"]:
            return f"the {mode} import check failed"
        answers.append(check[1])
    if not answers:
        return "nothing to import"
    if build_lab.TARGETS[target]["python"].endswith("t") and any(a.get("gil_enabled") for a in answers):
        return "importing it turned the GIL back on"
    return None


def cmd_install_test(args: argparse.Namespace) -> int:
    if (why := build_lab.interpreter_problem(args.target)) is not None:
        print(f"gap_wheels: this Python isn't {args.target}'s: {why}", file=sys.stderr)
        return 3
    staged = json.loads((Path(args.staged) / "staged.json").read_text(encoding="utf-8"))
    tested = {}
    for w in staged:
        if w["target"] != args.target:
            continue
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            try:
                why = install_and_import(Path(args.staged) / w["wheel"], args.target, Path(tmp))
            except Exception as e:  # noqa: BLE001  (one bad wheel fails itself, not its target's whole test)
                why = f"the test itself failed ({type(e).__name__})"
        print(f"{w['wheel']}: {why or 'ok'}")
        tested[w["wheel"]] = why is None
    Path(args.out).write_text(json.dumps({"target": args.target, "tested": tested}) + "\n", encoding="utf-8")
    return 0


def read_tested(folder: Path) -> dict[str, dict[str, bool]]:
    """By target: {wheel: passed} from the untrusted install-test artifacts (each tested-*/tested.json)."""
    out: dict[str, dict[str, bool]] = {}
    paths = [*folder.glob("tested-*/tested.json"), *folder.glob("tested.json")] if folder.is_dir() else []
    for path in sorted(paths):
        if path.is_symlink() or path.stat().st_size > MAX_TESTED_BYTES:
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (UnicodeDecodeError, ValueError, RecursionError):
            continue
        if not isinstance(data, dict) or not isinstance(data.get("tested"), dict) or data.get("target") not in build_lab.TARGETS:
            continue
        if data["target"] in out:  # two answers for one target: neither counts
            out[data["target"]] = {}
            continue
        out[data["target"]] = {k: v for k, v in data["tested"].items() if isinstance(k, str) and v is True}
    return out


def select(staged: list[dict], tested: dict[str, dict[str, bool]], folder: Path,
           published: dict[str, dict]) -> tuple[list[dict], list[str]]:  # fmt: skip
    """(entries to publish now, a line saying what happened to each staged wheel). `staged` is the staging job's own
    output, the only thing trusted here: a wheel publishes when its file in `folder` has that sha256 and its
    target's install test passed. A filename already published keeps its first bytes."""
    new, lines = [], []
    for w in staged:
        name = w["wheel"]
        path = folder / name
        if not path.is_file() or path.is_symlink() or sha256(path) != w["sha256"]:
            lines.append(f"{name}: the staged file isn't the verified one; not published")
        elif not tested.get(w["target"], {}).get(name):
            lines.append(f"{name}: didn't pass the install test on a fresh runner; not published")
        elif name in published:
            same = published[name].get("sha256") == w["sha256"]
            lines.append(f"{name}: {'already published' if same else 'published before with other bytes; kept those'}")
        else:
            new.append(w)
            lines.append(f"{name}: published")
    return new, lines


def trusted_staged(text: str) -> list[dict]:
    """The staging job's `staged` output, checked for shape (it's ours, but a bad one must stop, not half-publish)."""
    staged = json.loads(text)
    keys = {"name", "version", "target", "wheel", "sha256", "sdist", "sdist_sha256", "builds", "licence",
            "requires_python", "verified", "batch"}  # fmt: skip
    if not isinstance(staged, list) or not all(
        isinstance(w, dict) and set(w) == keys and isinstance(w["wheel"], str) and ASSET.fullmatch(w["wheel"])
        and isinstance(w["sha256"], str) and SHA256.fullmatch(w["sha256"]) and w["target"] in build_lab.TARGETS
        and tag_ok(w["wheel"], w["target"])
        for w in staged
    ):
        raise SystemExit("gap_wheels: --staged-json isn't what stage writes")
    return staged


def cmd_publish(args: argparse.Namespace) -> int:
    """Copy the wheels to publish into RELEASE_DIR and add them to the index file; list in RELEASE_DIR/withdraw.txt
    the published wheels of projects that have since opted out (removed from the index here)."""
    staged = trusted_staged(args.staged_json)
    staged_dir = Path(args.files)
    index_path = Path(args.index)
    index = json.loads(index_path.read_text(encoding="utf-8")) if index_path.is_file() else {"schema": INDEX_SCHEMA, "wheels": []}
    published = {w["wheel"]: w for w in index["wheels"]}
    new, lines = select(staged, read_tested(Path(args.tested)), staged_dir, published)
    release = Path(args.release_dir)
    release.mkdir(parents=True, exist_ok=True)
    today = dt.datetime.now(dt.UTC).date().isoformat()
    for w in new:
        shutil.copyfile(staged_dir / w["wheel"], release / w["wheel"])
        index["wheels"].append({**w, "published": today, "publish_run": args.run})
    optout = opted_out(Path(args.optout))
    withdrawn = [w["wheel"] for w in index["wheels"] if build_lab.canonical(w["name"]) in optout]
    index["wheels"] = sorted((w for w in index["wheels"] if w["wheel"] not in withdrawn),
                             key=lambda w: (build_lab.canonical(w["name"]), w["wheel"]))  # fmt: skip
    (release / "withdraw.txt").write_text("".join(f"{n}\n" for n in withdrawn), encoding="utf-8")
    index_path.write_text(json.dumps(index, indent=1) + "\n", encoding="utf-8")
    lines += [f"{n}: withdrawn (the project opted out)" for n in withdrawn]
    text = f"**{len(new)} published**, {len(index['wheels'])} in the index\n\n" + "".join(f"- {ln}\n" for ln in lines)
    print(text)
    if args.summary:
        with open(args.summary, "a", encoding="utf-8") as f:
            f.write(text)
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("plan")
    p.add_argument("builds")
    p.add_argument("--max", type=int, default=100)
    p.add_argument("--only")
    p.add_argument("--rotate", action="store_true", help="start further on each week (for the scheduled batch)")
    p.add_argument("--github-output")
    p.set_defaults(func=cmd_plan)
    b = sub.add_parser("build")
    for flag in ("--name", "--version", "--target", "--runner", "--out"):
        b.add_argument(flag, required=True)
    b.add_argument("--attempt", type=int, required=True)
    b.add_argument("--wheel-dir", help="copy the built wheel here (the build run uploads it as `wheel`)")
    b.set_defaults(func=cmd_build)
    m = sub.add_parser("merge")
    m.add_argument("results")
    m.add_argument("--plan", required=True)
    m.add_argument("--out", required=True)
    m.add_argument("--summary")
    m.set_defaults(func=cmd_merge)
    d = sub.add_parser("dispatch")
    for flag in ("--plan", "--batch", "--repo", "--sha", "--since"):
        d.add_argument(flag, required=True)
    d.add_argument("--parallel", type=int, default=20, help="most of this batch's builds running at once")
    d.set_defaults(func=cmd_dispatch)
    c = sub.add_parser("collect")
    for flag in ("--plan", "--batch", "--repo", "--sha", "--since", "--results"):
        c.add_argument(flag, required=True)
    c.add_argument("--wait", type=int, default=240, help="minutes to wait for the build runs")
    c.set_defaults(func=cmd_collect)
    st = sub.add_parser("stage")
    for flag in ("--manifest", "--staged", "--repo"):
        st.add_argument(flag, required=True)
    st.add_argument("--optout", default="data/gap-wheels-optout.txt")
    st.add_argument("--summary")
    st.add_argument("--github-output")
    st.set_defaults(func=cmd_stage)
    t = sub.add_parser("install-test")
    for flag in ("--staged", "--target", "--out"):
        t.add_argument(flag, required=True)
    t.set_defaults(func=cmd_install_test)
    pb = sub.add_parser("publish")
    for flag in ("--staged-json", "--files", "--tested", "--index", "--release-dir"):
        pb.add_argument(flag, required=True)
    pb.add_argument("--optout", default="data/gap-wheels-optout.txt")
    pb.add_argument("--run", default="")
    pb.add_argument("--summary")
    pb.set_defaults(func=cmd_publish)
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
