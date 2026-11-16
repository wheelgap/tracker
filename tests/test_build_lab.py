"""Offline tests for scripts/build_lab.py: the plan, the merge job's checks on untrusted results, and the helpers."""

import json
import subprocess
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

from scripts import build_lab

TARGETS = ["3.14 win-arm64", "3.14t win-arm64", "3.14t linux-x86_64", "3.14 musl-aarch64", "3.14 linux-x86_64"]


def pkg(rank, name, version, statuses):
    return {"rank": rank, "name": name, "version": version, "pure": False, "error": None, "results": statuses}


COVERAGE = {
    "targets": [{"label": t} for t in TARGETS],
    "packages": [
        pkg(2, "tiktoken", "0.14.0", ["source", "source", "wheel", "wheel", "wheel"]),
        pkg(1, "cryptography", "50.0.2", ["source", "wheel", "wheel", "wheel", "wheel"]),
        pkg(3, "orjson", "3.13.0", ["wheel", "source", "source", "wheel", "wheel"]),
        pkg(4, "torch", "2.15.0", ["missing", "missing", "wheel", "missing", "wheel"]),  # no sdist: nothing to build
        pkg(5, "weird", "1.0; rm -rf /", ["source"] * 5),  # not a version: never planned
        pkg(6, "musly", "1.0", ["wheel", "wheel", "wheel", "source", "source"]),  # linux-x86_64 isn't a lab target
    ],
}

GOOD = {
    "schema": 1,
    "name": "tiktoken",
    "version": "0.14.0",
    "target": "3.14 win-arm64",
    "runner": "windows-11-arm",
    "python": "3.14.2",
    "outcome": "built",
    "seconds": 87,
    "wheel": "cp314-cp314-win_arm64",
    "extensions": 1,
    "import": "ok",
    "gil": None,
    "gil_module": None,
    "reason": None,
    "excerpt": "",
    "toolchains": ["cl"],
}
FAILED = dict(GOOD, outcome="failed", seconds=283, wheel=None, extensions=None, reason="needs OpenSSL",
              excerpt="error: OpenSSL headers not found", **{"import": None})  # fmt: skip
JOB = {"id": "0", "name": "tiktoken", "version": "0.14.0", "target": "3.14 win-arm64", "runner": "windows-11-arm",
       "python": "3.14", "rank": 2}  # fmt: skip
FT_JOB = dict(JOB, target="3.14t win-arm64", python="3.14t")
FT = dict(GOOD, target="3.14t win-arm64", python="3.14.2t", wheel="cp314-cp314t-win_arm64", gil="re-enabled")


def pairs(jobs):
    return [(j["name"], j["target"]) for j in jobs]


class Plan(unittest.TestCase):
    def test_most_downloaded_first_taking_turns_between_targets(self):
        jobs = build_lab.plan(COVERAGE, None, 10, "2026-11-15")
        self.assertEqual(
            pairs(jobs),
            [
                ("cryptography", "3.14 win-arm64"),
                ("tiktoken", "3.14t win-arm64"),
                ("orjson", "3.14t linux-x86_64"),
                ("musly", "3.14 musl-aarch64"),
                ("tiktoken", "3.14 win-arm64"),
                ("orjson", "3.14t win-arm64"),
            ],
        )
        self.assertEqual([j["id"] for j in jobs], [str(i) for i in range(6)])
        self.assertEqual(jobs[0]["runner"], "windows-11-arm")
        self.assertTrue(jobs[3]["image"].startswith("python:3.14-alpine@sha256:"))
        self.assertNotIn("image", jobs[0])

    def test_limit_and_recent_results(self):
        self.assertEqual(len(build_lab.plan(COVERAGE, None, 2, "2026-11-15")), 2)
        recent = {"results": [dict(GOOD, name="Cryptography", version="50.0.2", date="2026-10-01")]}
        self.assertNotIn(("cryptography", "3.14 win-arm64"), pairs(build_lab.plan(COVERAGE, recent, 10, "2026-11-15")))
        # After REFRESH_DAYS it's built again; a new release is built at once.
        self.assertIn(("cryptography", "3.14 win-arm64"), pairs(build_lab.plan(COVERAGE, recent, 10, "2027-03-01")))
        older = {"results": [dict(GOOD, name="cryptography", version="49.0.0", date="2026-11-14")]}
        self.assertIn(("cryptography", "3.14 win-arm64"), pairs(build_lab.plan(COVERAGE, older, 10, "2026-11-15")))


