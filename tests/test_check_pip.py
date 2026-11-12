"""Offline checks for check_pip.py: a fake pip (or a fake subprocess.run) and fake tags, so no network and no
wheelgap install."""

import io
import json
import os
import subprocess
import sys
import tempfile
import types
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

from scripts import check_pip

TARGETS = [
    {"label": "3.13 linux-x86_64", "python": "3.13", "platform": "linux-x86_64", "glibc": "2.36"},
    {"label": "3.13 win-arm64", "python": "3.13", "platform": "win-arm64"},
    {"label": "3.14t musl-aarch64", "python": "3.14t", "platform": "musl-aarch64", "musl": "1.2"},
]
TAGS = {
    "3.13 linux-x86_64": ["manylinux_2_36_x86_64", "manylinux_2_35_x86_64"],
    "3.13 win-arm64": ["win_arm64"],
    "3.14t musl-aarch64": ["musllinux_1_2_aarch64", "musllinux_1_1_aarch64"],
}


def package(rank, name, results, error=None, version="1.0", pure=False):
    return {"rank": rank, "name": name, "version": version, "error": error, "pure": pure, "results": results}


def scan(packages):
    return {
        "schema": 1,
        "generated": "2026-11-12T03:20:00+00:00",
        "wheelgap": "0.9.0",
        "targets": TARGETS,
        "packages": packages,
    }


class FakePip:
    """Picks a wheel for the (name, first platform tag) pairs in `wheels`; None in `wheels` is a pip error."""

    def __init__(self, wheels):
        self.wheels, self.calls = wheels, []

    def __call__(self, name, args):
        version = args[-1].split("==")[1]
        assert args[-1] == f"{name}=={version}"
        platform = args[args.index("--platform") + 1]
        self.calls.append(args)
        answer = self.wheels.get((name, platform), False)
        if answer is None:
            return None, "pip: connection reset"
        return (f"{name}-{version}-py3-none-any.whl", None) if answer else (None, None)


def run(packages, wheels, labels=None, top=200):
    pip = FakePip(wheels)
    with mock.patch.object(check_pip, "pip_version", return_value="26.3"):
        result = check_pip.agreement(
            scan(packages), top, labels or [t["label"] for t in TARGETS], pip, lambda t: TAGS[t["label"]], workers=2
        )
    return result, pip


