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
- `summary`: per target label, counts of `wheel`, `source` (sdist only, so it builds on install), `missing` (can't install) and `unknown`.
- `packages`: `rank`, `name`, `version` (the latest release), `downloads` (30 days), `pure` (has a wheel that works everywhere), `error` and `results`.

The same scan is also published as [`data/coverage.csv`](https://wheelgap.github.io/tracker/data/coverage.csv): one row per package, with a column per target named by its label, so you can open it in a spreadsheet or load it with `pandas.read_csv`. It's built from `coverage.json` when the site is published, by [`scripts/build_csv.py`](scripts/build_csv.py).

`data/changes.json` lists, for each night, the packages that gained or lost a wheel since the previous scan: `[{"date", "gained": [...], "lost": [...]}]`, oldest first, for the last 90 days. Each item is `{name, version, target, from, to}`, where `target` is a label from `targets` and `from`/`to` are the results before and after, e.g. `"from": "source", "to": "wheel"`. Only changes to or from `wheel` are listed; a result that was or became `unknown` (a failed lookup) isn't, and nor are packages entering or leaving the top 5,000. A night when the scan moves to a new wheelgap version is skipped, since its differences come from wheelgap rather than the packages. It's written by [`scripts/update_changes.py`](scripts/update_changes.py).

`data/most-wanted.json` lists, for each target, the 50 most-downloaded packages with no wheel there (`source` or `missing`), by `rank`: `{"generated", "targets": {"3.13 musl-aarch64": [{rank, name, version, status}, ...], ...}}`. Packages whose lookup failed are left out. It's built when the site is published, by [`scripts/build_most_wanted.py`](scripts/build_most_wanted.py):

```sh
curl -fsS https://wheelgap.github.io/tracker/data/most-wanted.json
```

The data is CC0: use it for anything. The code is MIT.

## API for scripts and agents

To check one package, fetch its own small file (about 1.3 KB) instead of the whole scan:

```sh
curl -fsS https://wheelgap.github.io/tracker/data/packages/numpy.json
```

It has the package's `name`, `version`, `rank`, `pure` and `error`, the scan's `generated` time and `wheelgap` version, and `results`: the same values as `coverage.json`, keyed by target label, e.g. `"3.13 linux-aarch64": "wheel"`. Use the [normalized name](https://packaging.python.org/en/latest/specifications/name-normalization/) (lowercase, with runs of `-`, `_` and `.` replaced by `-`); a 404 means the package isn't in the scan's top 5,000, or the name isn't normalized. [`data/packages.json`](https://wheelgap.github.io/tracker/data/packages.json) lists every package with its version, and the targets, including the glibc, musl and macOS baselines each result assumes. Both carry `"schema": 1`, which goes up if a field changes meaning or is removed. The files are built from `coverage.json` when the site is published, by [`scripts/build_lookup.py`](scripts/build_lookup.py).

## Badges

Each package has a [shields.io endpoint](https://shields.io/badges/endpoint-badge) badge showing how many of the eight native platforms (Pyodide left out) have a wheel for the newest non-free-threaded Python in the scan, labelled with that Python. It's green when every platform has one, then yellow, orange and red as fewer do, and grey `unknown` if the lookup failed. For numpy:

```markdown
[![wheels](https://img.shields.io/endpoint?url=https://wheelgap.github.io/tracker/badge/numpy.json)](https://wheelgap.github.io/tracker/#package=numpy)
```

Use the normalized name, as for the API; the package lookup on the site shows the snippet with a Copy button. Badges are built when the site is published, by [`scripts/build_badges.py`](scripts/build_badges.py), and only exist for packages in the top 5,000.

## Package pages

Each package also has a plain HTML page, e.g. [`p/numpy/`](https://wheelgap.github.io/tracker/p/numpy/), with the same status table as the lookup, a one-line summary of its gaps for the newest non-free-threaded Python, and the `wheelgap --explain` command. The lookup links to it, and [`sitemap.xml`](https://wheelgap.github.io/tracker/sitemap.xml) lists them all. They're built when the site is published, by [`scripts/build_pages.py`](scripts/build_pages.py). [Campaign pages](https://wheelgap.github.io/tracker/campaigns/), starting with [musllinux aarch64 wheels for the top 500](https://wheelgap.github.io/tracker/campaigns/musl-aarch64/), are built the same night by [`scripts/build_campaigns.py`](scripts/build_campaigns.py) from [`campaigns.toml`](campaigns.toml).

## Caveats

- Only the **latest release** of each package is checked. Your pinned version may differ, so for that, run `uvx wheelgap` on your lockfile.
- **Pyodide** counts wheels on PyPI only. Pyodide also ships its own builds of many popular packages; `wheelgap --pyodide-lock` knows about those.
- Linux wheels are judged against glibc 2.36 and musl 1.2, and macOS against 14.0, which are wheelgap's defaults.

## Contributing

The site is deliberately simple, so it's a good place to start. Ideas and fixes are welcome, as are reports of anything that looks wrong ([open an issue](https://github.com/wheelgap/tracker/issues)). The bigger plan is on the [roadmap](https://github.com/wheelgap/wheelgap/discussions/43).

If you want to help a project ship a missing wheel, see [How to get a missing wheel added upstream](docs/adding-wheels-upstream.md).

The scripts have offline tests in `tests/`: run them with `uvx pytest` (or `python -m unittest discover -s tests`). The [Check](.github/workflows/check.yml) workflow runs them, `python3 scripts/check_notes.py`, and `node --check site/app.js`, on every pull request.

Package notes (workarounds, alternatives, upstream issues) live in [`notes/`](notes/), one TOML file per package. The format is in [`notes/README.md`](notes/README.md).