class Problems(unittest.TestCase):
    def test_good_results_pass(self):
        self.assertEqual(build_lab.problems(GOOD, JOB), [])
        self.assertEqual(build_lab.problems(FAILED, JOB), [])
        self.assertEqual(build_lab.problems(FT, FT_JOB), [])
        # A pure-Python fallback wheel: built and imported, no extension module, so no GIL answer.
        self.assertEqual(build_lab.problems(dict(FT, wheel="py3-none-any", extensions=0, gil=None), FT_JOB), [])
        timeout = dict(FAILED, outcome="timeout", seconds=2400, reason=None, excerpt="")
        self.assertEqual(build_lab.problems(timeout, JOB), [])

    def test_a_result_for_another_pair_is_rejected(self):
        # A build can rewrite its own result file: it must not speak for another package, version or target.
        for key, value in [("name", "cryptography"), ("version", "0.15.0"), ("target", "3.14t win-arm64"),
                           ("runner", "ubuntu-24.04")]:  # fmt: skip
            self.assertTrue(build_lab.problems(dict(GOOD, **{key: value}), JOB), key)

    def test_shape_types_and_vocabulary(self):
        bad = [
            dict(GOOD, extra=1),
            {k: v for k, v in GOOD.items() if k != "excerpt"},
            dict(GOOD, date="2026-11-15"),  # the merge job dates results itself
            dict(GOOD, outcome="great"),
            dict(GOOD, seconds=-1),
            dict(GOOD, seconds=True),
            dict(GOOD, seconds=10**9),
            dict(GOOD, **{"import": None}),  # built, but no import result
            dict(GOOD, wheel=None),
            dict(GOOD, wheel="cp314-cp314-linux_x86_64"),  # not a Windows on ARM wheel
            dict(GOOD, wheel="py3-none-any"),  # a pure wheel with an extension module
            dict(GOOD, extensions=-1),
            dict(FAILED, wheel="cp314-cp314-win_arm64"),  # a wheel from a build that failed
            dict(FAILED, **{"import": "ok"}),
            dict(GOOD, reason="needs OpenSSL"),  # a reason on a build that worked
            dict(FAILED, reason="made up"),
            dict(FAILED, reason=None),
            dict(GOOD, gil="kept-off"),  # not a free-threaded job
            dict(GOOD, excerpt="\x1b[31merror"),
            dict(GOOD, excerpt="é"),
            dict(GOOD, excerpt="x" * 401),
            dict(GOOD, toolchains=["rustc", "rustc"]),
            dict(GOOD, toolchains=["curl"]),
            dict(GOOD, python="3.13.1"),
            dict(GOOD, python="3.14.2t"),
            dict(GOOD, python="3.14.٢"),  # a non-ASCII digit
            dict(GOOD, schema=True),
            # Unhashable or odd values are rejected, never a crash.
            dict(GOOD, outcome=[]),
            dict(GOOD, **{"import": {}}),
            dict(GOOD, gil=["kept-off"]),
            dict(FAILED, reason={"x": 1}),
            dict(GOOD, toolchains=[["cl"]]),
            dict(GOOD, name=["tiktoken"]),
            "not an object",
        ]
        for result in bad:
            self.assertTrue(build_lab.problems(result, JOB), result)
        self.assertTrue(build_lab.problems(dict(FT, gil=None), FT_JOB))  # an extension imported, but no GIL answer
        self.assertTrue(build_lab.problems(dict(FT, gil="maybe"), FT_JOB))
        self.assertTrue(build_lab.problems(dict(FT, **{"import": "failed"}), FT_JOB))