class AgreementTests(unittest.TestCase):
    def test_matches_and_mismatches(self):
        packages = [
            package(1, "everywhere", ["wheel", "wheel", "wheel"], pure=True),
            # wheelgap says source on win-arm64, but pip finds a wheel; and wheel on Linux where pip finds none.
            package(2, "disputed", ["wheel", "source", "missing"]),
        ]
        wheels = {
            ("everywhere", "manylinux_2_36_x86_64"): True,
            ("everywhere", "win_arm64"): True,
            ("everywhere", "musllinux_1_2_aarch64"): True,
            ("disputed", "win_arm64"): True,
        }
        result, _ = run(packages, wheels)
        self.assertEqual((result["checks"], result["matched"], result["skipped"], result["errors"]), (6, 4, 0, 0))
        self.assertEqual(result["compiled"], {"packages": 1, "checks": 3, "matched": 1})
        self.assertEqual(result["packages"], 2)
        self.assertEqual(result["pip"], "26.3")
        self.assertEqual(result["wheelgap"], "0.9.0")
        self.assertEqual(result["scan"], "2026-11-12T03:20:00+00:00")
        self.assertEqual(
            result["mismatches"],
            [
                {
                    "name": "disputed",
                    "version": "1.0",
                    "target": "3.13 linux-x86_64",
                    "wheelgap": "wheel",
                    "pip": "no wheel",
                    "file": None,
                },
                {
                    "name": "disputed",
                    "version": "1.0",
                    "target": "3.13 win-arm64",
                    "wheelgap": "source",
                    "pip": "wheel",
                    "file": "disputed-1.0-py3-none-any.whl",
                },
            ],
        )

    def test_source_and_missing_both_count_as_no_wheel(self):
        result, _ = run([package(1, "sdistonly", ["source", "missing", "source"])], {})
        self.assertEqual((result["checks"], result["matched"]), (3, 3))

    def test_unknown_failed_lookups_and_pip_errors_are_not_checks(self):
        packages = [package(rank, f"ok{rank}", ["source"] * 3) for rank in range(3, 10)]
        packages += [
            package(1, "flaky", ["unknown", "wheel", "wheel"]),
            # A lookup error with a version still isn't checked.
            package(2, "gone", ["unknown"] * 3, error="lookup failed"),
            package(10, "noversion", ["unknown"] * 3, version=None),
        ]
        wheels = {("flaky", "win_arm64"): True, ("flaky", "musllinux_1_2_aarch64"): None}
        result, pip = run(packages, wheels)
        self.assertEqual((result["checks"], result["matched"], result["skipped"], result["errors"]), (22, 22, 7, 1))
        self.assertEqual(result["packages"], 8)
        called = {args[-1] for args in pip.calls}
        self.assertNotIn("gone==1.0", called)  # pip isn't asked about failed lookups or unknown cells
        self.assertEqual(len(pip.calls), 23)

    def test_too_many_errors_writes_nothing(self):
        # One answer and 799 outages must not become "100% of 1 checks".
        packages = [package(rank, f"pkg{rank}", ["source"] * 3) for rank in range(1, 21)]
        wheels = {(f"pkg{rank}", "win_arm64"): None for rank in range(1, 21)}
        with self.assertRaisesRegex(check_pip.TooManyErrors, "couldn't answer 20 of 60"):
            run(packages, wheels)

    def test_errors_up_to_five_percent_are_allowed(self):
        packages = [package(rank, f"pkg{rank}", ["source"] * 3) for rank in range(1, 21)]
        result, _ = run(
            packages, {("pkg1", "win_arm64"): None, ("pkg2", "win_arm64"): None, ("pkg3", "win_arm64"): None}
        )
        self.assertEqual((result["checks"], result["errors"]), (57, 3))  # 3 of 60 is exactly 5%

    def test_top_by_rank_and_chosen_targets(self):
        packages = [package(rank, f"pkg{rank}", ["source"] * 3) for rank in (5, 1, 3)]
        result, pip = run(packages, {}, labels=["3.13 win-arm64"], top=2)
        self.assertEqual(sorted(args[-1] for args in pip.calls), ["pkg1==1.0", "pkg3==1.0"])  # workers: any order
        self.assertEqual((result["targets"], result["top"], result["checks"]), (["3.13 win-arm64"], 2, 2))

    def test_unknown_target_is_an_error(self):
        with self.assertRaisesRegex(ValueError, "not in the scan: 3.13 pyodide"):
            run([], {}, labels=["3.13 pyodide"])

    def test_pip_args(self):
        target, tags = TARGETS[2], TAGS["3.14t musl-aarch64"]
        self.assertEqual(
            check_pip.pip_args("orjson", "3.11.4", target, tags),
            [
                *("install", "--dry-run", "--ignore-installed", "--no-deps", "--only-binary=:all:"),
                *("--index-url", "https://pypi.org/simple"),
                *("--platform", "musllinux_1_2_aarch64", "--platform", "musllinux_1_1_aarch64"),
                *("--python-version", "3.14", "--implementation", "cp", "--abi", "cp314t", "orjson==3.11.4"),
            ],
        )


class WheelgapTagsTests(unittest.TestCase):
    """wheelgap_tags with a stand-in wheelgap.targets, since the Check workflow doesn't install wheelgap."""

    def setUp(self):
        self.calls = []

        def build_targets(pythons, platforms, **versions):
            self.calls.append((pythons, platforms, versions))
            return [types.SimpleNamespace(platform_tags=lambda: ["tag_a", "tag_b"])]

        targets = types.ModuleType("wheelgap.targets")
        targets.PLATFORMS = {
            "linux-x86_64": ("manylinux", "x86_64"),
            "musl-aarch64": ("musllinux", "aarch64"),
            "win-arm64": ("windows", "win_arm64"),
            "pyodide": ("pyodide", "wasm32"),
        }
        targets.build_targets = build_targets
        package = types.ModuleType("wheelgap")
        package.targets = targets
        patcher = mock.patch.dict(sys.modules, {"wheelgap": package, "wheelgap.targets": targets})
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_keeps_the_scans_libc_versions(self):
        target = {"label": "3.13 linux-x86_64", "python": "3.13", "platform": "linux-x86_64", "glibc": "2.28"}
        target |= {"musl": "1.1", "macos": "13.0"}
        self.assertEqual(check_pip.wheelgap_tags(target), ["tag_a", "tag_b"])
        self.assertEqual(self.calls, [(["3.13"], ["linux-x86_64"], {"glibc": "2.28", "musl": "1.1", "macos": "13.0"})])

    def test_refuses_pyodide(self):
        with self.assertRaisesRegex(ValueError, "pip can't check 3.13 pyodide"):
            check_pip.wheelgap_tags({"label": "3.13 pyodide", "python": "3.13", "platform": "pyodide"})
        self.assertEqual(self.calls, [])


