// The repo checker's worker: loads Pyodide and wheelgap, fetches the repo's files from GitHub, each package's files
// (for an input that doesn't list them) and the fixes' release lists from PyPI, and the gap wheel list from wheelgap's
// gap wheel index (all from the browser, nothing goes through a server of ours), and runs driver.py on them.
// Messages in: {id, type: "load", repo} | {id, type: "check", lockfile, options}. Out: {id, type, ...}.

import { loadPyodide } from "https://cdn.jsdelivr.net/pyodide/v314.0.8/full/pyodide.mjs";
import { rawUrl, trimProject, trimReleases, projectNameFromMetadata, metadataFiles } from "./check-logic.mjs";

// The wheelgap release the page runs; tests/test_check_driver.py tests driver.py against the same one.
// When this changes, re-read what that release's pypi._fill_json_data, pypi._newest_matching, pypi._sha256,
// cutoff.before_cutoff and suggest._has_wheel read from PyPI's JSON: trimProject/trimReleases in check-logic.mjs keep
// only those fields.
export const WHEELGAP = "wheelgap==0.14.0";
const MAX_FILE = 25 * 1024 * 1024;
const MAX_FIX_LOOKUPS = 80;
const MAX_GAP_WHEELS_BYTES = 4 * 1024 * 1024;
const GAP_WHEELS_URL = "https://wheelgap.github.io/wheels/data/gap-wheels.json";
const MAX_LOOKUPS = 300; // more packages than that stay unknown, never counted as gaps
// The installer settings files wheelgap reads beside a requirements file, a pyproject.toml or a Pipfile.lock and in the
// folders above it (driver.CONFIG_NAMES; tests/test_check_driver.py checks they match).
const CONFIG_NAMES = ["uv.toml", "pyproject.toml", "pdm.toml"];

let latestCheck = null;
let gapWheelList = null; // one request, including a failed one, for the life of this worker

let ready = null; // the driver module, once Pyodide and wheelgap are in
let latestLoad = null; // the id of the newest load asked for: an older one that finishes later is dropped
const loaded = new Map(); // by root folder: the loaded repo {owner, name, ref, root, fetched}; only the newest is kept
const releases = new Map(); // PyPI release lists by name, kept across checks

const post = (msg) => self.postMessage(msg);

async function start() {
  post({ type: "progress", stage: "python" });
  const py = await loadPyodide();
  post({ type: "progress", stage: "wheelgap" });
  await py.loadPackage("micropip");
  await py.pyimport("micropip").install(WHEELGAP);
  py.FS.writeFile("/home/pyodide/driver.py", await (await fetch(new URL("./driver.py", import.meta.url))).text());
  const driver = py.pyimport("driver");
  const version = py.runPython("import wheelgap; wheelgap.__version__");
  post({ type: "ready", wheelgap: version });
  return { py, driver };
}

class Failure extends Error {
  constructor(kind, message) {
    super(message);
    this.kind = kind;
  }
}

async function getJson(url) {
  let r;
  try {
    r = await fetch(url, { headers: { Accept: "application/vnd.github+json" } });
  } catch {
    throw new Failure("network", "Couldn't reach GitHub. Check your connection and try again.");
  }
  if (r.status === 404) throw new Failure("not-found", "No such public repository (or branch). Private repos can't be read here: run uvx wheelgap in your checkout instead.");
  if (r.status === 429 || (r.status === 403 && r.headers.get("x-ratelimit-remaining") === "0")) {
    const reset = Number(r.headers.get("x-ratelimit-reset"));
    const when = reset ? new Date(reset * 1000).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" }) : "an hour";
    throw new Failure("rate-limit", `GitHub's limit for anonymous requests from your network (60 an hour) is used up. Try again after ${when}, or run uvx wheelgap locally.`);
  }
  if (!r.ok) throw new Failure("github", `GitHub answered ${r.status}. Try again in a minute.`);
  return r.json();
}

async function getText(url) {
  const r = await fetch(url);
  if (!r.ok) throw new Failure("file", `Couldn't fetch ${url.split("/").slice(6).join("/")} (${r.status}).`);
  const big = () => new Failure("file", `${url.split("/").pop()} is over 25 MB, too big to check in the browser.`);
  if (Number(r.headers.get("content-length")) > MAX_FILE) throw big();
  const bytes = new Uint8Array(await r.arrayBuffer()); // content-length may be missing (compressed responses)
  if (bytes.byteLength > MAX_FILE) throw big();
  return bytes;
}

// Runs fn over items, at most `limit` at a time.
async function pool(items, limit, fn) {
  const out = new Array(items.length);
  let next = 0;
  async function lane() {
    while (next < items.length) {
      const i = next++;
      out[i] = await fn(items[i], i);
    }
  }
  await Promise.all(Array.from({ length: Math.min(limit, items.length) }, lane));
  return out;
}

