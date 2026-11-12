# How to get a missing wheel added upstream

This is a short guide for someone who has found a missing wheel on the [tracker](https://wheelgap.github.io/tracker/) and wants to help the project ship one. It is the “how-to for adding wheels upstream” from [phase 3 of the roadmap](https://github.com/wheelgap/wheelgap/discussions/43).

The usual fix is a CI job, not a patch to the library itself. The notes below stay short on purpose: follow the project’s own contributing guide, and prefer links to current tooling docs over a copied config that will go stale.

## 1. Ask first

Search the project’s issues and discussions for the platform (musllinux, aarch64, Windows on ARM, free-threaded builds, and so on). If nobody has asked, open **one** issue that:

- links the package’s page on this tracker, `https://wheelgap.github.io/tracker/p/<name>/` (use the package’s normalized name);
- names the platforms that have no wheel;
- offers to do the work.

Respect a “not now”. Never file the same issue across many projects at once; a burst of drive-by tickets is worse than a missing wheel. Wait for a go-ahead before opening a PR.

## 2. The usual fix: cibuildwheel

Most projects already ship wheels with [cibuildwheel](https://cibuildwheel.pypa.io/) in GitHub Actions. The [setup page](https://cibuildwheel.pypa.io/en/stable/setup/) and the [options reference](https://cibuildwheel.pypa.io/en/stable/options/) are the place to start. When you add a platform, cover:

- **musllinux** (Alpine and other musl systems). cibuildwheel builds these by default on Linux; see [Linux builds](https://cibuildwheel.pypa.io/en/stable/platforms/#linux). If they are missing, the project usually skips them: [pygit2#1266](https://github.com/libgit2/pygit2/pull/1266) is a compact example that removed its `*musl*` skip and published the wheels.
- **Linux aarch64.** GitHub’s [arm64 hosted runners](https://docs.github.com/en/actions/reference/runners/github-hosted-runners#standard-github-hosted-runners-for-public-repositories) (`ubuntu-24.04-arm`) build natively. QEMU still works, and is much slower: [psutil#2425](https://github.com/giampaolo/psutil/pull/2425) added manylinux aarch64 wheels with QEMU, while [PyYAML#864](https://github.com/yaml/pyyaml/pull/864) moved its aarch64 jobs to native `ubuntu-24.04-arm`.
- **Windows on ARM.** cibuildwheel documents [Windows ARM64 builds](https://cibuildwheel.pypa.io/en/stable/platforms/#windows-arm64). It can cross-compile them on any Windows runner (select it with [`archs`](https://cibuildwheel.pypa.io/en/stable/options/#archs); only setuptools and setuptools-rust builds cross-compile), but tests need a native `windows-11-arm` runner; cross-compiled ARM64 wheels go out untested. [PyYAML#864](https://github.com/yaml/pyyaml/pull/864) is an example of adding `win_arm64` to an existing matrix on `windows-11-arm`. More in [Windows on ARM (win_arm64)](#windows-on-arm-win_arm64) below.
- **Free-threaded builds** (`cp314t`, `cp315t`): cibuildwheel builds them by default (`cp314t` since 3.1, `cp315t` since 4.2), so a missing one usually means the project skips `cp31?t-*` or pins an older cibuildwheel. Removing that skip declares free-threading support, so leave that decision to the maintainers.
- **Testing the wheel** in CI with [`test-command`](https://cibuildwheel.pypa.io/en/stable/options/#test-command) (`CIBW_TEST_COMMAND`), so a broken wheel does not get uploaded.

### Windows on ARM (win_arm64)

The [Windows on ARM campaign](https://wheelgap.github.io/tracker/campaigns/win-arm64/) lists the most-downloaded packages that ship Windows x86_64 wheels but no ARM64 ones. The specifics:

- **Runner.** GitHub’s `windows-11-arm` runners are [free for public repositories](https://github.blog/changelog/2025-08-07-arm64-hosted-runners-for-public-repositories-are-now-generally-available/). On one, cibuildwheel’s default `archs` (`auto`) builds ARM64, so adding `windows-11-arm` to the existing `os` matrix is often the whole change, and the wheels are tested like the others.
- **Cross-compiling** instead, on the existing x64 Windows job: set `CIBW_ARCHS_WINDOWS: ARM64` (or `AMD64 ARM64` to keep the x64 wheels in the same job). This only works for setuptools and setuptools-rust builds, and the ARM64 tests are skipped, so prefer the native runner when the project tests its wheels. Rust extensions also need the `aarch64-pc-windows-msvc` target installed.
- **Usual blockers:**
  - **No ARM64 build of a C dependency.** Many projects download prebuilt x64 libraries (OpenSSL, libpq, zlib, and so on) in their Windows job. The ARM64 job needs an ARM64 build of each, for example from vcpkg’s `arm64-windows` triplet, or built in CI. Look for this first: it is often most of the work, and the reason a maintainer may say “not now”.
  - **MSVC flags and x86-only code.** Flags such as `/arch:AVX2`, and SSE or AVX intrinsics from `<immintrin.h>`, only exist on x86. Guard them with `#if defined(_M_X64) || defined(_M_IX86)` and keep the portable code path for `_M_ARM64`, rather than adding NEON code nobody asked for.
  - **Test dependencies without a win_arm64 wheel.** The test step then builds them from source or fails. Check them with `uvx wheelgap <name> -t win-arm64` before blaming the package itself.
- **Checking a release:** pip can confirm a wheel exists without a Windows machine: `pip download --no-deps --only-binary=:all: --platform win_arm64 --python-version 3.14 --implementation cp --abi cp314 -d /tmp/check <name>==<version>`.

Keep the change to the workflow and the project’s existing test suite. Don’t add a new backend or rewrite the package’s build unless the maintainers ask for that.

## 3. Pure-Python packages that only ship an sdist

If the package has no compiled code, a single `py3-none-any` wheel covers every platform at once. The usual `pyproject.toml` is:

```toml
[build-system]
requires = ["setuptools>=69"]
build-backend = "setuptools.build_meta"
```

Then, from a clean checkout:

```sh
python -m pip install build
python -m build
# dist/ will contain both the sdist and a py3-none-any wheel
```

[Packaging Python Projects](https://packaging.python.org/en/latest/tutorials/packaging-projects/) walks through the same steps, and the PyPA guide to [publishing with GitHub Actions](https://packaging.python.org/en/latest/guides/publishing-package-distribution-releases-using-github-actions-ci-cd-workflows/) covers trusted publishing to PyPI. A project that already uses setuptools but never runs `python -m build` in CI often only needs that job, not a new backend.

## 4. How to check it worked

Against the new release, once it is on PyPI:

```sh
uvx wheelgap <name>==<new version> -t all --all
```

That should show a ✓ on the platforms you added (without `--all`, a package with no gaps prints a single summary line). Add `-p 3.14t` to check free-threaded wheels. This tracker’s nightly scan will pick the new version up the next night; the package page and the most-wanted lists update when that scan is published.

If `wheelgap` still reports `source` or `missing` for a platform you think you built, rerun with `--explain` to see each wheel wheelgap rejected and why, check the built wheel with `auditwheel show` (Linux only; `delocate-listdeps` on macOS), and compare against wheelgap’s glibc, musl and macOS baselines, which are listed in [the tracker caveats](https://github.com/wheelgap/tracker#caveats).