FETCHED = "Fetched page https://pypi.org/simple/my-pkg/ as application/vnd.pypi.simple.v1+json"
NO_MATCH = "ERROR: No matching distribution found for My_Pkg==1.0"


def report(*urls):
    return {"version": "1", "install": [{"download_info": {"url": url}} for url in urls]}


class RunPipTests(unittest.TestCase):
    """run_pip with subprocess.run faked: pip's exit code, its log, and the --report it writes."""

    def run_pip(self, returncode=0, stdout="", stderr="", report=None, timeout=False):
        self.calls = []

        def fake(cmd, **kwargs):
            self.calls.append((cmd, kwargs))
            if timeout:
                raise subprocess.TimeoutExpired(cmd, kwargs["timeout"])
            if report is not None:
                Path(cmd[cmd.index("--report") + 1]).write_text(json.dumps(report), encoding="utf-8")
            return subprocess.CompletedProcess(cmd, returncode, stdout, stderr)

        with mock.patch.object(check_pip.subprocess, "run", fake):
            return check_pip.run_pip("My_Pkg", check_pip.pip_args("My_Pkg", "1.0", TARGETS[1], ["win_arm64"]))

    def test_wheel(self):
        url = "https://files.pythonhosted.org/packages/ab/my_pkg-1.0-cp313-cp313-win_arm64.whl"
        self.assertEqual(
            self.run_pip(stdout=FETCHED, report=report(url)), ("my_pkg-1.0-cp313-cp313-win_arm64.whl", None)
        )
        cmd, _ = self.calls[0]
        self.assertTrue({"--isolated", "--no-cache-dir", "--disable-pip-version-check", "-vv"} <= set(cmd))
        self.assertFalse(Path(cmd[cmd.index("--report") + 1]).parent.exists())  # the report's folder is deleted

    def test_no_wheel_needs_the_project_page(self):
        self.assertEqual(self.run_pip(returncode=1, stdout=FETCHED, stderr=NO_MATCH), (None, None))

    def test_outages_and_other_failures_are_errors(self):
        cases = {
            "retry, then no match": (
                "WARNING: Retrying (Retry(total=4)) after connection broken by 'NewConnectionError': /simple/my-pkg/",
                NO_MATCH,
            ),
            "could not fetch (404, 429)": (
                "Could not fetch URL https://pypi.org/simple/my-pkg/: 429 Client Error - skipping",
                NO_MATCH,
            ),
            "fetched, but retried on the way": (f"{FETCHED}\nWARNING: Retrying (Retry(total=4)) after ...", NO_MATCH),
            "another project's page": ("Fetched page https://pypi.org/simple/other/ as text/html", NO_MATCH),
            "another failure": (FETCHED, "ERROR: Exception: something broke"),
        }
        for case, (stdout, stderr) in cases.items():
            with self.subTest(case):
                wheel, error = self.run_pip(returncode=1, stdout=stdout, stderr=stderr)
                self.assertIsNone(wheel)
                self.assertTrue(error)

    def test_timeout(self):
        self.assertEqual(self.run_pip(timeout=True), (None, f"pip timed out after {check_pip.PIP_TIMEOUT} s"))

    def test_unexpected_results_are_errors(self):
        cases = [
            (1, report("https://files.example/my_pkg-1.0-py3-none-any.whl")),  # failed, whatever the report says
            (0, None),  # succeeded without a report
            (0, report()),  # succeeded, installing nothing
            (0, report("https://files.example/my_pkg-1.0.tar.gz")),
            (0, report("https://files.example/a-1-py3-none-any.whl", "https://files.example/b-1-py3-none-any.whl")),
        ]
        for returncode, planned in cases:
            with self.subTest(returncode=returncode, report=planned):
                wheel, error = self.run_pip(returncode=returncode, stdout=FETCHED, report=planned)
                self.assertIsNone(wheel)
                self.assertTrue(error)

    def test_pip_environment_is_dropped(self):
        with mock.patch.dict(os.environ, {"PIP_INDEX_URL": "https://example.invalid/simple", "pip_retries": "0"}):
            self.run_pip(returncode=1, stdout=FETCHED, stderr=NO_MATCH)
        env = self.calls[0][1]["env"]
        self.assertEqual([key for key in env if key.upper().startswith("PIP_")], [])
        self.assertEqual(env.get("PATH"), os.environ.get("PATH"))


