"""Offline checks for check_pip.py: a fake pip and fake tags, so no network and no wheelgap install."""

import json
import tempfile
import unittest
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


def package(rank, name, results, error=None, version="1.0"):
    return {"rank": rank, "name": name, "version": version, "error": error, "results": results}


def scan(packages):
    return {
        "schema": 1,
        "generated": "2026-11-12T03:20:00+00:00",
        "wheelgap": "0.9.0",
        "targets": TARGETS,
        "packages": packages,
    }


class FakePip:
    """Downloads a wheel for the (name, first platform tag) pairs in `wheels`; None in `wheels` is a pip error."""

    def __init__(self, wheels):
        self.wheels, self.calls = wheels, []

    def __call__(self, args):
        name, version = args[-1].split("==")
        platform = args[args.index("--platform") + 1]
        dest = Path(args[args.index("-d") + 1])
        assert dest.is_dir() and not any(dest.iterdir()), "the folder is emptied after every download"
        self.calls.append(args)
        answer = self.wheels.get((name, platform), False)
        if answer is None:
            return None, "pip: connection reset"
        if answer:
            filename = f"{name}-{version}-py3-none-any.whl"
            (dest / filename).write_bytes(b"")
            return filename, None
        return None, None


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
            package(1, "everywhere", ["wheel", "wheel", "wheel"]),
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
        packages = [
            package(1, "flaky", ["unknown", "wheel", "wheel"]),
            package(2, "gone", ["unknown"] * 3, error="lookup failed", version=None),
        ]
        wheels = {("flaky", "win_arm64"): True, ("flaky", "musllinux_1_2_aarch64"): None}
        result, pip = run(packages, wheels)
        self.assertEqual((result["checks"], result["matched"], result["skipped"], result["errors"]), (1, 1, 4, 1))
        self.assertEqual(result["packages"], 1)
        self.assertEqual(len(pip.calls), 2)  # pip isn't asked about unknown cells or failed lookups

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
        args = check_pip.pip_args("orjson", "3.11.4", target, tags, "/tmp/x")
        self.assertEqual(
            args,
            [
                *(
                    "download",
                    "--no-deps",
                    "--only-binary=:all:",
                    "--index-url",
                    "https://pypi.org/simple",
                    "-d",
                    "/tmp/x",
                ),
                *("--platform", "musllinux_1_2_aarch64", "--platform", "musllinux_1_1_aarch64"),
                *("--python-version", "3.14", "--implementation", "cp", "--abi", "cp314t", "orjson==3.11.4"),
            ],
        )


class WriteTests(unittest.TestCase):
    def test_writes_json_and_no_temporary_file(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "data" / "agreement.json"
            check_pip.write({"checks": 1}, path)
            self.assertEqual(json.loads(path.read_text(encoding="utf-8")), {"checks": 1})
            self.assertEqual([p.name for p in path.parent.iterdir()], ["agreement.json"])


if __name__ == "__main__":
    unittest.main()
