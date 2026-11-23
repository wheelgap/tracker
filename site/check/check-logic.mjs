// Pure helpers for the repo checker (check.mjs and worker.mjs), kept apart so tests/site/check-logic.test.mjs can run
// them under node with no DOM. Nothing here touches the network.

export const PLATFORMS = [
  ["linux-x86_64", "Linux x86_64"],
  ["linux-aarch64", "Linux ARM64"],
  ["musl-x86_64", "Alpine x86_64"],
  ["musl-aarch64", "Alpine ARM64"],
  ["macos-arm64", "macOS Apple silicon"],
  ["macos-x86_64", "macOS Intel"],
  ["win-amd64", "Windows x64"],
  ["win-arm64", "Windows on ARM"],
];
export const PYTHONS = ["3.13", "3.14", "3.15", "3.14t", "3.15t"];
export const GAP = new Set(["source", "missing", "unknown"]);

const NAME = /^[A-Za-z0-9_.-]{1,100}$/;

// A branch, tag or commit as it can appear in a raw.githubusercontent.com path: no "..", empty or "." segment, so
// the URL can't be normalised into another repo's.
export function validRef(ref) {
  return /^[A-Za-z0-9._\/-]{1,200}$/.test(ref) && ref.split("/").every((p) => p && p !== "." && p !== "..");
}

