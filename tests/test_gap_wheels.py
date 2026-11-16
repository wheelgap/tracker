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


if __name__ == "__main__":
    unittest.main()