function write(py, root, path, bytes) {
  const full = `${root}/${path}`;
  py.FS.mkdirTree(full.slice(0, full.lastIndexOf("/")));
  py.FS.writeFile(full, bytes);
}

function forget(py, root) {
  loaded.delete(root);
  py.pyimport("shutil").rmtree.callKwargs(root, { ignore_errors: true });
}

async function load(r, id, requestedFile = null) {
  const { py, driver } = await ready;
  const ref = r.ref || "HEAD";
  post({ id, type: "progress", stage: "tree" });
  const tree = await getJson(
    `https://api.github.com/repos/${r.owner}/${r.name}/git/trees/${encodeURIComponent(ref)}?recursive=1`,
  );
  const paths = (tree.tree || []).filter((e) => e.type === "blob").map((e) => e.path);
  const plan = JSON.parse(driver.plan(JSON.stringify(paths)));
  const wanted = requestedFile || r.file || null;
  // A truncated file list may leave the named file out: fetch it anyway, and a missing one fails by its own name.
  const fileInTree = Boolean(wanted && (paths.includes(wanted) || tree.truncated));
  const root = `/repos/${id}`;
  const files = [...plan.workflows, ...plan.dockerfiles, ...(plan.python_version ? [plan.python_version] : [])];
  post({ id, type: "progress", stage: "files", count: files.length });
  const unread = [];
  await pool(files, 8, async (p) => {
    try {
      write(py, root, p, await getText(rawUrl(r, ref, p)));
    } catch (e) {
      unread.push({ path: p, line: null, reason: e.message });
    }
  });
  py.FS.mkdirTree(root);
  // As in a checkout, wheelgap's walk up for installer settings files (configs_for in driver.py) stops at the repo's
  // root, the first folder with a .git: none above it is read.
  py.FS.mkdirTree(`${root}/.git`);
  const detected = JSON.parse(driver.detect(root));
  detected.unreadable.push(...unread);
  let projectName = null;
  // #229's requirements files under other names don't count: a lone root pyproject.toml beside them is still read.
  for (const metaFile of metadataFiles(plan.lockfiles.filter((l) => !l.other), paths)) {
    try {
      const text = new TextDecoder().decode(await getText(rawUrl(r, ref, metaFile)));
      projectName = projectNameFromMetadata(metaFile, text);
    } catch {
      // ignore metadata fetch failure; fall back to normal verdict
    }
    if (projectName) break;
  }
  if (id !== latestLoad) {
    forget(py, root);
    throw new Failure("stale", "superseded by a newer load"); // the page has moved on and ignores this
  }
  for (const old of [...loaded.keys()]) forget(py, old);
  // `configs`: the repo's installer settings files, or null when GitHub cut the file list short, so one may be missing
  // from it: then the driver keeps every file that reads them private.
  loaded.set(root, { ...r, ref, root, fetched: new Set(), configs: tree.truncated ? null : plan.configs });
  return { plan, detected, truncated: Boolean(tree.truncated), ref, root, fileInTree, projectName };
}

// A pyproject.toml is fetched when it's the file to check, and installer settings files (a pyproject.toml, uv.toml or
// pdm.toml) when they're beside the file to check or above it (fetchConfigs). wheelgap also reads a pyproject.toml
// beside a lockfile, when it's there, for the project's settings ([tool.uv] no-binary, an index), so any of them that
// isn't `keep` (the file to check and its own settings files) is removed before the driver reads that file: each
// file reads as it does when it's the first one checked, whichever file the visitor looked at before it.
function dropOtherProjects(py, repo, keep) {
  for (const path of [...repo.fetched]) {
    if (keep.includes(path) || !CONFIG_NAMES.includes(path.split("/").pop())) continue;
    try {
      py.FS.unlink(`${repo.root}/${path}`);
    } catch {
      // already gone: there's nothing left to remove, and the next check mustn't trip over it again
    }
    repo.fetched.delete(path);
  }
}

// The repo's installer settings files (plan()'s `configs`) for the driver, always as a string: "null" when they
// aren't known, which keeps every file that reads them private.
function configsJson(repo) {
  return JSON.stringify(Array.isArray(repo.configs) ? repo.configs : null);
}

