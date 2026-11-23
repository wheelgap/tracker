// node --test tests/site/check-logic.test.mjs: the repo checker's pure helpers (site/check/check-logic.mjs).
import { test } from "node:test";
import assert from "node:assert/strict";
import {
  parseRepo, inputHint, rawUrl, pickLockfile, autoPick, isSideFile, pickRequestedFile, isReadableFile, fileKind, toQuery, fromQuery, sortPythons, freeThreaded, fixFor, rowsFor, headline,
  cliCommand, packageCliCommand, projectNameFromMetadata, metadataFiles, fixPrompt, verdictText, shareText, nextSteps, validRef, labVerdict, labFor, canonicalName, gapWheelFor,
  gapWheelFixText, withoutUnreadable, unreadableReason, tileText, missedText, privateText, badgeMarkdown, trimProject, trimReleases,
  fixLinks, validIssuesUrl,
} from "../../site/check/check-logic.mjs";

test("PyPI trimming keeps the pinned consumer's fields without changing the input", () => {
  const file = {
    filename: "demo-1.0-py3-none-any.whl", requires_python: ">=3.13", yanked: "withdrawn",
    packagetype: "bdist_wheel", upload_time_iso_8601: "2026-11-21T00:00:00Z", size: 123,
    url: "https://files.pythonhosted.org/unused", digests: { sha256: "unused" }, comment_text: "unused",
  };
  const doc = {
    info: { version: "1.0", project_urls: { Source: "https://example.org/src" }, home_page: "https://example.org/",
      description: "unused", requires_python: ">=3.13", yanked: true },
    urls: [file], releases: { "1.0": [file], "0.9": [] }, last_serial: 123,
  };
  const before = structuredClone(doc);
  const keptFile = { filename: file.filename, requires_python: ">=3.13", yanked: "withdrawn" };
  const keptReleases = { "1.0": [keptFile], "0.9": [] };
  assert.deepEqual(trimProject(doc), {
    info: { version: "1.0", project_urls: doc.info.project_urls, home_page: doc.info.home_page },
    urls: [keptFile], releases: keptReleases,
  });
  assert.deepEqual(trimReleases(doc.releases), keptReleases);
  assert.deepEqual(doc, before);
  // A JSON key named __proto__ must remain an ordinary entry, not set the returned object's prototype.
  const special = trimReleases(JSON.parse('{"__proto__": [], "constructor": []}'));
  assert.deepEqual(Object.keys(special), ["__proto__", "constructor"]);
  assert.equal(Object.getPrototypeOf(special), Object.prototype);
});

test("trimming preserves malformed and missing shapes instead of inventing empty file lists", () => {
  for (const bad of [null, false, 3, "not JSON metadata", [], [null]]) {
    assert.deepEqual(trimProject(bad), bad);
    assert.deepEqual(trimReleases(bad), bad);
    assert.deepEqual(trimProject({ info: bad, urls: bad, releases: bad }), { info: bad, urls: bad, releases: bad });
  }
  assert.deepEqual(trimProject({ message: "Not Found" }), {}); // still missing urls and releases
  assert.deepEqual(trimProject({ info: { version: "1.0" } }), { info: { version: "1.0" } });
  assert.deepEqual(trimProject({ urls: [], releases: {} }), { urls: [], releases: {} }); // valid empty is distinct
  const files = [null, false, "bad", [], {}, { filename: null }, { filename: 1 }, { yanked: false },
    { filename: "demo.whl", requires_python: null, yanked: false }];
  assert.deepEqual(trimProject({ urls: files }), { urls: files });
  assert.deepEqual(trimReleases({ "1.0": files, "2.0": null, "3.0": {} }), { "1.0": files, "2.0": null, "3.0": {} });
  // Invalid values of consumed metadata are the consumer's decision too, not the trimmer's.
  assert.deepEqual(trimProject({ info: { version: null, project_urls: [], home_page: 4 } }),
    { info: { version: null, project_urls: [], home_page: 4 } });
});

test("parseRepo reads the forms people paste", () => {
  const plain = { owner: "fastapi", name: "fastapi", ref: null, dir: null, file: null };
  assert.deepEqual(parseRepo("fastapi/fastapi"), plain);
  assert.deepEqual(parseRepo("https://github.com/fastapi/fastapi"), plain);
  assert.deepEqual(parseRepo("github.com/fastapi/fastapi.git/"), plain);
  assert.deepEqual(parseRepo("git@github.com:fastapi/fastapi.git"), plain);
  assert.deepEqual(parseRepo("https://www.github.com/fastapi/fastapi?tab=readme#top"), plain);
  assert.deepEqual(parseRepo("https://github.com/o/r/tree/dev/backend/api"), { owner: "o", name: "r", ref: "dev", dir: "backend/api", file: null });
  assert.deepEqual(parseRepo("https://github.com/o/r/blob/main/backend/uv.lock"), { owner: "o", name: "r", ref: "main", dir: "backend", file: "backend/uv.lock" });
  assert.deepEqual(parseRepo("https://github.com/o/r/blob/main/uv.lock"), { owner: "o", name: "r", ref: "main", dir: null, file: "uv.lock" });
  assert.deepEqual(
    parseRepo("https://github.com/celery/celery/blob/main/requirements/default.txt"),
    { owner: "celery", name: "celery", ref: "main", dir: "requirements", file: "requirements/default.txt" },
  );
  assert.deepEqual(
    parseRepo("https://github.com/o/r/blob/main/my%20dir/my%20reqs.txt"),
    { owner: "o", name: "r", ref: "main", dir: "my dir", file: "my dir/my reqs.txt" },
  );
});

test("parseRepo refuses what isn't a repo", () => {
  for (const bad of ["", "fastapi", "https://gitlab.com/a", "a b/c", "../x", "o/..", "o/r/tree/main/../../etc", "javascript:alert(1)", "o/r/tree/ma in/x"]) {
    assert.equal(parseRepo(bad), null, bad);
  }
});

test("pickRequestedFile checks a named file, adds a readable one from the tree, or says why it fell back", () => {
  const locks = [{ path: "pyproject.toml", offline: false }, { path: "uv.lock", offline: true }];
  assert.deepEqual(pickRequestedFile(locks, null, false, null), { lock: locks[0], lockfiles: locks, auto: true, notice: null, choose: null });
  assert.deepEqual(pickRequestedFile(locks, "uv.lock", true, null), { lock: locks[1], lockfiles: locks, auto: false, notice: null, choose: null });
  const added = pickRequestedFile(locks, "requirements/default.txt", true, null);
  assert.deepEqual(added.lock, { path: "requirements/default.txt", offline: false, added: true });
  assert.deepEqual(added.lockfiles.map((l) => l.path), ["pyproject.toml", "uv.lock", "requirements/default.txt"]);
  assert.equal(added.auto, false);
  assert.deepEqual(pickRequestedFile(locks, "tests/poetry.lock", true, null).lock, { path: "tests/poetry.lock", offline: true, added: true });
  // The only checkable file being one the link named: the badge's default must still come from the repo's own list.
  const only = pickRequestedFile([], "requirements/base.txt", true, null);
  assert.equal(only.lock.path, "requirements/base.txt");
  assert.equal(pickLockfile(only.lockfiles.filter((l) => !l.added), null), null);
  assert.equal(isReadableFile("REQS.TXT"), false);
  const missing = pickRequestedFile(locks, "requirements/gone.txt", false, null);
  assert.equal(missing.lock, locks[0]);
  assert.equal(missing.auto, true);
  assert.equal(missing.notice, "requirements/gone.txt wasn't found in this repository.");
  assert.equal(pickRequestedFile(locks, "setup.cfg", true, null).notice, "setup.cfg isn't a file this page reads.");
  assert.deepEqual(pickRequestedFile([], "setup.cfg", true, null), { lock: null, lockfiles: [], auto: true, notice: "setup.cfg isn't a file this page reads.", choose: null });
  for (const yes of ["uv.lock", "a/Pipfile.lock", "pixi.lock", "pylock.dev.toml", "uv-py312.lock", "requirements-dev.lock", "x/reqs.in", "pyproject.toml"]) {
    assert.equal(isReadableFile(yes), true, yes);
  }
  // script.py.lock: the pinned wheelgap 0.13.0 doesn't read a script's lock ("don't know how to read"), so it's
  // refused here with the fallback, never checked and reported as unreadable.
  for (const no of ["setup.cfg", "README.md", "setup.py", "Cargo.lock", "script.py.lock", "report.json", "pipfile.lock"]) {
    assert.equal(isReadableFile(no), false, no);
  }
  assert.equal(pickRequestedFile(locks, "scripts/tool.py.lock", true, null).notice, "scripts/tool.py.lock isn't a file this page reads.");
});

