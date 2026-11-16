"""Offline tests for scripts/gap_wheels.py: the plan, the merge job's checks on untrusted results, and the verdicts."""

import hashlib
import io
import json
import subprocess
import tarfile
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest import mock

from scripts import gap_wheels


def row(name, version, target, **over):
    r = {"name": name, "version": version, "target": target, "outcome": "built", "import": "ok", "extensions": 2,
         "gil": "kept-off" if target.split()[0].endswith("t") else None}  # fmt: skip
    r.update(over)
    return r


BUILDS = {
    "results": [
        row("tiktoken", "0.14.0", "3.14 win-arm64"),
        row("lz4", "4.4.5", "3.14t win-arm64"),
        row("zstandard", "0.25.0", "3.14 musl-aarch64"),
        row("broken", "1.0", "3.14 win-arm64", outcome="failed"),
        row("noimport", "1.0", "3.14 win-arm64", **{"import": "failed"}),
        row("pureish", "1.0", "3.14 win-arm64", extensions=0),  # nothing compiled: PyPI's pure wheel would do
        row("gilon", "1.0", "3.14t win-arm64", gil="re-enabled"),
        row("weird", "1.0; rm -rf /", "3.14 win-arm64"),
        row("elsewhere", "1.0", "3.14 linux-x86_64"),  # not a lab target
    ]
}


def good(job, **over):
    platform = {"3.14 win-arm64": "cp314-cp314-win_arm64", "3.14t win-arm64": "cp314-cp314t-win_arm64",
                "3.14 musl-aarch64": "cp314-cp314-musllinux_1_2_aarch64"}[job["target"]]  # fmt: skip
    r = {
        "schema": 1,
        "name": job["name"],
        "version": job["version"],
        "target": job["target"],
        "attempt": job["attempt"],
        "runner": job["runner"],
        "sdist": f"{job['name']}-{job['version']}.tar.gz",
        "sdist_sha256": "a" * 64,
        "wheel": f"{job['name']}-{job['version']}-{platform}.whl",
        "sha256": "b" * 64,
        "installs": True if job["attempt"] == 1 else None,
    }
    r.update(over)
    return r


class Plan(unittest.TestCase):
    def test_only_clean_builds_on_lab_targets(self):
        pairs = {(p["name"], p["target"]) for p in gap_wheels.clean_pairs(BUILDS)}
        self.assertEqual(pairs, {("tiktoken", "3.14 win-arm64"), ("lz4", "3.14t win-arm64"),
                                 ("zstandard", "3.14 musl-aarch64")})  # fmt: skip

    def test_two_builds_per_pair_and_docker_for_alpine(self):
        jobs = gap_wheels.plan(BUILDS, 10)
        self.assertEqual(len(jobs), 6)
        self.assertEqual({(j["id"], j["attempt"]) for j in jobs}, {(i, a) for i in "012" for a in (1, 2)})
        alpine = [j for j in jobs if j["target"] == "3.14 musl-aarch64"]
        self.assertTrue(alpine and all(j["image"].startswith("python:3.14-alpine@sha256:") for j in alpine))

    def test_max_and_only(self):
        self.assertEqual(len(gap_wheels.plan(BUILDS, 1)), 2)
        self.assertEqual({j["name"] for j in gap_wheels.plan(BUILDS, 10, {"lz4"})}, {"lz4"})