// wheelgap takes a requirements file, a pyproject.toml or a Pipfile.lock as PyPI's alone only if no uv.toml,
// pyproject.toml or pdm.toml in its folder or a folder above it names another index (a [tool.uv] index, say), which it
// reads from disk. So the ones the repo has there (the driver's configs_for) are fetched before the driver reads the
// file, and kept while it does: returns them, for dropOtherProjects. One that can't be fetched is left out, and the
// driver, finding it in the repo's list but not on disk, looks nothing of the file up on PyPI.
async function fetchConfigs(id, repo, lockfile) {
  const { py, driver } = await ready;
  if (id !== latestCheck) throw new Failure("stale", "superseded by a newer check");
  const near = JSON.parse(driver.configs_for(lockfile, configsJson(repo)));
  await pool(near.filter((p) => !repo.fetched.has(p)), 4, async (p) => {
    let bytes;
    try {
      bytes = await getText(rawUrl(repo, repo.ref, p));
    } catch {
      return; // not on disk, so the driver keeps the file private and the page says why
    }
    if (!loaded.has(repo.root)) return; // don't recreate a removed root
    write(py, repo.root, p, bytes);
    repo.fetched.add(p);
  });
  if (!loaded.has(repo.root)) throw new Failure("stale", "superseded by a newer load");
  return near;
}

// Asks the driver which packages a lookups file (requirements file, pyproject.toml, Pipfile.lock, pixi.lock)
// needs looked up, fetching its includes (-r, -c) from the same repo and ref when it names them. An include
// that's a URL, or outside the repo, comes back as an error naming it and stops here. A file wheelgap can't read
// needs no lookups: driver.check() reports why, as it does for a lockfile. Only the newest check reads or removes
// files: a superseded one stops here, so it can't take away the file a newer check is about to read.
async function lookups(id, repo, lockfile, keep) {
  const { py, driver } = await ready;
  for (let tries = 0; tries < 10; tries++) {
    if (id !== latestCheck) throw new Failure("stale", "superseded by a newer check");
    dropOtherProjects(py, repo, keep);
    const n = JSON.parse(driver.needs(repo.root, lockfile, configsJson(repo)));
    if (!n.error) return n.lookups ?? [];
    if (n.kind !== "include") return [];
    if (!n.path || repo.fetched.has(n.path)) throw new Failure("file", n.error);
    post({ id, type: "progress", stage: "lockfile", path: n.path });
    const bytes = await getText(rawUrl(repo, repo.ref, n.path));
    if (!loaded.has(repo.root)) throw new Failure("stale", "superseded by a newer load");
    write(py, repo.root, n.path, bytes);
    repo.fetched.add(n.path);
  }
  throw new Failure("file", "This file's includes go too deep to read here.");
}

async function check(id, root, lockfile, options) {
  const { py, driver } = await ready;
  const repo = loaded.get(root); // the repo this check was asked about, never whichever loaded last
  if (!repo) throw new Failure("state", "That repository is no longer loaded. Check it again.");
  if (!repo.fetched.has(lockfile)) {
    post({ id, type: "progress", stage: "lockfile", path: lockfile });
    const bytes = await getText(rawUrl(repo, repo.ref, lockfile));
    if (!loaded.has(root)) throw new Failure("stale", "superseded by a newer load"); // don't recreate a removed root
    write(py, repo.root, lockfile, bytes);
    repo.fetched.add(lockfile);
  }
  // A file that doesn't list every package's files is checked from PyPI JSON fetched here, the way the CLI
  // does; packages over the cap, or that fail, stay unknown, never counted as gaps. `missed` counts them, for the
  // page to say why: over the cap, not on PyPI (a 404), or a lookup that failed.
  const keep = [lockfile, ...(await fetchConfigs(id, repo, lockfile))];
  const wanted = await lookups(id, repo, lockfile, keep);
  const fetched = {};
  const missed = { cap: MAX_LOOKUPS, over_cap: Math.max(0, wanted.length - MAX_LOOKUPS), not_found: 0, failed: 0 };
  if (wanted.length) {
    post({ id, type: "progress", stage: "lookups" });
    await pool(wanted.slice(0, MAX_LOOKUPS), 6, async (l) => {
      if (id !== latestCheck) return; // superseded while looking up: the rest of its requests aren't made
      const key = l.version ? `${l.name}==${l.version}` : l.name;
      try {
        const url = `https://pypi.org/pypi/${encodeURIComponent(l.name)}/${l.version ? `${encodeURIComponent(l.version)}/` : ""}json`;
        // A lookup that stalls is given up on after 30 seconds, so one request can't hold the whole check.
        const r = await fetch(url, { signal: AbortSignal.timeout(30000) });
        if (r.ok) {
          const doc = trimProject(await r.json());
          fetched[key] = doc;
          // A project's JSON (a range's lookup) has the release list fixes() asks PyPI for, so it's kept for that.
          // A pinned release's JSON has no release list: kept, it would stop fixes() from ever fetching one.
          if (!l.version && doc?.releases && !releases.has(l.name)) releases.set(l.name, doc.releases);
        } else if (r.status === 404) {
          fetched[key] = null; // not on PyPI: stays unknown
          missed.not_found++;
        } else missed.failed++; // 429 or 5xx: the package stays "not looked up"
      } catch {
        missed.failed++; // network trouble, a timeout, or an answer that isn't JSON: stays "not looked up"
      }
    });
  }
  // The driver keeps the last check's targets for fixes(), so a superseded check must never reach it.
  if (id !== latestCheck) throw new Failure("stale", "superseded by a newer check");
  post({ id, type: "progress", stage: "checking" });
  dropOtherProjects(py, repo, keep); // again: a check that this one superseded may have fetched one since
  // Always a string: Pyodide may hand a JS null to Python as something other than None.
  const result = JSON.parse(driver.check(repo.root, lockfile, JSON.stringify(options), JSON.stringify(fetched), configsJson(repo)));
  if (!result.error) result.missed = missed;
  return result;
}