test("pickRequestedFile's own pick is autoPick()'s: several requirements files under other names are asked about", () => {
  const other = (path) => ({ path, offline: false, other: true });
  const several = [other("requirements/base.txt"), other("requirements/docs.txt")];
  // No file named, and a named file the page can't check: no guess, the files to ask between, and the notice.
  assert.deepEqual(pickRequestedFile(several, null, false, null), { lock: null, lockfiles: several, auto: true, notice: null, choose: several });
  assert.deepEqual(pickRequestedFile(several, "README.md", true, null), { lock: null, lockfiles: several, auto: true, notice: "README.md isn't a file this page reads.", choose: several });
  assert.deepEqual(pickRequestedFile([other("requirements-dev.txt")], null, false, null).choose, [other("requirements-dev.txt")]);
  // A named one is checked, listed or added.
  assert.equal(pickRequestedFile(several, "requirements/docs.txt", false, null).choose, null);
  assert.equal(pickRequestedFile(several, "requirements/docs.txt", false, null).lock.path, "requirements/docs.txt");
  const added = pickRequestedFile(several, "deps/prod.txt", true, null);
  assert.deepEqual([added.lock.path, added.choose], ["deps/prod.txt", null]);
});

test("a named file is checked offline exactly when wheelgap reads it as a lockfile that lists its files", () => {
  // As sources.file_kind in wheelgap 0.13.0, and driver.py's OFFLINE_KINDS: by the reader its name picks, not by an
  // exact name, so a pylock.dev.toml or uv-py312.lock isn't read as a lookups file (its left-out Pythons would show
  // false gaps), and a requirements-dev.lock is still looked up.
  const kinds = {
    "uv.lock": "uv", "a/pylock.toml": "pylock", "poetry.lock": "poetry", "pdm.lock": "pdm", "Pipfile.lock": "pipfile",
    "pixi.lock": "pixi", "pyproject.toml": "pyproject", "pylock.dev.toml": "pylock", "uv-py312.lock": "uv",
    "requirements-dev.lock": "requirements", "requirements.uv.lock": "requirements", "x/dev.txt": "requirements",
    "reqs.in": "requirements", "script.py.lock": null, "REQS.TXT": null, "report.json": null,
  };
  for (const [path, kind] of Object.entries(kinds)) assert.equal(fileKind(path), kind, path);
  const offline = (path) => pickRequestedFile([], path, true, null).lock.offline;
  for (const path of ["pylock.dev.toml", "envs/uv-py312.lock", "tests/poetry.lock", "examples/pdm.lock"]) assert.equal(offline(path), true, path);
  for (const path of ["requirements-dev.lock", "requirements.uv.lock", "a/Pipfile.lock", "b/pixi.lock", "c/pyproject.toml", "reqs.in"]) {
    assert.equal(offline(path), false, path);
  }
});

test("inputHint identifies package names and non-GitHub hosts, and ignores everything else", () => {
  assert.deepEqual(inputHint("numpy"), { kind: "package", name: "numpy" });
  assert.deepEqual(inputHint("Pillow"), { kind: "package", name: "pillow" });
  assert.deepEqual(inputHint("ruamel.yaml"), { kind: "package", name: "ruamel-yaml" });
  assert.deepEqual(inputHint("https://gitlab.com/owner/project"), { kind: "other-host", host: "gitlab.com" });
  assert.deepEqual(inputHint("https://codeberg.org/owner/project"), { kind: "other-host", host: "codeberg.org" });
  assert.deepEqual(inputHint("git@gitlab.com:owner/project.git"), { kind: "other-host", host: "gitlab.com" });
  assert.deepEqual(inputHint("ssh://git@gitlab.com/owner/project.git"), { kind: "other-host", host: "gitlab.com" });
  assert.deepEqual(inputHint("git+https://gitlab.com/owner/project"), { kind: "other-host", host: "gitlab.com" });
  assert.deepEqual(inputHint("gitlab.com/owner/project"), { kind: "other-host", host: "gitlab.com" });
  // A bare domain is a host, not a package called gitlab-com.
  assert.deepEqual(inputHint("gitlab.com"), { kind: "other-host", host: "gitlab.com" });
  assert.deepEqual(inputHint("codeberg.org"), { kind: "other-host", host: "codeberg.org" });
  assert.deepEqual(inputHint("zope.interface"), { kind: "package", name: "zope-interface" });
  assert.deepEqual(inputHint("jaraco.net"), { kind: "package", name: "jaraco-net" });
  assert.deepEqual(inputHint("https://pypi.org/project/NumPy/"), { kind: "package", name: "numpy" });
  assert.deepEqual(inputHint("pypi.org/project/ruamel.yaml/0.18.6/"), { kind: "package", name: "ruamel-yaml" });
  // Every input that gets a hint is one parseRepo() rejects, or the page would check it as a GitHub repo instead.
  for (const hinted of [
    "numpy", "gitlab.com/owner/project", "codeberg.org/owner/project", "bitbucket.org/o/r", "gitlab.com/owner",
    "https://gitlab.com/owner/project", "git@gitlab.com:owner/project.git", "pypi.org/project/numpy", "https://pypi.org/project/numpy/",
  ]) {
    assert.ok(inputHint(hinted), hinted);
    assert.equal(parseRepo(hinted), null, hinted);
  }
  // GitHub's own hosts get the default message, not "GitHub repositories only".
  for (const none of [
    "owner/repo", "https://github.com/owner/repo", "", "   ", "numpy scipy", "<script>", "numpy<2", "github.com", "www.github.com",
    "https://raw.githubusercontent.com/o/r/main/uv.lock", "https://api.github.com/repos/o/r", "https://gist.github.com/u/1",
  ]) {
    assert.equal(inputHint(none), null, none);
  }
});

test("rawUrl encodes each path segment", () => {
  assert.equal(rawUrl({ owner: "o", name: "r" }, "HEAD", "a b/uv.lock"), "https://raw.githubusercontent.com/o/r/HEAD/a%20b/uv.lock");
  assert.equal(rawUrl({ owner: "o", name: "r" }, "feat/x", "uv.lock"), "https://raw.githubusercontent.com/o/r/feat/x/uv.lock");
});

test("pickLockfile prefers the URL's folder, then plan()'s first file, whichever kind it is", () => {
  // In plan()'s order: shallowest folder first, then wheelgap's own order, a pyproject.toml last in its folder.
  const locks = [
    { path: "requirements.txt", offline: false },
    { path: "pyproject.toml", offline: false },
    { path: "backend/uv.lock", offline: true },
    { path: "backend/requirements.txt", offline: false },
    { path: "docs/requirements.txt", offline: false },
  ];
  // `wheelgap` in the root reads requirements.txt: a lockfile deeper in the repo doesn't outrank it.
  assert.equal(pickLockfile(locks, null).path, "requirements.txt");
  assert.equal(pickLockfile(locks, "backend").path, "backend/uv.lock");
  // The folder the URL named wins, also when its only file is checked by PyPI lookups.
  assert.equal(pickLockfile(locks, "docs").path, "docs/requirements.txt");
  assert.equal(pickLockfile(locks, "elsewhere").path, "requirements.txt");
  assert.equal(pickLockfile(locks.slice(1), null).path, "pyproject.toml");
  assert.equal(pickLockfile([], null), null);
});

test("an unreadable pyproject.toml the page picked itself gives way to the repo's other files", () => {
  const locks = [{ path: "pyproject.toml", offline: false }, { path: "backend/uv.lock", offline: true }];
  assert.deepEqual(withoutUnreadable(locks, "pyproject.toml", true), [locks[1]]);
  assert.equal(withoutUnreadable(locks, "pyproject.toml", false), null); // a link or the visitor named it: say why
  assert.equal(withoutUnreadable(locks, "backend/uv.lock", true), null); // a broken lockfile is reported, not skipped
  assert.equal(withoutUnreadable([locks[0]], "pyproject.toml", true), null); // nothing else to check
  const nested = [{ path: "a/pyproject.toml", offline: false }, { path: "a/b/pyproject.toml", offline: false }];
  assert.deepEqual(withoutUnreadable(nested, "a/pyproject.toml", true), [nested[1]]);
  // Celery-style repo: an auto-picked pyproject.toml with no [project] table moves on to its requirements files, and
  // with several of those the page asks which one (autoPick), as `wheelgap DIR` lists them: none is picked by name.
  const celery = [
    { path: "pyproject.toml", offline: false },
    { path: "requirements/default.txt", offline: false, other: true },
    { path: "requirements/docs.txt", offline: false, other: true },
  ];
  const rest = withoutUnreadable(celery, "pyproject.toml", true);
  assert.deepEqual(autoPick(rest, null), { lock: null, choose: rest });
});