class Merge(unittest.TestCase):
    def test_merge_keeps_one_entry_per_package_and_target_dated_by_the_merge(self):
        old = {
            "results": [
                dict(GOOD, version="0.13.0", date="2026-08-01"),
                dict(GOOD, name="orjson", version="3.13.0", date="2026-08-01"),
            ]
        }
        jobs = [JOB, dict(JOB, id="1", name="cryptography", version="50.0.2")]
        results = {"0": GOOD, "1": dict(GOOD, name="cryptography", version="49.0.0")}  # 1 lies about its version
        merged, notes = build_lab.merge(old, results, jobs, "2026-11-15")
        self.assertEqual({(r["name"], r["version"], r["date"]) for r in merged["results"]},
                         {("tiktoken", "0.14.0", "2026-11-15"), ("orjson", "3.13.0", "2026-08-01")})  # fmt: skip
        self.assertEqual(merged["updated"], "2026-11-15")
        self.assertEqual(len(notes), 1)
        self.assertIn("rejected", notes[0])
        missing, notes = build_lab.merge(None, {}, [JOB], "2026-11-15")
        self.assertEqual(missing["results"], [])
        self.assertIn("no result", notes[0])

    def test_a_hostile_result_file_is_rejected_not_fatal(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            files = {
                "0": b"[" * 5000 + b"]" * 5000,  # RecursionError when parsed
                "1": json.dumps(dict(GOOD, outcome=[])).encode(),  # unhashable
                "2": json.dumps(GOOD).encode(),
                "3": b"\xff\xfe not utf-8",
            }
            for i, data in files.items():
                (tmp / "results" / f"result-{i}").mkdir(parents=True)
                (tmp / "results" / f"result-{i}" / "result.json").write_bytes(data)
            (tmp / "results" / "result-9").mkdir()  # not in the plan: never read
            (tmp / "results" / "result-9" / "result.json").write_text(
                json.dumps(dict(GOOD, name="x")), encoding="utf-8"
            )
            jobs = [
                dict(JOB, id="0", name="a"),
                dict(JOB, id="1", name="b"),
                dict(JOB, id="2"),
                dict(JOB, id="3", name="c"),
            ]
            (tmp / "plan.json").write_text(json.dumps(jobs), encoding="utf-8")
            builds = tmp / "builds.json"
            self.assertEqual(build_lab.main(["merge", str(builds), str(tmp / "results"), str(tmp / "plan.json")]), 0)
            data = json.loads(builds.read_text(encoding="utf-8"))
            self.assertEqual([r["name"] for r in data["results"]], ["tiktoken"])
            self.assertTrue(build_lab.DATE.fullmatch(data["results"][0]["date"]))
            build_lab.plan(COVERAGE, data, 10, data["results"][0]["date"])  # a later plan reads it


class Helpers(unittest.TestCase):
    def test_classify_reads_error_lines(self):
        cases = {
            "error: failed to run custom build command: OpenSSL headers not found": "needs OpenSSL",
            "Cargo, the Rust package manager, is not installed or is not on PATH.": "needs a Rust toolchain",
            "error: Microsoft Visual C++ 14.0 or greater is required.": "needs a C/C++ compiler",
            "src/x.c:1:10: fatal error: libpq-fe.h: No such file or directory": "needs a system library",
            "x.c(12): error C2065: undeclared identifier": "compile error",
            "Using OpenSSL 3.0 from the system\nerror: linking failed": "compile error",  # a mention isn't a cause
            "it just stopped": "other",
        }
        for log, reason in cases.items():
            self.assertEqual(build_lab.classify(log), reason, log)

    def test_fetch_and_resolve_failures_arent_recorded(self):
        self.assertTrue(build_lab.UNRECORDED.search("Read timed out. (read timeout=15)"))
        self.assertFalse(build_lab.UNRECORDED.search("error: command 'cl.exe' failed with exit code 2"))
        own = "ERROR: Could not find a version that satisfies the requirement tiktoken==0.14.0 (from versions: none)"
        self.assertTrue(build_lab.unrecorded(own, "tiktoken", "0.14.0"))
        dep = "ERROR: Could not find a version that satisfies the requirement setuptools-rust>=1.12"
        self.assertFalse(build_lab.unrecorded(dep, "tiktoken", "0.14.0"))  # a missing build dependency is recorded
        self.assertTrue(build_lab.unrecorded("Read timed out.", "tiktoken", "0.14.0"))
        self.assertEqual(
            build_lab.classify(
                "error: subprocess-exited-with-error\ninstalling build dependencies for pyarrow did not run successfully."
            ),
            "build dependency failed",
        )

    def test_excerpt_is_short_printable_ascii(self):
        text = build_lab.excerpt("ok\n\x1b[31merror: café failed\x1b[0m\nerror: " + "x" * 1000)
        self.assertLessEqual(len(text), build_lab.MAX_EXCERPT)
        self.assertTrue(all(32 <= ord(c) < 127 for c in text))
        self.assertEqual(build_lab.problems(dict(FAILED, reason="other", excerpt=text), JOB), [])

    def test_wheel_modules_and_tag(self):
        with tempfile.TemporaryDirectory() as tmp:
            wheel = Path(tmp) / "grpcio-1.85.1-cp314-cp314t-linux_x86_64.whl"
            with zipfile.ZipFile(wheel, "w") as z:
                for name in ["grpc/__init__.py", "grpc/_cython/__init__.py",
                             "grpc/_cython/cygrpc.cpython-314t-x86_64-linux-gnu.so", "grpcio-1.85.1.dist-info/RECORD"]:  # fmt: skip
                    z.writestr(name, "")
            self.assertEqual(build_lab.wheel_modules(wheel), (["grpc", "grpc._cython", "grpc._cython.cygrpc"],
                                                              ["grpc._cython.cygrpc"], 1))  # fmt: skip
            self.assertEqual(build_lab.wheel_names(wheel),
                             {"modules": ["grpc._cython.cygrpc"], "packages": ["grpc", "grpc._cython"]})  # fmt: skip
            self.assertEqual(build_lab.wheel_tag(wheel), "cp314-cp314t-linux_x86_64")
            lib = Path(tmp) / "llvmlite-0.50.0-cp314-cp314-linux_x86_64.whl"  # a ctypes library, not a module
            with zipfile.ZipFile(lib, "w") as z:
                for name in ["llvmlite/__init__.py", "llvmlite/binding/__init__.py", "llvmlite/binding/libllvmlite.so"]:
                    z.writestr(name, "")
            self.assertEqual(build_lab.wheel_modules(lib), (["llvmlite", "llvmlite.binding"], [], 0))
            win = Path(tmp) / "x-1.0-cp314-cp314-win_arm64.whl"
            with zipfile.ZipFile(win, "w") as z:
                z.writestr("_x.cp314-win_arm64.pyd", "")
                z.writestr("y/z.abi3.so", "")
            self.assertEqual(build_lab.wheel_modules(win), (["_x", "y.z"], ["_x", "y.z"], 2))
            ns = Path(tmp) / "zope.interface-8.7-py3-none-any.whl"  # a namespace package: no top-level __init__
            with zipfile.ZipFile(ns, "w") as z:
                for name in [
                    "zope/interface/__init__.py",
                    "zope/interface/declarations.py",
                    "zope/interface/tests/t.py",
                ]:
                    z.writestr(name, "")
            self.assertEqual(build_lab.wheel_modules(ns), (["zope.interface", "zope.interface.declarations"], [], 0))
            # zope.interface's names, not zope.event's: a missing zope.event is a dependency, not the wheel's own.
            self.assertEqual(build_lab.wheel_names(ns), {"modules": ["zope.interface.declarations",
                                                                     "zope.interface.tests.t"],
                                                         "packages": ["zope.interface"]})  # fmt: skip
            tests = Path(tmp) / "greenlet-3.3-cp314-cp314t-win_arm64.whl"  # its test extension needs psutil
            with zipfile.ZipFile(tests, "w") as z:
                for name in ["greenlet/__init__.py", "greenlet/_greenlet.cp314t-win_arm64.pyd",
                             "greenlet/tests/_test_extension.cp314t-win_arm64.pyd"]:  # fmt: skip
                    z.writestr(name, "")
            self.assertEqual(build_lab.wheel_modules(tests), (["greenlet", "greenlet._greenlet"],
                                                              ["greenlet._greenlet"], 2))  # fmt: skip

    def test_import_check(self):
        # The check that runs on the lab's runners, here on fake packages. A module the wheel should have but doesn't
        # is a failure; one from outside the wheel (an optional or undeclared dependency) is left out.
        missing_stdlib = "fcntl" if sys.platform == "win32" else "msvcrt"  # in the stdlib, but not on this platform
        files = {
            "good/__init__.py": "",
            "good/opt.py": "import cffi_backend_not_here\n",
            "lost/__init__.py": "import lost._speedups\n",  # an extension the build left out
            "_ext.py": "",
            "zope/interface/__init__.py": "import zope.event_not_here\n",  # a namespace sibling: a dependency
            "clib.py": "import ruamel_not_here\n",
            "osonly.py": f"import {missing_stdlib}\n",
            "noname.py": "raise ModuleNotFoundError('no name given')\n",
            "linkerr.py": "raise ImportError('undefined symbol: foo')\n",
            "ctypeslib.py": "raise ImportError('dynamic module does not define module export function (PyInit_ctypeslib)')\n",
            # matplotlib's Tk backend on Alpine: _tkinter can't load libtk, and the backend's init fails with it.
            "tkshape.py": "try:\n    raise ImportError('Error loading shared library libtk8.6.so', name='_tkinter',"
            " path='/usr/local/lib/python3.14/lib-dynload/_tkinter.so')\n"
            "except ImportError as e:\n    raise ImportError('initialization failed') from e\n",
        }
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for name, text in files.items():
                (root / name).parent.mkdir(parents=True, exist_ok=True)
                (root / name).write_text(text)
            names = root / "names.json"
            names.write_text(json.dumps({"modules": ["good.opt", "_ext", "clib", "osonly", "noname",
                                                     "linkerr", "ctypeslib", "tkshape"],
                                         "packages": ["good", "lost", "zope.interface"]}))  # fmt: skip

            def run(mode, *modules):
                c = subprocess.run([sys.executable, "-c", build_lab.IMPORT_CHECK, mode, str(names), *modules],
                                   capture_output=True, text=True, cwd=root, check=False)  # fmt: skip
                line = [ln for ln in c.stdout.splitlines() if ln.startswith("WHEELGAP-LAB ")]
                return c.returncode, json.loads(line[0].removeprefix("WHEELGAP-LAB "))["loaded"] if line else None

            self.assertEqual(run("top", "good", "good.opt"), (0, ["good"]))
            self.assertEqual(run("top", "clib"), (0, []))
            self.assertEqual(run("top", "zope.interface"), (0, []))
            self.assertEqual(run("top", "_ext", "lost")[0], 1)  # one module loading doesn't excuse another's own gap
            self.assertEqual(run("top", "osonly")[0], 1)  # a stdlib module missing here fails here
            self.assertEqual(run("top", "noname")[0], 1)
            self.assertEqual(run("top", "ctypeslib")[0], 1)
            self.assertEqual(run("extensions", "osonly", "ctypeslib", "clib", "tkshape", "_ext"), (0, ["_ext"]))
            self.assertEqual(run("top", "tkshape")[0], 1)
            self.assertEqual(run("extensions", "_ext", "linkerr")[0], 1)
            self.assertEqual(run("extensions", "_ext", "lost")[0], 1)

    def test_run_refuses_what_the_plan_never_makes_and_a_wrong_interpreter(self):
        self.assertEqual(build_lab.main(["run", "--name", "x; rm", "--version", "1", "--target", "3.14 win-arm64",
                                         "--runner", "r", "--out", "/dev/null"]), 2)  # fmt: skip
        self.assertEqual(build_lab.main(["run", "--name", "x", "--version", "1", "--target", "3.14 amiga",
                                         "--runner", "r", "--out", "/dev/null"]), 2)  # fmt: skip
        # Whatever runs these tests isn't every lab target at once.
        self.assertTrue(any(build_lab.interpreter_problem(t) for t in build_lab.TARGETS))


class Census(unittest.TestCase):
    def test_top_and_refresh(self):
        self.assertEqual(pairs(build_lab.plan(COVERAGE, None, 50, "2026-11-15", top=2)),
                         [("cryptography", "3.14 win-arm64"), ("tiktoken", "3.14t win-arm64"), ("tiktoken", "3.14 win-arm64")])  # fmt: skip
        recent = {"results": [dict(GOOD, name="cryptography", version="50.0.2", date="2026-11-14")]}
        self.assertNotIn(("cryptography", "3.14 win-arm64"), pairs(build_lab.plan(COVERAGE, recent, 50, "2026-11-15")))
        self.assertIn(("cryptography", "3.14 win-arm64"),
                      pairs(build_lab.plan(COVERAGE, recent, 50, "2026-11-15", refresh=True)))  # fmt: skip
        self.assertEqual(pairs(build_lab.plan(COVERAGE, recent, 50, "2026-11-15", only={"cryptography", "orjson"})),
                         [("cryptography", "3.14 win-arm64"), ("orjson", "3.14t win-arm64"),
                          ("orjson", "3.14t linux-x86_64")])  # fmt: skip

    def test_unsupported_by_design_and_gil_module(self):
        log = "Getting requirements to build wheel did not run successfully.\nRuntimeError: uvloop does not support Windows at the moment"
        self.assertEqual(build_lab.classify(log), "unsupported platform")
        ft = dict(FT, gil_module="brotli._brotli")
        self.assertEqual(build_lab.problems(ft, FT_JOB), [])
        self.assertTrue(build_lab.problems(dict(GOOD, gil_module="x"), JOB))  # no GIL re-enabled: no module
        self.assertTrue(build_lab.problems(dict(ft, gil_module="x; rm"), FT_JOB))
        self.assertTrue(build_lab.problems(dict(ft, gil_module="a" * 101), FT_JOB))

    def test_merge_links_the_run_from_its_own_environment(self):
        merged, _ = build_lab.merge(
            None, {"0": GOOD}, [JOB], "2026-11-15", "https://github.com/wheelgap/tracker/actions/runs/1"
        )
        self.assertEqual(merged["results"][0]["log"], "https://github.com/wheelgap/tracker/actions/runs/1")
        self.assertTrue(build_lab.problems(dict(GOOD, log="https://evil.example"), JOB))  # a build can't set it



class NewestPython(unittest.TestCase):
    def test_315_on_linux_and_windows_x64_is_planned(self):
        coverage = {
            "targets": [{"label": "3.15 linux-x86_64"}, {"label": "3.15 win-amd64"}, {"label": "3.15 macos-arm64"}],
            "packages": [pkg(1, "pyyaml", "6.0.3", ["source", "source", "source"])],
        }
        planned = {(j["name"], j["target"], j["runner"]) for j in build_lab.plan(coverage, None, 10, "2026-11-16")}
        self.assertEqual(planned, {("pyyaml", "3.15 linux-x86_64", "ubuntu-24.04"),
                                   ("pyyaml", "3.15 win-amd64", "windows-2025")})  # fmt: skip


if __name__ == "__main__":
    unittest.main()