class Verify(unittest.TestCase):
    def setUp(self):
        self.jobs = gap_wheels.plan(BUILDS, 1, {"tiktoken"})
        self.published = {}
        self.one, self.two = self.jobs

    def verdict(self, first, second):
        results = {}
        if first is not None:
            results[("0", 1)] = first
        if second is not None:
            results[("0", 2)] = second
        published = {("tiktoken", "0.14.0"): {"tiktoken-0.14.0.tar.gz": "a" * 64}}
        published |= self.published
        verified, report = gap_wheels.verify(self.jobs, results, lambda name, version: published[(name, version)])
        return verified, report[0]["verdict"]

    def test_identical_and_installs(self):
        verified, verdict = self.verdict(good(self.one), good(self.two))
        self.assertEqual(verdict, "verified")
        self.assertEqual(verified[0]["wheel"], "tiktoken-0.14.0-cp314-cp314-win_arm64.whl")
        self.assertEqual(verified[0]["sha256"], "b" * 64)

    def test_builds_differ(self):
        self.assertEqual(self.verdict(good(self.one), good(self.two, sha256="c" * 64))[1], "the two builds differ")

    def test_different_sdists(self):
        self.assertEqual(self.verdict(good(self.one), good(self.two, sdist_sha256="c" * 64))[1],
                         "the two builds differ")  # fmt: skip

    def test_doesnt_install(self):
        self.assertEqual(self.verdict(good(self.one, installs=False), good(self.two))[1],
                         "pip couldn't install it without compiling")  # fmt: skip

    def test_the_sdist_must_be_pypis(self):
        both = (good(self.one, sdist_sha256="c" * 64), good(self.two, sdist_sha256="c" * 64))
        self.assertEqual(self.verdict(*both)[1], "the sdist isn't the one PyPI publishes")
        self.published = {("tiktoken", "0.14.0"): None}
        self.assertEqual(self.verdict(good(self.one), good(self.two))[1], "couldn't check the sdist against PyPI")

    def test_a_wheel_without_its_sdist_is_rejected(self):
        for over in ({"sdist": None}, {"sdist_sha256": None}):
            with self.subTest(over):
                verdict = self.verdict(good(self.one, **over), good(self.two, **over))[1]
                self.assertTrue(verdict.startswith("rejected"), verdict)

    def test_normalized_versions_and_compressed_tags(self):
        for wheel in ("tiktoken-0.14.0-cp314-cp314-win_arm64.win_amd64.whl", "tiktoken-0.14.0-cp314-cp314-WIN_ARM64.whl"):
            with self.subTest(wheel):
                self.assertTrue(self.verdict(good(self.one, wheel=wheel), good(self.two, wheel=wheel))[1].startswith(
                    "verified" if "win_amd64" in wheel else "rejected"))  # fmt: skip
        self.jobs[0]["version"] = self.jobs[1]["version"] = "0.14.0RC1"
        wheel = "tiktoken-0.14.0rc1-cp314-cp314-win_arm64.whl"
        published = {("tiktoken", "0.14.0RC1"): {"tiktoken-0.14.0RC1.tar.gz": "a" * 64}}
        results = {("0", 1): good(self.jobs[0], wheel=wheel, sdist="tiktoken-0.14.0RC1.tar.gz"),
                   ("0", 2): good(self.jobs[1], wheel=wheel, sdist="tiktoken-0.14.0RC1.tar.gz")}  # fmt: skip
        report = gap_wheels.verify(self.jobs, results, lambda n, v: published[(n, v)])[1]
        self.assertEqual(report[0]["verdict"], "verified")

    def test_missing_result(self):
        self.assertEqual(self.verdict(good(self.one), None)[1], "a build has no result")

    def test_no_wheel(self):
        self.assertEqual(self.verdict(good(self.one), good(self.two, wheel=None, sha256=None))[1], "a build made no wheel")

    def test_a_result_for_another_job_is_rejected(self):
        for over in ({"name": "numpy"}, {"version": "0.13.0"}, {"target": "3.14t win-arm64"}, {"attempt": 1},
                     {"runner": "ubuntu-24.04"}):  # fmt: skip
            with self.subTest(over):
                verified, verdict = self.verdict(good(self.one), good(self.two, **over))
                self.assertTrue(verdict.startswith("rejected"), verdict)
                self.assertEqual(verified, [])

    def test_a_wheel_for_another_project_version_or_platform_is_rejected(self):
        for wheel in ("numpy-0.14.0-cp314-cp314-win_arm64.whl", "tiktoken-0.13.0-cp314-cp314-win_arm64.whl",
                      "tiktoken-0.14.0-cp314-cp314-win_amd64.whl", "tiktoken-0.14.0-py3-none-any.whl",
                      "../evil.whl", "tiktoken.whl"):  # fmt: skip
            with self.subTest(wheel):
                verdict = self.verdict(good(self.one, wheel=wheel), good(self.two, wheel=wheel))[1]
                self.assertTrue(verdict.startswith("rejected"), verdict)

    def test_malformed_results_are_rejected(self):
        for bad in ("not JSON", [], {**good(self.two), "extra": 1}, good(self.two, sha256="B" * 64),
                    good(self.two, installs="yes"), good(self.two, installs=True), good(self.two, schema=2),
                    good(self.two, sdist="x;y.tar.gz"), good(self.two, sha256=None)):  # fmt: skip
            with self.subTest(bad):
                self.assertTrue(self.verdict(good(self.one), bad)[1].startswith("rejected"))