test("autoPick keeps the exactly-named files' pick, checks a lone other requirements file, and asks between several", () => {
  const other = (path) => ({ path, offline: false, other: true });
  const named = [{ path: "pyproject.toml", offline: false }, { path: "backend/uv.lock", offline: true }];
  // Exactly-named files decide as pickLockfile() does, wherever the others are (they come last in plan() anyway).
  assert.equal(autoPick([...named, other("requirements-dev.txt")], null).lock.path, "pyproject.toml");
  // A subfolder's file beside a root requirements-dev.txt: `wheelgap DIR` counts that one as a rival (load_directory's
  // `rivals`), so it asks between them, and so does the page since #238. Before, the page checked backend/uv.lock.
  assert.deepEqual(autoPick([named[1], other("requirements-dev.txt")], null), { lock: null, choose: [named[1], other("requirements-dev.txt")], why: "rivals" });
  assert.equal(autoPick([...named, other("backend/requirements-prod.txt")], "backend").lock.path, "backend/uv.lock");
  assert.equal(autoPick([other("requirements/base.txt")], null).lock.path, "requirements/base.txt");
  // Several: none, whatever their names or the folder a URL named.
  const several = [other("requirements/base.txt"), other("requirements/docs.txt"), other("requirements/local.txt")];
  assert.deepEqual(autoPick(several, null), { lock: null, choose: several });
  assert.deepEqual(autoPick(several, "requirements"), { lock: null, choose: several });
  assert.deepEqual(autoPick([], null), { lock: null, choose: null });
});

// #238: with no input at the root, the rules of `wheelgap DIR` (load_directory() and _nested_inputs() in wheelgap's
// sources.py on main): one input per subfolder, up to 3 folders down, checked only when it's the only one, isn't
// named for development, tests, docs or examples, and the root isn't a project of its own.
const at = (path) => ({ path, offline: /(uv|poetry|pdm)\.lock$|pylock\.toml$/.test(path) });
const req = (path) => ({ path, offline: false, other: true });

test("autoPick checks the only input in a subfolder, and says so (MiroFish: backend/uv.lock)", () => {
  assert.deepEqual(autoPick([at("backend/uv.lock")], null), { lock: at("backend/uv.lock"), choose: null, why: "subfolder" });
  // One per folder, its first in plan()'s order: a pyproject.toml beside a lockfile isn't a second input.
  const backend = [at("backend/uv.lock"), at("backend/requirements.txt"), at("backend/pyproject.toml")];
  assert.equal(autoPick(backend, null).lock.path, "backend/uv.lock");
  assert.equal(autoPick([at("a/b/c/uv.lock")], null).lock.path, "a/b/c/uv.lock"); // 3 folders down
  // A root pyproject.toml of tool settings only (not a project) the page moved on from doesn't stop it.
  assert.equal(autoPick([at("backend/uv.lock")], null, { pyprojects: { "pyproject.toml": "settings" } }).lock.path, "backend/uv.lock");
  // Nor does a setup.py elsewhere, or a link's folder with nothing in it.
  assert.equal(autoPick([at("backend/uv.lock")], null, { rootProjectFiles: [] }).lock.path, "backend/uv.lock");
  assert.equal(autoPick([at("backend/uv.lock")], "docs").lock.path, "backend/uv.lock");
  assert.deepEqual(pickRequestedFile([at("backend/uv.lock")], null, false, null), {
    lock: at("backend/uv.lock"), lockfiles: [at("backend/uv.lock")], auto: true, notice: null, choose: null, why: "subfolder",
  });
});

test("autoPick asks when the root is a project, the subfolder inputs are several or side files, or more could be missing", () => {
  // transformers: a root pyproject.toml with no dependencies the page could read, beside setup.py, and
  // benchmark_v2/requirements.txt, a benchmarks file by its folder's name.
  const transformers = [at("benchmark_v2/requirements.txt")];
  for (const ctx of [{}, { rootProjectFiles: ["setup.py"], pyprojects: { "pyproject.toml": "settings" } }, { pyprojects: { "pyproject.toml": "empty" } }]) {
    assert.deepEqual(autoPick(transformers, null, ctx), { lock: null, choose: transformers, why: "side" });
  }
  // langchain: many libs/*/uv.lock, nothing at the root.
  const langchain = ["core", "langchain", "text-splitters", "partners/openai", "partners/anthropic"].map((d) => at(`libs/${d}/uv.lock`));
  assert.deepEqual(autoPick(langchain, null), { lock: null, choose: langchain, why: "subfolders" });
  // cpython: Doc/pylock.toml, a docs file.
  assert.deepEqual(autoPick([at("Doc/pylock.toml")], null), { lock: null, choose: [at("Doc/pylock.toml")], why: "side" });
  // A root setup.py (or setup.cfg) and one tools/requirements.txt: the root is the project, so it's asked about.
  for (const name of ["setup.py", "setup.cfg"]) {
    assert.deepEqual(autoPick([at("tools/requirements.txt")], null, { rootProjectFiles: [name] }), {
      lock: null, choose: [at("tools/requirements.txt")], why: "project", project: `Its own dependencies are in ${name}, which wheelgap can't read`,
    });
  }
  // A root pyproject.toml the page tried and moved on from, when it's a project: [project] with nothing in it, one
  // wheelgap refused (dynamic dependencies, a [build-system]), or one whose packages aren't looked up on PyPI.
  const why = (state) => autoPick([at("backend/uv.lock")], null, { pyprojects: { "pyproject.toml": state } }).project;
  assert.equal(why("empty"), "Its pyproject.toml lists no dependencies");
  assert.equal(why("unread"), "Its own dependencies are in pyproject.toml, which wheelgap can't read");
  assert.equal(why("private"), "Its own dependencies are in pyproject.toml, which this page can't look up");
  // A subfolder's pyproject.toml the page tried is no word on the root.
  assert.equal(autoPick([at("backend/uv.lock")], null, { pyprojects: { "app/pyproject.toml": "unread" } }).why, "subfolder");
  // GitHub listed part of the tree, or plan() left files out: the one seen may not be the only one.
  assert.deepEqual(autoPick([at("backend/uv.lock")], null, { partial: true }), { lock: null, choose: [at("backend/uv.lock")], why: "subfolders" });
  // A side folder anywhere on the way down, in any case.
  for (const path of ["tests/uv.lock", "e2e_tests/app/uv.lock", "Examples/agent/uv.lock", "docs/source/requirements.txt", "services/api/benchmarks/uv.lock"]) {
    assert.equal(autoPick([at(path)], null).why, "side", path);
  }
  // A side input doesn't count against the one that isn't, and it's offered with it only when there's no other.
  assert.deepEqual(autoPick([at("backend/uv.lock"), at("docs/requirements.txt")], null).lock.path, "backend/uv.lock");
  // A requirements file under another name beside a subfolder's requirements.txt could be the project's too.
  const docker = [at("docker/main/requirements.txt"), req("docker/main/requirements-wheels.txt"), req("docker/main/requirements-dev.txt")];
  assert.deepEqual(autoPick(docker, null), { lock: null, choose: docker.slice(0, 2), why: "rivals" });
  assert.equal(autoPick([at("docker/main/uv.lock"), req("docker/main/requirements-wheels.txt")], null).lock.path, "docker/main/uv.lock");
});

test("autoPick reads the root's other requirements files as wheelgap's _other_requirements() does", () => {
  // Only requirements*.txt in a folder, and any .txt in its requirements/ folder: plan() also marks
  // prod-requirements.txt and dev_requirements.txt, which `wheelgap DIR` doesn't read, so they don't decide the pick.
  const backend = at("backend/uv.lock");
  for (const names of [["prod-requirements.txt"], ["dev-requirements.txt"], ["dev-requirements.txt", "test-requirements.txt"], ["dev_requirements.txt"]]) {
    assert.deepEqual(autoPick([backend, ...names.map(req)], null), { lock: backend, choose: null, why: "subfolder" }, names.join(" "));
  }
  // Nor beside a subfolder's requirements.txt: backend/docker-requirements.txt is no rival, backend/requirements-prod.txt is.
  const reqs = at("backend/requirements.txt");
  assert.deepEqual(autoPick([reqs, req("backend/docker-requirements.txt")], null), { lock: reqs, choose: null, why: "subfolder" });
  assert.equal(autoPick([reqs, req("backend/requirements-prod.txt")], null).why, "rivals");
  // A lone one of those at the root is no input `wheelgap DIR` reads: offered, not checked.
  assert.deepEqual(autoPick([req("prod-requirements.txt")], null), { lock: null, choose: [req("prod-requirements.txt")], why: "elsewhere" });
});

test("autoPick never takes the one input it lists for the only one when wheelgap DIR finds more in folders plan() skips", () => {
  // build/requirements.txt: plan() skips build/ (wheelgap 0.13.0's suggest_targets._SKIP_DIRS), wheelgap's
  // _nested_inputs() doesn't, so `wheelgap DIR` asks between it and backend/uv.lock.
  const backend = at("backend/uv.lock");
  assert.deepEqual(autoPick([backend], null, { unlistedInputs: ["build/requirements.txt"] }), { lock: null, choose: [backend], why: "subfolders" });
  assert.deepEqual(autoPick([backend], null, { unlistedInputs: ["dist/pkg/uv.lock"] }).lock, null);
  // A side one there (tests/, examples/) doesn't count against it, as in `wheelgap DIR`.
  assert.deepEqual(autoPick([backend], null, { unlistedInputs: ["tests/requirements.txt", "examples/app/uv.lock"] }), { lock: backend, choose: null, why: "subfolder" });
});

