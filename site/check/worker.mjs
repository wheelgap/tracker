// The repo checker's worker: loads Pyodide and wheelgap, fetches the repo's files from GitHub and the fixes' release
// lists from PyPI (all from the browser, nothing goes through a server of ours), and runs driver.py on them.
// Messages in: {id, type: "load", repo} | {id, type: "check", lockfile, options}. Out: {id, type, ...}.

import { loadPyodide } from "https://cdn.jsdelivr.net/pyodide/v314.0.8/full/pyodide.mjs";
import { rawUrl } from "./check-logic.mjs";

// The wheelgap release the page runs; tests/test_check_driver.py tests driver.py against the same one.
export const WHEELGAP = "wheelgap==0.13.0";
const MAX_FILE = 25 * 1024 * 1024;
const MAX_FIX_LOOKUPS = 80;

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

async function load(r, id) {
  const { py, driver } = await ready;
  const ref = r.ref || "HEAD";
  post({ id, type: "progress", stage: "tree" });
  const tree = await getJson(
    `https://api.github.com/repos/${r.owner}/${r.name}/git/trees/${encodeURIComponent(ref)}?recursive=1`,
  );
  const paths = (tree.tree || []).filter((e) => e.type === "blob").map((e) => e.path);
  const plan = JSON.parse(driver.plan(JSON.stringify(paths)));
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
  const detected = JSON.parse(driver.detect(root));
  detected.unreadable.push(...unread);
  if (id !== latestLoad) {
    forget(py, root);
    throw new Failure("stale", "superseded by a newer load"); // the page has moved on and ignores this
  }
  for (const old of [...loaded.keys()]) forget(py, old);
  loaded.set(root, { ...r, ref, root, fetched: new Set() });
  return { plan, detected, truncated: Boolean(tree.truncated), ref, root };
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
  post({ id, type: "progress", stage: "checking" });
  const result = JSON.parse(driver.check(repo.root, lockfile, JSON.stringify(options)));
  return result;
}

// Looks up on PyPI the gap rows the driver marked as PyPI packages (most gaps first; never a private index's names)
// and asks the driver for each one's nearest fix. A failed lookup isn't remembered, so the next check retries it.
async function fixes(rows) {
  const { driver } = await ready;
  const wanted = rows.filter((r) => r.lookup && r.gaps.length);
  const names = [...new Set([...wanted].sort((a, b) => b.gaps.length - a.gaps.length).map((r) => r.name))];
  const todo = names.filter((n) => !releases.has(n)).slice(0, MAX_FIX_LOOKUPS);
  await pool(todo, 6, async (n) => {
    try {
      const r = await fetch(`https://pypi.org/pypi/${encodeURIComponent(n)}/json`);
      if (r.ok) releases.set(n, (await r.json()).releases ?? null);
      else if (r.status === 404) releases.set(n, null);
    } catch {
      // network trouble: not cached
    }
  });
  const have = Object.fromEntries(names.filter((n) => releases.get(n)).map((n) => [n, releases.get(n)]));
  return JSON.parse(driver.fixes(JSON.stringify(have), JSON.stringify(wanted)));
}

self.onmessage = async ({ data }) => {
  const { id, type } = data;
  try {
    if (type === "load") {
      latestLoad = id;
      post({ id, type: "loaded", ...(await load(data.repo, id)) });
    } else if (type === "check") {
      const result = await check(id, data.root, data.lockfile, data.options);
      post({ id, type: "result", result });
      if (!result.error) {
        let found = {};
        try {
          if (result.rows.some((r) => r.lookup)) found = await fixes(result.rows);
        } catch {
          // a failed lookup leaves those rows "not looked up"; the result above stands
        }
        post({ id, type: "fixes", fixes: found });
      }
    }
  } catch (e) {
    post({ id, type: "error", kind: e.kind || "internal", message: e.kind ? e.message : `Something went wrong: ${e.message || e}` });
  }
};

ready = start();
ready.catch((e) => post({ type: "error", kind: "startup", message: `Couldn't start Python in your browser: ${e.message || e}` }));
