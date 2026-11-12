# wheelgap tracker

**Python wheel coverage for the top 5,000 PyPI packages**, across Linux x86_64 and ARM64, Alpine (musl), macOS, Windows x86_64 and ARM64 for Python 3.13, 3.14 and 3.15 and free-threaded 3.14t and 3.15t, plus Pyodide where it has a build (3.13 and 3.14).

**→ [wheelgap.github.io/tracker](https://wheelgap.github.io/tracker/)**

A package without a wheel for your platform is compiled from source on install (slow, and it needs a compiler), or can't be installed at all. This site shows where those gaps are, package by package.

## How it works

- Every night, [`.github/workflows/scan.yml`](.github/workflows/scan.yml) runs [wheelgap](https://github.com/wheelgap/wheelgap)'s scanner (`python -m wheelgap.scan`) over the top packages from [top-pypi-packages](https://hugovk.dev/top-pypi-packages/). It checks each package's latest release against every platform.
- The full result goes to [`data/coverage.json`](data/coverage.json). A one-line summary per day is appended to [`data/history.json`](data/history.json), so coverage over time is kept without storing every scan.
- The site in [`site/`](site/) is plain HTML, CSS and JavaScript, with no build step. It's published to GitHub Pages together with the data.

## Using the data

`data/coverage.json` (schema 1):

- `targets`: the list of `{label, python, platform}`. Each package's `results` lines up with it.
- `summary`: per target label, counts of `wheel`, `source` (sdist only, so it builds on install), `missing` (can't install), `unknown` (the lookup failed) and `bundled`.
- `packages`: `rank`, `name`, `version` (the latest release), `downloads` (30 days), `pure` (has a wheel that works everywhere), `error` and `results`.
- `pyodide_locks`: the Pyodide releases the scan used, as `{python, pyodide_version, source}`, where `source` is the committed lock file it read, e.g. `pyodide/0.29.5-pyodide-lock.json` (see [Pyodide releases](#pyodide-releases); `pyodide_version` is `"?"` because Pyodide's lock files don't say, so go by the file name).

`bundled` only appears on a `pyodide` target: there's no wheel on PyPI for Pyodide, but the pinned Pyodide release for that Python ships its own build of the package, so it installs there. That build is **often an older version** than the `version` scanned (the latest release on PyPI): Pyodide 0.29.5 ships pandas 2.3.3, for example. Treat `bundled` like `wheel` if you're asking "does it install in Pyodide?", and like a gap if you're asking "is there a wheel for the latest release?". The per-package files below say which version Pyodide ships. A package with a Pyodide wheel on PyPI is `wheel` whether or not Pyodide also bundles it. A scan without `pyodide_locks` (or with none for that Python) didn't look at Pyodide's own builds, so it counts those packages as `missing` or `source`.

The same scan is also published as [`data/coverage.csv`](https://wheelgap.github.io/tracker/data/coverage.csv): one row per package, with a column per target named by its label, so you can open it in a spreadsheet or load it with `pandas.read_csv`. It's built from `coverage.json` when the site is published, by [`scripts/build_csv.py`](scripts/build_csv.py).

`data/changes.json` lists, for each night, the packages that gained or lost a wheel since the previous scan: `[{"date", "gained": [...], "lost": [...]}]`, oldest first, for the last 90 days. Each item is `{name, version, target, from, to}`, where `target` is a label from `targets` and `from`/`to` are the results before and after, e.g. `"from": "source", "to": "wheel"`. Only changes to or from `wheel` are listed; a result that was or became `unknown` (a failed lookup) isn't, nor is a change between `wheel` and `bundled` in either direction (it installs either way), and nor are packages entering or leaving the top 5,000. A change between `bundled` and `source` or `missing` isn't listed either, since it isn't about a wheel: so when a Pyodide release is bumped, the Pyodide bars can move with no entry here. A night when the scan moves to a new wheelgap version is skipped, since its differences come from wheelgap rather than the packages. It's written by [`scripts/update_changes.py`](scripts/update_changes.py).

`data/campaigns/<id>.json` is a [campaign](#package-pages)'s starting to-do list: `{"schema": 1, "campaign", "target", "top", "start", "recorded", "wheelgap", "packages": [{name, rank, status}, ...]}`, where `status` is the package's result on the campaign's target at the start. The nightly scan writes it the first time it sees a campaign in [`campaigns.toml`](campaigns.toml) whose `start` has come, and commits it with the rest of `data/`. It waits for a night when every package in the campaign's top N was looked up, so the list is never frozen from a partial scan. Recorded after the start night, it is a reconstruction: that night's to-do list plus the packages that gained the wheel since the start and were on the list then, going by `changes.json`; `recorded` gives the date, and the page says so. It's never rewritten after that, except by a deliberate maintainer edit. The campaign page counts as done the packages on this list that have a wheel on the target now (or, on Pyodide, are `bundled`), wherever they rank, so its progress outlasts the 90 days `changes.json` keeps. If `campaign`, `top`, `start` or the target in the file no longer match `campaigns.toml`, the page shows no progress until the file is deleted, and the next scan records a new one.

`data/most-wanted.json` lists, for each target, the 50 most-downloaded packages with no wheel there (`source` or `missing`; `bundled` isn't a gap), by `rank`: `{"generated", "targets": {"3.13 musl-aarch64": [{rank, name, version, status}, ...], ...}}`. Packages whose lookup failed are left out. It's built when the site is published, by [`scripts/build_most_wanted.py`](scripts/build_most_wanted.py):

```sh
curl -fsS https://wheelgap.github.io/tracker/data/most-wanted.json
```

`data/agreement.json` is the nightly cross-check against pip itself, written by [`scripts/check_pip.py`](scripts/check_pip.py). For the top 200 packages on `3.13 linux-x86_64`, `3.13 linux-aarch64`, `3.13 musl-aarch64` and `3.13 win-arm64`, pip is asked what it would install of the version the scan checked (`pip install --dry-run --ignore-installed --no-deps --only-binary=:all: NAME==VERSION`, with the target's Python, ABI and every platform tag wheelgap accepts for it), and its answer is compared with the scan's, `wheel` against everything else. pip's "no wheel" counts only if it fetched the package's page on PyPI without a retry or a failed fetch, so an outage is never read as an answer. Fields (`"schema": 1`):

- `date`: the day of the check; `scan`: the scan's `generated` time; `wheelgap` and `pip`: the versions used;
- `top` and `targets`: the sample; `packages`: how many of the top were looked up and checked;
- `checks` and `matched`: the cells pip answered, and how many agree with the scan;
- `mismatches`: every disagreement, as `{name, version, target, wheelgap, pip, file}`, where `wheelgap` is the scan's result, `pip` is `wheel` or `no wheel`, and `file` is the wheel pip picked (or `null`);
- `compiled`: `{packages, checks, matched}` for the packages without a pure-Python wheel, where the answer depends on the platform (most of the top 200 are pure Python, so the overall rate is easy to earn);
- `skipped`: cells the scan couldn't look up (`unknown`, or a failed lookup), which aren't checks;
- `errors`: cells pip couldn't answer (a network error, say), which aren't checks either.

Both sides use wheelgap's own tag lists: the platform tags come from the wheelgap release that made the scan, so this checks which wheels wheelgap finds in each release, not the tag lists themselves, which [wheelgap's own weekly accuracy check](https://github.com/wheelgap/wheelgap/blob/main/CONTRIBUTING.md#checking-wheelgap-against-pip) covers. pip is asked about `NAME==VERSION`, which lets it take a yanked wheel when nothing else fits, while the scan skips yanked files, so such a release shows up as a mismatch. The check runs in its own job after the scan and can never stop the scan from being committed or published. If pip couldn't answer more than 5% of the cells, or the file fails [`scripts/check_agreement.py`](scripts/check_agreement.py), nothing is committed and the previous file stays. The site shows the rate while the file is at most three days old.

The data is CC0: use it for anything. The code is MIT.

## API for scripts and agents

To check one package, fetch its own small file (about 1.7 KB) instead of the whole scan:

```sh
curl -fsS https://wheelgap.github.io/tracker/data/packages/numpy.json
```

It has the package's `name`, `version`, `rank`, `pure` and `error`, the scan's `generated` time and `wheelgap` version, and `results`: the same values as `coverage.json`, keyed by target label, e.g. `"3.13 linux-aarch64": "wheel"`. For the scan's pinned Pyodide releases it has `pyodide`: per pyodide target label, `{pyodide, lock}`, the release and its `pyodide-lock.json` URL (pass it to `wheelgap --pyodide-lock` to get the same answer). And `bundled`: per `bundled` result's label, `{pyodide, bundled_version, vs_latest}`, the version that release ships and how it compares with the scanned `version` (`same`, `older`, or `different` when it isn't a plain release or isn't older), all `null` if the lock couldn't be matched; both are `{}` when there's nothing to say. It also has `notes`: the package's current [notes](notes/README.md), each with the format's `platforms`, `kind`, `text`, `links`, `versions` (`null` if the note has none) and `updated` (`YYYY-MM-DD`), or `[]`. A note is left out once it's stale: its `versions` don't match the scanned version, or every target of every platform it lists has a wheel (or, on Pyodide, is `bundled`). Use the [normalized name](https://packaging.python.org/en/latest/specifications/name-normalization/) (lowercase, with runs of `-`, `_` and `.` replaced by `-`); a 404 means the package isn't in the scan's top 5,000, or the name isn't normalized. [`data/packages.json`](https://wheelgap.github.io/tracker/data/packages.json) lists every package with its version, and the targets, including the glibc, musl and macOS baselines each result assumes. [`data/pyodide.json`](https://wheelgap.github.io/tracker/data/pyodide.json) gives, per Python, the pinned Pyodide release (`{"pythons": {"3.13": {pyodide, lock}}}`). All three carry `"schema": 1`, which goes up if a field changes meaning or is removed. The files are built from `coverage.json` when the site is published, by [`scripts/build_lookup.py`](scripts/build_lookup.py).

## Badges

Each package has a [shields.io endpoint](https://shields.io/badges/endpoint-badge) badge showing how many of the eight native platforms (Pyodide left out) have a wheel for the newest non-free-threaded Python in the scan, labelled with that Python. It's green when every platform has one, then yellow, orange and red as fewer do, and grey `unknown` if the lookup failed. For numpy:

```markdown
[![wheels](https://img.shields.io/endpoint?url=https://wheelgap.github.io/tracker/badge/numpy.json)](https://wheelgap.github.io/tracker/#package=numpy)
```

Use the normalized name, as for the API; the package lookup on the site shows the snippet with a Copy button. Badges are built when the site is published, by [`scripts/build_badges.py`](scripts/build_badges.py), and only exist for packages in the top 5,000.

## Package pages

Each package also has a plain HTML page, e.g. [`p/numpy/`](https://wheelgap.github.io/tracker/p/numpy/), with the same status table as the lookup, a one-line summary of its gaps for the newest non-free-threaded Python, the `wheelgap --explain` command, and the package's current [notes](notes/README.md). A page with a gap also has an "Add a note" link to a pre-filled `notes/<name>.toml` on GitHub. The lookup links to it, and [`sitemap.xml`](https://wheelgap.github.io/tracker/sitemap.xml) lists them all. They're built when the site is published, by [`scripts/build_pages.py`](scripts/build_pages.py). [Campaign pages](https://wheelgap.github.io/tracker/campaigns/), starting with [musllinux aarch64 wheels for the top 500](https://wheelgap.github.io/tracker/campaigns/musl-aarch64/), are built the same night by [`scripts/build_campaigns.py`](scripts/build_campaigns.py) from [`campaigns.toml`](campaigns.toml). A to-do package with a current note for the campaign's platform links the note, and one whose note names an alternative (kind `alternative`) moves to a separate "Has an alternative" list, out of To do.

## Pyodide releases

The scan counts Pyodide's own builds as `bundled` for one pinned Pyodide release per Python: [`pyodide/locks.json`](pyodide/locks.json) lists each with the `pyodide-lock.json` URL it was copied from and its sha256, and the copies are committed next to it, so the nightly scan never fetches them and a Pyodide release can't change the results unannounced. Today that's Pyodide 0.29.5 for Python 3.13 and 314.0.7 for 3.14 (Pyodide for 3.15 is only in alpha). [`scan.yml`](.github/workflows/scan.yml) passes the files to `--pyodide-lock`, `scripts/check_scan.py` refuses a scan whose locks aren't these files, and the publish step reads the same files to show which version each bundled package is. To bump one, add the new file and its entry, point `scan.yml` at it, and keep the old file until a scan has run with the new one; the tests check the files against the manifest and `scan.yml`.

## Caveats

- Only the **latest release** of each package is checked. Your pinned version may differ, so for that, run `uvx wheelgap` on your lockfile.
- **Pyodide** results count both wheels on PyPI and Pyodide's own builds (`bundled`), for the Pyodide release pinned for each Python (see [Pyodide releases](#pyodide-releases)). A bundled build is often an older version than the latest release shown; the package page and the lookup say which. Another Pyodide release may bundle different packages or versions; check yours with `wheelgap --pyodide-lock`.
- Linux wheels are judged against glibc 2.36 and musl 1.2, and macOS against 14.0, which are wheelgap's defaults.

## Contributing

The site is deliberately simple, so it's a good place to start. Ideas and fixes are welcome, as are reports of anything that looks wrong ([open an issue](https://github.com/wheelgap/tracker/issues)). The bigger plan is on the [roadmap](https://github.com/wheelgap/wheelgap/discussions/43).

If you want to help a project ship a missing wheel, see [How to get a missing wheel added upstream](docs/adding-wheels-upstream.md).

The scripts have offline tests in `tests/`: run them with `uvx pytest` (or `python -m unittest discover -s tests`). The site's helpers in `site/logic.js` have node tests: `node --test tests/site/logic.test.js`. The [Check](.github/workflows/check.yml) workflow runs both, `python3 scripts/check_notes.py`, and `node --check` on the site's scripts, on every pull request.

Package notes (workarounds, alternatives, upstream issues) live in [`notes/`](notes/), one TOML file per package, and are shown on the package pages and in the API. The format is in [`notes/README.md`](notes/README.md).