test("autoPick keeps root files first, and offers deeper files without picking one", () => {
  // A file at the root, or in the folder a link names, is picked as before, whatever is in the subfolders.
  const repo = [at("uv.lock"), at("Doc/pylock.toml"), at("libs/core/uv.lock"), at("libs/langchain/uv.lock")];
  assert.deepEqual(autoPick(repo, null), { lock: at("uv.lock"), choose: null });
  assert.deepEqual(autoPick(repo, "libs/core"), { lock: at("libs/core/uv.lock"), choose: null });
  assert.deepEqual(autoPick([at("pyproject.toml"), at("backend/uv.lock")], null, { rootProjectFiles: ["setup.py"] }), { lock: at("pyproject.toml"), choose: null });
  // A lone requirements file under another name at the root comes before the subfolders, as it does in `wheelgap DIR`.
  assert.deepEqual(autoPick([at("backend/uv.lock"), req("requirements/base.txt")], null), { lock: req("requirements/base.txt"), choose: null });
  // Nothing up to 3 folders down: no pick, the files there are to choose from.
  const deep = [at("a/b/c/d/uv.lock"), req("tools/requirements-prod.txt")];
  assert.deepEqual(autoPick(deep, null), { lock: null, choose: deep, why: "elsewhere" });
  // transformers today: after its root pyproject.toml, only utils/checkers-requirements.txt, which `wheelgap DIR`
  // doesn't read by itself (it's under another name, in a subfolder). Before #238 the page checked it.
  assert.deepEqual(autoPick([req("utils/checkers-requirements.txt")], null, { rootProjectFiles: ["setup.py"] }), {
    lock: null, choose: [req("utils/checkers-requirements.txt")], why: "elsewhere",
  });
});

test("a lone requirements file named for development, tests, docs or examples is offered, never checked on its own", () => {
  // As `wheelgap DIR` on main (_is_side): it lists such a file and never checks it by itself.
  const other = (path) => ({ path, offline: false, other: true });
  for (const path of ["requirements-dev.txt", "requirements/test.txt", "requirements_docs.txt", "requirements/ci.txt", "examples/requirements-prod.txt", "requirements-Typing.txt"]) {
    assert.deepEqual(autoPick([other(path)], null), { lock: null, choose: [other(path)] }, path);
  }
  for (const path of ["requirements/base.txt", "requirements-prod.txt", "requirements/local.txt", "requirements-devices.txt", "requirements/latest.txt"]) {
    assert.equal(autoPick([other(path)], null).lock?.path, path, path);
  }
  assert.equal(isSideFile("requirements-dev.txt"), true);
  assert.equal(isSideFile("requirements/base.txt"), false);
});

test("a file with nothing to check is no answer; one whose packages were skipped or aren't installed is", () => {
  const error = "pyproject.toml: no [project] table, so there are no dependencies to read";
  assert.equal(unreadableReason({ kind: "source", error }), error);
  // A [project] table with no dependencies: read without an error, and nothing in it to check.
  const empty = { looked_up: true, listed: 0, packages: 0, skipped: [], rows: [] };
  assert.equal(unreadableReason(empty), "it lists no dependencies");
  assert.equal(unreadableReason({ ...empty, listed: 2 }), null); // left off pypi.org, or no target picked installs them
  assert.equal(unreadableReason({ ...empty, listed: 3, packages: 3 }), null);
  assert.equal(unreadableReason({ ...empty, looked_up: false }), null); // a lockfile with no packages says so itself
  assert.equal(unreadableReason({ ...empty, listed: undefined }), null); // a driver that doesn't count them
  assert.equal(unreadableReason({ kind: "targets", error: "unknown platform" }), null);
  assert.equal(unreadableReason({ kind: "needs-index", error: "poetry.lock needs the index" }), null);
});

test("an all-private pyproject gives way only when automatically picked and another file remains", () => {
  const result = { lockfile: "pyproject.toml", looked_up: true, listed: 3, packages: 0,
    private: ["fastapi", "mylib", "numpy"], private_count: 3, skipped: ["fastapi", "mylib", "numpy"] };
  const reason = "its index or source settings don't identify which packages are from PyPI";
  const locks = [{ path: result.lockfile }, { path: "backend/uv.lock" }];
  assert.equal(unreadableReason(result), reason);
  assert.deepEqual(withoutUnreadable(locks, result.lockfile, true), [locks[1]]);
  assert.equal(withoutUnreadable(locks, result.lockfile, false), null);
  assert.equal(withoutUnreadable([locks[0]], result.lockfile, true), null);
  // Repeated names with different markers are separate entries, but the display names are deduplicated.
  assert.equal(unreadableReason({ ...result, listed: 2, private_count: 2, private: ["numpy"] }), reason);
  // Kept private because settings files that apply to it couldn't all be read: the reason says that instead.
  assert.equal(unreadableReason({ ...result, configs_unread: ["uv.toml"] }), "installer settings that apply to it couldn't be read here (uv.toml)");
  assert.equal(unreadableReason({ ...result, configs_unknown: true }), "GitHub listed only part of the repo's files, so installer settings that apply to it may be missing");
  assert.equal(unreadableReason({ ...result, configs: ["pyproject.toml"], configs_unread: [] }), reason);
  for (const change of [
    { packages: 1, private_count: 2 }, // some packages were checked
    { private_count: 2 }, // another package was skipped for a different reason, or excluded by markers
    { listed: 2, private_count: 0, private: [] }, // all marker-excluded
    { listed: 0, private_count: 0, looked_up: false }, // empty offline lock
    { private_count: undefined }, // no exact count from an older driver
    { error: "unreadable" },
    { lockfile: "requirements.txt" },
  ]) assert.equal(unreadableReason({ ...result, ...change }), null, JSON.stringify(change));
});

test("a result's query round-trips, and unknown values are dropped", () => {
  const state = { repo: { owner: "o", name: "r", ref: "dev" }, lockfile: "backend/uv.lock", pythons: ["3.14", "3.14t"], platforms: ["linux-aarch64", "win-arm64"] };
  const q = toQuery(state);
  assert.equal(q, "repo=o/r&ref=dev&lock=backend/uv.lock&py=3.14,3.14t&t=linux-aarch64,win-arm64");
  const back = fromQuery(`?${q}`);
  assert.deepEqual(back, { repo: { owner: "o", name: "r", ref: "dev", dir: null, file: null }, lockfile: "backend/uv.lock", pythons: ["3.14", "3.14t"], platforms: ["linux-aarch64", "win-arm64"] });
  const reqQuery = toQuery({ repo: { owner: "celery", name: "celery", ref: "main" }, lockfile: "requirements/default.txt", pythons: ["3.13"], platforms: ["linux-x86_64"] });
  assert.equal(reqQuery, "repo=celery/celery&ref=main&lock=requirements/default.txt&py=3.13&t=linux-x86_64");
  assert.deepEqual(fromQuery(`?${reqQuery}`), {
    repo: { owner: "celery", name: "celery", ref: "main", dir: null, file: null },
    lockfile: "requirements/default.txt",
    pythons: ["3.13"],
    platforms: ["linux-x86_64"],
  });
  const odd = fromQuery("?repo=o/r&lock=../x&py=3.14,2.7,evil&t=linux-x86_64,amiga");
  assert.equal(odd.lockfile, null);
  for (const bad of ["/x/requirements.txt", "a//b.txt", "./uv.lock", "a/./uv.lock"]) {
    assert.equal(fromQuery(`?repo=o/r&lock=${encodeURIComponent(bad)}`).lockfile, null, bad);
  }
  assert.deepEqual(odd.pythons, ["3.14"]);
  assert.deepEqual(odd.platforms, ["linux-x86_64"]);
  assert.equal(fromQuery("?repo=nope"), null);
  assert.equal(fromQuery(""), null);
});