class WriteTests(unittest.TestCase):
    def test_writes_json_and_no_temporary_file(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "data" / "agreement.json"
            check_pip.write({"checks": 1}, path)
            self.assertEqual(json.loads(path.read_text(encoding="utf-8")), {"checks": 1})
            self.assertEqual([p.name for p in path.parent.iterdir()], ["agreement.json"])

    def test_a_failed_write_keeps_the_previous_file(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "agreement.json"
            path.write_text('{"checks": 1}\n', encoding="utf-8")
            with self.assertRaises(TypeError):
                check_pip.write({"checks": object()}, path)  # not JSON
            with mock.patch.object(Path, "replace", side_effect=OSError("disk full")), self.assertRaises(OSError):
                check_pip.write({"checks": 2}, path)
            self.assertEqual(path.read_text(encoding="utf-8"), '{"checks": 1}\n')
            self.assertEqual([p.name for p in path.parent.iterdir()], ["agreement.json"])


class MainTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.coverage = self.root / "coverage.json"
        self.coverage.write_text(json.dumps(scan([package(1, "one", ["source"] * 3)])), encoding="utf-8")
        self.out = self.root / "out" / "agreement.json"

    def main(self, installed="0.9.0", pip="26.3", agreement=None, argv=()):
        result = {"matched": 1, "checks": 1, "mismatches": [], "skipped": 0, "errors": 0}
        result["compiled"] = {"packages": 1, "checks": 1, "matched": 1}
        calls = []

        def fake_agreement(scan, top, labels, workers):
            calls.append((top, labels, workers))
            if isinstance(agreement, Exception):
                raise agreement
            return result

        stdout, stderr = io.StringIO(), io.StringIO()
        with (
            mock.patch.object(check_pip, "installed_wheelgap", return_value=installed),
            mock.patch.object(check_pip, "pip_version", return_value=pip),
            mock.patch.object(check_pip, "agreement", fake_agreement),
            redirect_stdout(stdout),
            redirect_stderr(stderr),
        ):
            try:
                code = check_pip.run([str(self.coverage), str(self.out), *argv])
            except SystemExit as e:
                code = e.code
        return code, stdout.getvalue(), stderr.getvalue(), calls

    def test_writes_the_result(self):
        code, out, _, calls = self.main(argv=["--top", "5", "--target", "3.13 win-arm64", "--workers", "3"])
        self.assertEqual(code, 0)
        self.assertEqual(calls, [(5, ["3.13 win-arm64"], 3)])
        self.assertEqual(json.loads(self.out.read_text(encoding="utf-8"))["checks"], 1)
        self.assertIn("matches pip on 1 of 1 checks", out)

    def test_default_targets(self):
        _, _, _, calls = self.main()
        self.assertEqual(calls, [(200, check_pip.DEFAULT_TARGETS, 8)])

    def test_refuses_another_wheelgap(self):
        code, _, err, calls = self.main(installed="0.8.0")
        self.assertEqual(code, 2)
        self.assertIn("made with wheelgap 0.9.0, but 0.8.0 is installed", err)
        self.assertEqual(calls, [])
        self.assertFalse(self.out.exists())

    def test_needs_wheelgap_and_pip(self):
        self.assertEqual(self.main(installed=None)[0], 2)
        self.assertEqual(self.main(pip=None)[0], 2)
        self.assertFalse(self.out.exists())

    def test_too_many_errors_exits_1_and_writes_nothing(self):
        code, _, err, _ = self.main(agreement=check_pip.TooManyErrors("pip couldn't answer 799 of 800 checks"))
        self.assertEqual(code, 1)
        self.assertIn("not written", err)
        self.assertFalse(self.out.exists())

    def test_a_crash_exits_2(self):
        code, _, err, _ = self.main(agreement=RuntimeError("boom"))
        self.assertEqual(code, 2)
        self.assertIn("RuntimeError: boom", err)
        self.assertFalse(self.out.exists())


if __name__ == "__main__":
    unittest.main()
