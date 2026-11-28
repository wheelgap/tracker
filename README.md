# wheelgap tracker

**Python wheel coverage for the top 5,000 PyPI packages**, across Linux x86_64 and ARM64, Alpine (musl), macOS, Windows x86_64 and ARM64 for Python 3.13, 3.14 and 3.15 and free-threaded 3.14t and 3.15t, plus Pyodide where it has a build (3.13 and 3.14).

**→ [wheelgap.github.io/tracker](https://wheelgap.github.io/tracker/)**

A package without a wheel for your platform is compiled from source on install (slow, and it needs a compiler), or can't be installed at all. This site shows where those gaps are, package by package.

The site's design, Precision UI, is by [Kunal Shah (@kunalkshah1)](https://github.com/kunalkshah1), the winner of the [redesign challenge](https://github.com/wheelgap/wheelgap/discussions/154).

## How it works

- Every night, [`.github/workflows/scan.yml`](.github/workflows/scan.yml) runs [wheelgap](https://github.com/wheelgap/wheelgap)'s scanner (`python -m wheelgap.scan`) over the top packages from [top-pypi-packages](https://hugovk.dev/top-pypi-packages/). It checks each package's latest release against every platform.
- The full result goes to [`data/coverage.json`](data/coverage.json). A one-line summary per day is appended to [`data/history.json`](data/history.json), so coverage over time is kept without storing every scan.
- The site in [`site/`](site/) is plain HTML, CSS and JavaScript, with no build step. It's published to GitHub Pages together with the data.

## Using the data

`data/coverage.json` (schema 1):

- `targets`: the list of `{label, python, platform}`. Each package's `results` lines up with it.
- `summary`: per target label, counts of `wheel`, `source` (sdist only, so it builds on install), `missing` (can't install), `unknown` (the lookup failed) and `bundled`.
- `packages`: `rank`, `name`, `version` (the latest release), `downloads` (30 days), `pure` (has a wheel that works everywhere), `error` and `results`.
- `dropped_names` (only when names were left out): `{count, names}` records names in the download ranking that PyPI
  answered with `not on index` (404). A name is left out once two scans in a row can't find it: the first night it
  stays in `packages` as `unknown`, so one bad answer from PyPI can't remove a real project.
  `scripts/drop_missing_names.py` removes the names before the scan is checked, adjusting `summary` and
  `lookup_errors` but keeping the remaining packages' original ranks. Other lookup errors stay in the scan; more
  than 25 names to leave out stops the scan instead of publishing it.
- `pyodide_locks`: the Pyodide releases the scan used, as `{python, pyodide_version, source}`, where `source` is the committed lock file it read, e.g. `pyodide/0.29.5-pyodide-lock.json` (see [Pyodide releases](#pyodide-releases); `pyodide_version` is `"?"` because Pyodide's lock files don't say, so go by the file name).

`bundled` only appears on a `pyodide` target: there's no wheel on PyPI for Pyodide, but the pinned Pyodide release for that Python ships its own build of the package, so it installs there. That build is **often an older version** than the `version` scanned (the latest release on PyPI): Pyodide 0.29.5 ships pandas 2.3.3, for example. Treat `bundled` like `wheel` if you're asking "does it install in Pyodide?", and like a gap if you're asking "is there a wheel for the latest release?". The per-package files below say which version Pyodide ships. A package with a Pyodide wheel on PyPI is `wheel` whether or not Pyodide also bundles it. A scan without `pyodide_locks` (or with none for that Python) didn't look at Pyodide's own builds, so it counts those packages as `missing` or `source`.

The same scan is also published as [`data/coverage.csv`](https://wheelgap.github.io/tracker/data/coverage.csv): one row per package, with a column per target named by its label, so you can open it in a spreadsheet or load it with `pandas.read_csv`. It's built from `coverage.json` when the site is published, by [`scripts/build_csv.py`](scripts/build_csv.py).

[`data/history.csv`](https://wheelgap.github.io/tracker/data/history.csv) has one row per night and target, oldest first. It includes the Python version, platform, category counts and the percentage with a wheel or bundled build, using the same one-decimal share as the site chart.

`data/changes.json` lists, for each night, the packages that gained or lost a wheel since the previous scan: `[{"date", "gained": [...], "lost": [...]}]`, oldest first, for the last 90 days. Each item is `{name, version, target, from, to}`, where `target` is a label from `targets` and `from`/`to` are the results before and after, e.g. `"from": "source", "to": "wheel"`. Only changes to or from `wheel` are listed; a result that was or became `unknown` (a failed lookup) isn't, nor is a change between `wheel` and `bundled` in either direction (it installs either way), and nor are packages entering or leaving the top 5,000. A change between `bundled` and `source` or `missing` isn't listed either, since it isn't about a wheel: so when a Pyodide release is bumped, the Pyodide bars can move with no entry here. A night when the scan moves to a new wheelgap version is skipped, since its differences come from wheelgap rather than the packages. It's written by [`scripts/update_changes.py`](scripts/update_changes.py).

`data/drops.json` lists the platforms top packages' new releases dropped: `{"schema": 1, "wheelgap", "drops": [{date, name, from, version, target}, ...], "waiting": [...]}`, newest first, for a year. A drop is a target where `from` (the release the previous nightly scan saw) had a wheel and `version` (tonight's current release) builds from source or has no distribution. A release's wheels are often still uploading the night it comes out, so every such target first waits in `waiting` (`{name, from, version, targets, since}`, where `since` is the `generated` time of the scan that first saw `version`), and is listed as a drop, dated the night it's confirmed, only if a scan at least 20 hours later still finds the same release without the wheel. A release with no wheel on any target yet keeps waiting until one shows up, or is compared as it is after 7 days. When a release has no wheel left on any target of one Python while it still has wheels for another, it ended support for that Python, so that Python's targets aren't listed. When the current release goes back to an older version, the newer one was yanked or deleted, and its drops are removed. Cells that were or are `unknown`, targets only one scan has or whose definition (or, for Pyodide, whose Pyodide lock) changed, and nights that move to a new wheelgap version are skipped, as for `changes.json`; a new wheelgap version also clears `waiting` (`wheelgap` is the version it was held under), and a package that leaves the top 5,000 stops waiting. The [Dropped platforms](https://wheelgap.github.io/tracker/drops/) page shows the latest, and [`drops.xml`](https://wheelgap.github.io/tracker/drops.xml) is an Atom feed with one entry per release that dropped a platform. It's written by [`scripts/update_drops.py`](scripts/update_drops.py), and the page and feed by [`scripts/build_drops.py`](scripts/build_drops.py) at publish time.

The [New wheels](https://wheelgap.github.io/tracker/new/) page shows top packages that recently gained prebuilt wheels, and [`new.xml`](https://wheelgap.github.io/tracker/new.xml) (plus per-platform feeds like `new/win-arm64.xml` for each supported platform) is an Atom feed with one entry per release and night it gained wheels. Gains that only undo a loss within the previous 7 days (wheels still uploading) are left out. The page and feeds are written by [`scripts/build_new_wheels.py`](scripts/build_new_wheels.py) at publish time.

`data/campaigns.json` lists every [campaign](#package-pages) with its headline numbers: `{"schema": 1, "generated", "wheelgap", "campaigns": [{id, title, platform, python, share, wheels, total, todo, url, data}, ...]}`, where `share` is the percentage of the top N with the wheel, `todo` counts the To do list, `url` is the campaign page and `data` is its status file, `data/campaigns/<id>.json`. That file holds the page's lists and headline numbers: `{"schema": 1, "generated", "wheelgap", "campaign", "title", "url", "target", "platform", "python", "top", "start", "compare", "share", "wheels", "total", "todo": [{rank, name, version, status, note?}, ...], "has_alternative": [...], "done": [{name, version, date, was}, ...], "baseline": {recorded, mismatch, packages}}`. `compare` is how To do is chosen: `"platform"` (a wheel for the campaign's Python on another platform of the same OS), `"python"` (a wheel on the campaign's platform for the Python before it) or `"gil"` (for a free-threaded `python` such as `"3.14t"`: a wheel on the campaign's platform for the same version's regular build). `note` is the URL of the package's note, when it has one. In `done`, `date` is the night the wheel appeared (`YYYY-MM-DD`) when `changes.json` still has it, otherwise `"before YYYY-MM-DD"` (before the oldest night it keeps) or `"not in the log"`, so check before parsing it as a date; `was` is the result on the starting list, and can be `""`. `share` is `null` when there are no packages to count. `done` is `null` when the recorded starting list is for other settings (`mismatch` lists them), and `baseline.recorded` is `null` while the starting list isn't recorded yet, in which case `baseline.packages` is today's to-do list plus the packages that gained the wheel since the start. Both are built when the site is published, so a script can follow a campaign without reading its HTML:

```sh
curl -fsS https://wheelgap.github.io/tracker/data/campaigns/musl-aarch64.json
```

In the repository, `data/campaigns/<id>.json` is a campaign's recorded starting to-do list, the status file's `baseline`: `{"schema": 1, "campaign", "target", "top", "start", "recorded", "wheelgap", "compare", "packages": [{name, rank, status}, ...]}`, where `status` is the package's result on the campaign's target at the start and `compare` is the rule the list was built by (`"platform"`, `"python"` for a new-Python campaign, or `"gil"` for a free-threaded one; a file recorded before the field existed has none, and isn't checked on it). The nightly scan writes it the first time it sees a campaign in [`campaigns.toml`](campaigns.toml) whose `start` has come, and commits it with the rest of `data/`. It waits for a night when every package in the campaign's top N was looked up, so the list is never frozen from a partial scan. Recorded after the start night, it is a reconstruction: that night's to-do list plus the packages that gained the wheel since the start and were on the list then, going by `changes.json`; `recorded` gives the date, and the page says so. It's never rewritten after that, except by a deliberate maintainer edit. The campaign page counts as done the packages on this list that have a wheel on the target now (or, on Pyodide, are `bundled`), wherever they rank, so its progress outlasts the 90 days `changes.json` keeps. If `campaign`, `top`, `start`, `compare` (when recorded) or the target in the file no longer match `campaigns.toml`, the page shows no progress until the file is deleted, and the next scan records a new one.

`data/most-wanted.json` lists, for each target, the 50 most-downloaded packages with no wheel there (`source` or `missing`; `bundled` isn't a gap), by `rank`: `{"generated", "targets": {"3.13 musl-aarch64": [{rank, name, version, status}, ...], ...}}`. Packages whose lookup failed are left out. It's built when the site is published, by [`scripts/build_most_wanted.py`](scripts/build_most_wanted.py):

```sh
curl -fsS https://wheelgap.github.io/tracker/data/most-wanted.json
```

`data/blockers.json` lists, for each target whose whole-tree installs were checked, up to 50 packages whose missing wheel (`source` or `missing`) stops the most *other* top-500 packages installing from wheels alone: `{"generated", "targets": {"3.14 win-arm64": {"checked": 498, "blockers": [{"name", "count", "examples": [...]}, ...]}, ...}}`. `count` is how many other top-500 trees it blocks, and `examples` are the three highest-ranked of them. A package's own tree doesn't count (that's its own missing wheel, which `most-wanted.json` ranks), a tree with the same blocker by two routes counts once, and only checked trees count (`not_checked` counts for nothing). It's built when the site is published, by [`scripts/build_blockers.py`](scripts/build_blockers.py):

```sh
curl -fsS https://wheelgap.github.io/tracker/data/blockers.json
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

To check one package, fetch its own small file (a few KB) instead of the whole scan:

```sh
curl -fsS https://wheelgap.github.io/tracker/data/packages/numpy.json
```

It has the package's `name`, `version`, `rank`, `pure` and `error`, the scan's `generated` time and `wheelgap` version, and `results`: the same values as `coverage.json`, keyed by target label, e.g. `"3.13 linux-aarch64": "wheel"`. For the scan's pinned Pyodide releases it has `pyodide`: per pyodide target label, `{pyodide, lock}`, the release and its `pyodide-lock.json` URL (pass it to `wheelgap --pyodide-lock` to get the same answer). And `bundled`: per `bundled` result's label, `{pyodide, bundled_version, vs_latest}`, the version that release ships and how it compares with the scanned `version` (by PEP 440: `same`, `older`, or `different` when it's newer or not a valid version), all `null` if the lock couldn't be matched; both are `{}` when there's nothing to say. It also has `notes`: the package's current [notes](notes/README.md), each with the format's `platforms`, `kind`, `text`, `links`, `versions` (`null` if the note has none) and `updated` (`YYYY-MM-DD`), plus `python` (the list of Python labels it applies to) only on a note that has one; `notes` is `[]` when there are none. A note is left out once it's stale: its `versions` don't match the scanned version, or every target of every platform it lists has a wheel (or, on Pyodide, is `bundled`); when the note has a `python` list, only the targets of those Pythons count. It also has `builds`: the [build lab](scripts/build_lab.py)'s results for the package, at most one per lab target (Windows on ARM, free-threaded Python, Alpine ARM, and Python 3.15 on Linux x86_64 and Windows x64), each for the newest release the lab built there, so compare its `version` with the package's: each with the `version` it built, `outcome` (`built`, `failed` or `timeout`), `seconds`, the `wheel` tag it got and how many `extensions` (compiled modules) it has, `import` (`deps-failed` when its dependencies didn't install, `failed` when importing its shallowest modules failed, `ok` when they imported and, for a wheel with extension modules, at least one of those loaded too: a module that only needs an optional dependency, a test helper or a library that isn't really a module isn't held against it), `gil` (`kept-off` or `re-enabled`, only for a free-threaded build with an extension module), the failure `reason` and a short log `excerpt`, the `toolchains` found, and the `runner`, `python` and `date` it ran on; `[]` when there are none. A result is the lab's dated observation on GitHub's runners, which have compilers and many system libraries preinstalled, not a promise about yours. It also has `changes`: for each target, the package's 3 newest entries from `data/changes.json` (which keeps 90 days), as `{date, from, target, to, version}`, where `from` and `to` are results; and `drops`: for each target, its 3 newest from `data/drops.json` (which keeps a year), as `{date, from, target, version}`, where `from` is the release that had the wheel and `version` the one without it. Both are newest first (within a day by target), and `[]` when there are none. Use the [normalized name](https://packaging.python.org/en/latest/specifications/name-normalization/) (lowercase, with runs of `-`, `_` and `.` replaced by `-`); a 404 means the package isn't in the scan's top 5,000, or the name isn't normalized. [`data/packages.json`](https://wheelgap.github.io/tracker/data/packages.json) lists every package with its version, and the targets, including the glibc, musl and macOS baselines each result assumes. [`data/pyodide.json`](https://wheelgap.github.io/tracker/data/pyodide.json) gives, per Python, the pinned Pyodide release (`{"pythons": {"3.13": {pyodide, lock}}}`). All three carry `"schema": 1`, which goes up if a field changes meaning or is removed. The files are built from `coverage.json` when the site is published, by [`scripts/build_lookup.py`](scripts/build_lookup.py). The [Data API](https://wheelgap.github.io/tracker/api/) page on the site sums these files up. A package's `changes` and `drops`, after one gained wheel and one dropped platform:

```json
{
  "changes": [{"date": "2026-11-26", "from": "source", "target": "3.14 linux-aarch64", "to": "wheel", "version": "2.1.0"}],
  "drops": [{"date": "2026-11-25", "from": "2.0.4", "target": "3.14 macos-arm64", "version": "2.1.0"}]
}
```

For scanned packages, the package JSON also has an optional `install` map keyed by the same target labels as `results`. Each value has a `status` (`wheel`, `source`, `missing`, or `not_checked`) and a `blockers` list. Each blocker contains its `name`, `version` (or `null`), `status` (`source` or `missing`), and `via` dependency path from the root. The dependency scan covers selected native targets for up to the top 500 packages, highest-ranked first, within a time limit. It pins each root to the latest release in the coverage snapshot and resolves its dependencies without extras. A tree that wheelgap cannot resolve or verify, or one it leaves a package out of, is marked `not_checked`. wheelgap picks the newest matching release of each dependency without backtracking, so on a tree where pip would backtrack the two can differ. Older snapshots, packages outside the top 500 and packages none of whose targets could be checked omit the key. The package pages show the same install results alongside their individual wheel coverage.

Some gap results have a `way_out` entry, keyed by the target label. For example, `{"3.14 win-arm64": {"release": "46.0.3", "kind": "older"}}` names the newest older release with a wheel there, within wheelgap's lookback. A `"newer"` entry names an upgrade with a wheel for that target which keeps the package's existing wheels on the scan targets. The nightly pass checks public PyPI release metadata once per affected package and leaves out hints it cannot verify. The package page shows the hint beside its gap. These hints cover the package's own wheels. Dependency status is shown in `install`.

## Badges

Each package has a [shields.io endpoint](https://shields.io/badges/endpoint-badge) badge showing how many of the eight native platforms (Pyodide left out) have a wheel for the newest non-free-threaded Python in the scan, labelled with that Python. It's green when every platform has one, then yellow, orange and red as fewer do, and grey `unknown` if the lookup failed. For numpy:

```markdown
[![wheels](https://img.shields.io/endpoint?url=https://wheelgap.github.io/tracker/badge/numpy.json)](https://wheelgap.github.io/tracker/#package=numpy)
```

Use the normalized name, as for the API; the package lookup on the site shows the snippet with a Copy button. Badges are built when the site is published, by [`scripts/build_badges.py`](scripts/build_badges.py), and only exist for packages in the top 5,000.

A pure-Python package gets a green badge even when `pip install` of it builds a dependency from source. For the top 500, which have whole-tree install results (`install` in the package JSON), `badge/<name>-tree.json` counts the platforms whose whole tree was checked and how many of them get every package in the tree as a wheel, such as `5/6 checked`. Those are the install scan's native targets ([`scripts/scan_install.py`](scripts/scan_install.py)): on 3.14, Linux x86_64, Linux ARM64, macOS Apple silicon, macOS Intel, Windows ARM64 and Alpine ARM64, but not Windows x86_64 or Alpine x86_64, so it's out of six where the package badge is out of eight. It's labelled `wheels+deps` and the Python those results are for, the newest non-free-threaded Python with a checked result, and uses the same colours. It's grey `partly checked` when any of those platforms wasn't checked. The package's page shows its snippet next to the other one:

```markdown
[![wheels with dependencies](https://img.shields.io/endpoint?url=https://wheelgap.github.io/tracker/badge/requests-tree.json)](https://wheelgap.github.io/tracker/p/requests/)
```

A package whose `<name>-tree` is another package's name (`dm` and `dm-tree`) gets no tree badge, so the two never share a file.

Those badges are for packages on PyPI. A repository can link its own check instead, with the [repo checker](https://wheelgap.github.io/tracker/check/)'s badge. It's a fixed image (`check/badge.svg`), so it shows no result: a click runs the check on the repo's default branch as it is that day, for the platforms and Pythons its CI names. After a check, the page's "Fix it" section has the snippet with a Copy button, which adds `&lock=path` when the lockfile isn't the one the page picks by itself:

```markdown
[![Check wheels](https://wheelgap.github.io/tracker/check/badge.svg)](https://wheelgap.github.io/tracker/check/?repo=owner/name)
```

## Package pages

Each package also has a plain HTML page, e.g. [`p/numpy/`](https://wheelgap.github.io/tracker/p/numpy/), with the same status table as the lookup, a one-line summary, the `wheelgap --explain` command, and the package's current [notes](notes/README.md). The summary, which is also the page's search snippet, says whether `pip install` gets wheels for the whole dependency tree on the newest non-free-threaded Python with checked install coverage; for a package without checked install results, it gives the package's own wheel gaps for the newest non-free-threaded Python instead. A page with a gap also has an "Add a note" link to a pre-filled `notes/<name>.toml` on GitHub. The lookup links to it, and [`sitemap.xml`](https://wheelgap.github.io/tracker/sitemap.xml) lists them all, with the home page, the [repo checker](https://wheelgap.github.io/tracker/check/), the campaign pages and the platform pages. A `robots.txt` naming the sitemap is written beside it, though crawlers only read one at a host's root, so it's ignored while the site lives under `/tracker/`. Each page also has a schema.org `SoftwareSourceCode` JSON-LD block (name, version, the page and PyPI links, and the same summary) for search engines. Under a `source` cell whose target the build lab covers, the page shows the verdict for that release, with a Build lab section giving the runner, Python version, date, and link to the workflow run. A Recent changes section lists up to ten of the package's most recent wheel gains and losses from `data/changes.json` (which keeps 90 days), and is left out when there are none. Where [wheelgap/wheels](https://wheelgap.github.io/wheels/) publishes an unofficial gap wheel for the exact release and target of a `source` or `missing` cell, the cell says so and a Gap wheels section gives the `pip install` command. They're built when the site is published, by [`scripts/build_pages.py`](scripts/build_pages.py). [Campaign pages](https://wheelgap.github.io/tracker/campaigns/), starting with [musllinux aarch64 wheels for the top 500](https://wheelgap.github.io/tracker/campaigns/musl-aarch64/), are built the same night by [`scripts/build_campaigns.py`](scripts/build_campaigns.py) from [`campaigns.toml`](campaigns.toml). A to-do package with a current note for the campaign's platform and Python links the note, and one whose note names an alternative (kind `alternative`) moves to a separate "Has an alternative" list, out of To do. A campaign for a new Python (`compare = "python"` in `campaigns.toml`) asks instead about the packages that have a wheel on its platform for the Python before it but none for the new one, since on a new Python most packages have no wheel on any platform yet. A campaign's `python` may be free-threaded (`python = "3.14t"`), and then it needs `compare = "gil"`, which is refused for any other `python`: it asks about the packages that have a wheel on its platform for the regular build of the same version (3.14 for 3.14t) but none for the free-threaded one, and leaves out those with no wheel for either.

There's also one page per platform, linked from the [platform index](https://wheelgap.github.io/tracker/platforms/), which lists every platform's share and number of packages without a wheel, worst first, and from the row headings of each package page's table, e.g. [`platforms/win-arm64/`](https://wheelgap.github.io/tracker/platforms/win-arm64/). Each gives the share of the top packages with a wheel there (or a Pyodide build, on Pyodide) for the newest non-free-threaded Python the platform has, and the 50 most-downloaded packages without one, linked to their pages. It links the platform's campaign, if there is one, and the build lab if the lab builds for that platform. An "Over time" table gives the share on the latest night, the night before, 7 and 30 nights before, and the first night in `data/history.json` (counting Pyodide builds on Pyodide, as `build_campaigns.trend()` does), and the lede adds the change over the last 30 nights, unless a new wheelgap version falls in that span; a row scanned with an earlier wheelgap than the latest night is marked as not like for like. A "Blocking the most installs" section lists up to 10 packages from `data/blockers.json` whose missing wheel stops the most other top-500 packages installing from wheels alone, with the count and examples, for the newest non-free-threaded Python whose whole-tree installs were checked on that platform (it can be older than the page's Python, so the heading names it); a platform with checked trees and no blockers says so, and one that isn't checked has no such section. They're built from the same scan by [`scripts/build_platform_pages.py`](scripts/build_platform_pages.py).

## Pyodide releases

The scan counts Pyodide's own builds as `bundled` for one pinned Pyodide release per Python: [`pyodide/locks.json`](pyodide/locks.json) lists each with the `pyodide-lock.json` URL it was copied from and its sha256, and the copies are committed next to it, so the nightly scan never fetches them and a Pyodide release can't change the results unannounced. Today that's Pyodide 0.29.5 for Python 3.13 and 314.0.7 for 3.14 (Pyodide for 3.15 is only in alpha). [`scan.yml`](.github/workflows/scan.yml) passes the files to `--pyodide-lock`, `scripts/check_scan.py` refuses a scan whose locks aren't these files, and the publish step reads the same files to show which version each bundled package is. To bump one, add the new file and its entry, point `scan.yml` at it, and keep the old file until a scan has run with the new one; the tests check the files against the manifest and `scan.yml`.

## Caveats

- Only the **latest release** of each package is checked. Your pinned version may differ, so for that, run `uvx wheelgap` on your lockfile.
- **Pyodide** results count both wheels on PyPI and Pyodide's own builds (`bundled`), for the Pyodide release pinned for each Python (see [Pyodide releases](#pyodide-releases)). A bundled build is often an older version than the latest release shown; the package page and the lookup say which. Another Pyodide release may bundle different packages or versions; check yours with `wheelgap --pyodide-lock`.
- Linux wheels are judged against glibc 2.36 and musl 1.2, and macOS against 14.0, which are wheelgap's defaults.

## Contributing

The site is deliberately simple, so it's a good place to start. Ideas and fixes are welcome, as are reports of anything that looks wrong ([open an issue](https://github.com/wheelgap/tracker/issues)). The bigger plan is on the [roadmap](https://github.com/wheelgap/wheelgap/discussions/43).

If you want to help a project ship a missing wheel, see [How to get a missing wheel added upstream](docs/adding-wheels-upstream.md).

### Working on the site

`site/` can't be opened as it is: its pages fetch `data/coverage.json`, `data/packages/<name>.json`, `data/most-wanted.json` and other files that only exist after the publish step ("Assemble the site" in [`scan.yml`](.github/workflows/scan.yml)) builds them, and the package, platform, campaign and New wheels pages are generated there too. To build the same `_site/` from the committed data (Python 3.11 or newer, since the scripts use `tomllib`, and nothing else), run from the repository root:

```sh
rm -rf _site && mkdir -p _site/data
cp -r site/. _site/
cp data/*.json _site/data/
python3 scripts/build_lookup.py data/coverage.json _site/data/packages
python3 scripts/build_csv.py data/coverage.json _site/data/coverage.csv \
  --history data/history.json _site/data/history.csv
python3 scripts/build_most_wanted.py data/coverage.json _site/data/most-wanted.json
python3 scripts/build_badges.py data/coverage.json _site/badge
curl -sfL --max-time 60 -o _site/data/gap-wheels.json https://wheelgap.github.io/wheels/data/gap-wheels.json \
  || echo "::warning::couldn't download gap-wheels.json from wheelgap.github.io/wheels"
curl -sfL --max-time 60 -o _site/data/status.json https://wheelgap.github.io/wheels/data/status.json \
  || echo "::warning::couldn't download status.json from wheelgap.github.io/wheels"
python3 scripts/build_pages.py data/coverage.json _site notes _site/data/gap-wheels.json _site/data/status.json
python3 scripts/build_campaigns.py data/coverage.json data/history.json data/changes.json campaigns.toml _site
python3 scripts/build_blockers.py data/coverage.json _site/data/blockers.json
python3 scripts/build_platform_pages.py data/coverage.json _site/data/most-wanted.json _site campaigns.toml --history data/history.json --blockers _site/data/blockers.json
python3 scripts/build_drops.py data/drops.json data/coverage.json _site
python3 scripts/build_new_wheels.py data/changes.json data/coverage.json _site
python3 -m http.server -d _site --bind 127.0.0.1
```

Then open <http://localhost:8000/> for the home page, or a package page such as <http://localhost:8000/p/numpy/>. `_site/` is in `.gitignore`. Rerun the commands after a change to `site/` or `scripts/`.

Package-page gap wheel hints use `gap-wheels.json` and the current `status.json` from [wheelgap/wheels](https://github.com/wheelgap/wheels), downloaded above as in the publish step. Only `in-index` and `yanked` wheels are offered; a yanked release is labelled and its command pins the exact version. A missing or malformed status file warns and falls back to `gap-wheels.json` alone; without that file there are no hints. To preview offline, leave out the downloads and the two optional JSON arguments. The publish step also builds the legacy gap wheel index (`build_gap_index.py`), which these commands skip. Two more things only work on the published site:

- `site/404.html` uses absolute `/tracker/` paths, so its styles load only under that prefix;
- the lookup fetches the gap wheel list from `../wheels/data/gap-wheels.json`, which a local server doesn't have, so it shows no gap wheels.

### PyTorch extra index snapshot

For a plain requirements file whose only other index is `download.pytorch.org`, the repo checker leaves packages
that PyTorch may serve as unknown. `PYTORCH_PROJECTS` in `site/check/driver.py` is a dated, canonical, sorted union
of those project names. Other packages can be checked and offered fixes from PyPI. The browser uses only this
static snapshot; it never fetches a PyTorch index.

To refresh the snapshot, run this from the repository root and replace the `PYTORCH_PROJECTS` block, including
its date, with the output:

```sh
python3 scripts/pytorch_projects.py
```

The script follows CPU, numbered CUDA, numbered ROCm, ROCm-preview and XPU channel links from
<https://download.pytorch.org/whl/>. It also includes the still-readable historical `cpu-cxx11-abi` index. The root
mixes channels with project names, so only those channel links are followed. Every selected index must download
and parse successfully before the script prints anything; a failure aborts instead of producing a partial list.
Its tests use local HTML fixtures and make no network requests.

The root links `cpu-pypi-pkg`, `cu117-pypi-cudnn`, `cu121-pypi-cudnn`, `cu121-full`, `cu124-full`, `cu126-full` and
`cu128-full` returned HTTP 403 when checked on 2026-11-25 and are excluded. PyTorch's
[index publisher](https://github.com/pytorch/test-infra/blob/8cd319865066bf94e5be2b0180d081175095c980/s3_management/manage_v2.py)
classifies their underlying underscore-named directories as legacy builds in `NOT_ACCEPTED_SUBDIR_PATTERNS` and
excludes them when generating indices. The snapshot does not claim to cover the contents of those unreadable
variants. Nightly, test and variant trees are outside this snapshot too; new channels or packages need a reviewed
refresh.

### Tests

The [Check](.github/workflows/check.yml) workflow runs these on every pull request; run the same before you open one:

```sh
uvx --python 3.14 pytest@8.4.2
python3 scripts/check_notes.py
pin=$(sed -n 's/^export const WHEELGAP = "wheelgap==\(.*\)";$/\1/p' site/check/worker.mjs)
uvx --python 3.14 --refresh-package wheelgap --with "wheelgap==$pin" pytest@8.4.2 tests/test_check_driver.py tests/test_check_parity.py
node --check site/logic.js && node --check site/not-found.js && node --check site/app.js && node --check site/lab.js && for f in site/check/*.mjs; do node --check "$f"; done
node --test tests/site/logic.test.js tests/site/scripts.test.js tests/site/check-logic.test.mjs tests/site/check-page.test.mjs tests/site/app.test.js tests/site/not-found.test.js
```

The first pytest run skips `tests/test_check_driver.py` and `tests/test_check_parity.py` when wheelgap isn't installed; the third command runs them with the wheelgap version the browser checker pins. These two files also need Node: they run the browser's actual JSON trimmers before comparing trimmed and full PyPI answers. `test_check_parity.py` runs that version's CLI and the checker's `driver.py` on the same files (every format the page reads) and compares the two answers; it needs no network, since the PyPI lookups are answered from canned JSON on 127.0.0.1. Without `uv`, run the script tests on your own Python (3.11 or newer) in a virtual environment, since Homebrew's and most Linux distributions' `python3` refuse a plain `pip install`: `python3 -m venv .venv && .venv/bin/python -m pip install pytest==8.4.2 && .venv/bin/python -m pytest`. `unittest` alone doesn't collect most of them.

Package notes (workarounds, alternatives, upstream issues) live in [`notes/`](notes/), one TOML file per package, and are shown on the package pages and in the API. The format is in [`notes/README.md`](notes/README.md).
