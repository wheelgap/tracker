"""Gap wheels: verify which of the build lab's clean builds are reproducible and install. Nothing is published.

    python3 scripts/gap_wheels.py plan data/builds.json [--max N] [--only a,b] --github-output FILE
    python scripts/gap_wheels.py build --name NAME --version VERSION --target TARGET --attempt 1|2 --runner R --out FILE
    python3 scripts/gap_wheels.py dispatch --plan PLAN_JSON --batch RUNID-ATTEMPT
    python3 scripts/gap_wheels.py collect --plan PLAN_JSON --batch B --repo R --sha SHA --since TIME --results DIR
    python3 scripts/gap_wheels.py merge RESULTS_DIR --plan PLAN_JSON --out MANIFEST [--summary FILE]

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
A build can then forge nothing but its own result. This step uploads no wheel anywhere: only results (hashes)
leave the build jobs.
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


def run_build(name: str, version: str, attempt: int, work: Path, members: dict[str, str] | None = None) -> dict:
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
        fields = run_build(args.name, args.version, args.attempt, Path(tmp), members)
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
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