class Merge(unittest.TestCase):
    def test_writes_manifest_and_summary(self):
        jobs = gap_wheels.plan(BUILDS, 2)
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            (tmp / "plan.json").write_text(json.dumps(jobs))
            for job in jobs:
                d = tmp / "results" / f"result-{job['id']}-{job['attempt']}"
                d.mkdir(parents=True)
                sha = "c" * 64 if job["id"] == "1" and job["attempt"] == 2 else "b" * 64
                (d / "result.json").write_text(json.dumps(good(job, sha256=sha)))
            big = tmp / "results" / "result-9-1"
            big.mkdir()
            (big / "result.json").write_text("x" * 20_000)  # not in the plan: never read
            sdists = {"lz4-4.4.5.tar.gz": "a" * 64, "tiktoken-0.14.0.tar.gz": "a" * 64}
            with mock.patch.object(gap_wheels, "pypi_sdists", lambda name, version: sdists):
                gap_wheels.main(["merge", str(tmp / "results"), "--plan", str(tmp / "plan.json"), "--out",
                                 str(tmp / "manifest.json"), "--summary", str(tmp / "summary.md")])  # fmt: skip
            manifest = json.loads((tmp / "manifest.json").read_text())
            summary = (tmp / "summary.md").read_text()
        self.assertEqual([w["name"] for w in manifest["wheels"]], ["lz4"])
        self.assertIn("**1 verified** of 2 pairs", summary)
        self.assertIn("the two builds differ", summary)

    def test_an_oversized_result_is_not_parsed(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "result.json"
            path.write_text(" " * 20_000 + "{}")
            self.assertEqual(gap_wheels.read_result(path), "too big")


class Unpack(unittest.TestCase):
    def test_refuses_paths_outside(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            sdist = tmp / "evil-1.0.tar.gz"
            with tarfile.open(sdist, "w:gz") as t:
                info = tarfile.TarInfo("../outside.txt")
                info.size = 1
                t.addfile(info, io.BytesIO(b"x"))
            with self.assertRaises(tarfile.TarError):
                gap_wheels.unpack(sdist, tmp / "dest")
            self.assertFalse((tmp / "outside.txt").exists())
            z = tmp / "evil-1.0.zip"
            with zipfile.ZipFile(z, "w") as f:
                f.writestr("../outside.txt", "x")
            with self.assertRaises(ValueError):
                gap_wheels.unpack(z, tmp / "dest")

    def test_one_top_folder(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            z = tmp / "ok-1.0.zip"
            with zipfile.ZipFile(z, "w") as f:
                f.writestr("ok-1.0/setup.py", "")
            self.assertEqual(gap_wheels.unpack(z, tmp / "dest").name, "ok-1.0")


SHA = "1" * 40


def build_run(job, batch="99-1", **over):
    run = {"id": 7, "display_title": gap_wheels.title(batch, job), "event": "workflow_dispatch", "head_branch": "main",
           "head_sha": SHA, "path": ".github/workflows/gap-wheel-build.yml", "created_at": "2026-11-16T02:00:05Z",
           "status": "completed"}  # fmt: skip
    run.update(over)
    return run


class Runs(unittest.TestCase):
    def setUp(self):
        self.jobs = gap_wheels.plan(BUILDS, 1, {"tiktoken"})
        self.one = self.jobs[0]

    def pick(self, runs):
        return gap_wheels.pick_runs(self.jobs, runs, "99-1", SHA, "2026-11-16T02:00:00Z")[("0", 1)]

    def test_title_matches_the_build_workflows_run_name(self):
        workflow = (Path(__file__).parent.parent / ".github/workflows/gap-wheel-build.yml").read_text()
        self.assertIn("run-name: gap-wheel ${{ inputs.batch }} ${{ inputs.name }} ${{ inputs.version }} "
                      "${{ inputs.target }} build ${{ inputs.attempt }}", workflow)  # fmt: skip
        self.assertEqual(gap_wheels.title("99-1", self.one), "gap-wheel 99-1 tiktoken 0.14.0 3.14 win-arm64 build 1")

    def test_the_one_run_this_batch_started(self):
        self.assertEqual(self.pick([build_run(self.one)])["id"], 7)

    def test_a_run_another_job_made_doesnt_count(self):
        for over in ({"event": "push"}, {"head_branch": "evil"}, {"head_sha": "2" * 40},
                     {"path": ".github/workflows/other.yml"}, {"created_at": "2026-11-16T01:59:59Z"}):  # fmt: skip
            with self.subTest(over):
                self.assertEqual(self.pick([build_run(self.one, **over)]), "the build run isn't one this batch started")

    def test_two_runs_with_one_title_both_lose(self):
        self.assertEqual(self.pick([build_run(self.one), build_run(self.one, id=8)]),
                         "more than one build run with its title")  # fmt: skip

    def test_another_batch_or_attempt_isnt_this_one(self):
        self.assertEqual(self.pick([build_run(self.one, batch="98-1"), build_run(self.jobs[1])]), "no build run")

    def test_unfinished(self):
        self.assertEqual(self.pick([build_run(self.one, status="in_progress")]), "the build run hasn't finished")

    def test_fetch_result_reads_only_a_small_result_json(self):
        def zipped(files):
            buf = io.BytesIO()
            with zipfile.ZipFile(buf, "w") as z:
                for name, data in files.items():
                    z.writestr(name, data)
            return buf.getvalue()

        listing = json.dumps({"artifacts": [{"id": 5, "name": "result", "size_in_bytes": 300}]}).encode()
        for archive, expected in ((zipped({"result.json": "{}"}), (b"{}", None)), (zipped({"other.json": "{}"}), None),
                                  (zipped({"result.json": "{}", "members.json": "[]"}), (b"{}", b"[]")),
                                  (zipped({"result.json": "{}", "members.json": "x" * 2_000_001}), (b"{}", None)),
                                  (zipped({"result.json": "x" * 20_000}), None), (b"not a zip", None)):  # fmt: skip
            with self.subTest(expected), mock.patch.object(gap_wheels, "gh", side_effect=[listing, archive]):
                self.assertEqual(gap_wheels.fetch_result("o/r", {"id": 7}), expected)
        two = json.dumps({"artifacts": [{"id": 5, "name": "result"}, {"id": 6, "name": "result"}]}).encode()
        with mock.patch.object(gap_wheels, "gh", side_effect=[two]):
            self.assertIsNone(gap_wheels.fetch_result("o/r", {"id": 7}))

    def test_collect_writes_results_and_run_urls_for_merge(self):
        runs = [build_run(job, id=10 + job["attempt"]) for job in self.jobs]
        results = {11: good(self.jobs[0]), 12: good(self.jobs[1])}
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            (tmp / "plan.json").write_text(json.dumps(self.jobs))
            with (
                mock.patch.object(gap_wheels, "list_runs", return_value=runs),
                mock.patch.object(gap_wheels, "fetch_result", lambda repo, run: (json.dumps(results[run["id"]]).encode(), b"{}")),
            ):
                gap_wheels.main(["collect", "--plan", str(tmp / "plan.json"), "--batch", "99-1", "--repo", "o/r",
                                 "--sha", SHA, "--since", "2026-11-16T02:00:00Z", "--results", str(tmp / "r")])  # fmt: skip
            self.assertEqual(json.loads((tmp / "r" / "runs.json").read_text()),
                             {"0-1": "https://github.com/o/r/actions/runs/11",
                              "0-2": "https://github.com/o/r/actions/runs/12"})  # fmt: skip
            self.assertEqual(json.loads((tmp / "r" / "result-0-2" / "result.json").read_text()), results[12])
            with mock.patch.object(gap_wheels, "pypi_sdists", lambda n, v: {"tiktoken-0.14.0.tar.gz": "a" * 64}):
                gap_wheels.main(["merge", str(tmp / "r"), "--plan", str(tmp / "plan.json"), "--out", str(tmp / "m.json")])
            wheel = json.loads((tmp / "m.json").read_text())["wheels"][0]
        self.assertEqual(wheel["builds"], ["https://github.com/o/r/actions/runs/11", "https://github.com/o/r/actions/runs/12"])

    def test_collect_refuses_odd_arguments(self):
        for flag, value in (("--batch", "1; rm"), ("--since", "yesterday"), ("--repo", "o/r?x"), ("--sha", "abc")):
            args = {"--batch": "99-1", "--since": "2026-11-16T02:00:00Z", "--repo": "o/r", "--sha": SHA, flag: value}
            with self.subTest(flag), self.assertRaises(SystemExit):
                gap_wheels.main(["collect", "--plan", "x", "--results", "r", *[x for kv in args.items() for x in kv]])


class Members(unittest.TestCase):
    def test_wheel_members(self):
        with tempfile.TemporaryDirectory() as tmp:
            wheel = Path(tmp) / "x-1.0-py3-none-any.whl"
            with zipfile.ZipFile(wheel, "w") as z:
                z.writestr("x/__init__.py", "")
                z.writestr("x/", "")
            self.assertEqual(gap_wheels.wheel_members(wheel), {"x/__init__.py": hashlib.sha256(b"").hexdigest()})

    def test_differing_names_the_files(self):
        a, b, c = "a" * 64, "b" * 64, "c" * 64
        first = {"pkg/_x.so": a, "pkg/__init__.py": b, "pkg-1.dist-info/RECORD": c}
        second = {"pkg/_x.so": b, "pkg/__init__.py": b, "pkg-1.dist-info/RECORD": a, "pkg/extra.py": a}
        self.assertEqual(gap_wheels.differing(first, second),
                         ", in `pkg-1.dist-info/RECORD`, `pkg/_x.so`, `pkg/extra.py`")  # fmt: skip
        many = {f"f{i}": a for i in range(8)}
        self.assertTrue(gap_wheels.differing(many, {}).endswith("(+3 more)"))

    def test_an_unreadable_wheel_has_no_members_but_still_a_result(self):
        with tempfile.TemporaryDirectory() as tmp:
            wheel = Path(tmp) / "x-1.0-py3-none-any.whl"
            wheel.write_bytes(b"not a zip")
            self.assertEqual(gap_wheels.wheel_members(wheel), {})

    def test_deeply_nested_members_json_doesnt_crash_merge(self):
        jobs = gap_wheels.plan(BUILDS, 1, {"tiktoken"})
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            (tmp / "plan.json").write_text(json.dumps(jobs))
            for job in jobs:
                d = tmp / "r" / f"result-{job['id']}-{job['attempt']}"
                d.mkdir(parents=True)
                (d / "result.json").write_text(json.dumps(good(job)))
                (d / "members.json").write_text("[" * 200_000 + "]" * 200_000)
            with mock.patch.object(gap_wheels, "pypi_sdists", lambda n, v: {"tiktoken-0.14.0.tar.gz": "a" * 64}):
                gap_wheels.main(["merge", str(tmp / "r"), "--plan", str(tmp / "plan.json"), "--out", str(tmp / "m.json")])
            self.assertEqual(len(json.loads((tmp / "m.json").read_text())["wheels"]), 1)

    def test_differing_ignores_untrusted_junk(self):
        good = {"pkg/_x.so": "a" * 64}
        for bad in (None, [], {"a|b": "a" * 64}, {"x": "nope"}, {"`x`": "a" * 64}, {"x" * 201: "a" * 64}):
            with self.subTest(bad):
                self.assertEqual(gap_wheels.differing(good, bad), "")

    def test_merge_reports_which_files_differ(self):
        jobs = gap_wheels.plan(BUILDS, 1, {"tiktoken"})
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            (tmp / "plan.json").write_text(json.dumps(jobs))
            for job, sha, so in ((jobs[0], "b" * 64, "1" * 64), (jobs[1], "c" * 64, "2" * 64)):
                d = tmp / "r" / f"result-{job['id']}-{job['attempt']}"
                d.mkdir(parents=True)
                (d / "result.json").write_text(json.dumps(good(job, sha256=sha)))
                (d / "members.json").write_text(json.dumps({"tiktoken/_tiktoken.pyd": so, "tiktoken/core.py": "3" * 64}))
            with mock.patch.object(gap_wheels, "pypi_sdists", lambda n, v: {"tiktoken-0.14.0.tar.gz": "a" * 64}):
                gap_wheels.main(["merge", str(tmp / "r"), "--plan", str(tmp / "plan.json"), "--out", str(tmp / "m.json"),
                                 "--summary", str(tmp / "s.md")])  # fmt: skip
            self.assertIn("the two builds differ, in `tiktoken/_tiktoken.pyd` |", (tmp / "s.md").read_text())


class Dispatch(unittest.TestCase):
    def setUp(self):
        self.jobs = gap_wheels.plan(BUILDS, 2)
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.plan = Path(self.tmp.name) / "plan.json"
        self.plan.write_text(json.dumps(self.jobs))
        self.calls = []
        sleep = mock.patch.object(gap_wheels.time, "sleep", lambda s: None)
        sleep.start()
        self.addCleanup(sleep.stop)

    def run_dispatch(self, tips, runs=list):
        def gh(args):
            self.calls.append(args)
            return (next(tips) + "\n").encode() if args[0] == "api" else b""

        with mock.patch.object(gap_wheels, "gh", gh), mock.patch.object(gap_wheels, "list_runs", lambda r, s: runs()):
            gap_wheels.main(["dispatch", "--plan", str(self.plan), "--batch", "99-1", "--repo", "o/r", "--sha", SHA,
                             "--since", "2026-11-16T02:00:00Z", "--parallel", "2"])  # fmt: skip

    def started(self):
        return [c for c in self.calls if c[:2] == ["workflow", "run"]]

    def test_starts_one_run_per_build_from_main_with_the_plans_values(self):
        self.run_dispatch(iter([SHA] * 4))
        started = self.started()
        self.assertEqual(len(started), 4)
        self.assertIn("main", started[0])
        self.assertIn("target=3.14t win-arm64", started[0])
        self.assertIn("batch=99-1", started[0])

    def test_stops_loudly_when_main_moves(self):
        with self.assertRaises(SystemExit) as raised:
            self.run_dispatch(iter([SHA, SHA, "2" * 40]))
        self.assertIn("after 2 of 4", str(raised.exception))
        self.assertEqual(len(self.started()), 2)

    def test_waits_while_the_batch_has_parallel_builds_running(self):
        busy = [{"display_title": "gap-wheel 99-1 a 1 t build 1", "status": "in_progress"}] * 2
        polls = iter([[], [], busy, busy, [], [], []])
        self.run_dispatch(iter([SHA] * 4), runs=lambda: next(polls))
        self.assertEqual(len(self.started()), 4)

    def test_a_start_that_failed_but_made_the_run_isnt_repeated(self):
        made = []

        def gh(args):
            if args[:2] == ["workflow", "run"]:
                made.append(args)
                raise subprocess.CalledProcessError(502, "gh")
            return (SHA + "\n").encode()

        def runs(repo, since):
            return [{"display_title": gap_wheels.title("99-1", j), "status": "queued"} for j in self.jobs[: len(made)]]

        with mock.patch.object(gap_wheels, "gh", gh), mock.patch.object(gap_wheels, "list_runs", runs):
            gap_wheels.main(["dispatch", "--plan", str(self.plan), "--batch", "99-1", "--repo", "o/r", "--sha", SHA,
                             "--since", "2026-11-16T02:00:00Z", "--parallel", "10"])  # fmt: skip
        self.assertEqual(len(made), 4)  # one POST per build, none repeated

    def test_retry(self):
        calls = iter([subprocess.CalledProcessError(1, "gh"), b"ok"])

        def flaky():
            value = next(calls)
            if isinstance(value, Exception):
                raise value
            return value

        self.assertEqual(gap_wheels.retry(flaky), b"ok")
        with self.assertRaises(subprocess.CalledProcessError):
            gap_wheels.retry(lambda: (_ for _ in ()).throw(subprocess.CalledProcessError(1, "gh")), attempts=2)


class Rotation(unittest.TestCase):
    def test_a_capped_batch_reaches_every_pair_in_turn(self):
        names = lambda start: [j["name"] for j in gap_wheels.plan(BUILDS, 1, start=start) if j["attempt"] == 1]
        everyone = {p["name"] for p in gap_wheels.clean_pairs(BUILDS)}
        self.assertEqual({n for week in range(len(everyone)) for n in names(week)}, everyone)
        self.assertEqual(names(0), names(len(everyone)))  # wraps around

    def test_members_too_big_to_list_still_leave_the_result(self):
        def fake_run_build(name, version, attempt, work, members, keep=None):
            members.update({f"pkg/f{i}.py": "a" * 64 for i in range(gap_wheels.MAX_MEMBERS + 1)})
            return {"sdist": None, "sdist_sha256": None, "wheel": None, "sha256": None, "installs": None}

        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "result.json"
            with (
                mock.patch.object(gap_wheels, "run_build", fake_run_build),
                mock.patch.object(gap_wheels.build_lab, "interpreter_problem", lambda target: None),
            ):
                gap_wheels.main(["build", "--name", "tiktoken", "--version", "0.14.0", "--target", "3.14 win-arm64",
                                 "--attempt", "1", "--runner", "windows-11-arm", "--out", str(out)])  # fmt: skip
            self.assertEqual(json.loads(out.read_text())["name"], "tiktoken")
            self.assertEqual(json.loads((Path(tmp) / "members.json").read_text()), {})



# --- stage, install-test and publish -------------------------------------------------------------------------------

WIN = "tiktoken-0.14.0-cp314-cp314-win_arm64.whl"


def verified(name="tiktoken", wheel=WIN, sha="c" * 64, target="3.14 win-arm64"):
    return {"name": name, "version": "0.14.0", "target": target, "wheel": wheel, "sha256": sha,
            "sdist": f"{name}-0.14.0.tar.gz", "sdist_sha256": "a" * 64,
            "builds": ["https://github.com/wheelgap/tracker/actions/runs/11",
                       "https://github.com/wheelgap/tracker/actions/runs/12"]}  # fmt: skip


MIT = {"license_expression": "MIT", "requires_python": ">=3.9"}


def zipped(members):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for name, data in members.items():
            z.writestr(name, data)
    return buf.getvalue()


class Stage(unittest.TestCase):
    def test_only_windows_wheels_with_a_licence_and_no_opt_out(self):
        self.assertIsNone(gap_wheels.stage_reason(verified(), MIT, set()))
        linux = verified(wheel="lz4-4.4.5-cp314-cp314-linux_aarch64.whl", target="3.14 musl-aarch64")
        self.assertIn("auditwheel", gap_wheels.stage_reason(linux, MIT, set()))
        self.assertEqual(gap_wheels.stage_reason(verified(), MIT, {"tiktoken"}), "the project opted out")
        self.assertEqual(gap_wheels.stage_reason(verified(), None, set()), "couldn't ask PyPI for the licence")
        self.assertIn("licence", gap_wheels.stage_reason(verified(), {"license": "see LICENSE"}, set()))
        plus = verified(wheel="tiktoken-0.14.0+local-cp314-cp314-win_arm64.whl")
        self.assertIn("release asset", gap_wheels.stage_reason(plus, MIT, set()))

    def test_only_the_targets_one_exact_tag(self):
        self.assertTrue(gap_wheels.tag_ok("x-1.0-cp314-cp314-win_arm64.whl", "3.14 win-arm64"))
        self.assertTrue(gap_wheels.tag_ok("x-1.0-cp314-cp314t-win_arm64.whl", "3.14t win-arm64"))
        for wheel, target in (
            ("x-1.0-cp314-cp314-win_arm64.win_amd64.whl", "3.14 win-arm64"),  # an ARM64 .pyd on x64
            ("x-1.0-cp314-cp314-win_arm64.linux_aarch64.whl", "3.14 win-arm64"),
            ("x-1.0-cp38-abi3-win_arm64.whl", "3.14 win-arm64"),  # other Pythons would take it untested
            ("x-1.0-py3-none-win_arm64.whl", "3.14 win-arm64"),
            ("x-1.0-cp314-cp314-win_arm64.whl", "3.14t win-arm64"),
            ("x-1.0-cp314-cp314t-linux_x86_64.whl", "3.14t linux-x86_64"),
            ("x-1.0-cp314-cp314-WIN_ARM64.whl", "3.14 win-arm64"),
        ):
            with self.subTest(wheel):
                self.assertFalse(gap_wheels.tag_ok(wheel, target))

    def test_licence(self):
        self.assertEqual(gap_wheels.licence({"license_expression": "Apache-2.0 OR MIT"}), "Apache-2.0 OR MIT")
        self.assertIsNone(gap_wheels.licence({"license_expression": "LicenseRef-Proprietary"}))
        self.assertIsNone(gap_wheels.licence({"license_expression": "MIT<script>"}))
        for not_osi in ("BUSL-1.1", "CC-BY-NC-4.0", "SSPL-1.0", "Elastic-2.0", "MIT AND BUSL-1.1", "CC0-1.0",
                        "MIT WITH BUSL-1.1", "Apache-2.0 WITH LicenseRef-NC", "AND", "MIT AND", "(MIT", "MIT)",
                        "MIT OR OR Apache-2.0"):  # fmt: skip
            with self.subTest(not_osi):
                self.assertIsNone(gap_wheels.licence({"license_expression": not_osi}))
        for osi in ("(MIT OR Apache-2.0) AND BSD-3-Clause", "GPL-2.0-or-later WITH Classpath-exception-2.0",
                    "apache-2.0", "LGPL-2.1+"):  # fmt: skip
            with self.subTest(osi):
                self.assertEqual(gap_wheels.licence({"license_expression": osi}), osi)
        osi = {"classifiers": ["License :: OSI Approved :: BSD License", "Programming Language :: Python"]}
        self.assertEqual(gap_wheels.licence(osi), "BSD License")
        # The legacy field: one line, an SPDX expression or an unambiguous name.
        self.assertEqual(gap_wheels.licence({"license": "BSD-3-Clause", "classifiers": []}), "BSD-3-Clause")
        self.assertEqual(gap_wheels.licence({"license": " Apache License 2.0 "}), "Apache-2.0")
        for name, spdx in gap_wheels.LICENCE_NAMES.items():
            self.assertEqual(gap_wheels.licence({"license": name.upper()}), spdx)
        self.assertEqual(gap_wheels.licence({"license": "MIT\tOR  Apache-2.0"}), "MIT OR Apache-2.0")
        self.assertEqual(gap_wheels.licence({"license_expression": "MIT AND PSF-2.0"}), "MIT AND PSF-2.0")
        for refused in ("MIT License\n\nCopyright (c) 2022 Someone\nPermission is hereby granted...",
                        "Apache License\n  Version 2.0, January 2004", "BSD", "Proprietary", "",
                        "see LICENSE", "MIT-ish", "x" * 150, "MIT License, no commercial use",
                        "PSF-2.0 OR Proprietary", "MIT/Apache-2.0"):  # fmt: skip
            with self.subTest(refused):
                self.assertIsNone(gap_wheels.licence({"license": refused}))
        self.assertIsNone(gap_wheels.licence("nope"))

    def test_unwrap_takes_only_the_verified_bytes(self):
        data = b"wheel bytes"
        sha = hashlib.sha256(data).hexdigest()
        self.assertEqual(gap_wheels.unwrap_wheel(zipped({WIN: data}), WIN, sha), data)
        self.assertIsNone(gap_wheels.unwrap_wheel(zipped({WIN: b"other"}), WIN, sha))  # substituted bytes
        self.assertIsNone(gap_wheels.unwrap_wheel(zipped({WIN: data, "extra.txt": b""}), WIN, sha))
        self.assertIsNone(gap_wheels.unwrap_wheel(zipped({"other.whl": data}), WIN, sha))
        self.assertIsNone(gap_wheels.unwrap_wheel(b"not a zip", WIN, sha))

    def test_stage_writes_the_files_and_outputs(self):
        data = b"wheel bytes"
        good_one = verified(sha=hashlib.sha256(data).hexdigest())
        linux = verified(name="lz4", wheel="lz4-4.4.5-cp314-cp314-linux_aarch64.whl", target="3.14 musl-aarch64")
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            (tmp / "m.json").write_text(json.dumps({"date": "2026-11-16", "run": "R", "wheels": [good_one, linux]}))
            out = tmp / "out.txt"
            args = gap_wheels.main.__globals__["argparse"].Namespace(
                manifest=str(tmp / "m.json"), staged=str(tmp / "staged"), repo="wheelgap/tracker",
                optout=str(tmp / "none"), summary=None, github_output=str(out))  # fmt: skip
            with mock.patch.object(gap_wheels, "pypi_info", return_value=MIT), \
                 mock.patch.object(gap_wheels, "missing_dlls", return_value=[]), \
                 mock.patch.object(gap_wheels, "fetch_wheel", return_value=zipped({WIN: data})) as fetch:  # fmt: skip
                gap_wheels.cmd_stage(args)
            fetch.assert_called_once_with("wheelgap/tracker", 11)  # build 1's run
            self.assertEqual((tmp / "staged" / WIN).read_bytes(), data)
            lines = dict(line.split("=", 1) for line in out.read_text().splitlines())
            staged = json.loads(lines["staged"])
            self.assertEqual([w["wheel"] for w in staged], [WIN])
            self.assertEqual(staged[0]["licence"], "MIT")
            self.assertEqual(staged[0]["requires_python"], ">=3.9")
            self.assertEqual(gap_wheels.trusted_staged(lines["staged"]), staged)
            self.assertEqual(json.loads(lines["tests"]), [{"target": "3.14 win-arm64", "runner": "windows-11-arm",
                                                           "python": "3.14", "slug": "3.14-win-arm64"}])  # fmt: skip

    def test_a_build_run_with_other_bytes_falls_back_to_the_other_build(self):
        data = b"wheel bytes"
        one = verified(sha=hashlib.sha256(data).hexdigest())
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            (tmp / "m.json").write_text(json.dumps({"wheels": [one]}))
            args = gap_wheels.main.__globals__["argparse"].Namespace(
                manifest=str(tmp / "m.json"), staged=str(tmp / "staged"), repo="r/r", optout=str(tmp / "none"),
                summary=None, github_output=None)  # fmt: skip
            archives = {11: zipped({WIN: b"forged"}), 12: zipped({WIN: data})}
            with mock.patch.object(gap_wheels, "pypi_info", return_value=MIT), \
                 mock.patch.object(gap_wheels, "missing_dlls", return_value=[]), \
                 mock.patch.object(gap_wheels, "fetch_wheel", side_effect=lambda repo, rid: archives[rid]):  # fmt: skip
                gap_wheels.cmd_stage(args)
            self.assertEqual((tmp / "staged" / WIN).read_bytes(), data)


class Publish(unittest.TestCase):
    def staged(self, tmp, data=b"wheel bytes"):
        w = {**verified(sha=hashlib.sha256(data).hexdigest()), "licence": "MIT", "requires_python": None,
             "verified": "2026-11-16", "batch": "R"}  # fmt: skip
        (tmp / "files").mkdir(exist_ok=True)
        (tmp / "files" / WIN).write_bytes(data)
        return w

    def mark_tested(self, tmp, target="3.14 win-arm64", ok=True, slug="3.14-win-arm64"):
        (tmp / "tested" / f"tested-{slug}").mkdir(parents=True, exist_ok=True)
        (tmp / "tested" / f"tested-{slug}" / "tested.json").write_text(json.dumps({"target": target, "tested": {WIN: ok}}))

    def publish(self, tmp, staged, optout=""):
        (tmp / "optout.txt").write_text(optout)
        args = gap_wheels.main.__globals__["argparse"].Namespace(
            staged_json=json.dumps(staged), files=str(tmp / "files"), tested=str(tmp / "tested"),
            index=str(tmp / "index.json"), release_dir=str(tmp / "release"), optout=str(tmp / "optout.txt"),
            run="RUN", summary=None)  # fmt: skip
        gap_wheels.cmd_publish(args)
        return json.loads((tmp / "index.json").read_text())

    def test_a_tested_verified_wheel_publishes(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            w = self.staged(tmp)
            self.mark_tested(tmp)
            index = self.publish(tmp, [w])
            self.assertEqual([x["wheel"] for x in index["wheels"]], [WIN])
            self.assertEqual(index["wheels"][0]["publish_run"], "RUN")
            self.assertTrue((tmp / "release" / WIN).is_file())
            # Again: already published, nothing new to upload.
            (tmp / "release" / WIN).unlink()
            index = self.publish(tmp, [w])
            self.assertEqual(len(index["wheels"]), 1)
            self.assertFalse((tmp / "release" / WIN).exists())

    def test_untested_failed_or_swapped_wheels_dont_publish(self):
        for case in ("untested", "failed", "swapped", "two answers"):
            with self.subTest(case), tempfile.TemporaryDirectory() as tmp:
                tmp = Path(tmp)
                w = self.staged(tmp)
                if case == "failed":
                    self.mark_tested(tmp, ok=False)
                elif case == "swapped":
                    self.mark_tested(tmp)
                    (tmp / "files" / WIN).write_bytes(b"forged by a test job")
                elif case == "two answers":
                    self.mark_tested(tmp)
                    self.mark_tested(tmp, slug="forged")
                index = self.publish(tmp, [w])
                self.assertEqual(index["wheels"], [])
                self.assertFalse((tmp / "release" / WIN).exists())

    def test_only_the_staging_jobs_output_is_trusted(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            w = self.staged(tmp)
            for bad in ([{**w, "wheel": "../x.whl"}], [{**w, "extra": 1}], [{**w, "sha256": "zz"}],
                        [{**w, "wheel": "lz4-1-cp314-cp314-linux_aarch64.whl"}], {"not": "a list"}):  # fmt: skip
                with self.subTest(bad), self.assertRaises(SystemExit):
                    gap_wheels.trusted_staged(json.dumps(bad))

    def test_an_opted_out_project_is_withdrawn(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            w = self.staged(tmp)
            self.mark_tested(tmp)
            self.publish(tmp, [w])
            index = self.publish(tmp, [], optout="# comment\nTikToken\n")
            self.assertEqual(index["wheels"], [])
            self.assertEqual((tmp / "release" / "withdraw.txt").read_text(), f"{WIN}\n")


class KeepTheWheel(unittest.TestCase):
    def test_build_passes_the_wheel_dir_through(self):
        seen = {}

        def fake_run_build(name, version, attempt, work, members, keep=None):
            seen["keep"] = keep
            return {"sdist": None, "sdist_sha256": None, "wheel": None, "sha256": None, "installs": None}

        with tempfile.TemporaryDirectory() as tmp:
            with (
                mock.patch.object(gap_wheels, "run_build", fake_run_build),
                mock.patch.object(gap_wheels.build_lab, "interpreter_problem", lambda target: None),
            ):
                gap_wheels.main(["build", "--name", "tiktoken", "--version", "0.14.0", "--target", "3.14 win-arm64",
                                 "--attempt", "2", "--runner", "windows-11-arm", "--out", str(Path(tmp) / "r.json"),
                                 "--wheel-dir", str(Path(tmp) / "wheel")])  # fmt: skip
        self.assertEqual(seen["keep"], Path(tmp) / "wheel")


def tiny_wheel(folder, body):
    """A pure wheel pip installs offline: demo-1.0, one module whose import runs `body`."""
    path = folder / "demo-1.0-py3-none-any.whl"
    files = {"demo.py": body, "demo-1.0.dist-info/METADATA": "Metadata-Version: 2.1\nName: demo\nVersion: 1.0\n",
             "demo-1.0.dist-info/WHEEL": "Wheel-Version: 1.0\nGenerator: test\nRoot-Is-Purelib: true\nTag: py3-none-any\n"}  # fmt: skip
    record = "".join(f"{n},,\n" for n in files) + "demo-1.0.dist-info/RECORD,,\n"
    with zipfile.ZipFile(path, "w") as z:
        for name, text in files.items():
            z.writestr(name, text)
        z.writestr("demo-1.0.dist-info/RECORD", record)
    return path


class InstallAndImport(unittest.TestCase):
    def test_a_wheel_that_imports_passes_and_one_that_raises_doesnt(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            (tmp / "ok").mkdir()
            (tmp / "bad").mkdir()
            ok = tiny_wheel(tmp / "ok", "VALUE = 1\n")
            bad = tiny_wheel(tmp / "bad", "raise RuntimeError('broken on import')\n")
            self.assertIsNone(gap_wheels.install_and_import(ok, "3.14 win-arm64", tmp / "w1"))
            self.assertEqual(gap_wheels.install_and_import(bad, "3.14 win-arm64", tmp / "w2"), "the top import check failed")


def pe(dlls, delay=()):
    """A minimal PE32+ file importing `dlls` (and delay-loading `delay`): what pe_imports reads, nothing more."""
    raw, va, size = 0x200, 0x1000, 0x400
    section = bytearray(size)
    names_at = 0x200
    imp_desc, delay_desc = 0, 0x100
    for n, name in enumerate(dlls):
        rva = va + names_at
        section[names_at : names_at + len(name) + 1] = name.encode() + b"\0"
        names_at += len(name) + 1
        section[imp_desc + n * 20 + 12 : imp_desc + n * 20 + 16] = rva.to_bytes(4, "little")
    for n, name in enumerate(delay):
        rva = va + names_at
        section[names_at : names_at + len(name) + 1] = name.encode() + b"\0"
        names_at += len(name) + 1
        section[delay_desc + n * 32 + 4 : delay_desc + n * 32 + 8] = rva.to_bytes(4, "little")
    head = bytearray(raw)
    head[0:2] = b"MZ"
    head[0x3C:0x40] = (0x40).to_bytes(4, "little")
    head[0x40:0x44] = b"PE\0\0"
    head[0x46:0x48] = (1).to_bytes(2, "little")  # one section
    head[0x54:0x56] = (240).to_bytes(2, "little")  # SizeOfOptionalHeader
    opt = 0x58
    head[opt : opt + 2] = (0x20B).to_bytes(2, "little")
    dirs = opt + 112
    head[dirs - 4 : dirs] = (16).to_bytes(4, "little")
    if dlls:
        head[dirs + 8 : dirs + 12] = (va + imp_desc).to_bytes(4, "little")
    if delay:
        head[dirs + 8 * 13 : dirs + 8 * 13 + 4] = (va + delay_desc).to_bytes(4, "little")
    sec = opt + 240
    head[sec : sec + 8] = b".rdata\0\0"
    head[sec + 8 : sec + 12] = size.to_bytes(4, "little")
    head[sec + 12 : sec + 16] = va.to_bytes(4, "little")
    head[sec + 16 : sec + 20] = size.to_bytes(4, "little")
    head[sec + 20 : sec + 24] = raw.to_bytes(4, "little")
    return bytes(head + section)


class Dlls(unittest.TestCase):
    def test_reads_imports_and_delay_loads(self):
        data = pe(["KERNEL32.dll", "python314.dll"], delay=["MSVCP140.dll"])
        self.assertEqual(gap_wheels.pe_imports(data), {"kernel32.dll", "python314.dll", "msvcp140.dll"})

    def test_garbage_and_truncated_files_are_unreadable(self):
        good_pe = pe(["kernel32.dll"])
        for bad in (b"", b"MZ", b"not a pe" * 100, good_pe[:0x150], good_pe[:0x210]):
            with self.subTest(len(bad)):
                self.assertIsNone(gap_wheels.pe_imports(bad))

    def test_a_wheel_needing_msvcp140_isnt_self_contained(self):
        system = pe(["python314.dll", "VCRUNTIME140.dll", "api-ms-win-crt-runtime-l1-1-0.dll", "KERNEL32.dll", "pdh.dll"])
        self.assertEqual(gap_wheels.missing_dlls(zipped({"pkg/_ext.cp314-win_arm64.pyd": system})), [])
        cpp = pe(["python314.dll", "MSVCP140.dll", "vcomp140.dll"])
        self.assertEqual(gap_wheels.missing_dlls(zipped({"pkg/_ext.pyd": cpp})), ["msvcp140.dll", "vcomp140.dll"])
        beside = zipped({"pkg/_ext.pyd": cpp, "pkg/msvcp140.dll": system, "pkg/vcomp140.dll": system})
        self.assertEqual(gap_wheels.missing_dlls(beside), [])
        # Elsewhere in the wheel isn't where Windows looks for the .pyd's DLLs.
        elsewhere = zipped({"pkg/_ext.pyd": cpp, "junk/msvcp140.dll": system, "pkg/vcomp140.dll": system})
        self.assertEqual(gap_wheels.missing_dlls(elsewhere), ["msvcp140.dll"])
        many = zipped({**{f"pkg/m{i}.pyd": system for i in range(500)}, "pkg/last.pyd": cpp})
        self.assertIsNone(gap_wheels.missing_dlls(many))  # too many to check: refused, not half-checked
        self.assertIsNone(gap_wheels.missing_dlls(zipped({"pkg/_ext.pyd": b"MZ junk"})))

    def test_an_unreadable_wheel_costs_only_itself(self):
        # A corrupt LZMA member raises lzma.LZMAError, which nothing expects: that wheel is skipped, the batch goes on.
        with mock.patch.object(gap_wheels, "fetch_wheel", return_value=b"x"), \
             mock.patch.object(gap_wheels, "unwrap_wheel", return_value=b"bytes"), \
             mock.patch.object(gap_wheels, "missing_dlls", side_effect=__import__("lzma").LZMAError("corrupt")):  # fmt: skip
            with tempfile.TemporaryDirectory() as tmp:
                tmp = Path(tmp)
                (tmp / "m.json").write_text(json.dumps({"wheels": [verified()]}))
                args = gap_wheels.main.__globals__["argparse"].Namespace(
                    manifest=str(tmp / "m.json"), staged=str(tmp / "staged"), repo="r/r", optout=str(tmp / "none"),
                    summary=None, github_output=None)  # fmt: skip
                with mock.patch.object(gap_wheels, "pypi_info", return_value=MIT):
                    self.assertEqual(gap_wheels.cmd_stage(args), 0)
                self.assertEqual(json.loads((tmp / "staged" / "staged.json").read_text()), [])


if __name__ == "__main__":
    unittest.main()