// "owner/name", a github.com URL (with /tree/REF/DIR or /blob/REF/FILE), or an SSH remote, to {owner, name, ref, dir, file}.
// ref, dir and file are null when the input doesn't name them. Returns null for anything else.
export function parseRepo(input) {
  let s = String(input ?? "").trim();
  if (!s || s.length > 500) return null;
  s = s.replace(/^git@github\.com:/i, "github.com/");
  s = s.replace(/^(https?:\/\/)?(www\.)?github\.com\//i, "");
  s = s.replace(/[?#].*$/, "").replace(/\/+$/, "");
  const parts = s.split("/");
  if (parts.length < 2) return null;
  const owner = parts[0];
  const name = parts[1].replace(/\.git$/i, "");
  // GitHub user and org names have no dots, so "gitlab.com/owner/project" is another host, not a GitHub owner.
  if (!NAME.test(owner) || !NAME.test(name) || owner.includes(".") || name === "." || name === "..") return null;
  let ref = null;
  let dir = null;
  let file = null;
  if ((parts[2] === "tree" || parts[2] === "blob") && parts[3]) {
    ref = decodeURIComponent(parts[3]);
    const rest = parts.slice(4).map(decodeURIComponent);
    if (parts[2] === "blob") {
      file = rest.length ? rest.join("/") : null;
      rest.pop(); // a file: its folder
    }
    dir = rest.length ? rest.join("/") : null;
  }
  if (ref !== null && !validRef(ref)) return null;
  if (dir !== null && (dir.split("/").some((p) => p === ".." || p === "") || dir.length > 300)) return null;
  if (file !== null && (file.split("/").some((p) => p === ".." || p === "") || file.length > 300)) return null;
  return { owner, name, ref, dir, file };
}

// The raw.githubusercontent.com URL of a file in the repo, each path segment encoded.
export function rawUrl(repo, ref, path) {
  const enc = (p) => p.split("/").map(encodeURIComponent).join("/");
  return `https://raw.githubusercontent.com/${repo.owner}/${repo.name}/${enc(ref)}/${enc(path)}`;
}

const isObject = (value) => value !== null && typeof value === "object" && !Array.isArray(value);
const pickFields = (value, fields) => isObject(value)
  ? Object.fromEntries(fields.filter((key) => Object.hasOwn(value, key)).map((key) => [key, value[key]]))
  : value;

// wheelgap 0.13.0: pypi._files/_supports_python/_fill_json_data and suggest._has_wheel read these file fields.
// Preserve malformed lists/entries and absent fields: normalising them to [] could turn "unknown" into a gap.
const trimFiles = (files) => Array.isArray(files)
  ? files.map((file) => pickFields(file, ["filename", "requires_python", "yanked"]))
  : files;

// Both range resolution (pypi._newest_matching) and fixes (suggest.suggest) read version -> file list.
export function trimReleases(releases) {
  return isObject(releases)
    ? Object.fromEntries(Object.entries(releases).map(([version, files]) => [version, trimFiles(files)]))
    : releases;
}

// Drop unused PyPI metadata before retaining it or serialising it into Pyodide. Re-audit the fields when the
// worker's wheelgap pin changes: _fill_json_data reads the URLs and info, _newest_matching also reads releases.
export function trimProject(doc) {
  if (!isObject(doc)) return doc;
  const trimmed = pickFields(doc, ["info", "urls", "releases"]);
  if (Object.hasOwn(trimmed, "info")) trimmed.info = pickFields(trimmed.info, ["version", "project_urls", "home_page"]);
  if (Object.hasOwn(trimmed, "urls")) trimmed.urls = trimFiles(trimmed.urls);
  if (Object.hasOwn(trimmed, "releases")) trimmed.releases = trimReleases(trimmed.releases);
  return trimmed;
}

// The file to start from: the first one in `dir` (the folder the URL named), else plan()'s first. plan() lists them
// shallowest folder first and, in a folder, in the order `wheelgap` looks for them there, with a pyproject.toml
// last. Every listed file can be checked, so a lockfile deeper in the repo never outranks a requirements.txt in the
// root, or in the folder the URL named. autoPick() goes no further than that folder and the root: with nothing in
// either, it follows `wheelgap DIR`'s rules for the subfolders instead of taking plan()'s first file.
export function pickLockfile(lockfiles, dir) {
  const inDir = (l) => dir !== null && l.path === `${dir}/${l.path.split("/").pop()}`;
  return lockfiles.find(inDir) ?? lockfiles[0] ?? null;
}

// Words in a requirements file's name (requirements-dev.txt, requirements/test.txt) or its folders that say its
// packages are for the tests, the docs, an example or development, not the project itself: wheelgap's _SIDE_WORDS
// (src/wheelgap/sources.py on main, after 0.13.0), split the way its _is_side() splits a name.
const SIDE_WORDS = new Set([
  "test", "tests", "testing", "example", "examples", "sample", "samples", "demo", "demos", "doc", "docs",
  "bench", "benchmark", "benchmarks", "fixtures", "templates",
  "dev", "develop", "development", "lint", "ci", "typing",
]);
export function isSideFile(path) {
  const parts = String(path).split("/");
  const base = parts.pop();
  const stem = base.includes(".") ? base.slice(0, base.lastIndexOf(".")) : base;
  return [...parts, stem].some((part) => part.toLowerCase().split(/[^a-z0-9]+/).some((word) => SIDE_WORDS.has(word)));
}

// How far below the root `wheelgap DIR` looks for an input when the root has none (wheelgap's NESTED_DEPTH, on main
// after 0.13.0): backend/uv.lock is 1 folder down.
export const NESTED_DEPTH = 3;

const folderOf = (path) => (path.includes("/") ? path.slice(0, path.lastIndexOf("/")) : "");

// Why the root is a project of its own, in `wheelgap DIR`'s words, so that the one input in its subfolders isn't taken
// for the project's (wheelgap's load_directory() and _unread_project() on main): a root pyproject.toml whose [project]
// lists nothing, a root setup.py or setup.cfg (`ctx.rootProjectFiles`, from plan()), or a root pyproject.toml that
// describes a project the page couldn't check. `ctx.pyprojects` says what became of each pyproject.toml the page
// tried and moved on from (onResult): "empty", "unread" (wheelgap refused it, and it's a project or may be one),
// "private" or "settings" (tool settings only: no project). null when the root isn't known to be a project.
function rootProject(ctx) {
  const root = ctx.pyprojects?.["pyproject.toml"];
  if (root === "empty") return "Its pyproject.toml lists no dependencies";
  for (const name of ["setup.py", "setup.cfg"]) {
    if ((ctx.rootProjectFiles || []).includes(name)) return `Its own dependencies are in ${name}, which wheelgap can't read`;
  }
  if (root === "unread") return "Its own dependencies are in pyproject.toml, which wheelgap can't read";
  if (root === "private") return "Its own dependencies are in pyproject.toml, which this page can't look up";
  return null;
}

// The file the page checks by itself, with the rules of `wheelgap DIR` (load_directory() and _nested_inputs() in
// wheelgap's src/wheelgap/sources.py on main, after 0.13.0; the page pins 0.13.0, so they're mirrored here):
// 1. An exactly-named file in the folder a link names, else in the root: pickLockfile()'s pick, as before.
// 2. Requirements files under other names in the root, as wheelgap's _other_requirements() reads them:
//    requirements*.txt there and any .txt in its requirements/ folder (plan() also marks prod-requirements.txt and
//    the like `other`, which `wheelgap DIR` doesn't read). The only one, unless its name says it's for development, tests, docs or examples (isSideFile), which
//    `wheelgap DIR` never checks on its own. With several, none: which one holds the project's dependencies can't be
//    told from a name.
// 3. Otherwise one input per subfolder up to NESTED_DEPTH folders down (the folders plan() skips are left out), as
//    `wheelgap FOLDER` reads it: its first exactly-named file in plan()'s order, a pyproject.toml last. A
//    pyproject.toml counts whether or not it lists dependencies, which the page can't tell before fetching it: that
//    can only make the page ask where `wheelgap DIR` wouldn't, never pick where it would ask. The only one is
//    checked (`why: "subfolder"`) when its folders' names aren't side words, the whole file list is known
//    (`ctx.partial` false, and no input that isn't side-named in a folder plan() skips but `wheelgap DIR` searches,
//    `ctx.unlistedInputs`: build/requirements.txt), the root isn't a project of its own (rootProject), and no other requirements file could as
//    well be the project's (a lone side one in the root, or one under another name beside it).
// Every other case picks none and returns the files to ask between in `choose`, with `why` saying in which way, so
// the page says so in `wheelgap DIR`'s words (chooseFile): "subfolders", "project" (with `project`, the reason),
// "rivals", "side" and "elsewhere" (no input `wheelgap DIR` reads up to NESTED_DEPTH folders down: only deeper files,
// or requirements files under other names in a subfolder, such as utils/checkers-requirements.txt). Without `why`,
// they're #229's requirements files under other names in the root, or its one side file.
// {lock: null, choose: null} when there's nothing at all.
export function autoPick(lockfiles, dir, ctx = {}) {
  const named = lockfiles.filter((l) => !l.other);
  for (const base of dir ? [dir, ""] : [""]) {
    const here = named.find((l) => folderOf(l.path) === base);
    if (here) return { lock: here, choose: null };
  }
  const others = lockfiles.filter((l) => l.other);
  // wheelgap's _other_requirements(folder): requirements*.txt in the folder, and any *.txt in its requirements/
  // folder. plan() also marks *-requirements.txt and *_requirements.txt, which `wheelgap DIR` doesn't read.
  const otherIn = (folder) => (l) => {
    const at = folderOf(l.path);
    return at === (folder ? `${folder}/requirements` : "requirements") || (at === folder && l.path.split("/").pop().startsWith("requirements"));
  };
  const rootOthers = others.filter(otherIn(""));
  if (rootOthers.length > 1) return { lock: null, choose: rootOthers };
  if (rootOthers.length === 1 && !isSideFile(rootOthers[0].path)) return { lock: rootOthers[0], choose: null };

  const main = [];
  const side = [];
  const folders = new Set();
  for (const l of named) {
    const folder = folderOf(l.path);
    if (folders.has(folder) || folder.split("/").length > NESTED_DEPTH) continue;
    folders.add(folder);
    (isSideFile(l.path) ? side : main).push(l);
  }
  const partial = ctx.partial || (ctx.unlistedInputs || []).some((p) => !isSideFile(p));
  if (main.length === 1 && !partial) {
    const [only] = main;
    const project = rootProject(ctx);
    if (project) return { lock: null, choose: main, why: "project", project };
    const folder = folderOf(only.path);
    const beside = fileKind(only.path) === "requirements"
      ? others.filter((l) => otherIn(folder)(l) && !isSideFile(l.path.split("/").pop()))
      : [];
    const rivals = [...rootOthers, ...beside];
    if (rivals.length) return { lock: null, choose: [only, ...rivals], why: "rivals" };
    return { lock: only, choose: null, why: "subfolder" };
  }
  if (main.length) return { lock: null, choose: main, why: "subfolders" };
  if (side.length) return { lock: null, choose: [...rootOthers, ...side], why: "side" };
  if (rootOthers.length) return { lock: null, choose: rootOthers }; // #229's lone side file in the root
  if (!lockfiles.length) return { lock: null, choose: null };
  // #229's lone side file anywhere else (examples/requirements-prod.txt), said as before.
  if (lockfiles.length === 1 && lockfiles[0].other && isSideFile(lockfiles[0].path)) return { lock: null, choose: lockfiles };
  return { lock: null, choose: lockfiles, why: "elsewhere" };
}

// wheelgap 0.13.0's sources.file_kind, which picks a file's reader by its name: the reader, or null for a name it
// doesn't read (and a pip report's .json, which this page doesn't offer). Re-check it when worker.mjs's pin changes:
// later releases read more names (a PEP 723 script's *.py.lock), which 0.13.0 refuses as "don't know how to read".
const KINDS = { "uv.lock": "uv", "pylock.toml": "pylock", "poetry.lock": "poetry", "pdm.lock": "pdm", "Pipfile.lock": "pipfile", "pixi.lock": "pixi" };
export function fileKind(path) {
  const base = String(path ?? "").split("/").pop();
  if (base === "pyproject.toml") return "pyproject";
  if (Object.hasOwn(KINDS, base)) return KINDS[base];
  if (base.startsWith("pylock.") && base.endsWith(".toml")) return "pylock";
  if (base.startsWith("requirements") && base.endsWith(".lock")) return "requirements";
  if (base.endsWith(".lock") && base.includes("uv")) return "uv";
  if (base.endsWith(".txt") || base.endsWith(".in")) return "requirements";
  return null;
}

// The readers whose lockfiles list each package's files, so the page checks them offline (driver.py's OFFLINE_KINDS,
// which decides by the same rule: a pylock.dev.toml is read like a pylock.toml, never as a lookups file).
const OFFLINE_KINDS = ["uv", "pylock", "poetry", "pdm"];

// Whether a file path not listed by plan() is one wheelgap reads by its name: a lockfile (also in a folder plan()
// skips, or past its limit), a requirements file (*.txt, *.in) or pyproject.toml.
export function isReadableFile(path) {
  return fileKind(path) !== null;
}

// autoPick()'s `why` and `project`, only when it has them, so #229's answers keep their shape.
const pickReason = (pick) => Object.fromEntries(["why", "project"].filter((k) => pick[k]).map((k) => [k, pick[k]]));

// Pick the file to check when a link or query may have named one explicitly.
// Returns { lock, lockfiles, auto, notice, choose }. `lockfiles` has the named file added at the end, marked `added` (so the
// repo's own automatic pick stays first, and the badge never treats it as the default) when it exists in the repo tree and is readable. `notice` says why a named file
// wasn't checked; the page adds which file it checked instead, since that can still change (withoutUnreadable).
// With no file named, or one that can't be checked, the page's own pick is autoPick()'s, so `choose` lists the files
// to ask between when it picks none (lock null); a named file that is checked has choose null.
export function pickRequestedFile(lockfiles, requested, inTree, dir, ctx = {}) {
  if (!requested) {
    const pick = autoPick(lockfiles, dir, ctx);
    return { lock: pick.lock, lockfiles, auto: true, notice: null, choose: pick.choose, ...pickReason(pick) };
  }
  const existing = lockfiles.find((l) => l.path === requested);
  if (existing) {
    return { lock: existing, lockfiles, auto: false, notice: null, choose: null };
  }
  if (inTree && isReadableFile(requested)) {
    const added = { path: requested, offline: OFFLINE_KINDS.includes(fileKind(requested)), added: true };
    return { lock: added, lockfiles: [...lockfiles, added], auto: false, notice: null, choose: null };
  }
  const notice = !isReadableFile(requested)
    ? `${requested} isn't a file this page reads.`
    : `${requested} wasn't found in this repository.`;
  const pick = autoPick(lockfiles, dir, ctx);
  return { lock: pick.lock, lockfiles, auto: true, notice, choose: pick.choose, ...pickReason(pick) };
}

// After wheelgap couldn't read `failed`: the repo's other files to pick from instead, or null to report the error.
// Only for a pyproject.toml the page picked itself: it's listed without knowing whether it has [project]
// dependencies to read (many hold only tool settings), so the repo's next file is the better answer. A file the
// visitor chose, or a lockfile that's broken, is reported instead.
export function withoutUnreadable(lockfiles, failed, auto) {
  const rest = lockfiles.filter((l) => l.path !== failed);
  return auto && failed.split("/").pop() === "pyproject.toml" && rest.length ? rest : null;
}

// An all-private pyproject has no browser answer. Use the driver's entry count, not private.length: its display
// names are deduplicated, while listed counts repeated requirements (for example, with different markers).
// When that's because installer settings beside it or above it couldn't all be read (configs_unread, configs_unknown:
// see privateText), the reason says so instead.
export function privatePyprojectReason(result) {
  if (!(!result.error && result.looked_up && result.lockfile?.split("/").pop() === "pyproject.toml"
    && result.packages === 0 && result.listed > 0 && result.private_count === result.listed)) return null;
  if (result.configs_unknown) return "GitHub listed only part of the repo's files, so installer settings that apply to it may be missing";
  if (result.configs_unread?.length) return `installer settings that apply to it couldn't be read here (${result.configs_unread.join(", ")})`;
  return "its index or source settings don't identify which packages are from PyPI";
}

// Why a file's result is no answer, so that withoutUnreadable() may move on from it, or null when the result stands:
// wheelgap couldn't read it, it lists no dependencies, or every pyproject dependency was skipped as private.
// Other skipped packages, or ones not installed on the targets picked, still constitute an answer.
export function unreadableReason(result) {
  if (result.kind === "source") return result.error || "wheelgap couldn't read it";
  if (!result.error && result.looked_up && result.listed === 0) return "it lists no dependencies";
  return privatePyprojectReason(result);
}

// A platform's tile, from its counts ({wheel, source, missing, unknown}): how many packages have no wheel there, a ✓
// only when every package was checked and has one, and a ? when none is known to lack one but some couldn't be
// checked, where a ✓ would read as a pass. `outside`: the lock leaves this Python out, so nothing is counted.
export function tileText(c, outside) {
  if (outside) return { mark: "–", note: "not locked" };
  const without = c.source + c.missing; // unknown is in the tooltip, never counted as "without a wheel"
  if (without) return { mark: String(without), note: "without a wheel" };
  return c.unknown ? { mark: "?", note: `${c.unknown} unknown` } : { mark: "✓", note: "all wheels" };
}

// Why packages of a looked-up file are unknown, from the worker's count of the lookups that got no answer
// ({cap, over_cap, not_found, failed}), or null when every lookup was answered (or the file needed none).
export function missedText(missed, lockfile) {
  if (!missed) return null;
  const why = [
    missed.over_cap ? `${missed.over_cap} beyond the ${missed.cap} packages this page looks up for one file` : null,
    missed.failed ? `${missed.failed} whose lookup failed or timed out (check again to retry)` : null,
    missed.not_found ? `${missed.not_found} that PyPI doesn't have under that name or version` : null,
  ].filter(Boolean);
  if (!why.length) return null;
  const local = missed.over_cap || missed.failed ? ` uvx wheelgap ${lockfile} in a checkout looks them up.` : "";
  return `Not looked up on PyPI, so shown as unknown: ${why.join("; ")}.${local}`;
}

// Why a looked-up file's packages were kept off pypi.org (driver.check's `private`), or null when none were. The
// driver keeps them off when the file, or a uv.toml, pyproject.toml or pdm.toml beside it or above it (`configs`),
// names another index; and when one of those settings files the repo has couldn't be fetched or is named in another
// case (`configs_unread`), or GitHub cut the file list short, so one may be missing from it (`configs_unknown`).
export function privateText(r) {
  const names = r.private || [];
  if (!names.length) return null;
  const list = (items, most = 8) => `${items.slice(0, most).join(", ")}${items.length > most ? `, and ${items.length - most} more` : ""}`;
  const after = `so these may not be PyPI's packages and their names aren't sent to pypi.org: ${list(names)}.`;
  const unread = r.configs_unread || [];
  if (r.configs_unknown || unread.length) {
    const why = r.configs_unknown
      ? `GitHub listed only part of this repo's files, so a uv.toml, pyproject.toml or pdm.toml beside ${r.lockfile} or in a folder above it may be missing from the list`
      : `${list(unread, 3)} (beside ${r.lockfile} or in a folder above it) couldn't be read here`;
    // wheelgap's CLI looks a requirements file's packages up on PyPI unless --index-url names another index, so the
    // command a visitor runs says so: the plain one would send these names to pypi.org.
    const retry = r.configs_unknown
      ? `In a checkout, run uvx wheelgap --index-url with your project's index and ${r.lockfile}: it reads them all, and without --index-url it would look these names up on PyPI.`
      : `Check again to retry, or in a checkout run uvx wheelgap --index-url with your project's index and ${r.lockfile}: without --index-url it would look these names up on PyPI.`;
    return `Not checked: ${why}. Such a file can point installers at an index other than PyPI (a [tool.uv] index, say), ${after} ${retry}`;
  }
  const near = (r.configs || []).filter((c) => c !== r.lockfile);
  const where = near.length ? `${r.lockfile} or its installer settings (${list(near, 3)})` : r.lockfile;
  return `Not checked: ${where} names an index or source other than PyPI (an --index-url or --extra-index-url line, a [tool.uv] index or sources setting, a Pipfile source), ${after} To check them on that index, run uvx wheelgap --index-url with its URL in a checkout.`;
}

// Page state <-> URL query, so a result can be shared. Unknown platforms and Pythons are dropped.
export function toQuery(state) {
  const q = new URLSearchParams();
  q.set("repo", `${state.repo.owner}/${state.repo.name}`);
  if (state.repo.ref) q.set("ref", state.repo.ref);
  if (state.lockfile) q.set("lock", state.lockfile);
  if (state.pythons?.length) q.set("py", state.pythons.join(","));
  if (state.platforms?.length) q.set("t", state.platforms.join(","));
  return q.toString().replace(/%2F/g, "/").replace(/%2C/g, ",");
}

// The Markdown for a README badge that links to the check of a repo. Only the repo goes in the link, never the ref,
// Pythons or platforms, so a click always checks the default branch as it is that day, on the targets its CI names
// then. `lock` is added only when the file shown (`lockfile`) isn't the one the page would pick by itself
// (`defaultLock`, from pickLockfile()), as in a monorepo. URLSearchParams encodes whatever could end the Markdown
// link early: a space, a quote or a bracket in a path.
export const BADGE_URL = "https://wheelgap.github.io/tracker/check/badge.svg";
export const CHECK_URL = "https://wheelgap.github.io/tracker/check/";
export function badgeMarkdown(repo, lockfile, defaultLock) {
  const q = new URLSearchParams();
  q.set("repo", `${repo.owner}/${repo.name}`);
  if (lockfile && lockfile !== defaultLock) q.set("lock", lockfile);
  return `[![Check wheels](${BADGE_URL})](${CHECK_URL}?${q.toString().replace(/%2F/g, "/")})`;
}

export function fromQuery(search) {
  const q = new URLSearchParams(search);
  const repoText = q.get("repo");
  if (!repoText) return null;
  const repo = parseRepo(repoText);
  if (!repo) return null;
  const ref = q.get("ref");
  if (ref && validRef(ref)) repo.ref = ref;
  const known = new Set(PLATFORMS.map(([p]) => p));
  const list = (k, ok) => (q.get(k) || "").split(",").filter((v) => ok(v));
  const lock = q.get("lock");
  return {
    repo,
    lockfile: lock && lock.split("/").every((p) => p && p !== "." && p !== "..") && lock.length < 300 ? lock : null,
    pythons: list("py", (v) => PYTHONS.includes(v) || /^3\.(1[0-9])t?$/.test(v)),
    platforms: list("t", (v) => known.has(v)),
  };
}

// The Python versions in display order: 3.13, 3.14, 3.14t, 3.15, 3.15t.
export function sortPythons(pythons) {
  const key = (v) => {
    const [maj, min] = v.replace(/t$/, "").split(".").map(Number);
    return maj * 1000 + min * 2 + (v.endsWith("t") ? 1 : 0);
  };
  return [...new Set(pythons)].sort((a, b) => key(a) - key(b));
}

// The free-threaded build of the newest Python picked, for the "+ free-threaded" shortcut (null if it's already in).
export function freeThreaded(pythons) {
  const plain = sortPythons(pythons).filter((v) => !v.endsWith("t"));
  const newest = plain.length ? plain[plain.length - 1] : "3.15";
  const [, min] = newest.split(".").map(Number);
  const ft = `3.${Math.max(min, 13)}t`;
  return pythons.includes(ft) ? null : ft;
}

// One row's fix, for the table and the prompt: {kind, version?, text}. kind is one of:
// "all" (one release has a wheel for every gap), "some" (releases for some gaps), "loses" (every newer release with a
// wheel for a gap drops a wheel the pin has elsewhere), "none" (no newer release has one), "private" (not looked up:
// its index isn't known to be PyPI, as with the CLI's --suggest, so its name stays off pypi.org), "unchecked" (no gap to fix: couldn't be checked) or
// "pending" (not looked up yet, or the lookup failed).
export function fixFor(row, fixes) {
  if (!row.gaps.length) return { kind: "unchecked", text: "" };
  if (row.lookup === false) return { kind: "private", text: "not looked up (index not known to be PyPI)" };
  const fix = fixes?.[`${row.name}==${row.version}`];
  if (fix === undefined) return { kind: "pending", text: "" };
  const found = Object.entries(fix.found);
  const loses = fix.loses || [];
  if (!found.length) {
    return loses.length
      ? { kind: "loses", text: "only releases that drop a wheel elsewhere" }
      : { kind: "none", text: "no newer release has a wheel" };
  }
  const versions = [...new Set(found.map(([, v]) => v))];
  const major = (v) => String(v).split(".")[0];
  const caution = versions.some((v) => major(v) !== major(row.version)) ? " (a new major version)" : "";
  if (found.length === row.gaps.length && versions.length === 1) {
    return { kind: "all", version: versions[0], text: `${versions[0]}${caution}` };
  }
  return {
    kind: "some",
    version: versions[versions.length - 1],
    text: `${versions.join(" or ")}${caution} for ${found.length} of ${row.gaps.length} gaps`,
  };
}

// The rows with a gap (or a target that couldn't be checked) on one Python, most such platforms first.
export function rowsFor(result, python) {
  const labels = result.targets.filter((t) => t.python === python).map((t) => t.label);
  const marked = (r, l) => r.gaps.includes(l) || (r.unknown || []).includes(l);
  return result.rows
    .map((r) => ({ ...r, here: labels.filter((l) => GAP.has(r.statuses[l]) && marked(r, l)) }))
    .filter((r) => r.here.length)
    .sort((a, b) => b.here.length - a.here.length || a.name.localeCompare(b.name));
}

// The headline numbers: distinct packages with no wheel somewhere (gaps), distinct packages that couldn't be checked
// somewhere and have no gap (unknown), out of how many, on how many counted targets.
export function headline(result) {
  const gaps = new Set(result.rows.filter((r) => r.gaps.length).map((r) => r.name));
  const unknown = new Set(result.rows.filter((r) => (r.unknown || []).length && !gaps.has(r.name)).map((r) => r.name));
  const counted = result.targets.filter((t) => !result.outside_lock.includes(t.python));
  return { gaps: gaps.size, unknown: unknown.size, packages: result.packages, targets: counted.length };
}

// The `uvx wheelgap` command that reproduces the result locally.
export function cliCommand(result, pythons, platforms) {
  const lock = /^[A-Za-z0-9._\/-]+$/.test(result.lockfile) ? result.lockfile : `'${result.lockfile.replace(/'/g, "")}'`;
  return `uvx wheelgap ${lock} -p ${sortPythons(pythons).join(",")} -t ${platforms.join(",")}`;
}

const PACKAGE_NAME = /^[A-Za-z0-9](?:[A-Za-z0-9._-]{0,98}[A-Za-z0-9])?$/;

// Read the published PyPI package name from a root setup.cfg ([metadata] name) or pyproject.toml ([project] name).
// Only accepts a plain valid package name (never attr:, file:, or characters a package name can't have). Anything
// read differently from configparser or TOML (a value continued on the next line, a quoted or dotted key) is null.
export function projectNameFromMetadata(filename, text) {
  if (typeof text !== "string") return null;
  let section = null;
  if (filename === "setup.cfg") {
    let key = false; // a key seen in this section: an indented line after it continues its value (configparser)
    const lines = text.split(/\r?\n/);
    const indent = (raw) => raw.match(/^\s*/)[0].length;
    for (let i = 0; i < lines.length; i++) {
      const raw = lines[i];
      const line = raw.trim();
      if (!line || line.startsWith("#") || line.startsWith(";")) continue;
      if (key && /^\s/.test(raw)) continue;
      // configparser's section names are case-sensitive and run to the line's last "]"; setuptools reads [metadata].
      const hdr = line.match(/^\[(.+)\]/);
      if (hdr) {
        section = hdr[1];
        key = false;
        continue;
      }
      key = true;
      if (section === "metadata") {
        const m = line.match(/^name\s*[=:]\s*(.*)$/i);
        if (m) {
          const val = m[1].trim();
          if (/^(?:attr|file)\s*:/i.test(val)) return null;
          // The next line that isn't blank or a comment, indented deeper than the key, continues the value
          // ("httpie\nevil" to configparser, blank lines in between or not): not a plain name.
          const next = lines.slice(i + 1).find((l) => l.trim() && !/^\s*[#;]/.test(l));
          if (next !== undefined && indent(next) > indent(raw)) return null;
          return PACKAGE_NAME.test(val) ? val : null;
        }
      }
    }
    return null;
  }
  if (filename === "pyproject.toml") {
    for (const raw of text.split(/\r?\n/)) {
      const line = raw.trim();
      if (!line || line.startsWith("#")) continue;
      // A multi-line string isn't tracked, so a "name = ..." inside one could be read as a key: fail closed.
      if (line.includes('"""') || line.includes("'''")) return null;
      // [table] or [[array.of.tables]], with spaces inside the brackets and a comment after, as TOML allows.
      const hdr = line.match(/^\[(\[?)\s*([^\[\]]*?)\s*\](\]?)\s*(?:#.*)?$/);
      if (hdr) {
        section = hdr[1] || hdr[3] ? `[${hdr[2]}]` : hdr[2];
        continue;
      }
      if (line.startsWith("[")) {
        section = null; // a header this doesn't read (a quoted name): nothing under it is [project]'s
        continue;
      }
      if (section === "project") {
        const m = line.match(/^name\s*=\s*(?:"([^"\\]+)"|'([^']+)')\s*(?:#.*)?$/);
        if (m) {
          const val = (m[1] ?? m[2]).trim();
          return PACKAGE_NAME.test(val) ? val : null;
        }
      }
    }
  }
  return null;
}

// The root files load() reads a project name from, in order, when the page has nothing else to check: a lone root
// pyproject.toml (it may fail to read: no [project] table, or dynamic dependencies), then a root setup.cfg. A
// pyproject.toml with only [build-system] beside a setup.cfg is the usual setuptools layout, so both are tried.
export function metadataFiles(lockfiles, paths) {
  const lonePyproject = lockfiles.length === 1 && lockfiles[0].path === "pyproject.toml";
  if (lockfiles.length && !lonePyproject) return [];
  return [...(lonePyproject ? ["pyproject.toml"] : []), ...(paths.includes("setup.cfg") ? ["setup.cfg"] : [])];
}

// The `uvx wheelgap --deps <package>` command for a library published on PyPI.
export function packageCliCommand(packageName, pythons, platforms) {
  if (!PACKAGE_NAME.test(String(packageName ?? ""))) return null;
  const parts = [`uvx wheelgap --deps ${packageName}`];
  if (pythons?.length) parts.push(`-p ${sortPythons(pythons).join(",")}`);
  if (platforms?.length) parts.push(`-t ${platforms.join(",")}`);
  return parts.join(" ");
}

// Look up a fill the Python driver already matched, for display only.
export function gapWheelFor(row, gapWheels, target) {
  return gapWheels?.cells.some((cell) => cell.name === row.name && cell.version === row.version && cell.target === target) ?? false;
}

// The Fix column's text when no release fixes a row: how many of the row's gaps a gap wheel fills, so a wheel for
// one target is never read as a fix for all of them. null when none is filled.
export function gapWheelFixText(row, gapWheels) {
  const filled = row.gaps.filter((target) => gapWheelFor(row, gapWheels, target)).length;
  if (!filled) return null;
  return filled === row.gaps.length ? "gap wheel available" : `gap wheel for ${filled} of ${row.gaps.length} gaps`;
}

// The check scripts/build_pages.py's valid_issues_url() makes before the package page links an issues_url (it's
// data from the package's PyPI metadata): an https:// URL with a host, printable ASCII only, no spaces or control
// characters, and no user@ part. Read from the text itself, as urlsplit reads it, so "https:///issues" has no host
// (WHATWG's URL would read one) and "https://a@b.example" is refused even with an empty user.
export function validIssuesUrl(url) {
  if (typeof url !== "string" || !/^[\x21-\x7E]+$/.test(url)) return false;
  const authority = url.match(/^https:\/\/([^/?#]*)/i)?.[1];
  if (!authority || authority.includes("@")) return false;
  try {
    const u = new URL(url);
    return u.protocol === "https:" && Boolean(u.hostname);
  } catch {
    return false;
  }
}

// The links under a row's Fix text when no release fixes it (`none` or `loses`) and no gap wheel fills it: the
// package's tracker page when the tracker has it, and its maintainers' issue tracker when that's known and valid.
// `trackerPkg` is what loadLab() kept for the package ({ok, builds, issues_url}). A row not looked up gets none.
export function fixLinks(row, f, trackerPkg = null, gapWheels = null) {
  if (!row || !f || row.lookup === false) return [];
  if (f.kind !== "none" && f.kind !== "loses") return [];
  if (gapWheels && gapWheelFixText(row, gapWheels)) return [];
  const links = [];
  const name = canonicalName(row.name || "");
  if (trackerPkg?.ok === true && name) links.push({ text: "coverage and build results", href: `../p/${name}/` });
  const issuesUrl = trackerPkg?.issues_url;
  if (validIssuesUrl(issuesUrl)) links.push({ text: "ask the maintainers", href: issuesUrl, rel: "noopener nofollow" });
  return links;
}

// A prompt to paste into Claude Code (or any coding agent): the gaps, the bumps that fix them, and how to confirm.
export function fixPrompt(repoName, result, fixes, pythons, platforms, gapWheels = null, tracker = {}) {
  const groups = { all: [], some: [], loses: [], none: [], private: [], pending: [] };
  for (const r of result.rows) {
    const f = fixFor(r, fixes);
    if (f.kind === "unchecked") continue;
    const gaps = r.gaps.join(", ");
    if (f.kind === "all") groups.all.push(`- ${r.name} ${r.version} -> ${f.text}`);
    else if (f.kind === "some") groups.some.push(`- ${r.name} ${r.version} -> ${f.text} (gaps on ${gaps})`);
    else if (f.kind === "none" || f.kind === "loses") {
      const issuesUrl = tracker?.[canonicalName(r.name)]?.issues_url;
      const ask = validIssuesUrl(issuesUrl) ? ` (ask the maintainers: ${issuesUrl})` : "";
      groups[f.kind].push(`- ${r.name} ${r.version}: ${gaps}${ask}`);
    } else groups[f.kind].push(`- ${r.name} ${r.version}: ${gaps}`);
  }
  const lines = [
    `In ${repoName}, wheelgap found dependencies in ${result.lockfile} with no prebuilt wheel for some of the platforms we target (Python ${sortPythons(pythons).join(", ")} on ${platforms.join(", ")}), so they'd be compiled from source or fail to install.`,
    "",
  ];
  const section = (key, title) => groups[key].length && lines.push(title, ...groups[key], "");
  section("all", "Bump these to the release named, which has wheels for every target where the pinned one is missing one:");
  section("some", "These have a newer release with wheels for some of the gaps:");
  section("loses", "For these, every newer release with the missing wheel drops a wheel the pinned one has elsewhere, so a bump trades one gap for another:");
  section("none", "No newer release has a wheel for these (consider dropping the target, another package, or building wheels):");
  section("private", "These weren't looked up, since the lockfile doesn't record their index as PyPI (Poetry and PDM locks don't, nor Git or path sources); look for a newer release with wheels on the index they come from:");
  section("pending", "These weren't looked up on PyPI, so check them with `uvx wheelgap --explain --suggest`:");
  if (gapWheels?.text) {
    lines.push(`For gaps a release bump does not cover, these gap wheels are ${gapWheels.unofficial}:`, gapWheels.text, "");
  }
  lines.push(
    "Check each bump against our version constraints (a new major version may need code changes), update the lockfile with the project's own tool, and confirm with:",
    `  ${cliCommand(result, pythons, platforms)}`,
    "",
    "To let the agent check packages itself next time: claude mcp add wheelgap -- uvx --from wheelgap wheelgap-mcp",
  );
  return lines.join("\n");
}

// The verdict's headline sentence.
export function verdictText(h) {
  // Nothing to count (every package skipped, or none listed): never a ✓, which would read as a pass.
  if (h.packages === 0) return "No packages were checked";
  const where = h.targets === 1 ? "this target" : `all ${h.targets} targets`;
  if (h.gaps === 0 && h.unknown) {
    return `No package is missing a wheel, but ${h.unknown} of ${h.packages} couldn't be checked on ${h.targets === 1 ? "this target" : "some targets"}`;
  }
  if (h.gaps === 0) return `✓ All ${h.packages} packages have a wheel for ${where}`;
  const some = h.targets === 1 ? "this target" : `at least one of the ${h.targets} targets`;
  return `${h.gaps} of ${h.packages} packages ${h.gaps === 1 ? "has" : "have"} no wheel for ${some}`;
}

// One-line plain-text summary of a result, ending with the share link. Built as text, never as HTML. The line is
// pasted where the page's alerts don't follow it, so `notChecked` (the packages left off pypi.org as a private
// index's, which the counts leave out) is said in the line itself, and a result with any is never a ✓.
export function shareText(repoName, h, pythons, platforms, url, notChecked = 0) {
  const tail = `${notChecked ? `; ${notChecked} more ${notChecked === 1 ? "wasn't" : "weren't"} checked (their index isn't known to be PyPI)` : ""} (checked with wheelgap) ${url}`;
  if (h.packages === 0) {
    return `${repoName}: no dependencies were checked (checked with wheelgap) ${url}`;
  }
  if (h.targets === 0) {
    return `${repoName}: lockfile leaves out every Python picked${tail}`;
  }
  if (h.gaps === 0 && h.unknown) {
    const where = h.targets === 1 ? "1 target" : "some targets";
    return `${repoName}: no dependency is missing a wheel, but ${h.unknown} of ${h.packages} couldn't be checked on ${where}${tail}`;
  }
  if (h.gaps === 0) {
    const deps = h.packages === 1 ? "1 dependency has" : `all ${h.packages} dependencies have`;
    const pyList = sortPythons(pythons).join(", ");
    const plats = platforms.length === 1 ? "1 platform" : `${platforms.length} platforms`;
    return `${notChecked ? "" : "✓ "}${repoName}: ${notChecked ? deps.replace(/ (has|have)$/, " checked $1") : deps} a wheel on Python ${pyList} for ${plats}${tail}`;
  }
  const verb = h.gaps === 1 ? "has" : "have";
  const where = h.targets === 1 ? "1 target" : `at least one of ${h.targets} targets`;
  return `${repoName}: ${h.gaps} of ${h.packages} dependencies ${verb} no wheel on ${where}${tail}`;
}

// "Ready for what's next?" shortcuts: the platforms and Pythons people most often move to, that aren't picked yet.
// Each is {label, pythons, platforms}: the whole selection to switch to.
export function nextSteps(pythons, platforms) {
  const out = [];
  const add = (label, py, plats) => out.push({ label, pythons: sortPythons(py), platforms: PLATFORMS.map(([p]) => p).filter((p) => plats.includes(p)) });
  const ft = freeThreaded(pythons);
  if (ft) add(`Free-threaded ${ft}`, [...pythons, ft], platforms);
  if (!platforms.includes("win-arm64")) add("Windows on ARM", pythons, [...platforms, "win-arm64"]);
  if (!platforms.includes("linux-aarch64")) add("Linux ARM64", pythons, [...platforms, "linux-aarch64"]);
  if (!platforms.includes("musl-x86_64") && !platforms.includes("musl-aarch64")) add("Alpine Linux", pythons, [...platforms, "musl-x86_64", "musl-aarch64"]);
  const newest = PYTHONS.filter((v) => !v.endsWith("t")).at(-1);
  if (!pythons.includes(newest)) add(`Python ${newest}`, [...pythons, newest], platforms);
  const all = PLATFORMS.map(([p]) => p);
  const everyFt = ft ? [...pythons, ft] : pythons;
  if (out.length > 1) add("Everything", everyFt, all);
  return out;
}

// The build lab's verdict for one result (a copy of labVerdict in ../logic.js; tests/site/check-logic.test.mjs checks
// they agree): {kind, text}, where a wheel with no extension module is never called a working compiled build.
export function labVerdict(r) {
  const mins = (s) => (s < 90 ? `${s} s` : `${Math.round(s / 60)} min`);
  if (r.outcome === "timeout") return { kind: "slow", text: `didn't finish building in ${mins(r.seconds)}` };
  if (r.outcome === "failed" && r.reason === "unsupported platform") return { kind: "unsupported", text: "the package doesn't support this platform (its own build says so)" };
  if (r.outcome === "failed") return { kind: "fails", text: r.reason === "other" ? "fails to build" : `fails to build: ${r.reason}` };
  if (r.import === "deps-failed") return { kind: "broken", text: `builds in ${mins(r.seconds)}, but its dependencies don't install` };
  if (r.import !== "ok") return { kind: "broken", text: `builds in ${mins(r.seconds)}, but doesn't import` };
  if (!r.extensions) {
    // The lab can't tell a pure-Python package (docopt) from an optional extension that was skipped.
    if (/-none-any$/.test(r.wheel || "")) {
      return { kind: "pure", text: `builds in ${mins(r.seconds)} as pure Python, with no compiled extension module (if it has an optional one, that was skipped)` };
    }
    return { kind: "partial", text: `builds in ${mins(r.seconds)}, but with no compiled extension module` };
  }
  if (r.gil === "re-enabled") return { kind: "gil", text: `builds in ${mins(r.seconds)}, but importing it turns the GIL back on` };
  return { kind: "works", text: `builds from source in ${mins(r.seconds)} and imports${r.gil === "kept-off" ? ", GIL stays off" : ""}` };
}

// The PEP 503 name the tracker files packages under.
export function canonicalName(name) {
  return String(name).toLowerCase().replace(/[-_.]+/g, "-");
}

// GitHub's own hosts (github.com, gist/api.github.com, raw.githubusercontent.com): not "another host".
function githubHost(host) {
  return host === "github.com" || host.endsWith(".github.com") || host === "githubusercontent.com" || host.endsWith(".githubusercontent.com");
}

function hostHint(host) {
  const h = host.toLowerCase().replace(/^www\./, "");
  return !h || githubHost(h) ? null : { kind: "other-host", host: h };
}

const FORGES = ["gitlab.com", "codeberg.org", "bitbucket.org", "sr.ht", "git.sr.ht", "gitea.com", "sourceforge.net", "salsa.debian.org"];

// Classify an input parseRepo() rejected: a single PyPI package name, a URL on another host, or null.
export function inputHint(text) {
  const s = String(text ?? "").trim();
  if (!s || /\s/.test(s)) return null;
  const pypi = s.match(/^(?:https?:\/\/)?(?:www\.)?pypi\.org\/(?:project|simple)\/([A-Za-z0-9._-]+)/i);
  if (pypi) return { kind: "package", name: canonicalName(pypi[1]) };
  const ssh = s.match(/^(?:ssh:\/\/)?git@([A-Za-z0-9.-]+)[:/]/i);
  if (ssh) return hostHint(ssh[1]);
  // A URL with a scheme (https://, git+https://, ssh://), or one without where the first segment is a host.
  const withScheme = /^[a-z][a-z0-9+.-]*:\/\//i.test(s);
  const first = s.split("/")[0];
  if (withScheme || (s.includes("/") && /^[A-Za-z0-9-]+(\.[A-Za-z0-9-]+)+$/.test(first) && /\.[A-Za-z]{2,}$/.test(first))) {
    try {
      return hostHint(new URL(withScheme ? s.replace(/^[a-z0-9.-]*\+/i, "") : `https://${s}`).hostname);
    } catch {
      return null;
    }
  }
  // A known forge's bare domain (gitlab.com, codeberg.org) is a host; other dotted names (jaraco.net) are packages.
  const bare = s.toLowerCase().replace(/^www\./, "");
  if (FORGES.includes(bare)) return hostHint(s);
  if (githubHost(bare)) return null;
  if (!s.includes("/") && /^[A-Za-z0-9](?:[A-Za-z0-9._-]{0,98}[A-Za-z0-9])?$/.test(s)) {
    return { kind: "package", name: canonicalName(s) };
  }
  return null;
}

// The lab result for exactly this release on this target (label like "3.14t win-arm64"), or null: a result for
// another version says nothing about the pinned one.
export function labFor(builds, version, label) {
  if (!Array.isArray(builds)) return null;
  return builds.find((b) => b && b.target === label && b.version === version) ?? null;
}
