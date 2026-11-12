# Package notes

Notes record what the nightly scan cannot: a binary extra to install, a system library to add first, a conda-forge build, or an upstream issue to follow. The scan still says *that* a wheel is missing; a note says *what to do about it*.

This is v0 of the format, proposed in [#27](https://github.com/wheelgap/tracker/issues/27). The earlier discussion in [wheelgap#109](https://github.com/wheelgap/wheelgap/discussions/109) stays open if the fields need to change. Notes are shown on each package's page (`p/<name>/`) and in its API file (`data/packages/<name>.json`) from the next publish after they're merged.

## Files

One UTF-8 TOML file per package, named for the [normalized package name](https://packaging.python.org/en/latest/specifications/name-normalization/) (lowercase, with runs of `-`, `_` and `.` replaced by a single `-`, so the name matches `^[a-z0-9]+(-[a-z0-9]+)*$`): `notes/psycopg2.toml`, `notes/mysqlclient.toml`.

Each file has one or more `[[note]]` tables and nothing else at the top level. Nothing else belongs in `notes/` besides this README: the checker reports subdirectories and non-`.toml` files.

```toml
[[note]]
platforms = ["linux-x86_64", "musl-x86_64"]  # wheelgap target names, or ["all"]
kind = "alternative"         # workaround | alternative | upstream-issue | other-source
text = "psycopg2 has no Linux wheels. psycopg2-binary does: `pip install psycopg2-binary`."
links = ["https://www.psycopg.org/docs/install.html"]
versions = "<3"              # optional: shown only while the latest release matches
updated = 2026-11-11
```

## Fields

These six are the only keys a note may have; the checker rejects any other.

- **platforms**: a list of wheelgap target names, taken from the `platform` field in `data/coverage.json`'s `targets` (`linux-x86_64`, `linux-aarch64`, `musl-x86_64`, `musl-aarch64`, `macos-x86_64`, `macos-arm64`, `win-amd64`, `win-arm64`, `pyodide`). Use `["all"]` when the note applies to every target, pyodide included. Do not mix `"all"` with named platforms, and list each platform once.
- **kind**: one of `workaround` (install a system library, set a flag), `alternative` (a different package or extra that has the wheel), `upstream-issue` (a ticket to follow), or `other-source` (conda-forge, a distro package, …).
- **text**: one or two sentences of plain text on a single line, non-empty, under 500 characters (499 at most). The only markup is backticks around commands and names (`` `pip install psycopg2-binary` ``), shown as code; no newlines, links or other Markdown. Write what someone should *do*.
- **links**: one or more `https://` URLs, each with a host, no spaces, control characters or `user@` part, and listed once, that back the claim: upstream docs, the issue, or a feedstock. Check what you claim.
- **versions** (optional): the releases the note applies to, as a small subset of a [PEP 440 version specifier](https://packaging.python.org/en/latest/specifications/version-specifiers/) that can be matched without the `packaging` library: one or more comma-separated clauses, each an operator (`==`, `!=`, `<=`, `>=`, `<`, `>`) and a plain release number such as `2` or `2.9.1`. A trailing `.*` is allowed only after `==` or `!=` (`"==2.*"`). `~=`, `===`, epochs, and pre-release, post-release, dev or local versions are rejected. For example `"<3"` or `">=2.8, <2.10"`. A note stops being shown once this no longer matches the latest release. Only a plain release number can be matched, so while the latest release is a pre-, post- or dev release (`3.0rc1`, `2.9.post1`), a note with `versions` is hidden.
- **updated**: a bare TOML date (`YYYY-MM-DD`, unquoted), the day the note was last checked. A string, a datetime such as `2026-11-11T09:00:00Z`, or a date in the future is rejected.

A note also retires itself once the scan shows a wheel on every platform it lists, meaning every Python target of that platform (3.13, 3.14, 3.14t and so on) has one. A note has no Python field, so it stays shown while any one of those targets lacks a wheel, even if the gap it describes (say, on 3.13) has closed and only free-threaded 3.14t is left. Because `["all"]` includes pyodide, where few compiled packages have wheels, such notes rarely retire; list named platforms when you can. A retired note stays in its file, so it can be updated or removed by hand.

On the site, a note's text is escaped and only backticks become code. A file the checker rejects is left out of the site whole, with a warning in the publish log.

## Checking

From the repository root, standard library only:

```sh
python3 scripts/check_notes.py
```

The [Check](../.github/workflows/check.yml) workflow runs this on every pull request, and `tests/test_check_notes.py` covers the error cases. Please run the checker before you send a notes change.

To add a note, open a pull request with the new `notes/<name>.toml`. Pick packages that are not already covered, and keep the claim checkable from the links you give.