// Looks up on PyPI the gap rows the driver marked as PyPI packages (most gaps first; never a private index's names)
// and asks the driver for each one's nearest fix. A failed lookup isn't remembered, so the next check retries it.
async function fixes(id, rows) {
  const { driver } = await ready;
  const wanted = rows.filter((r) => r.lookup && r.gaps.length);
  const names = [...new Set([...wanted].sort((a, b) => b.gaps.length - a.gaps.length).map((r) => r.name))];
  const todo = names.filter((n) => !releases.has(n)).slice(0, MAX_FIX_LOOKUPS);
  await pool(todo, 6, async (n) => {
    try {
      const r = await fetch(`https://pypi.org/pypi/${encodeURIComponent(n)}/json`);
      if (r.ok) releases.set(n, trimReleases((await r.json()).releases ?? null));
      else if (r.status === 404) releases.set(n, null);
    } catch {
      // network trouble: not cached
    }
  });
  const have = Object.fromEntries(names.filter((n) => releases.get(n)).map((n) => [n, releases.get(n)]));
  if (id !== latestCheck) return {};
  return JSON.parse(driver.fixes(JSON.stringify(have), JSON.stringify(wanted)));
}

async function fetchGapWheels() {
  const response = await fetch(GAP_WHEELS_URL, { credentials: "omit" });
  if (!response.ok || !response.body || Number(response.headers.get("content-length")) > MAX_GAP_WHEELS_BYTES) return null;
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let text = "";
  let size = 0;
  try {
    while (true) {
      const { done, value } = await reader.read();
      if (done) return text + decoder.decode();
      size += value.byteLength;
      if (size > MAX_GAP_WHEELS_BYTES) {
        await reader.cancel();
        return null;
      }
      text += decoder.decode(value, { stream: true });
    }
  } finally {
    reader.releaseLock();
  }
}

// The gap wheel list, fetched once while the fixes are looked up: null when no row has a gap.
function gapWheelListFor(rows) {
  if (!rows.some((row) => row.gaps.length)) return Promise.resolve(null);
  gapWheelList ??= fetchGapWheels().catch(() => null);
  return gapWheelList;
}

// Asked only after fixes(), as wheelgap --fix does: a gap wheel for a gap an upgrade fixes too is only the fallback.
async function gapWheels(id, list) {
  const { driver } = await ready;
  return list && latestCheck === id ? JSON.parse(driver.gap_wheels(list)) : null;
}

self.onmessage = async ({ data }) => {
  const { id, type } = data;
  try {
    if (type === "load") {
      latestLoad = id;
      latestCheck = null;
      post({ id, type: "loaded", ...(await load(data.repo, id, data.file || null)) });
    } else if (type === "check") {
      latestCheck = id;
      const result = await check(id, data.root, data.lockfile, data.options);
      post({ id, type: "result", result });
      if (!result.error) {
        const pendingList = gapWheelListFor(result.rows);
        let found = {};
        try {
          if (result.rows.some((r) => r.lookup)) found = await fixes(id, result.rows);
        } catch {
          // a failed lookup leaves those rows "not looked up"; the result above stands
        }
        post({ id, type: "fixes", fixes: found });
        const gap = await gapWheels(id, await pendingList).catch(() => null);
        if (gap && latestCheck === id) post({ id, type: "gap-wheels", gapWheels: gap });
      }
    }
  } catch (e) {
    post({ id, type: "error", kind: e.kind || "internal", message: e.kind ? e.message : `Something went wrong: ${e.message || e}` });
  }
};

ready = start();
ready.catch((e) => post({ type: "error", kind: "startup", message: `Couldn't start Python in your browser: ${e.message || e}` }));