test("the badge Markdown links the check of the repo alone, and names the lockfile only when it isn't the default", () => {
  const repo = { owner: "fastapi", name: "full-stack-fastapi-template", ref: "dev", dir: "backend" };
  const badge = "[![Check wheels](https://wheelgap.github.io/tracker/check/badge.svg)]";
  const check = "https://wheelgap.github.io/tracker/check/";
  // Never the ref, the Pythons or the platforms: a click checks the default branch on its CI's targets that day.
  const plain = `${badge}(${check}?repo=fastapi/full-stack-fastapi-template)`;
  assert.equal(badgeMarkdown(repo, "uv.lock", "uv.lock"), plain);
  assert.equal(badgeMarkdown(repo, null, null), plain);
  assert.equal(badgeMarkdown(repo, "backend/uv.lock", "uv.lock"), `${badge}(${check}?repo=fastapi/full-stack-fastapi-template&lock=backend/uv.lock)`);
  // A path with characters that would end the Markdown link, or the query, early.
  const odd = "my app/a(1)&b=c#d/100%/[x] \"q\"/uv.lock";
  const md = badgeMarkdown({ owner: "o", name: "r" }, odd, "uv.lock");
  assert.equal(md, `${badge}(${check}?repo=o/r&lock=my+app/a%281%29%26b%3Dc%23d/100%25/%5Bx%5D+%22q%22/uv.lock)`);
  const url = md.slice(badge.length + 1, -1);
  assert.doesNotMatch(url, /[\s()[\]<>"']/);
  // The page reads the link back as the same repo and file, with nothing else picked.
  assert.deepEqual(fromQuery(new URL(url).search), { repo: { owner: "o", name: "r", ref: null, dir: null, file: null }, lockfile: odd, pythons: [], platforms: [] });
});

test("Pythons sort with each free-threaded build after its own", () => {
  assert.deepEqual(sortPythons(["3.15t", "3.13", "3.15", "3.14t", "3.14", "3.14"]), ["3.13", "3.14", "3.14t", "3.15", "3.15t"]);
  assert.equal(freeThreaded(["3.12", "3.14"]), "3.14t");
  assert.equal(freeThreaded(["3.10", "3.11"]), "3.13t"); // free-threading starts at 3.13
  assert.equal(freeThreaded(["3.14", "3.14t"]), null);
});

const row = (name, version, gaps, statuses = {}, extra = {}) => ({ name, version, gaps, unknown: [], keep: [], statuses, lookup: true, ...extra });

test("fixFor says one release, some, none, or not looked up", () => {
  const r = row("numpy", "1.26.4", ["3.14 linux-x86_64", "3.14 win-arm64"]);
  assert.deepEqual(fixFor(r, null), { kind: "pending", text: "" });
  assert.deepEqual(fixFor(r, { "numpy==1.26.4": { found: {}, loses: [] } }), { kind: "none", text: "no newer release has a wheel" });
  const all = fixFor(r, { "numpy==1.26.4": { found: { "3.14 linux-x86_64": "2.3.2", "3.14 win-arm64": "2.3.2" }, loses: [] } });
  assert.deepEqual(all, { kind: "all", version: "2.3.2", text: "2.3.2 (a new major version)" });
  const some = fixFor(r, { "numpy==1.26.4": { found: { "3.14 linux-x86_64": "1.26.5" }, loses: [] } });
  assert.deepEqual(some, { kind: "some", version: "1.26.5", text: "1.26.5 for 1 of 2 gaps" });
});

const result = {
  lockfile: "uv.lock",
  packages: 10,
  outside_lock: ["3.15"],
  targets: [
    { label: "3.14 linux-x86_64", python: "3.14", platform: "linux-x86_64" },
    { label: "3.14 win-arm64", python: "3.14", platform: "win-arm64" },
    { label: "3.15 linux-x86_64", python: "3.15", platform: "linux-x86_64" },
  ],
  rows: [
    row("a", "1.0", ["3.14 win-arm64"], { "3.14 linux-x86_64": "wheel", "3.14 win-arm64": "source", "3.15 linux-x86_64": "missing" }),
    row("b", "2.0", ["3.14 linux-x86_64", "3.14 win-arm64"], { "3.14 linux-x86_64": "missing", "3.14 win-arm64": "missing", "3.15 linux-x86_64": "wheel" }),
  ],
};

test("rowsFor lists one Python's gaps, most first; headline counts distinct packages on counted targets", () => {
  assert.deepEqual(rowsFor(result, "3.14").map((r) => [r.name, r.here.length]), [["b", 2], ["a", 1]]);
  assert.deepEqual(rowsFor(result, "3.15"), []); // outside the lock: check() reports no gaps there
  assert.deepEqual(headline(result), { gaps: 2, unknown: 0, packages: 10, targets: 2 });
});

test("the CLI command and the fix prompt reproduce the check", () => {
  assert.equal(cliCommand(result, ["3.14t", "3.14"], ["linux-x86_64", "win-arm64"]), "uvx wheelgap uv.lock -p 3.14,3.14t -t linux-x86_64,win-arm64");
  assert.equal(cliCommand({ lockfile: "my app/uv.lock" }, ["3.14"], ["linux-x86_64"]), "uvx wheelgap 'my app/uv.lock' -p 3.14 -t linux-x86_64");
  assert.equal(packageCliCommand("httpie", ["3.14", "3.13"], ["linux-x86_64", "win-arm64"]), "uvx wheelgap --deps httpie -p 3.13,3.14 -t linux-x86_64,win-arm64");
  assert.equal(packageCliCommand("httpie; rm -rf /", ["3.14"], ["linux-x86_64"]), null);
});

test("projectNameFromMetadata reads plain names from setup.cfg and pyproject.toml and rejects dynamic or invalid ones", () => {
  assert.equal(projectNameFromMetadata("setup.cfg", "[metadata]\nname = httpie\nversion = 3.2.4\n"), "httpie");
  assert.equal(projectNameFromMetadata("setup.cfg", "[metadata]\nname = attr: pkg.__name__\n"), null);
  assert.equal(projectNameFromMetadata("setup.cfg", "[metadata]\nname = file: NAME.txt\n"), null);
  assert.equal(projectNameFromMetadata("setup.cfg", "[metadata]\nname = bad name!\n"), null);
  assert.equal(projectNameFromMetadata("setup.cfg", "[options]\nname = wrong-section\n"), null);
  assert.equal(projectNameFromMetadata("pyproject.toml", '[project]\nname = "x"\ndynamic = ["dependencies"]\n'), "x");
  assert.equal(projectNameFromMetadata("pyproject.toml", "[project]\nname = 'my_pkg'\n"), "my_pkg");
  assert.equal(projectNameFromMetadata("pyproject.toml", '[tool.ruff]\nname = "nope"\n'), null);
  assert.equal(projectNameFromMetadata("setup.py", 'setup(name="ignored")\n'), null);
});

test("projectNameFromMetadata reads a setup.cfg as configparser does and a pyproject.toml's headers as TOML does", () => {
  const cfg = (text) => projectNameFromMetadata("setup.cfg", text);
  assert.equal(cfg("[metadata]\nName: httpie\n"), "httpie"); // keys are case-insensitive; ":" works like "="
  assert.equal(cfg("[metadata] # comment\nname = httpie\n"), "httpie");
  assert.equal(cfg("[metadata]\n  name = httpie\n"), "httpie"); // an indented first key is still a key
  // Section names are case-sensitive, and an indented line after a key continues that key's value.
  assert.equal(cfg("[METADATA]\nname = httpie\n"), null);
  assert.equal(cfg("[ metadata ]\nname = httpie\n"), null);
  assert.equal(cfg("[metadata]\ndescription = a tool\n  name = other\n"), null);
  assert.equal(cfg("[metadata]\nname = httpie # inline comments aren't stripped\n"), null);
  assert.equal(cfg("[metadata]\nname =\n  httpie\n"), null);
  const toml = (text) => projectNameFromMetadata("pyproject.toml", text);
  assert.equal(toml('[project] # the package\nname = "httpie"\n'), "httpie");
  assert.equal(toml('[ project ]\nname = "httpie"\n'), "httpie");
  // A name under any other table, an array of tables included, is never [project]'s.
  assert.equal(toml('[build-system]\nrequires = ["setuptools"]\n[[tool.uv.index]] # torch\nname = "pytorch"\n'), null);
  assert.equal(toml('[project]\nversion = "1"\n[tool.x] # c\nname = "nope"\n'), null);
  assert.equal(toml('["project"]\nname = "unsure"\n'), null);
});

test("projectNameFromMetadata refuses a value configparser continues and a multi-line TOML string", () => {
  const cfg = (text) => projectNameFromMetadata("setup.cfg", text);
  // configparser reads "httpie\nevil": an indented line after the key continues it, blank lines in between or not.
  assert.equal(cfg("[metadata]\nname = httpie\n  evil\n"), null);
  assert.equal(cfg("[metadata]\nname = httpie\n\n  more\n"), null);
  assert.equal(cfg("[metadata]\nname = httpie\n# c\nversion = 1\n"), "httpie");
  const toml = (text) => projectNameFromMetadata("pyproject.toml", text);
  // A "name = ..." inside a multi-line string isn't a key, and the reader doesn't track strings: it refuses.
  assert.equal(toml('[project]\ndescription = """\nname = "evil"\n"""\nname = "real"\n'), null);
  assert.equal(toml("[project]\ndescription = '''\nname = \"evil\"\n'''\nname = \"real\"\n"), null);
  assert.equal(toml('[project]\nname = "real"\ndescription = """x"""\n'), "real");
});

test("the project name is read from a lone root pyproject.toml, then a root setup.cfg, and only then", () => {
  const pyproject = [{ path: "pyproject.toml", offline: false }];
  assert.deepEqual(metadataFiles([], ["setup.cfg", "setup.py"]), ["setup.cfg"]);
  assert.deepEqual(metadataFiles([], ["setup.py"]), []);
  // The usual setuptools layout: a pyproject.toml with only [build-system], and the name in setup.cfg.
  assert.deepEqual(metadataFiles(pyproject, ["pyproject.toml", "setup.cfg"]), ["pyproject.toml", "setup.cfg"]);
  assert.deepEqual(metadataFiles(pyproject, ["pyproject.toml"]), ["pyproject.toml"]);
  // Another file to check, or a pyproject.toml that isn't the root's: no package fallback.
  assert.deepEqual(metadataFiles([...pyproject, { path: "requirements.txt", offline: false }], ["setup.cfg"]), []);
  assert.deepEqual(metadataFiles([{ path: "app/pyproject.toml", offline: false }], ["setup.cfg"]), []);
  assert.deepEqual(metadataFiles([], ["pkg/setup.cfg"]), []);
});

test("fixPrompt reproduces the check", () => {
  const fixes = { "a==1.0": { found: { "3.14 win-arm64": "1.1" }, loses: [] }, "b==2.0": { found: {}, loses: [] } };
  const prompt = fixPrompt("o/r", result, fixes, ["3.14"], ["linux-x86_64", "win-arm64"]);
  assert.match(prompt, /- a 1\.0 -> 1\.1\n/);
  assert.match(prompt, /No newer release has a wheel for these[^\n]*\n- b 2\.0: 3\.14 linux-x86_64, 3\.14 win-arm64/);
  assert.match(prompt, /uvx wheelgap uv\.lock -p 3\.14 -t linux-x86_64,win-arm64/);
  assert.doesNotMatch(prompt, /weren't looked up/);
  assert.match(fixPrompt("o/r", result, null, ["3.14"], ["linux-x86_64"]), /weren't looked up on PyPI/);
});

test("the verdict reads right for one target and many", () => {
  assert.equal(verdictText({ gaps: 1, packages: 77, targets: 1 }), "1 of 77 packages has no wheel for this target");
  assert.equal(verdictText({ gaps: 5, packages: 79, targets: 4 }), "5 of 79 packages have no wheel for at least one of the 4 targets");
  assert.equal(verdictText({ gaps: 0, packages: 10, targets: 1 }), "✓ All 10 packages have a wheel for this target");
  assert.equal(verdictText({ gaps: 0, packages: 10, targets: 6 }), "✓ All 10 packages have a wheel for all 6 targets");
  // Every package skipped (a requirements file that names another index, say), or none listed: never a ✓.
  assert.equal(verdictText({ gaps: 0, unknown: 0, packages: 0, targets: 4 }), "No packages were checked");
});

test("the verdict counts checked and unchecked packages without claiming a complete pass", () => {
  const cases = [
    [1, 0, "✓ 1 package has a wheel for this target"],
    [1, 1, "1 checked package has a wheel for this target; 1 more wasn't checked (its index isn't known to be PyPI)"],
    [1, 2, "1 checked package has a wheel for this target; 2 more weren't checked (their index isn't known to be PyPI)"],
    [3, 0, "✓ All 3 packages have a wheel for this target"],
    [3, 1, "All 3 checked packages have a wheel for this target; 1 more wasn't checked (its index isn't known to be PyPI)"],
    [3, 2, "All 3 checked packages have a wheel for this target; 2 more weren't checked (their index isn't known to be PyPI)"],
  ];
  for (const [packages, notChecked, expected] of cases) {
    assert.equal(verdictText({ gaps: 0, unknown: 0, packages, targets: 1 }, notChecked), expected);
  }
  assert.equal(verdictText({ gaps: 1, packages: 1, targets: 1 }), "1 package has no wheel for this target");
  assert.equal(
    verdictText({ gaps: 1, packages: 3, targets: 2 }, 1),
    "1 of 3 checked packages has no wheel for at least one of the 2 targets; 1 more wasn't checked (its index isn't known to be PyPI)",
  );
  assert.equal(
    verdictText({ gaps: 0, unknown: 1, packages: 3, targets: 2 }, 2),
    "No package is missing a wheel, but 1 of 3 couldn't be checked on some targets; 2 more weren't checked (their index isn't known to be PyPI)",
  );
  assert.equal(
    verdictText({ gaps: 0, unknown: 1, packages: 1, targets: 1 }, 1),
    "No package is missing a wheel, but 1 of 1 couldn't be checked on this target; 1 more wasn't checked (its index isn't known to be PyPI)",
  );
});

test("shareText formats clean, gaps, unknowns, single target, and special characters as plain text", () => {
  const url = "https://wheelgap.github.io/tracker/check/?repo=owner/name";
  const sixPlatforms = ["linux-x86_64", "linux-aarch64", "musl-x86_64", "macos-arm64", "win-amd64", "win-arm64"];
  assert.equal(
    shareText("owner/name", { gaps: 0, unknown: 0, packages: 212, targets: 12 }, ["3.14", "3.13"], sixPlatforms, url),
    `✓ owner/name: all 212 dependencies have a wheel on Python 3.13, 3.14 for 6 platforms (checked with wheelgap) ${url}`,
  );
  assert.equal(
    shareText("owner/name", { gaps: 3, unknown: 0, packages: 212, targets: 6 }, ["3.14"], sixPlatforms, url),
    `owner/name: 3 of 212 dependencies have no wheel on at least one of 6 targets (checked with wheelgap) ${url}`,
  );
  // Unknowns must never carry a ✓.
  const withUnknown = shareText("owner/name", { gaps: 0, unknown: 2, packages: 212, targets: 6 }, ["3.14"], sixPlatforms, url);
  assert.equal(
    withUnknown,
    `owner/name: no dependency is missing a wheel, but 2 of 212 couldn't be checked on some targets (checked with wheelgap) ${url}`,
  );
  assert.ok(!withUnknown.includes("✓"));
  // One target / one platform / one gap.
  assert.equal(
    shareText("owner/name", { gaps: 0, unknown: 0, packages: 1, targets: 1 }, ["3.14"], ["linux-x86_64"], url),
    `✓ owner/name: 1 dependency has a wheel on Python 3.14 for 1 platform (checked with wheelgap) ${url}`,
  );
  assert.equal(
    shareText("owner/name", { gaps: 1, unknown: 0, packages: 10, targets: 1 }, ["3.14"], ["linux-x86_64"], url),
    `owner/name: 1 of 10 dependencies has no wheel on 1 target (checked with wheelgap) ${url}`,
  );
  assert.equal(
    shareText("owner/name", { gaps: 0, unknown: 1, packages: 10, targets: 1 }, ["3.14"], ["linux-x86_64"], url),
    `owner/name: no dependency is missing a wheel, but 1 of 10 couldn't be checked on 1 target (checked with wheelgap) ${url}`,
  );
  // Nothing checked: never a ✓.
  assert.equal(
    shareText("owner/name", { gaps: 0, unknown: 0, packages: 0, targets: 4 }, ["3.14"], ["linux-x86_64"], url),
    `owner/name: no dependencies were checked (checked with wheelgap) ${url}`,
  );
  // Plain text, never HTML-escaped: characters like <, >, &, ", ' stay verbatim.
  const rawName = `a&b/<c>"d'`;
  const line = shareText(rawName, { gaps: 0, unknown: 0, packages: 5, targets: 1 }, ["3.14"], ["linux-x86_64"], url);
  assert.equal(
    line,
    `✓ a&b/<c>"d': all 5 dependencies have a wheel on Python 3.14 for 1 platform (checked with wheelgap) ${url}`,
  );
  assert.ok(!line.includes("&amp;") && !line.includes("&lt;"));
});

test("shareText never reads as fully clean when a private index's packages weren't checked", () => {
  // The line is pasted without the page's alerts, so it says how many were left off pypi.org, and drops the ✓.
  const url = "https://wheelgap.github.io/tracker/check/?repo=owner/name";
  const clean = { gaps: 0, unknown: 0, packages: 12, targets: 2 };
  const line = shareText("owner/name", clean, ["3.14"], ["linux-x86_64", "win-amd64"], url, 5);
  assert.equal(
    line,
    `owner/name: all 12 dependencies checked have a wheel on Python 3.14 for 2 platforms; 5 more weren't checked (their index isn't known to be PyPI) (checked with wheelgap) ${url}`,
  );
  assert.ok(!line.includes("✓"));
  assert.equal(
    shareText("owner/name", { ...clean, packages: 1 }, ["3.14"], ["linux-x86_64"], url, 1),
    `owner/name: 1 dependency checked has a wheel on Python 3.14 for 1 platform; 1 more wasn't checked (its index isn't known to be PyPI) (checked with wheelgap) ${url}`,
  );
  assert.equal(
    shareText("owner/name", { ...clean, gaps: 2 }, ["3.14"], ["linux-x86_64"], url, 3),
    `owner/name: 2 of 12 dependencies have no wheel on at least one of 2 targets; 3 more weren't checked (their index isn't known to be PyPI) (checked with wheelgap) ${url}`,
  );
  // Every package private: nothing was checked, and the line says only that.
  assert.equal(
    shareText("owner/name", { ...clean, packages: 0 }, ["3.14"], ["linux-x86_64"], url, 4),
    `owner/name: no dependencies were checked (checked with wheelgap) ${url}`,
  );
  // No private packages: the line is unchanged, ✓ and all.
  assert.equal(shareText("owner/name", clean, ["3.14"], ["linux-x86_64"], url, 0), shareText("owner/name", clean, ["3.14"], ["linux-x86_64"], url));
  assert.match(shareText("owner/name", clean, ["3.14"], ["linux-x86_64"], url), /^✓ /);
});

test("next steps offer what isn't picked yet, as whole selections", () => {
  const steps = nextSteps(["3.14"], ["linux-x86_64"]);
  assert.deepEqual(steps.map((s) => s.label), ["Free-threaded 3.14t", "Windows on ARM", "Linux ARM64", "Alpine Linux", "Python 3.15", "Everything"]);
  assert.deepEqual(steps[0], { label: "Free-threaded 3.14t", pythons: ["3.14", "3.14t"], platforms: ["linux-x86_64"] });
  assert.deepEqual(steps[3].platforms, ["linux-x86_64", "musl-x86_64", "musl-aarch64"]);
  assert.equal(steps.at(-1).platforms.length, 8);
  const all = ["linux-x86_64", "linux-aarch64", "musl-x86_64", "musl-aarch64", "macos-arm64", "macos-x86_64", "win-amd64", "win-arm64"];
  assert.deepEqual(nextSteps(["3.15", "3.15t"], all), []);
});

test("fixFor: a bump that drops a wheel elsewhere isn't 'no newer wheel', and private packages aren't looked up", () => {
  const r = row("foo", "1.0", ["3.13 linux-aarch64"]);
  const loses = fixFor(r, { "foo==1.0": { found: {}, loses: ["3.13 linux-aarch64"] } });
  assert.equal(loses.kind, "loses");
  const priv = row("corp-utils", "1.0", ["3.13 linux-x86_64"], {}, { lookup: false });
  assert.equal(fixFor(priv, { "corp-utils==1.0": { found: { "3.13 linux-x86_64": "9.9" }, loses: [] } }).kind, "private");
  assert.equal(fixFor(row("x", "1", [], {}, { unknown: ["3.13 linux-x86_64"] }), {}).kind, "unchecked");
  const res = { ...result, rows: [r, priv] };
  const prompt = fixPrompt("o/r", res, { "foo==1.0": { found: {}, loses: ["3.13 linux-aarch64"] } }, ["3.13"], ["linux-aarch64"]);
  assert.match(prompt, /trades one gap for another[^\n]*\n- foo 1\.0: 3\.13 linux-aarch64/);
  assert.match(prompt, /doesn't record their index as PyPI[^\n]*\n- corp-utils 1\.0/);
  assert.doesNotMatch(prompt, /don't come from PyPI/);
  assert.doesNotMatch(prompt, /No newer release has a wheel/);
  assert.doesNotMatch(prompt, /9\.9/);
});

test("unknown cells are listed but not counted as missing a wheel", () => {
  const res = {
    ...result,
    outside_lock: [],
    rows: [row("u", "1", [], { "3.14 linux-x86_64": "unknown", "3.14 win-arm64": "wheel" }, { unknown: ["3.14 linux-x86_64"] })],
  };
  assert.deepEqual(headline(res), { gaps: 0, unknown: 1, packages: 10, targets: 3 });
  assert.deepEqual(rowsFor(res, "3.14").map((r) => r.name), ["u"]);
  assert.equal(verdictText(headline(res)), "No package is missing a wheel, but 1 of 10 couldn't be checked on some targets");
});

test("a platform's tile shows a ✓ only when every package was checked and has a wheel", () => {
  assert.deepEqual(tileText({ wheel: 10, source: 0, missing: 0, unknown: 0 }, false), { mark: "✓", note: "all wheels" });
  assert.deepEqual(tileText({ wheel: 7, source: 2, missing: 1, unknown: 4 }, false), { mark: "3", note: "without a wheel" });
  // None known to lack a wheel, but some couldn't be checked (PyPI unreachable, over the lookup cap): never a ✓.
  assert.deepEqual(tileText({ wheel: 9, source: 0, missing: 0, unknown: 1 }, false), { mark: "?", note: "1 unknown" });
  assert.deepEqual(tileText({ wheel: 0, source: 0, missing: 0, unknown: 18 }, false), { mark: "?", note: "18 unknown" });
  assert.deepEqual(tileText({ wheel: 0, source: 0, missing: 0, unknown: 0 }, true), { mark: "–", note: "not locked" });
});

test("a platform's tile includes private packages in its unchecked count", () => {
  const counts = { wheel: 3, source: 0, missing: 0, unknown: 0 };
  assert.deepEqual(tileText(counts, false, 0), { mark: "✓", note: "all wheels" });
  assert.deepEqual(tileText(counts, false, 1), { mark: "?", note: "1 not checked" });
  assert.deepEqual(tileText(counts, false, 2), { mark: "?", note: "2 not checked" });
  assert.deepEqual(tileText({ ...counts, unknown: 1 }, false, 2), { mark: "?", note: "3 not checked" });
  assert.deepEqual(tileText({ ...counts, source: 1 }, false, 2), { mark: "1", note: "without a wheel" });
  assert.deepEqual(tileText(counts, true, 2), { mark: "–", note: "not locked" });
});

test("the page says why lookups got no answer, and nothing when they all did", () => {
  assert.equal(missedText(undefined, "uv.lock"), null); // a lockfile read from its own files
  assert.equal(missedText({ cap: 300, over_cap: 0, not_found: 0, failed: 0 }, "requirements.txt"), null);
  assert.equal(
    missedText({ cap: 300, over_cap: 12, not_found: 1, failed: 2 }, "requirements.txt"),
    "Not looked up on PyPI, so shown as unknown: 12 beyond the 300 packages this page looks up for one file; " +
      "2 whose lookup failed or timed out (check again to retry); 1 that PyPI doesn't have under that name or version. " +
      "uvx wheelgap requirements.txt in a checkout looks them up.",
  );
  // The command line wouldn't find these either, so it isn't offered for them.
  assert.equal(
    missedText({ cap: 300, over_cap: 0, not_found: 2, failed: 0 }, "Pipfile.lock"),
    "Not looked up on PyPI, so shown as unknown: 2 that PyPI doesn't have under that name or version.",
  );
});

test("the page says why a file's packages were kept off pypi.org, naming the settings files behind it", () => {
  const base = { lockfile: "app/requirements.txt", private: [], configs: [], configs_unread: [], configs_unknown: false };
  assert.equal(privateText(base), null);
  assert.equal(privateText({ lockfile: "uv.lock", skipped: [] }), null); // a result from before the fields existed
  const names = { ...base, private: ["corp-secret-lib"] };
  const after = "so these may not be PyPI's packages and their names aren't sent to pypi.org: corp-secret-lib.";
  const index = "names an index or source other than PyPI (an --index-url or --extra-index-url line, a [tool.uv] index or sources setting, a Pipfile source)";
  assert.equal(
    privateText(names),
    `Not checked: app/requirements.txt ${index}, ${after} To check them on that index, run uvx wheelgap --index-url with its URL in a checkout.`,
  );
  // The settings files read for it may be the ones that name it.
  assert.equal(
    privateText({ ...names, configs: ["pyproject.toml", "app/uv.toml"] }),
    `Not checked: app/requirements.txt or its installer settings (pyproject.toml, app/uv.toml) ${index}, ${after} To check them on that index, run uvx wheelgap --index-url with its URL in a checkout.`,
  );
  // One that couldn't be fetched (or is named in another case), or a file list GitHub cut short: what they say about an index isn't known.
  assert.equal(
    privateText({ ...names, configs_unread: ["pyproject.toml"] }),
    `Not checked: pyproject.toml (beside app/requirements.txt or in a folder above it) couldn't be read here. Such a file can point installers at an index other than PyPI (a [tool.uv] index, say), ${after} Check again to retry, or in a checkout run uvx wheelgap --index-url with your project's index and app/requirements.txt: without --index-url it would look these names up on PyPI.`,
  );
  assert.equal(
    privateText({ ...names, configs_unknown: true }),
    `Not checked: GitHub listed only part of this repo's files, so a uv.toml, pyproject.toml or pdm.toml beside app/requirements.txt or in a folder above it may be missing from the list. Such a file can point installers at an index other than PyPI (a [tool.uv] index, say), ${after} In a checkout, run uvx wheelgap --index-url with your project's index and app/requirements.txt: it reads them all, and without --index-url it would look these names up on PyPI.`,
  );
  const many = privateText({ ...base, private: Array.from({ length: 10 }, (_, i) => `p${i}`), configs_unread: ["a/pdm.toml", "a/uv.toml", "pdm.toml", "uv.toml"] });
  assert.match(many, /^Not checked: a\/pdm\.toml, a\/uv\.toml, pdm\.toml, and 1 more \(beside/);
  assert.match(many, /p0, p1, p2, p3, p4, p5, p6, p7, and 2 more\./);
});

test("privateText never advises a command that would look the withheld names up on PyPI", () => {
  // wheelgap 0.13.0's CLI looks a requirements file's or pyproject.toml's packages up on PyPI unless --index-url names
  // another index, whatever the file says; so every branch's command carries --index-url, and says why.
  const base = { lockfile: "requirements.txt", private: ["corp-pkg"], configs: [], configs_unread: [], configs_unknown: false };
  for (const reader of ["requirements", "pyproject", "pipfile", "pixi", undefined]) {
    const text = privateText({ ...base, reader });
    assert.match(text, /corp-pkg\. To check them on that index, run uvx wheelgap --index-url with its URL in a checkout\.$/, String(reader));
    assert.doesNotMatch(text, /checks them on PyPI's files/);
  }
  for (const extra of [{ configs_unknown: true }, { configs_unread: ["pyproject.toml"] }]) {
    const text = privateText({ ...base, ...extra });
    assert.match(text, /uvx wheelgap --index-url with your project's index and requirements\.txt: .*without --index-url it would look these names up on PyPI\.$/);
  }
});

test("refs with '..' or empty segments are refused", () => {
  assert.ok(validRef("main") && validRef("feature/x") && validRef("v1.2.3"));
  for (const bad of ["..", "a/../b", "a//b", "/a", "a/", ".", "a b"]) assert.equal(validRef(bad), false, bad);
  assert.equal(parseRepo("https://github.com/o/r/tree/../../other/repo"), null);
  assert.equal(fromQuery("?repo=o/r&ref=../../x/y").repo.ref, null);
});

test("the checker's lab verdicts match the tracker's", async () => {
  const { createRequire } = await import("node:module");
  const site = createRequire(import.meta.url)("../../site/logic.js");
  const base = { outcome: "built", seconds: 87, wheel: "cp314-cp314-win_arm64", extensions: 1, import: "ok", gil: null, reason: null };
  const cases = [
    {}, { gil: "kept-off", seconds: 289 }, { gil: "re-enabled" }, { extensions: 0, wheel: "py3-none-any" }, { extensions: 0 },
    { import: "failed" }, { import: "deps-failed" }, { outcome: "failed", reason: "needs OpenSSL" }, { outcome: "failed", reason: "other" }, { outcome: "failed", reason: "unsupported platform" },
    { outcome: "timeout", seconds: 2400 },
  ];
  for (const c of cases) assert.deepEqual(labVerdict({ ...base, ...c }), site.labVerdict({ ...base, ...c }), JSON.stringify(c));
});

test("a lab result counts only for its own release and target", () => {
  const builds = [{ target: "3.14 win-arm64", version: "0.14.0", outcome: "built" }];
  assert.equal(labFor(builds, "0.14.0", "3.14 win-arm64").outcome, "built");
  assert.equal(labFor(builds, "0.13.0", "3.14 win-arm64"), null);
  assert.equal(labFor(builds, "0.14.0", "3.14t win-arm64"), null);
  assert.equal(labFor("nope", "0.14.0", "3.14 win-arm64"), null);
  assert.equal(canonicalName("Typing_Extensions.x"), "typing-extensions-x");
});


test("the copy prompt includes only the driver's gap wheel instructions", () => {
  const result = { lockfile: "uv.lock", rows: [] };
  const gapWheels = {
    cells: [{ name: "fastlib", version: "2.0.0", target: "3.13 win-amd64" }],
    text: "[[tool.uv.index]]\nname = \"wheelgap\"",
    unofficial: "unofficial, built by wheelgap",
  };
  assert.equal(gapWheelFor({ name: "fastlib", version: "2.0.0" }, gapWheels, "3.13 win-amd64"), true);
  assert.equal(gapWheelFor({ name: "fastlib", version: "2.0.1" }, gapWheels, "3.13 win-amd64"), false);
  // The Fix column counts over all of a row's gaps: one filled target of two isn't "available" for the row.
  const row = { name: "fastlib", version: "2.0.0", gaps: ["3.13 win-amd64"] };
  assert.equal(gapWheelFixText(row, gapWheels), "gap wheel available");
  assert.equal(gapWheelFixText({ ...row, gaps: ["3.13 win-amd64", "3.13 win-arm64"] }, gapWheels), "gap wheel for 1 of 2 gaps");
  assert.equal(gapWheelFixText({ ...row, version: "2.0.1" }, gapWheels), null);
  assert.equal(gapWheelFixText(row, null), null);
  const prompt = fixPrompt("o/r", result, {}, ["3.13"], ["win-amd64"], gapWheels);
  assert.match(prompt, /unofficial, built by wheelgap/);
  assert.ok(prompt.includes(gapWheels.text));
});

test("fixLinks picks a row's links under Fix: none, loses, gap wheel, private, and invalid issues URLs", () => {
  const rNone = { name: "demo-pkg", version: "1.0", gaps: ["3.14 linux-x86_64"], lookup: true };
  const fNone = { kind: "none", text: "no newer release has a wheel" };

  // 1. none with issues_url:
  const withIssues = fixLinks(rNone, fNone, { ok: true, issues_url: "https://github.com/org/demo/issues" });
  assert.deepEqual(withIssues, [
    { text: "coverage and build results", href: "../p/demo-pkg/" },
    { text: "ask the maintainers", href: "https://github.com/org/demo/issues", rel: "noopener nofollow" },
  ]);

  // none without issues_url:
  const withoutIssues = fixLinks(rNone, fNone, { ok: true });
  assert.deepEqual(withoutIssues, [
    { text: "coverage and build results", href: "../p/demo-pkg/" },
  ]);

  // 2. loses:
  const fLoses = { kind: "loses", text: "only releases that drop a wheel elsewhere" };
  const losesLinks = fixLinks(rNone, fLoses, { ok: true, issues_url: "https://github.com/org/demo/issues" });
  assert.deepEqual(losesLinks, [
    { text: "coverage and build results", href: "../p/demo-pkg/" },
    { text: "ask the maintainers", href: "https://github.com/org/demo/issues", rel: "noopener nofollow" },
  ]);

  // 3. a row a gap wheel fills (no links needed):
  const gapWheels = {
    cells: [{ name: "demo-pkg", version: "1.0", target: "3.14 linux-x86_64" }],
    text: "gap-wheels",
    unofficial: "unofficial",
  };
  assert.deepEqual(fixLinks(rNone, fNone, { ok: true, issues_url: "https://github.com/org/demo/issues" }, gapWheels), []);

  // 4. private (none):
  const rPriv = { name: "corp-lib", version: "1.0", gaps: ["3.14 linux-x86_64"], lookup: false };
  const fPriv = { kind: "private", text: "not looked up" };
  assert.deepEqual(fixLinks(rPriv, fPriv, { ok: true, issues_url: "https://github.com/org/demo/issues" }), []);

  // 5. an issues_url that isn't https or is on an unknown host (dropped):
  const notHttps = fixLinks(rNone, fNone, { ok: true, issues_url: "http://insecure.example/issues" });
  assert.deepEqual(notHttps, [{ text: "coverage and build results", href: "../p/demo-pkg/" }]);

  const noHost = fixLinks(rNone, fNone, { ok: true, issues_url: "https:///issues" });
  assert.deepEqual(noHost, [{ text: "coverage and build results", href: "../p/demo-pkg/" }]);

  const userinfo = fixLinks(rNone, fNone, { ok: true, issues_url: "https://github.com@evil.example/issues" });
  assert.deepEqual(userinfo, [{ text: "coverage and build results", href: "../p/demo-pkg/" }]);

  const controlChar = fixLinks(rNone, fNone, { ok: true, issues_url: "https://github.com/org/demo/issues\n" });
  assert.deepEqual(controlChar, [{ text: "coverage and build results", href: "../p/demo-pkg/" }]);

  // A package the tracker doesn't have, or no answer yet: no links (loadLab() keeps no issues_url for it).
  assert.deepEqual(fixLinks(rNone, fNone, { ok: false, builds: [], issues_url: null }), []);
  assert.deepEqual(fixLinks(rNone, fNone, null), []);

  // fixPrompt includes the issue tracker URL for none and loses groups when known
  const prompt = fixPrompt(
    "o/r",
    { lockfile: "uv.lock", rows: [rNone] },
    { "demo-pkg==1.0": { found: {}, loses: [] } },
    ["3.14"],
    ["linux-x86_64"],
    null,
    { "demo-pkg": { issues_url: "https://github.com/org/demo/issues" } }
  );
  assert.match(prompt, /- demo-pkg 1\.0: 3\.14 linux-x86_64 \(ask the maintainers: https:\/\/github\.com\/org\/demo\/issues\)/);
});

test("validIssuesUrl makes the package page's valid_issues_url() check, host by any name", () => {
  // scripts/build_pages.py's valid_issues_url() has no list of hosts: any https URL with a host that isn't user@,
  // with printable ASCII only. The page links what the package page links, and nothing it refuses.
  for (const yes of ["https://github.com/o/r/issues", "https://bugs.launchpad.net/x", "https://jira.example.org/browse/X",
    "HTTPS://gitlab.com/o/r/-/issues", "https://localhost/issues", "https://github.com:443/o/r/issues?q=is%3Aopen#top"]) {
    assert.equal(validIssuesUrl(yes), true, yes);
  }
  for (const no of ["http://github.com/o/r/issues", "https:///issues", "https://", "https://a@b.example/", "https://@b.example/",
    "https://:@b.example/", "https://b.example/ x", "https://b.example/\u00e9", "https://b.example/\x1b[2J", "javascript:alert(1)",
    "//github.com/o/r", "", null, undefined, 42]) {
    assert.equal(validIssuesUrl(no), false, String(no));
  }
});

