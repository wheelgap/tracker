// node --test tests/site/check-page.test.mjs: the repo checker's page (site/check/index.html) and the part of
// check.mjs that shows and hides its sections. check.mjs needs a browser, so its function is run on stand-in elements.
import { test } from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import * as checkLogic from "../../site/check/check-logic.mjs";

const read = (name) => fs.readFileSync(new URL(`../../site/check/${name}`, import.meta.url), "utf8");
const TOOL = "https://github.com/wheelgap/wheelgap";

test("the line under a result links the tool's repo and its GitHub Action, as static text", () => {
  const html = read("index.html");
  const line = html.match(/<p class="note star-line" id="star-line" hidden>(.*?)<\/p>/s);
  assert.ok(line, "the line is in the page, hidden until a result is shown");
  const out = html.slice(html.indexOf('<main class="wrap" id="out" hidden>'), html.indexOf("</main>"));
  assert.ok(out.lastIndexOf("</section>") < out.indexOf('id="star-line"'), "it comes after the result's sections");
  const links = [...line[1].matchAll(/<a href="([^"]+)">(.*?)<\/a>/g)].map((m) => [m[1], m[2]]);
  assert.deepEqual(links, [
    [`${TOOL}/blob/main/docs/ci.md#github-action`, "GitHub Action"],
    [TOOL, '<span aria-hidden="true">★</span> star the project on GitHub'],
  ]);
  assert.match(line[1], /^wheelgap is open source\. /);
  // Plain links: the page's Content-Security-Policy allows no remote images or frames, so no star-count widget.
  assert.doesNotMatch(line[1], /<(img|iframe|script)\b/);
  assert.doesNotMatch(read("check.mjs"), /star-line[^\n]*innerHTML/);
});

test("the line shows with a result (gaps or none) and hides when there is nothing to show", () => {
  const source = read("check.mjs").match(/^function hideResultSections\(.*?^}$/ms);
  assert.ok(source, "hideResultSections is in check.mjs");
  const made = new Map();
  const element = (key) => {
    if (!made.has(key)) made.set(key, { hidden: null, classList: { remove() {} }, closest: () => element(`${key} section`) });
    return made.get(key);
  };
  const hideResultSections = new Function("$", `${source[0]}; return hideResultSections;`)(element);
  hideResultSections(false);        // render(): a result, whether it found gaps or every wheel
  assert.equal(element("star-line").hidden, false);
  assert.equal(element("gaps-title section").hidden, false);
  hideResultSections(true);         // no lockfile found
  assert.equal(element("star-line").hidden, true);
  hideResultSections(true, true);   // a lockfile the browser can't check
  assert.equal(element("star-line").hidden, true);
  assert.equal(element("setup-title section").hidden, false);
});

test("a result with no package checked shows the verdict and the setup, but no bars or table to read as a pass", () => {
  const source = read("check.mjs").match(/^function render\(\) \{.*?^}$/ms);
  assert.ok(source, "render is in check.mjs");
  const parts = ["hideResultSections", "renderVerdict", "renderSetup", "renderTargets", "renderTable", "renderNext"];
  const calls = [];
  const render = (packages) => new Function("state", ...parts, `${source[0]}; return render;`)(
    { result: { packages } },
    ...parts.map((name) => (...args) => { calls.push([name, ...args]); }),
  );
  render(0)(); // every package skipped as a private index's, or a file that lists none
  assert.deepEqual(calls, [["hideResultSections", false], ["renderVerdict"], ["renderSetup"], ["hideResultSections", true, true]]);
  calls.length = 0;
  render(3)();
  assert.deepEqual(calls.map(([name]) => name), parts);
});

// worker.mjs needs a browser and Pyodide, so its check() is run on stand-ins for both: `needs` is what the driver asks
// to be looked up, `fetch` answers for pypi.org, and `checked` collects what the driver's check() is handed.
// `configs` is the repo's list of installer settings files (null for a truncated tree), `near` what the driver's
// configs_for() names for the file, and `files` what GitHub answers for them (a missing one fails to fetch).
function workerCheck({
  needs, fetch, releases = new Map(), lockfile = "requirements.txt", fetched = [lockfile], gone = [],
  configs = [], near = [], files = {},
}) {
  const source = read("worker.mjs");
  const fn = (name) => {
    const found = source.match(new RegExp(`^(?:async )?function ${name}\\(.*?^}$`, "ms"));
    assert.ok(found, `${name} is in worker.mjs`);
    return found[0];
  };
  const checked = [];
  const fixed = [];
  const removed = [];
  const asked = []; // the driver's needs() calls
  const nearAsked = []; // the driver's configs_for() calls
  const requested = []; // files asked of GitHub
  const written = [];
  // `gone`: paths the worker believes it wrote that aren't in Pyodide's files any more, so removing them throws.
  const unlink = (path) => { removed.push(path); if (gone.includes(path)) throw new Error(`ENOENT: ${path}`); };
  const driver = {
    needs: (...args) => { asked.push(args); return JSON.stringify(needs); },
    configs_for: (...args) => { nearAsked.push(args); return JSON.stringify(near); },
    check: (...args) => { checked.push(args); return JSON.stringify({ rows: [] }); },
    fixes: (...args) => { fixed.push(args); return "{}"; },
  };
  const repo = { root: "/repos/1", ref: "HEAD", fetched: new Set(fetched), configs };
  const env = {
    fetch, releases, MAX_LOOKUPS: 300, MAX_FIX_LOOKUPS: 80, CONFIG_NAMES: ["uv.toml", "pyproject.toml", "pdm.toml"],
    rawUrl: (r, ref, path) => `raw:${ref}:${path}`,
    getText: async (url) => {
      const path = url.split(":").pop();
      requested.push(path);
      if (!(path in files)) throw new Error(`Couldn't fetch ${path} (404).`);
      return files[path];
    },
    write: (py, root, path, bytes) => written.push([root, path, bytes]),
    trimProject: checkLogic.trimProject, trimReleases: checkLogic.trimReleases,
    ready: Promise.resolve({ py: { FS: { unlink } }, driver }),
    loaded: new Map([[repo.root, repo]]),
    post: () => {},
  };
  const made = new Function("env", `
    const { fetch, releases, MAX_LOOKUPS, MAX_FIX_LOOKUPS, CONFIG_NAMES, ready, loaded, post, trimProject, trimReleases, rawUrl, getText, write } = env;
    let latestCheck = 7;
    class Failure extends Error { constructor(kind, message) { super(message); this.kind = kind; } }
    ${fn("pool")}
    ${fn("configsJson")}
    ${fn("fetchConfigs")}
    ${fn("lookups")}
    ${fn("dropOtherProjects")}
    ${fn("check")}
    ${fn("fixes")}
    return {
      check: (lockfile) => check(7, "/repos/1", lockfile, { pythons: ["3.14"] }),
      fixes: (rows) => fixes(7, rows), supersede: () => { latestCheck = 8; },
    };
  `)(env);
  return {
    check: () => made.check(lockfile), fixes: made.fixes, supersede: made.supersede,
    checked, fixed, releases, removed, repo, asked, nearAsked, requested, written,
  };
}

test("check trims pinned and project JSON before the driver and keeps only trimmed releases for fixes", async () => {
  // digests.sha256 (for --hash options) and upload_time_iso_8601 (for exclude-newer) are read since wheelgap 0.14.0.
  const file = {
    filename: "fastlib-1.0.tar.gz", requires_python: ">=3.13", yanked: false, digests: { md5: "unused", sha256: "00ab" },
    upload_time: "2025-10-15T16:16:21", upload_time_iso_8601: "2025-10-15T16:16:21.072038Z", size: 1,
  };
  const doc = { info: { version: "1.0", description: "unused".repeat(1000) }, urls: [file], releases: { "1.0": [file] }, last_serial: 123 };
  const needs = { lookups: [{ name: "fastlib", version: null }, { name: "pinned", version: "1.0" }], total: 2 };
  const worker = workerCheck({ needs, fetch: async () => ({ ok: true, json: async () => structuredClone(doc) }) });
  await worker.check();
  const keptFile = {
    filename: file.filename, requires_python: ">=3.13", yanked: false, upload_time_iso_8601: file.upload_time_iso_8601,
    digests: { sha256: "00ab" },
  };
  const kept = { info: { version: "1.0" }, urls: [keptFile], releases: { "1.0": [keptFile] } };
  assert.deepEqual(JSON.parse(worker.checked[0][3]), { fastlib: kept, "pinned==1.0": kept });
  assert.deepEqual([...worker.releases], [["fastlib", kept.releases]]); // never cache a pinned response
  assert.ok(worker.checked[0][3].length < JSON.stringify({ fastlib: doc, "pinned==1.0": doc }).length / 2);
});

test("fixes trims newly fetched releases and reuses the trimmed releases from check", async () => {
  const fetched = [];
  const file = { filename: "fastlib-2.0-py3-none-any.whl", yanked: false, url: "https://files.pythonhosted.org/unused" };
  const doc = { info: { description: "unused" }, urls: [], releases: { "2.0": [file] } };
  const worker = workerCheck({
    needs: { lookups: [{ name: "cached", version: null }], total: 1 },
    fetch: async (url) => { fetched.push(url); return { ok: true, json: async () => structuredClone(doc) }; },
  });
  await worker.check();
  const rows = ["cached", "fresh"].map((name) => ({ name, version: "1.0", lookup: true, gaps: ["3.13 linux-x86_64"], keep: [] }));
  await worker.fixes(rows);
  const kept = { "2.0": [{ filename: file.filename, yanked: false }] };
  assert.deepEqual(fetched, ["https://pypi.org/pypi/cached/json", "https://pypi.org/pypi/fresh/json"]);
  assert.deepEqual([...worker.releases], [["cached", kept], ["fresh", kept]]);
  assert.deepEqual(JSON.parse(worker.fixed[0][0]), { cached: kept, fresh: kept });
  assert.deepEqual(JSON.parse(worker.fixed[0][1]), rows);
  await worker.fixes(rows);
  assert.equal(fetched.length, 2); // both compact lists survive for the next check's fixes
});

test("the worker looks up what the driver names on pypi.org and hands the answers to the driver's check", async () => {
  const urls = [];
  const fetch = async (url) => {
    urls.push(url);
    if (url.includes("/gone/")) return { ok: false, status: 404 };
    // As PyPI answers: a release's JSON has no `releases`, a project's JSON does.
    const doc = url.endsWith("/numpy/2.3.4/json") ? { info: {}, urls: [] } : { info: {}, urls: [], releases: { "2.0.0": [] } };
    return { ok: true, status: 200, json: async () => doc };
  };
  const needs = { lookups: [{ name: "numpy", version: "2.3.4" }, { name: "fastlib", version: null }, { name: "gone", version: "1.0" }], total: 3 };
  const { check, checked, releases } = workerCheck({ needs, fetch });
  await check();
  assert.deepEqual(urls.sort(), [
    "https://pypi.org/pypi/fastlib/json",
    "https://pypi.org/pypi/gone/1.0/json",
    "https://pypi.org/pypi/numpy/2.3.4/json",
  ]);
  assert.equal(checked.length, 1);
  const [root, file, , lookups] = checked[0];
  assert.deepEqual([root, file], ["/repos/1", "requirements.txt"]);
  assert.deepEqual(JSON.parse(lookups), {
    "numpy==2.3.4": { info: {}, urls: [] },
    fastlib: { info: {}, urls: [], releases: { "2.0.0": [] } },
    "gone==1.0": null, // a 404: the driver reports it as unknown
  });
  // fixes() asks PyPI for a project's release list unless this cache has its name. A pinned release's JSON has no
  // release list, so caching it under the name would leave every gap of a pinned file "not looked up".
  assert.deepEqual([...releases], [["fastlib", { "2.0.0": [] }]]);
});

test("a lockfile that lists its files makes no request to PyPI, and the driver's check is still handed JSON text", async () => {
  const fetch = async () => assert.fail("no lookup was asked for");
  const { check, checked } = workerCheck({ needs: { lookups: [], total: 0 }, fetch });
  await check();
  assert.equal(checked[0][3], "{}"); // never a JS null, which Pyodide may not turn into None
});

test("the worker counts the lookups that got no answer, for the page to say why packages are unknown", async () => {
  const signals = [];
  const fetch = async (url, init) => {
    signals.push(init?.signal);
    if (url.includes("/down/")) throw new TypeError("Failed to fetch");
    if (url.includes("/slow/")) throw new DOMException("The operation timed out.", "TimeoutError");
    if (url.includes("/busy/")) return { ok: false, status: 503 };
    if (url.includes("/gone/")) return { ok: false, status: 404 };
    if (url.includes("/html/")) return { ok: true, status: 200, json: async () => { throw new SyntaxError("Unexpected token <"); } };
    return { ok: true, status: 200, json: async () => ({ info: {}, urls: [] }) };
  };
  const names = ["fine", "down", "slow", "busy", "gone", "html"];
  const needs = { lookups: names.map((name) => ({ name, version: "1.0" })), total: names.length };
  const { check, checked } = workerCheck({ needs, fetch });
  const result = await check();
  assert.deepEqual(result.missed, { cap: 300, over_cap: 0, not_found: 1, failed: 4 });
  // Only an answer, or PyPI's "no such release", reaches the driver: the rest have no key, and stay "not looked up".
  assert.deepEqual(JSON.parse(checked[0][3]), { "fine==1.0": { info: {}, urls: [] }, "gone==1.0": null });
  // Every lookup can be given up on after a while: a stalled one would otherwise hold the whole check.
  assert.equal(signals.length, names.length);
  assert.ok(signals.every((signal) => signal instanceof AbortSignal));
});

test("lookups over the cap aren't made, and the result says how many there were", async () => {
  const urls = [];
  const fetch = async (url) => {
    urls.push(url);
    return { ok: true, status: 200, json: async () => ({ info: {}, urls: [] }) };
  };
  const needs = { lookups: Array.from({ length: 301 }, (_, i) => ({ name: `pkg${i}`, version: "1.0" })), total: 301 };
  const { check, checked } = workerCheck({ needs, fetch });
  const result = await check();
  assert.equal(urls.length, 300);
  assert.deepEqual(result.missed, { cap: 300, over_cap: 1, not_found: 0, failed: 0 });
  const lookups = JSON.parse(checked[0][3]);
  assert.equal(Object.keys(lookups).length, 300);
  assert.equal("pkg300==1.0" in lookups, false); // no key: the driver reports it as unknown, never as a gap
});

test("a check superseded while it looks packages up stops asking PyPI and never reaches the driver's check", async () => {
  let supersede = () => {};
  const urls = [];
  const fetch = async (url) => {
    urls.push(url);
    supersede(); // a newer check arrives while this one is in flight
    return { ok: true, status: 200, json: async () => ({ info: {}, urls: [] }) };
  };
  const needs = { lookups: Array.from({ length: 20 }, (_, i) => ({ name: `pkg${i}`, version: "1.0" })), total: 20 };
  const worker = workerCheck({ needs, fetch });
  supersede = worker.supersede;
  await assert.rejects(worker.check(), (e) => e.kind === "stale");
  assert.equal(worker.checked.length, 0); // the driver keeps the last check's targets for fixes(): not a stale one's
  assert.ok(urls.length <= 6, `the six in flight at most, not all twenty: ${urls.length}`);
});

test("a pyproject.toml that was fetched to be checked is gone again before another file is read", async () => {
  // wheelgap reads the pyproject.toml beside a uv.lock for [tool.uv] settings when it's there: left behind, a uv.lock
  // checked after the pyproject.toml could answer differently from the same uv.lock checked first.
  const fetch = async () => assert.fail("no lookup was asked for");
  const fetched = ["uv.lock", "pyproject.toml", "backend/pyproject.toml", "requirements.txt"];
  const lock = workerCheck({ needs: { lookups: [], total: 0 }, fetch, lockfile: "uv.lock", fetched });
  await lock.check();
  assert.deepEqual(lock.removed, ["/repos/1/pyproject.toml", "/repos/1/backend/pyproject.toml"]);
  assert.deepEqual([...lock.repo.fetched], ["uv.lock", "requirements.txt"]); // fetched again if it's picked again
  // The one being checked stays, of course.
  const project = workerCheck({ needs: { lookups: [], total: 0 }, fetch, lockfile: "pyproject.toml", fetched });
  await project.check();
  assert.deepEqual(project.removed, ["/repos/1/backend/pyproject.toml"]);
  assert.equal(project.checked[0][1], "pyproject.toml");
  // A check that's already superseded removes nothing: the newer check may be about to read that file.
  const stale = workerCheck({ needs: { lookups: [], total: 0 }, fetch, lockfile: "uv.lock", fetched });
  stale.supersede();
  await assert.rejects(stale.check(), (e) => e.kind === "stale");
  assert.deepEqual([stale.removed, stale.checked], [[], []]);
  // One that's already gone from the worker's files isn't an error, and isn't tried again on the next check.
  const missing = workerCheck({ needs: { lookups: [], total: 0 }, fetch, lockfile: "uv.lock", fetched, gone: ["/repos/1/pyproject.toml"] });
  await missing.check();
  assert.deepEqual(missing.removed, ["/repos/1/pyproject.toml", "/repos/1/backend/pyproject.toml"]);
  assert.deepEqual([...missing.repo.fetched], ["uv.lock", "requirements.txt"]);
  assert.equal(missing.checked.length, 1);
});

test("the settings files beside the file or above it are fetched first, kept for its check, and the list handed over", async () => {
  // wheelgap reads a pyproject.toml's [tool.uv] index (or a uv.toml's, a pdm.toml's) beside a requirements file or
  // above it: the driver names the ones the repo has, the worker fetches them, and they stay while the file is read.
  const fetch = async () => assert.fail("no lookup was asked for");
  const configs = ["other/pyproject.toml", "other/uv.toml", "pyproject.toml", "services/uv.toml"];
  const bytes = new Uint8Array([1]);
  const worker = workerCheck({
    needs: { lookups: [], total: 0 }, fetch, lockfile: "services/api/requirements.txt", configs,
    // Fetched for an earlier check of another file: none of them is this one's.
    fetched: ["services/api/requirements.txt", "other/pyproject.toml", "other/uv.toml", "uv.lock"],
    near: ["pyproject.toml", "services/uv.toml"], files: { "pyproject.toml": bytes, "services/uv.toml": bytes },
  });
  await worker.check();
  assert.deepEqual(worker.nearAsked, [["services/api/requirements.txt", JSON.stringify(configs)]]);
  assert.deepEqual(worker.requested.sort(), ["pyproject.toml", "services/uv.toml"]);
  assert.deepEqual(worker.written.map(([root, path]) => [root, path]).sort(), [["/repos/1", "pyproject.toml"], ["/repos/1", "services/uv.toml"]]);
  // The other file's settings are gone before the driver reads this one; this one's stay, and so does the lockfile.
  assert.deepEqual(worker.removed, ["/repos/1/other/pyproject.toml", "/repos/1/other/uv.toml"]);
  assert.deepEqual([...worker.repo.fetched].sort(), ["pyproject.toml", "services/api/requirements.txt", "services/uv.toml", "uv.lock"]);
  // The driver gets the repo's whole list, to tell a settings file that wasn't fetched from one that isn't there.
  assert.deepEqual(worker.asked, [["/repos/1", "services/api/requirements.txt", JSON.stringify(configs)]]);
  assert.equal(worker.checked.length, 1);
  assert.deepEqual([worker.checked[0][1], worker.checked[0][4]], ["services/api/requirements.txt", JSON.stringify(configs)]);
  // Checked again, nothing is fetched twice.
  const again = worker.requested.length;
  await worker.check();
  assert.equal(worker.requested.length, again);
});

test("a settings file that fails to fetch isn't written, and the driver is still handed the list that names it", async () => {
  // The driver finds it in the list but not on disk, and keeps the file private: the check goes on and says why.
  const fetch = async () => assert.fail("no lookup was asked for");
  const worker = workerCheck({ needs: { lookups: [], total: 0 }, fetch, configs: ["pyproject.toml"], near: ["pyproject.toml"] });
  await worker.check();
  assert.deepEqual([worker.requested, worker.written], [["pyproject.toml"], []]);
  assert.deepEqual([...worker.repo.fetched], ["requirements.txt"]); // tried again on the next check
  assert.equal(worker.asked[0][2], '["pyproject.toml"]');
  assert.equal(worker.checked[0][4], '["pyproject.toml"]');
  await worker.check();
  assert.deepEqual(worker.requested, ["pyproject.toml", "pyproject.toml"]);
});

test("with a truncated file list the driver is handed null for the settings files, always as text", async () => {
  const fetch = async () => assert.fail("no lookup was asked for");
  const worker = workerCheck({ needs: { lookups: [], total: 0 }, fetch, configs: null });
  await worker.check();
  assert.deepEqual([worker.nearAsked[0][1], worker.asked[0][2], worker.checked[0][4]], ["null", "null", "null"]);
  assert.deepEqual(worker.requested, []);
});

test("the worker keeps a truncated tree's settings files unknown, and stops wheelgap's walk at the repo's root", () => {
  const load = read("worker.mjs").match(/^async function load\(.*?^}$/ms)[0];
  assert.match(load, /configs: tree\.truncated \? null : plan\.configs/);
  assert.match(load, /py\.FS\.mkdirTree\(`\$\{root\}\/\.git`\)/);
});

test("build lab results are fetched only for gap rows the driver marked as PyPI packages", async () => {
  // A uv.lock, poetry.lock or pylock.toml can pin a package to a private index: check() marks its row `lookup: false`,
  // and its name must not be requested from the tracker's pages either.
  const source = read("check.mjs").match(/^async function loadLab\(.*?^}$/ms);
  assert.ok(source, "loadLab is in check.mjs");
  const urls = [];
  const fetch = async (url) => { urls.push(String(url)); return { ok: true, json: async () => ({ builds: [] }) }; };
  const state = { lab: {}, result: null };
  const loadLab = new Function("state", "fetch", "canonicalName", "LAB_LOOKUPS", "location", "renderTable", `${source[0]}; return loadLab;`)(
    state, fetch, checkLogic.canonicalName, 120, { href: "https://wheelgap.github.io/tracker/check/" }, () => {},
  );
  await loadLab({ rows: [
    { name: "corp-secret-lib", version: "1.0", gaps: ["3.14 linux-x86_64"], lookup: false },
    { name: "Fast_Lib", version: "2.0", gaps: ["3.14 linux-x86_64"], lookup: true },
    { name: "unknown-only", version: "1.0", gaps: [], lookup: false },
  ] });
  assert.deepEqual(urls, ["https://wheelgap.github.io/tracker/data/packages/fast-lib.json"]);
  assert.deepEqual(Object.keys(state.lab), ["fast-lib"]);
});

test("the badge is a small static image on the page's own origin, offered as Markdown under the copy buttons", () => {
  const svg = read("badge.svg");
  assert.ok(Buffer.byteLength(svg) < 1500, "around 1 KB");
  assert.match(svg, /^<svg xmlns="http:\/\/www\.w3\.org\/2000\/svg" width="146" height="20" role="img" aria-label="wheelgap: check wheels"><title>wheelgap: check wheels<\/title>/);
  // Nothing that runs or loads: GitHub serves README images through its proxy, which drops such an SVG.
  assert.doesNotMatch(svg, /<script|<style|<image|<use|<foreignObject|href=|\bon[a-z]+=|@import|url\((?!#)/i);
  assert.deepEqual([...svg.matchAll(/>([^<>]+)<\/text>/g)].map((m) => m[1]), ["wheelgap", "wheelgap", "check wheels", "check wheels"]);

  const html = read("index.html");
  const fix = html.slice(html.indexOf('id="next-title"'), html.indexOf('id="caveats"'));
  const section = fix.match(/<section id="badge" aria-labelledby="badge-title">(.*?)<\/section>/s);
  assert.ok(section, "the badge is in the Fix it section");
  assert.ok(fix.indexOf('id="copy-link"') < fix.indexOf('id="badge"'), "under the two Copy buttons");
  assert.match(section[1], /<h3 id="badge-title">Add a badge to your README<\/h3>/);
  // Same origin, so the page's `img-src 'self'` allows it; and it says it isn't a result.
  assert.match(section[1], /<img class="badge-img" src="badge\.svg" alt="wheelgap: check wheels" width="146" height="20">/);
  assert.match(html, /img-src 'self' data:;/);
  assert.match(section[1], /shows no result/);
  assert.match(section[1], /<pre><code id="badge-md"><\/code><\/pre>\s*<button type="button" class="action" id="copy-badge">Copy badge Markdown<\/button>/);

  const page = read("check.mjs");
  // Filled in as text, from the pure helper, and copied the way the other buttons copy.
  // The default is what a visit with the repo alone checks by itself: autoPick() over the repo's own files, never
  // one a blob link or ?lock= added (`added`), so the link keeps lock= for that file; none when it would ask which.
  // With what the page knows of the root (autoPick()'s ctx, #238), so a repo it would ask about gets no default.
  assert.match(page, /\$\("badge-md"\)\.textContent = badgeMarkdown\(state\.repo, r\.lockfile, autoPick\(state\.plan\.lockfiles\.filter\(\(l\) => !l\.added\), null, state\.pickContext \?\? \{\}\)\.lock\?\.path \?\? null\);/);
  const repoFiles = [{ path: "pyproject.toml", offline: false }];
  const added = { path: "requirements/default.txt", offline: false, added: true };
  const defaultOf = (lockfiles) => checkLogic.autoPick(lockfiles.filter((l) => !l.added), null).lock?.path ?? null;
  const repo = { owner: "celery", name: "celery" };
  assert.match(checkLogic.badgeMarkdown(repo, added.path, defaultOf([...repoFiles, added])), /\?repo=celery\/celery&lock=requirements\/default\.txt\)$/);
  assert.match(checkLogic.badgeMarkdown(repo, added.path, defaultOf([added])), /&lock=requirements\/default\.txt\)$/); // its only file
  assert.match(checkLogic.badgeMarkdown(repo, "pyproject.toml", defaultOf([...repoFiles, added])), /\?repo=celery\/celery\)$/);
  // Several requirements files under other names: a visit asks, so every badge names its file.
  const others = [{ path: "requirements/base.txt", offline: false, other: true }, { path: "requirements/docs.txt", offline: false, other: true }];
  assert.equal(defaultOf(others), null);
  assert.match(page, /\$\("copy-badge"\)\.onclick = \(e\) => copy\(e\.currentTarget, \$\("badge-md"\)\.textContent, "Badge Markdown copied"\);/);
  assert.doesNotMatch(page, /innerHTML/);
});

test("a clean verdict offers the README badge in the verdict card and hides it for gaps or unknowns", () => {
  const html = read("index.html");
  const card = html.match(/<section aria-labelledby="verdict" class="verdict-card">(.*?)<\/section>/s);
  assert.ok(card, "verdict-card is in index.html");
  assert.match(card[1], /<div id="verdict-badge" class="verdict-badge" hidden>/);
  assert.match(card[1], /Keep it that way: add the check to your README/);
  assert.match(card[1], /<img class="badge-img" src="badge\.svg" alt="wheelgap: check wheels" width="146" height="20">/);
  assert.match(card[1], /<button type="button" class="action" id="copy-verdict-badge">Copy badge Markdown<\/button>/);

  const actions = html.match(/<div class="actions">(.*?)<\/div>/s);
  assert.ok(actions, "actions block is in index.html");
  assert.match(actions[1], /id="copy-link"[^<]*<\/button>\s*<button type="button" class="action" id="copy-share">Copy result as text<\/button>/);

  const source = read("check.mjs");
  const rv = source.match(/^function renderVerdict\(\) \{.*?^}$/ms);
  const rn = source.match(/^function renderNext\(\) \{.*?^}$/ms);
  const nc = source.match(/^function notCheckedCount\(.*?^}$/ms);
  const countedSrc = source.match(/^const counted = (.*);$/m);
  const shareSrc = source.match(/^\$\("copy-share"\)\.onclick = .*;$/m);
  assert.ok(rv && rn && nc && countedSrc && shareSrc, "the verdict, next steps and share handler are in check.mjs");

  const run = (rows, packages = 5, targets = [{ label: "3.14 linux-x86_64", python: "3.14", platform: "linux-x86_64" }], extra = {}) => {
    const made = new Map();
    const element = (key) => {
      if (!made.has(key)) {
        const classes = new Set();
        made.set(key, {
          hidden: null,
          textContent: "",
          classList: { toggle: (c, on) => (on ? classes.add(c) : classes.delete(c)), has: (c) => classes.has(c) },
          closest: () => element(`${key} section`),
          replaceChildren() {},
        });
      }
      return made.get(key);
    };
    const state = {
      repo: { owner: "o", name: "r" },
      ref: "main",
      plan: { lockfiles: [{ path: "uv.lock" }] },
      pythons: ["3.14"],
      platforms: ["linux-x86_64"],
      result: { lockfile: "uv.lock", packages, targets, outside_lock: [], rows, skipped: [], private: [], looked_up: false, ...extra },
    };
    const fns = new Function(
      "$", "state", "el", "headline", "verdictText", "nextSteps", "sortPythons", "missedText", "cliCommand", "badgeMarkdown",
      "pickLockfile", "autoPick", "privatePyprojectReason", "privateText", "shareText", "copy", "location",
      `${nc[0]}\n${rv[0]}\n${rn[0]}\n${countedSrc[0]}\n${shareSrc[0]}\nreturn { renderVerdict, renderNext };`,
    )(
      element, state, () => ({}), checkLogic.headline, checkLogic.verdictText, checkLogic.nextSteps, checkLogic.sortPythons,
      checkLogic.missedText, checkLogic.cliCommand, checkLogic.badgeMarkdown, checkLogic.pickLockfile, checkLogic.autoPick,
      checkLogic.privatePyprojectReason, checkLogic.privateText, checkLogic.shareText,
      (button, text) => { button.copiedText = text; }, { href: "https://wheelgap.github.io/tracker/check/?repo=o/r" },
    );
    fns.renderVerdict();
    fns.renderNext();
    return element;
  };

  // Clean result: verdict badge shown, bottom section renamed and fix-prompt hidden.
  const clean = run([]);
  assert.equal(clean("verdict-badge").hidden, false);
  assert.equal(clean("verdict section").classList.has("clean"), true);
  assert.equal(clean("next-title").textContent, "Keep it green");
  assert.equal(clean("copy-prompt").hidden, true);
  assert.equal(clean("prompt-note").hidden, true);
  // Nothing left unchecked: the command is the plain one, with no index placeholder.
  assert.equal(clean("cli").textContent, "uvx wheelgap uv.lock -p 3.14 -t linux-x86_64");

  // Result with gaps: verdict badge hidden, Fix it heading and fix-prompt shown.
  const withGaps = run([{ name: "a", gaps: ["3.14 linux-x86_64"], unknown: [] }]);
  assert.equal(withGaps("verdict-badge").hidden, true);
  assert.equal(withGaps("verdict section").classList.has("clean"), false);
  assert.equal(withGaps("next-title").textContent, "Fix it");
  assert.equal(withGaps("copy-prompt").hidden, false);
  assert.equal(withGaps("prompt-note").hidden, false);
  assert.equal(withGaps("cli").textContent, "uvx wheelgap uv.lock -p 3.14 -t linux-x86_64");

  // Result with unknowns: verdict badge hidden, not clean.
  const withUnknowns = run([{ name: "u", gaps: [], unknown: ["3.14 linux-x86_64"] }]);
  assert.equal(withUnknowns("verdict-badge").hidden, true);
  assert.equal(withUnknowns("verdict section").classList.has("clean"), false);
  assert.equal(withUnknowns("next-title").textContent, "Fix it");

  // Nothing checked: verdict badge hidden.
  const empty = run([], 0);
  assert.equal(empty("verdict-badge").hidden, true);

  // Clean, but a private index's packages weren't checked: no badge offer and no "Keep it green".
  const withPrivate = run([], 5, undefined, { private: ["corp"], skipped: ["corp"] });
  assert.equal(withPrivate("verdict-badge").hidden, true);
  assert.equal(withPrivate("next-title").textContent, "Fix it");
  assert.equal(withPrivate("verdict section").classList.has("clean"), false);
  assert.doesNotMatch(withPrivate("verdict").textContent, /✓/);
  assert.match(withPrivate("verdict").textContent, /1 more wasn't checked/);
  // The command offered below is for the visitor's own index, so it must carry the placeholder the alert asks for.
  // A placeholder, never a real URL, and the flag appears once.
  assert.equal(withPrivate("cli").textContent, "uvx wheelgap uv.lock -p 3.14 -t linux-x86_64 --index-url YOUR_INDEX_URL");

  // Repeated requirements for the same private package count once in the displayed package total.
  const repeatedPrivate = run([], 1, undefined, { private: ["corp"], private_count: 2, skipped: ["corp"] });
  assert.equal(repeatedPrivate("verdict section").classList.has("clean"), false);
  assert.equal(
    repeatedPrivate("verdict").textContent,
    "1 checked package has a wheel for this target; 1 more wasn't checked (its index isn't known to be PyPI)",
  );
  assert.equal(repeatedPrivate("next-title").textContent, "Fix it");
  const shareButton = repeatedPrivate("copy-share");
  shareButton.onclick({ currentTarget: shareButton });
  assert.match(shareButton.copiedText, /; 1 more wasn't checked/);
  assert.doesNotMatch(shareButton.copiedText, /✓|2 more/);

  // Both new copy buttons use copy() like the existing ones.
  assert.match(source, /\$\("copy-verdict-badge"\)\.onclick = \(e\) => copy\(e\.currentTarget, \$\("badge-md"\)\.textContent, "Badge Markdown copied"\);/);
  assert.match(source, /\$\("copy-share"\)\.onclick = \(e\) => copy\(e\.currentTarget, shareText\(/);
  // The share line names only the shown result's counted targets: never a Python the lockfile leaves out, and never
  // a selection whose check hasn't come back yet.
  const counted = new Function("state", `return ${countedSrc[1]};`)({
    result: {
      outside_lock: ["3.15"],
      targets: [
        { python: "3.14", platform: "linux-x86_64" }, { python: "3.14", platform: "win-amd64" },
        { python: "3.15", platform: "linux-x86_64" }, { python: "3.15", platform: "win-amd64" },
      ],
    },
  });
  assert.deepEqual(counted("python"), ["3.14"]);
  assert.deepEqual(counted("platform"), ["linux-x86_64", "win-amd64"]);
  assert.match(source, /shareText\([^\n]*counted\("python"\), counted\("platform"\)/);
  assert.match(source, /shareText\([^\n]*location\.href, notCheckedCount\(state\.result\)\)/);
  assert.doesNotMatch(source, /shareText\([^\n]*state\.pythons/);
});

test("renderTable shows the links in the Fix cell and the package-name link for a package the tracker has, and neither for one it doesn't", () => {
  const source = read("check.mjs");
  const fn = (name) => {
    const found = source.match(new RegExp(`^(?:async )?function ${name}\\(.*?^}$`, "ms"));
    assert.ok(found, `${name} is in check.mjs`);
    return found[0];
  };
  const elements = new Map();
  const $ = (id) => {
    if (!elements.has(id)) {
      elements.set(id, {
        textContent: "",
        hidden: false,
        children: [],
        classList: { toggle() {}, remove() {}, add() {} },
        closest: () => ({ classList: { toggle() {}, remove() {} } }),
        replaceChildren: (...kids) => { elements.get(id).children = kids; },
      });
    }
    return elements.get(id);
  };
  const el = (tag, attrs = {}, ...kids) => {
    const text = attrs.text || "";
    const flatKids = kids.flat().filter(Boolean);
    const kidTexts = flatKids.map((k) => (typeof k === "string" ? k : k?.text || k?.textContent || "")).join("");
    return {
      tag,
      attrs,
      text: text + kidTexts,
      textContent: text + kidTexts,
      children: flatKids,
    };
  };

  const state = {
    python: "3.14",
    platform: "linux-x86_64",
    fixesDone: true,
    showAll: false,
    fixes: {
      "has-tracker==1.0": { found: {}, loses: [] },
      "no-tracker==2.0": { found: {}, loses: [] },
    },
    gapWheels: null,
    lab: {
      "has-tracker": { ok: true, builds: [], issues_url: "https://github.com/org/has-tracker/issues" },
      "no-tracker": { ok: false, builds: [], issues_url: null },
    },
    result: {
      lockfile: "uv.lock",
      outside_lock: [],
      targets: [{ python: "3.14", platform: "linux-x86_64", label: "3.14 linux-x86_64" }],
      rows: [
        {
          name: "has-tracker",
          version: "1.0",
          gaps: ["3.14 linux-x86_64"],
          statuses: { "3.14 linux-x86_64": "source" },
          lookup: true,
          here: ["3.14 linux-x86_64"],
        },
        {
          name: "no-tracker",
          version: "2.0",
          gaps: ["3.14 linux-x86_64"],
          statuses: { "3.14 linux-x86_64": "source" },
          lookup: true,
          here: ["3.14 linux-x86_64"],
        },
      ],
    },
  };

  const NAMES = { "linux-x86_64": "Linux x86_64" };
  const STATUS_TEXT = { source: "source" };
  const LAB_SHORT = {};
  const FIX_TEXT = { none: () => "no newer release has a wheel" };
  const SHOWN = 60;

  const runTable = new Function(
    "$", "state", "el", "rowsFor", "fixFor", "gapWheelFixText", "canonicalName", "fixLinks", "labFor", "labVerdict", "gapWheelFor", "gapWheelTitle", "NAMES", "STATUS_TEXT", "LAB_SHORT", "FIX_TEXT", "SHOWN",
    `
    ${fn("renderTable")}
    return renderTable;
    `
  )(
    $, state, el,
    checkLogic.rowsFor, checkLogic.fixFor, checkLogic.gapWheelFixText, checkLogic.canonicalName,
    checkLogic.fixLinks, checkLogic.labFor, checkLogic.labVerdict, checkLogic.gapWheelFor, checkLogic.gapWheelTitle,
    NAMES, STATUS_TEXT, LAB_SHORT, FIX_TEXT, SHOWN
  );

  runTable();

  const table = $("gap-table");
  assert.equal(table.hidden, false);
  const tbody = table.children[1];
  assert.ok(tbody, "tbody was rendered");
  const trs = tbody.children;
  assert.equal(trs.length, 2);

  // Row 1: has-tracker (tracker has the package)
  const trHas = trs[0];
  const pkgHas = trHas.children[0]; // td.pkg
  const fixHas = trHas.children[2]; // td.fix

  // Package name links to tracker page, and PyPI link is beside it
  const pkgHasLinks = pkgHas.children.filter((c) => c.tag === "a");
  assert.equal(pkgHasLinks.length, 2);
  assert.equal(pkgHasLinks[0].attrs.href, "../p/has-tracker/");
  assert.equal(pkgHasLinks[0].text, "has-tracker");
  assert.equal(pkgHasLinks[1].attrs.href, "https://pypi.org/project/has-tracker/1.0");
  assert.equal(pkgHasLinks[1].text, "PyPI");

  // Fix cell has "coverage and build results" and "ask the maintainers"
  const fixHasLinks = fixHas.children.flatMap((c) => c.children || []).filter((c) => c.tag === "a");
  assert.equal(fixHasLinks.length, 2);
  assert.equal(fixHasLinks[0].attrs.href, "../p/has-tracker/");
  assert.equal(fixHasLinks[0].text, "coverage and build results");
  assert.equal(fixHasLinks[1].attrs.href, "https://github.com/org/has-tracker/issues");
  assert.equal(fixHasLinks[1].text, "ask the maintainers");
  assert.equal(fixHasLinks[1].attrs.rel, "noopener nofollow");

  // Row 2: no-tracker (tracker does NOT have the package)
  const trNo = trs[1];
  const pkgNo = trNo.children[0]; // td.pkg
  const fixNo = trNo.children[2]; // td.fix

  // Package name links directly to PyPI, no separate tracker link, no PyPI link beside it
  const pkgNoLinks = pkgNo.children.filter((c) => c.tag === "a");
  assert.equal(pkgNoLinks.length, 1);
  assert.equal(pkgNoLinks[0].attrs.href, "https://pypi.org/project/no-tracker/2.0");
  assert.equal(pkgNoLinks[0].text, "no-tracker");

  // Fix cell has neither link
  const fixNoLinks = fixNo.children.flatMap((c) => c.children || []).filter((c) => c.tag === "a");
  assert.equal(fixNoLinks.length, 0);
});

test("noLockfile offers the PyPI package command when setup.cfg names one, and keeps the plain verdict otherwise", () => {
  const source = read("check.mjs");
  const fb = source.match(/^function showPackageFallback\(.*?^}$/ms);
  const nl = source.match(/^function noLockfile\(.*?^}$/ms);
  assert.ok(fb && nl, "showPackageFallback and noLockfile are in check.mjs");

  const run = (projectName) => {
    const made = new Map();
    const element = (key) => {
      if (!made.has(key)) {
        made.set(key, { hidden: null, textContent: "", children: [], replaceChildren(...kids) { this.children = kids; } });
      }
      return made.get(key);
    };
    const el = (tag, attrs = {}, ...kids) => ({ tag, attrs, kids });
    const state = {
      repo: { owner: "httpie", name: "cli" },
      projectName,
      truncated: false,
      pythons: [],
      platforms: [],
      detected: { pythons: ["3.13"], platforms: ["linux-x86_64", "win-arm64"] },
    };
    const noLockfile = new Function(
      "$", "el", "state", "NAMES", "sortPythons", "packageCliCommand", "canonicalName", "copy", "hideResultSections", "fetch", "location",
      `${fb[0]}\n${nl[0]}\nreturn noLockfile;`,
    )(
      element, el, state, Object.fromEntries(checkLogic.PLATFORMS), checkLogic.sortPythons,
      checkLogic.packageCliCommand, checkLogic.canonicalName, () => {}, () => {}, undefined, { href: "https://wheelgap.github.io/tracker/check/" },
    );
    noLockfile();
    return element;
  };

  const withPkg = run("httpie");
  assert.equal(withPkg("verdict").textContent, "No lockfile found, but this looks like the PyPI package httpie.");
  assert.equal(withPkg("alerts").children[0].tag, "pre");
  assert.equal(
    withPkg("alerts").children[0].kids[0].attrs.text,
    "uvx wheelgap --deps httpie -p 3.13 -t linux-x86_64,win-arm64",
  );

  const withoutPkg = run(null);
  assert.equal(withoutPkg("verdict").textContent, "No lockfile found");
  assert.doesNotMatch(withoutPkg("verdict-detail").textContent, /Run uvx wheelgap in a checkout/);
  assert.deepEqual(withoutPkg("alerts").children, []);
});

test("the package fallback goes under the error when the lone root pyproject.toml can't be read", () => {
  const source = read("check.mjs");
  const fn = source.match(/^function onResult\(.*?^}$/ms);
  assert.ok(fn, "onResult is in check.mjs");
  const run = ({ lockfile = "pyproject.toml", projectName = "httpie", kind = "source", lockfiles = [{ path: lockfile, offline: false }], project } = {}) => {
    const calls = [];
    const made = new Map();
    const $ = (id) => (made.has(id) ? made.get(id) : made.set(id, { hidden: false, textContent: "" }).get(id));
    const state = { lockfile, projectName, auto: true, plan: { lockfiles }, repo: { dir: null }, detected: { unreadable: [] } };
    const onResult = new Function(
      "state", "$", "status", "needsIndex", "unreadableReason", "withoutUnreadable", "privatePyprojectReason", "autoPick", "chooseFile",
      "runCheck", "showPackageFallback", `${fn[0]}; return onResult;`,
    )(
      state, $, (text) => calls.push(["status", text]), () => calls.push(["needsIndex"]), checkLogic.unreadableReason,
      checkLogic.withoutUnreadable, checkLogic.privatePyprojectReason, checkLogic.autoPick, (files) => calls.push(["choose", files]),
      () => calls.push(["runCheck"]), (name) => calls.push(["fallback", name]),
    );
    onResult({ kind, error: "pyproject.toml: [project] dependencies is dynamic (set by the build backend), so wheelgap can't read it", project });
    return calls;
  };
  const calls = run();
  assert.match(calls[0][1], /^Wheelgap couldn't read pyproject\.toml: /); // capitalised with no named-file notice before it
  assert.deepEqual(calls[1], ["fallback", "httpie"]);
  assert.deepEqual(run({ projectName: null }).map(([c]) => c), ["status"]);
  // A pyproject.toml with another file to move on to is replaced by it, not answered with the package: by the only
  // input in a subfolder when the root is no project of its own (the driver's `project`), and otherwise by the
  // question which file to check (#238), as `wheelgap DIR` asks.
  assert.deepEqual(run({ lockfiles: [{ path: "pyproject.toml" }, { path: "backend/requirements.txt" }], project: false }), [["runCheck"]]);
  const asked = run({ lockfiles: [{ path: "pyproject.toml" }, { path: "backend/requirements.txt" }] });
  assert.deepEqual(asked.map(([c]) => c), ["choose"]);
  assert.deepEqual(asked[0][1].map((l) => l.path), ["backend/requirements.txt"]);
  assert.deepEqual(run({ lockfiles: [{ path: "pyproject.toml" }, { path: "docs/requirements.txt" }], project: false }).map(([c]) => c), ["choose"]);
  assert.deepEqual(run({ lockfile: "requirements.txt" }).map(([c]) => c), ["status"]);
});

test("with no lockfile, the package command uses this repo's targets, never the last repo's", () => {
  const source = read("check.mjs");
  const fn = source.match(/^function onLoaded\(.*?^}$/ms);
  assert.ok(fn, "onLoaded is in check.mjs");
  const seen = [];
  const state = { repo: { owner: "httpie", name: "cli", dir: null }, query: null, pythons: ["3.12"], platforms: ["macos-x86_64"] };
  const onLoaded = new Function(
    "state", "pickRequestedFile", "sortPythons", "NAMES", "status", "$", "noLockfile", "runCheck", `${fn[0]}; return onLoaded;`,
  )(
    state, checkLogic.pickRequestedFile, checkLogic.sortPythons, Object.fromEntries(checkLogic.PLATFORMS), () => {},
    () => ({ disabled: false }), () => seen.push([state.pythons, state.platforms]), () => seen.push("runCheck"),
  );
  onLoaded({ plan: { lockfiles: [] }, detected: { pythons: ["3.14"], platforms: ["linux-x86_64", "win-arm64"] }, projectName: "httpie" });
  assert.deepEqual(seen, [[["3.14"], ["linux-x86_64", "win-arm64"]]]);
});

// onLoaded() and onResult() with the worker and the DOM stood in: `chooseFile` and `runCheck` record what they're
// asked to do.
function pickPage(lockfiles, { query = null, lockfile = null } = {}) {
  const source = read("check.mjs");
  const fn = (name) => source.match(new RegExp(`^function ${name}\\(.*?^}$`, "ms"))[0];
  const calls = [];
  const $ = () => ({ hidden: false, textContent: "", disabled: false });
  const state = { repo: { owner: "celery", name: "celery", dir: null }, query, lockfile, auto: true, detected: { unreadable: [] } };
  const page = new Function(
    "state", "$", "status", "autoPick", "pickRequestedFile", "privatePyprojectReason", "sortPythons", "NAMES", "noLockfile",
    "needsIndex", "runCheck", "chooseFile", "unreadableReason", "withoutUnreadable",
    `${fn("onLoaded")}\n${fn("onResult")}\nreturn { onLoaded, onResult };`,
  )(
    state, $, () => {}, checkLogic.autoPick, checkLogic.pickRequestedFile, checkLogic.privatePyprojectReason,
    checkLogic.sortPythons, Object.fromEntries(checkLogic.PLATFORMS),
    () => calls.push(["noLockfile"]), () => calls.push(["needsIndex"]), () => calls.push(["runCheck", state.lockfile]),
    (files) => calls.push(["choose", files.map((l) => l.path)]), checkLogic.unreadableReason, checkLogic.withoutUnreadable,
  );
  const loaded = () => page.onLoaded({
    plan: { lockfiles: [...lockfiles] }, detected: { pythons: ["3.14"], platforms: ["linux-x86_64"], unreadable: [] },
    truncated: false, ref: "HEAD", root: "/repos/1",
  });
  return { page, state, calls, loaded };
}

test("with several requirements files under other names and nothing else, the page asks which one, never guesses", () => {
  const celery = [
    { path: "pyproject.toml", offline: false },
    { path: "requirements/default.txt", offline: false, other: true },
    { path: "requirements/docs.txt", offline: false, other: true },
  ];
  // The pyproject.toml (tool settings only) is tried first, then the visitor is asked: no runtime-sounding name wins.
  const p = pickPage(celery);
  p.loaded();
  assert.deepEqual(p.calls, [["runCheck", "pyproject.toml"]]);
  p.page.onResult({ kind: "source", error: "pyproject.toml: no [project] table, so there are no dependencies to read" });
  assert.deepEqual(p.calls.at(-1), ["choose", ["requirements/default.txt", "requirements/docs.txt"]]);
  assert.equal(p.calls.filter(([c]) => c === "runCheck").length, 1);
  // Only those files: asked straight away.
  const only = pickPage(celery.slice(1));
  only.loaded();
  assert.deepEqual(only.calls, [["choose", ["requirements/default.txt", "requirements/docs.txt"]]]);
  assert.deepEqual(only.state.platforms, ["linux-x86_64"]); // set, so the picker's check has targets
  // One such file is the answer, as `wheelgap DIR` reads its only one; a link that names a file checks that one.
  const one = pickPage(celery.slice(1, 2));
  one.loaded();
  assert.deepEqual(one.calls, [["runCheck", "requirements/default.txt"]]);
  // But a lone one named for development is offered, not checked.
  const dev = pickPage([{ path: "requirements-dev.txt", offline: false, other: true }]);
  dev.loaded();
  assert.deepEqual(dev.calls, [["choose", ["requirements-dev.txt"]]]);
  assert.match(read("check.mjs"), /\$\("lock-pick"\)\.hidden = locks\.length < 2 && Boolean\(state\.lockfile\);/);
  const named = pickPage(celery.slice(1), { query: { lockfile: "requirements/docs.txt" } });
  named.loaded();
  assert.deepEqual(named.calls, [["runCheck", "requirements/docs.txt"]]);
  // An exactly-named file anywhere still comes first, as before these files were listed.
  // A link to a file the page can't read, in such a repo: the notice, then the question.
  const readme = pickPage(celery.slice(1), { query: { lockfile: "README.md" } });
  readme.loaded();
  assert.deepEqual(readme.calls, [["choose", ["requirements/default.txt", "requirements/docs.txt"]]]);
  assert.equal(readme.state.fileNotice, "README.md isn't a file this page reads.");
  // chooseFile() itself: the named-file notice first, then (when the repo names its PyPI package) the --deps command.
  const chooseSrc = read("check.mjs").match(/^function chooseFile\(.*?^}$/ms)[0];
  const shown = new Map();
  const box = (id) => (shown.has(id) ? shown.get(id) : shown.set(id, { hidden: true, textContent: "", replaceChildren(...kids) { this.children = kids; } }).get(id));
  const chooseWith = (st) => new Function(
    "state", "$", "el", "status", "packageCliCommand", "renderSetup", "hideResultSections", "history", "toQuery", `${chooseSrc}; return chooseFile;`,
  )(st, box, (tag, attrs = {}, ...kids) => ({ tag, attrs, kids }), () => {}, checkLogic.packageCliCommand, () => {}, () => {}, { replaceState() {} }, checkLogic.toQuery);
  const files = [{ path: "requirements/base.txt" }, { path: "requirements/docs.txt" }];
  chooseWith({ repo: { owner: "o", name: "r" }, fileNotice: "README.md isn't a file this page reads.", projectName: "httpie", pythons: ["3.14"], platforms: ["linux-x86_64"] })(files);
  const alerts = box("alerts").children;
  assert.equal(alerts.length, 2);
  assert.deepEqual(alerts[0], { tag: "p", attrs: { class: "alert warn", text: "README.md isn't a file this page reads. Pick a file to check instead." }, kids: [] });
  assert.equal(alerts[1].kids[0], "Or check the published package httpie's dependencies: ");
  assert.deepEqual(alerts[1].kids[1], { tag: "code", attrs: { text: "uvx wheelgap --deps httpie -p 3.14 -t linux-x86_64" }, kids: [] });
  chooseWith({ repo: { owner: "o", name: "r" }, fileNotice: null, projectName: null, pythons: ["3.14"], platforms: ["linux-x86_64"] })(files);
  assert.deepEqual(box("alerts").children, []);
  // Requirements files under other names in the root come before the subfolders, as in `wheelgap DIR`: several of
  // them are asked about, never passed over for a subfolder's lockfile (#238).
  const nested = pickPage([...celery.slice(1), { path: "backend/uv.lock", offline: true }]);
  nested.loaded();
  assert.deepEqual(nested.calls, [["choose", ["requirements/default.txt", "requirements/docs.txt"]]]);
  assert.match(read("check.mjs"), /state\.lockfile \? \[\] : \[el\("option", \{ value: "", disabled: true, selected: true, text: "Choose a file…" \}\)\]/);
  // While nothing is picked, a platform or Python chip only redraws the setup: there's no file to check yet.
  const run = read("check.mjs").match(/^function runCheck\(\) \{.*?^}$/ms)[0];
  const drawn = [];
  const runCheck = new Function("state", "renderSetup", "keepFocus", "worker", `${run}; return runCheck;`)(
    { lockfile: null }, () => drawn.push("setup"), (fn) => fn(), { postMessage: () => drawn.push("posted") },
  );
  runCheck();
  assert.deepEqual(drawn, ["setup"]);
});

// Exercise the page's actual selection and render pipeline, with the worker and DOM stood in. The driver contract
// is pinned by test_check_driver.py; these tests check what the visitor sees after that result reaches the page.
function resultPage(locks, query = null, { dir = null, rootProjectFiles = [], unlistedInputs = [] } = {}) {
  const source = read("check.mjs");
  const fn = (name) => source.match(new RegExp(`^function ${name}\\(.*?^}$`, "ms"))[0];
  const elements = new Map();
  class Element {
    constructor() {
      this.children = [];
      this.hidden = false;
      const classes = new Set();
      this.classList = {
        remove: (v) => classes.delete(v),
        contains: (v) => classes.has(v),
        toggle: (v, on) => on ? classes.add(v) : classes.delete(v),
      };
    }
    set textContent(text) { this.children = [text]; }
    get textContent() { return this.children.map((c) => typeof c === "string" ? c : c.textContent).join(""); }
    append(...kids) { this.children.push(...kids); }
    replaceChildren(...kids) { this.children = kids; }
    setAttribute(k, v) { this[k] = v; }
    addEventListener(k, v) { this[`on${k}`] = v; }
    closest() { return this.section ??= new Element(); }
  }
  const $ = (id) => {
    if (!elements.has(id)) elements.set(id, new Element());
    return elements.get(id);
  };
  const state = { repo: { owner: "example", name: "project", dir }, query,
    python: null, platform: null, pythons: [], platforms: [] };
  const posts = [];
  const urls = []; // the page's address, each time it's replaced
  const env = { ...checkLogic, $, state, document: { createElement: () => new Element() },
    NAMES: Object.fromEntries(checkLogic.PLATFORMS), STAGES: {},
    worker: { postMessage: (m) => posts.push(m) }, history: { replaceState: (data, title, url) => urls.push(url) },
    status() {}, loadLab() {}, keepFocus: (fn) => fn(), renderTargets() {}, renderTable() {}, renderNext() {},
  };
  const functions = ["el", "chip", "onLoaded", "runCheck", "onResult", "render", "notCheckedCount", "renderVerdict", "renderSetup", "hideResultSections", "chooseFile"];
  const page = new Function(...Object.keys(env), `let seq = 0; ${functions.map(fn).join("\n")}; return {onLoaded, onResult};`)(...Object.values(env));
  page.onLoaded({ plan: { lockfiles: locks, workflows: [], root_project_files: rootProjectFiles, unlisted_inputs: unlistedInputs },
    detected: { found: [], unreadable: [], pythons: ["3.14"], platforms: ["linux-x86_64"] }, ref: "HEAD", root: "/repos/1" });
  return { ...page, $, state, posts, urls };
}

const privateResult = {
  wheelgap: "0.13.0", lockfile: "pyproject.toml", looked_up: true, listed: 3, packages: 0, private_count: 3,
  private: ["fastapi", "mylib", "numpy"], skipped: ["fastapi", "mylib", "numpy"], rows: [], outside_lock: [],
  targets: [{ python: "3.14", platform: "linux-x86_64", label: "3.14 linux-x86_64" }],
};
const projectLocks = [{ path: "pyproject.toml", offline: false }, { path: "backend/uv.lock", offline: true }];

test("an auto-picked all-private root pyproject records why, and asks before checking a subfolder's file instead", () => {
  // The root's pyproject.toml lists the project's dependencies, the page just can't look them up: the only input in a
  // subfolder isn't taken for the project's (#238), as `wheelgap DIR` wouldn't take it beside a root project.
  const page = resultPage([...projectLocks]);
  page.onResult(privateResult);
  assert.deepEqual(page.posts.map((m) => m.lockfile), ["pyproject.toml"]);
  assert.equal(page.state.lockfile, null);
  assert.equal(page.$("verdict").textContent, "Pick the file to check");
  assert.match(page.$("verdict-detail").textContent, /^example\/project has no lockfile, requirements\.txt or pyproject\.toml dependencies at its root\. Its own dependencies are in pyproject\.toml, which this page can't look up, so the one input in its subfolders, backend\/uv\.lock, isn't taken for the project's\./);
  assert.match(page.$("read-list").textContent, /Not read: pyproject.toml.*index or source settings/);
  assert.equal(page.$("out").hidden, false);
  // Picked under "Lockfile", it's checked, and the verdict doesn't call it the page's own pick.
  page.$("lockfile").value = "backend/uv.lock";
  page.$("lockfile").onchange();
  assert.deepEqual(page.posts.map((m) => m.lockfile), ["pyproject.toml", "backend/uv.lock"]);
  page.onResult({ ...privateResult, lockfile: "backend/uv.lock", looked_up: false, packages: 2,
    private: [], private_count: 0, skipped: [] });
  assert.match(page.$("verdict").textContent, /All 2 packages have a wheel/);
  assert.doesNotMatch(page.$("verdict-detail").textContent, /the only one in a subfolder/);
});

test("an all-private pyproject never gives way to a looked-up file its settings cover", () => {
  // backend/requirements.txt is under the root pyproject.toml's [tool.uv] index settings, so its names are private
  // too: no second check is posted, and the page stays on pyproject.toml with the local command.
  const page = resultPage([{ path: "pyproject.toml", offline: false }, { path: "backend/requirements.txt", offline: false }]);
  page.onResult(privateResult);
  assert.equal(page.posts.length, 1, "no fallback request");
  assert.equal(page.state.lockfile, "pyproject.toml");
  assert.match(page.$("verdict").textContent, /pyproject.toml wasn't checked/);
  // An offline lockfile anywhere sends no names anywhere, so it's offered; but as the root pyproject.toml is the
  // project's, it's asked about, not checked by itself (#238).
  const offline = resultPage([{ path: "pyproject.toml", offline: false }, { path: "backend/requirements.txt", offline: false }, { path: "backend/uv.lock", offline: true }]);
  offline.onResult(privateResult);
  assert.deepEqual(offline.posts.map((m) => m.lockfile), ["pyproject.toml"]);
  assert.deepEqual(offline.state.plan.lockfiles.map((l) => l.path), ["backend/uv.lock"]);
  assert.match(offline.$("verdict-detail").textContent, /the one input in its subfolders, backend\/uv\.lock, isn't taken for the project's/);
  // A nested private pyproject covers only its own folder: a looked-up file beside it stays, one elsewhere is next.
  // (Picked by itself through a link to its folder: with nothing at the root, app/ and tools/ would be asked about.)
  const nested = resultPage([{ path: "app/pyproject.toml", offline: false }, { path: "app/requirements.txt", offline: false }, { path: "tools/requirements.txt", offline: false }], null, { dir: "app" });
  nested.onResult({ ...privateResult, lockfile: "app/pyproject.toml" });
  assert.deepEqual(nested.posts.map((m) => m.lockfile), ["app/pyproject.toml", "tools/requirements.txt"]);
});

for (const [label, locks, query] of [
  ["no alternative", [projectLocks[0]], null],
  ["explicit link", projectLocks, { lockfile: "pyproject.toml" }],
  ["visitor's selection", projectLocks, null],
]) test(`an all-private pyproject with ${label} stays selected and shows the local command`, () => {
  const page = resultPage([...locks], query);
  if (label === "visitor's selection") {
    // Render a prior result to install the real picker's handler, then choose pyproject.toml.
    page.onResult({ ...privateResult, packages: 1, private_count: 2 });
    page.$("lockfile").value = "pyproject.toml";
    page.$("lockfile").onchange();
  }
  const posted = page.posts.length;
  page.onResult(privateResult);
  assert.equal(page.posts.length, posted, "no fallback request");
  assert.equal(page.state.lockfile, "pyproject.toml");
  assert.match(page.$("verdict").textContent, /pyproject.toml wasn't checked.*index or source settings/);
  assert.match(page.$("alerts").textContent, /uvx wheelgap pyproject.toml -p 3.14 -t linux-x86_64/);
  assert.match(page.$("alerts").textContent, /or commit a uv.lock/);
  assert.equal(page.$("out").hidden, false);
  assert.equal(page.$("verdict").closest().hidden, false);
  assert.equal(page.$("verdict").closest().classList.contains("clean"), false);
  for (const id of ["targets-title", "gaps-title", "next-title"]) assert.equal(page.$(id).closest().hidden, true);
  assert.equal(page.$("setup-title").closest().hidden, false);
  // A later useful result clears the private-file recovery text and restores the results.
  page.onResult({ ...privateResult, packages: 1, private_count: 0, private: [], skipped: [] });
  assert.doesNotMatch(page.$("alerts").textContent, /or commit a uv.lock/);
  assert.equal(page.$("gaps-title").closest().hidden, false);
});

// #238: with no input at the root, the page follows `wheelgap DIR` into the subfolders. Shaped like
// huggingface/transformers: a root pyproject.toml of tool settings beside setup.py, and only benchmark and example
// requirements files below it.
const transformersLocks = () => [
  { path: "pyproject.toml", offline: false },
  { path: "benchmark_v2/requirements.txt", offline: false },
  { path: "examples/pytorch/text-classification/requirements.txt", offline: false },
];
const noProjectTable = { kind: "source", error: "pyproject.toml: no [project] table, so there are no dependencies to read" };

test("after the root pyproject.toml, a repo shaped like transformers shows the chooser, not a result", () => {
  for (const project of [false, true, undefined]) { // tool settings only, a [build-system], or a driver that doesn't say
    const page = resultPage(transformersLocks(), null, { rootProjectFiles: ["setup.py"] });
    assert.deepEqual(page.posts.map((m) => m.lockfile), ["pyproject.toml"]);
    page.onResult({ ...noProjectTable, project });
    assert.deepEqual(page.posts.map((m) => m.lockfile), ["pyproject.toml"], "no check of benchmark_v2/requirements.txt");
    assert.equal(page.state.lockfile, null);
    assert.equal(page.state.result, null);
    assert.equal(page.$("verdict").textContent, "Pick the file to check");
    assert.equal(page.$("verdict-detail").textContent,
      "example/project has no lockfile, requirements.txt or pyproject.toml dependencies at its root, and the only inputs it has are, by their names, for development, tests, docs or examples, which wheelgap doesn't check on its own: benchmark_v2/requirements.txt, examples/pytorch/text-classification/requirements.txt. To check one anyway, pick it under \"Lockfile\" below.");
    assert.match(page.$("read-list").textContent, /Not read: pyproject.toml \(pyproject.toml: no \[project\] table/);
    assert.equal(page.$("gaps-title").closest().hidden, true);
    assert.equal(page.$("setup-title").closest().hidden, false); // the picker
    // The address drops the lock= of the pyproject.toml it moved on from: reloaded or shared, it asks again rather
    // than checking that file as one a link named.
    assert.match(page.urls[0], /[?&]lock=pyproject\.toml(&|$)/);
    assert.doesNotMatch(page.urls.at(-1), /lock=/);
    assert.match(page.urls.at(-1), /^\?repo=example\/project&py=3\.14&t=linux-x86_64$/);
  }
  // transformers' tree today: the benchmark and example folders are ones plan() skips, so after the root
  // pyproject.toml only utils/checkers-requirements.txt is left, which `wheelgap DIR` doesn't read by itself.
  const today = resultPage([{ path: "pyproject.toml", offline: false }, { path: "utils/checkers-requirements.txt", offline: false, other: true }],
    null, { rootProjectFiles: ["setup.py"] });
  today.onResult({ ...noProjectTable, project: false });
  assert.deepEqual(today.posts.map((m) => m.lockfile), ["pyproject.toml"]);
  assert.equal(today.$("verdict-detail").textContent,
    "example/project has no input that wheelgap reads by itself at its root or in a folder up to 3 down, only utils/checkers-requirements.txt. To check one anyway, pick it under \"Lockfile\" below.");
  // Without the setup.py, and with a non-side folder instead, the root still isn't a project unless the driver says so.
  const locks = [{ path: "pyproject.toml", offline: false }, { path: "app/requirements.txt", offline: false }];
  const settings = resultPage([...locks]);
  settings.onResult({ ...noProjectTable, project: false });
  assert.deepEqual(settings.posts.map((m) => m.lockfile), ["pyproject.toml", "app/requirements.txt"]);
  const built = resultPage([...locks]);
  built.onResult({ ...noProjectTable, project: true });
  assert.deepEqual(built.posts.map((m) => m.lockfile), ["pyproject.toml"]);
  assert.match(built.$("verdict-detail").textContent, /Its own dependencies are in pyproject\.toml, which wheelgap can't read, so the one input in its subfolders, app\/requirements\.txt, isn't taken for the project's/);
  const empty = resultPage([...locks]);
  empty.onResult({ ...privateResult, listed: 0, private_count: 0, private: [], skipped: [] }); // [project] lists nothing
  assert.deepEqual(empty.posts.map((m) => m.lockfile), ["pyproject.toml"]);
  assert.match(empty.$("verdict-detail").textContent, /\. Its pyproject\.toml lists no dependencies, so the one input in its subfolders, app\/requirements\.txt, isn't taken for the project's\./);
});

test("the only input in a subfolder is checked, and the verdict says it came from a subfolder", () => {
  // Shaped like MiroFish: nothing at the root but backend/uv.lock.
  const page = resultPage([{ path: "backend/uv.lock", offline: true }]);
  assert.deepEqual(page.posts.map((m) => m.lockfile), ["backend/uv.lock"]);
  page.onResult({ ...privateResult, lockfile: "backend/uv.lock", looked_up: false, packages: 2, private: [], private_count: 0, skipped: [] });
  assert.match(page.$("verdict").textContent, /All 2 packages have a wheel/);
  assert.match(page.$("verdict-detail").textContent, /\. No input at the root: checked backend\/uv\.lock, the only one in a subfolder\.$/);
  // An input `wheelgap DIR` finds in a folder plan() skips (build/): the one listed isn't the only one, so it asks.
  const build = resultPage([{ path: "backend/uv.lock", offline: true }], null, { unlistedInputs: ["build/requirements.txt"] });
  assert.equal(build.posts.length, 0);
  assert.match(build.$("verdict-detail").textContent, /but inputs in its subfolders \(and maybe more: the page sees only part of this repo's files\): backend\/uv\.lock\./);
  // Several, as in langchain's libs/: asked straight away, in `wheelgap DIR`'s words.
  const libs = resultPage(["core", "langchain", "partners/openai"].map((d) => ({ path: `libs/${d}/uv.lock`, offline: true })));
  assert.equal(libs.posts.length, 0);
  assert.equal(libs.$("verdict-detail").textContent,
    "example/project has no lockfile, requirements.txt or pyproject.toml dependencies at its root, but inputs in its subfolders: libs/core/uv.lock, libs/langchain/uv.lock, libs/partners/openai/uv.lock. Which one holds the project's dependencies can't be told, so pick it under \"Lockfile\" below.");
  // A root file is checked as before, and its verdict says nothing about subfolders.
  const root = resultPage([{ path: "uv.lock", offline: true }, { path: "backend/uv.lock", offline: true }]);
  assert.deepEqual(root.posts.map((m) => m.lockfile), ["uv.lock"]);
  root.onResult({ ...privateResult, lockfile: "uv.lock", looked_up: false, packages: 2, private: [], private_count: 0, skipped: [] });
  assert.doesNotMatch(root.$("verdict-detail").textContent, /subfolder/);
});

test("some private and some checked packages remain an answer even with another file available", () => {
  const page = resultPage([...projectLocks]);
  page.onResult({ ...privateResult, packages: 1, private_count: 2 });
  assert.equal(page.posts.length, 1);
  assert.equal(page.state.lockfile, "pyproject.toml");
  assert.doesNotMatch(page.$("verdict").textContent, /wasn't checked/);
  assert.match(page.$("alerts").textContent, /Not checked: pyproject.toml/);
});

test("a blob link checks the named file when readable, or announces the fallback when not", () => {
  const source = read("check.mjs");
  const fn = source.match(/^function onLoaded\(.*?^}$/ms);
  assert.ok(fn, "onLoaded is in check.mjs");

  const runOnLoaded = ({ repo, lockfiles, fileInTree, query = null }) => {
    const state = { repo, query };
    let ran = 0;
    const onLoaded = new Function(
      "state", "pickRequestedFile", "sortPythons", "NAMES", "status", "$", "noLockfile", "runCheck",
      `${fn[0]}; return onLoaded;`,
    )(
      state,
      checkLogic.pickRequestedFile,
      checkLogic.sortPythons,
      Object.fromEntries(checkLogic.PLATFORMS),
      () => {},
      () => ({ disabled: false }),
      () => {},
      () => { ran++; },
    );
    onLoaded({
      plan: { lockfiles: [...lockfiles] },
      detected: { pythons: ["3.13"], platforms: ["linux-x86_64"] },
      truncated: false,
      ref: "main",
      root: "/repos/1",
      fileInTree,
    });
    return { state, ran };
  };

  // Blob link to requirements/default.txt (unlisted, exists in tree): adds and checks it.
  const reqs = runOnLoaded({
    repo: checkLogic.parseRepo("https://github.com/celery/celery/blob/main/requirements/default.txt"),
    lockfiles: [{ path: "pyproject.toml", offline: false }],
    fileInTree: true,
  });
  assert.equal(reqs.ran, 1);
  assert.equal(reqs.state.lockfile, "requirements/default.txt");
  assert.equal(reqs.state.auto, false);
  assert.equal(reqs.state.fileNotice, null);
  // Added after the repo's own files, so the badge's automatic pick (lockfiles[0]) is still pyproject.toml and
  // the badge link keeps lock=requirements/default.txt.
  assert.deepEqual(reqs.state.plan.lockfiles.map((l) => l.path), ["pyproject.toml", "requirements/default.txt"]);
  assert.equal(checkLogic.pickLockfile(reqs.state.plan.lockfiles, null).path, "pyproject.toml");

  // Blob link to listed backend/uv.lock: checks backend/uv.lock, not root uv.lock.
  const nested = runOnLoaded({
    repo: checkLogic.parseRepo("https://github.com/o/r/blob/main/backend/uv.lock"),
    lockfiles: [{ path: "uv.lock", offline: true }, { path: "backend/uv.lock", offline: true }],
    fileInTree: true,
  });
  assert.equal(nested.ran, 1);
  assert.equal(nested.state.lockfile, "backend/uv.lock");
  assert.equal(nested.state.auto, false);
  assert.equal(nested.state.fileNotice, null);

  // Blob link to README.md: announces why it wasn't read and falls back to automatic pick.
  const readme = runOnLoaded({
    repo: checkLogic.parseRepo("https://github.com/o/r/blob/main/README.md"),
    lockfiles: [{ path: "uv.lock", offline: true }],
    fileInTree: true,
  });
  assert.equal(readme.ran, 1);
  assert.equal(readme.state.lockfile, "uv.lock");
  assert.equal(readme.state.auto, true);
  assert.equal(readme.state.fileNotice, "README.md isn't a file this page reads.");

  // A uv.lock in a folder plan() skips (examples/) is still a file the page reads: it's checked, not refused.
  const example = runOnLoaded({
    repo: checkLogic.parseRepo("https://github.com/o/r/blob/main/examples/app/uv.lock"),
    lockfiles: [{ path: "uv.lock", offline: true }],
    fileInTree: true,
  });
  assert.equal(example.state.lockfile, "examples/app/uv.lock");
  assert.equal(example.state.fileNotice, null);

  // The verdict names the file it ended up checking, read at render time: after a tool-settings-only
  // pyproject.toml gives way to requirements.txt, the notice must not still say pyproject.toml.
  assert.match(source, /text: `\$\{state\.fileNotice\} Checked \$\{r\.lockfile\} instead\.`/);
  assert.match(read("worker.mjs"), /paths\.includes\(wanted\) \|\| tree\.truncated/);
});

test("showInputError builds package and other-host hints as DOM nodes inside the status region", () => {
  const html = read("index.html");
  assert.match(html, /<p class="meta" id="status" role="status" aria-live="polite">/);

  const source = read("check.mjs");
  const st = source.match(/^function status\(.*?^}$/ms);
  const hintFn = source.match(/^function showInputError\(.*?^}$/ms);
  assert.ok(st && hintFn, "status and showInputError are in check.mjs");

  const statusEl = {
    textContent: "",
    children: [],
    error: false,
    replaceChildren(...kids) { this.children = kids; this.textContent = ""; },
    classList: { toggle(name, on) { if (name === "error") statusEl.error = Boolean(on); } },
  };
  const el = (tag, attrs = {}, ...kids) => ({ tag, attrs, kids });
  const state = { inputError: false };
  const showInputError = new Function(
    "$", "el", "inputHint", "state",
    `${st[0]}\n${hintFn[0]}\nreturn showInputError;`,
  )(() => statusEl, el, checkLogic.inputHint, state);

  // Package name: links to ../p/<canonical>/ and ../, with the typed name inserted as a plain text node.
  showInputError("numpy");
  assert.equal(state.inputError, true);
  assert.deepEqual(statusEl.children[1], { tag: "a", attrs: { href: "../p/numpy/" }, kids: ["See its wheel coverage"] });
  showInputError("Pillow");
  assert.equal(statusEl.error, true);
  assert.equal(statusEl.children[0], "Pillow looks like a package, not a repository. ");
  assert.deepEqual(statusEl.children[1], { tag: "a", attrs: { href: "../p/pillow/" }, kids: ["See its wheel coverage"] });
  assert.equal(statusEl.children[2], ", or ");
  assert.deepEqual(statusEl.children[3], { tag: "a", attrs: { href: "../" }, kids: ["search packages"] });

  // Non-GitHub host: mentions uvx wheelgap and links to getting started.
  showInputError("https://gitlab.com/owner/project");
  assert.equal(statusEl.error, true);
  assert.match(statusEl.children[0], /public GitHub repositories only/);
  assert.deepEqual(statusEl.children[1], { tag: "code", attrs: { text: "uvx wheelgap" }, kids: [] });
  assert.equal(statusEl.children[3].tag, "a");
  assert.equal(statusEl.children[3].attrs.href, "https://github.com/wheelgap/wheelgap/blob/main/docs/getting-started.md");

  // Anything else: keeps the default message as textContent.
  showInputError("<script>");
  assert.equal(statusEl.error, true);
  assert.equal(statusEl.textContent, "That doesn't look like a GitHub repository. Try owner/name or a github.com URL.");
  assert.doesNotMatch(source, /innerHTML/);

  // Wired to the form and to a bad ?repo=, and the worker's start-up lines don't overwrite the hint.
  assert.match(source, /if \(!repo\) return showInputError\(text\);/);
  assert.match(source, /if \(rawRepo\) \{\n\s+\$\("repo"\)\.value = rawRepo;\n\s+showInputError\(rawRepo\);/);
  assert.match(source, /if \(data\.id === undefined \? !state\.inputError : data\.id === seq\) status\(/);
  assert.match(source, /if \(!state\.repo && !state\.inputError\) status\(`Ready\./);
  assert.match(source, /function start\(repo, query = null\) \{\n\s+state\.inputError = false;/);
});

test("an all-private root pyproject.toml gives way to no requirements file its settings cover, other-named or not", () => {
  // #223's folder filter applies to #229's requirements files under other names too: no second check, no chooser.
  const page = resultPage([
    { path: "pyproject.toml", offline: false },
    { path: "requirements/default.txt", offline: false, other: true },
    { path: "requirements/docs.txt", offline: false, other: true },
  ]);
  page.onResult(privateResult);
  assert.equal(page.posts.length, 1, "no fallback request");
  assert.equal(page.state.lockfile, "pyproject.toml");
  assert.match(page.$("verdict").textContent, /pyproject.toml wasn't checked/);
});

test("the package fallback and the chooser: a named file's notice comes first, and the chooser offers --deps", () => {
  const source = read("check.mjs");
  // showPackageFallback() puts the named-file notice before the command; chooseFile() adds the package command.
  assert.match(source, /\$\("alerts"\)\.replaceChildren\(\.\.\.notice, \.\.\.items\);/);
  assert.match(source, /const cmd = state\.projectName \? packageCliCommand\(state\.projectName, state\.pythons, state\.platforms\) : null;/);
  // noLockfile() keeps #226's notice prefix and has no "Run uvx wheelgap in a checkout" sentence.
  const nl = source.match(/^function noLockfile\(.*?^}$/ms)[0];
  assert.match(nl, /\$\{prefix\}The repo check reads/);
  assert.doesNotMatch(nl, /Run uvx wheelgap in a checkout/);
  // The worker reads a lone root pyproject.toml's name even beside requirements files under other names (#229).
  assert.match(read("worker.mjs"), /metadataFiles\(plan\.lockfiles\.filter\(\(l\) => !l\.other\), paths\)/);
  assert.match(read("worker.mjs"), /return \{ plan, detected, truncated: Boolean\(tree\.truncated\), ref, root, fileInTree, projectName \};/);
});

test("renderVerdict alerts on public extra indexes and avoids --index-url advice for private indexes", () => {
  const source = read("check.mjs");
  const rv = source.match(/^function renderVerdict\(\) \{.*?^}$/ms);
  const nc = source.match(/^function notCheckedCount\(.*?^}$/ms);
  assert.ok(rv && nc, "renderVerdict and notCheckedCount are in check.mjs");

  const run = (resultExtra) => {
    const made = new Map();
    const element = (key) => {
      if (!made.has(key)) {
        made.set(key, {
          hidden: null,
          textContent: "",
          children: [],
          classList: { toggle() {} },
          closest: () => element(`${key} section`),
          replaceChildren(...kids) { this.children = kids; },
        });
      }
      return made.get(key);
    };
    const el = (tag, attrs = {}, ...kids) => ({ tag, attrs, kids });
    const state = {
      repo: { owner: "o", name: "r" },
      ref: "main",
      pythons: ["3.13"],
      platforms: ["linux-x86_64"],
      result: {
        lockfile: "requirements.txt",
        packages: 2,
        targets: [{ label: "3.13 linux-x86_64", python: "3.13", platform: "linux-x86_64" }],
        outside_lock: [],
        rows: [],
        skipped: [],
        private: [],
        extra_indexes: [],
        looked_up: true,
        ...resultExtra,
      },
    };
    const renderVerdict = new Function(
      "$", "state", "el", "headline", "verdictText", "nextSteps", "sortPythons", "missedText", "privatePyprojectReason",
      "privateText", "cliCommand", "runCheck", `${nc[0]}\n${rv[0]}; return renderVerdict;`,
    )(
      element, state, el, checkLogic.headline, checkLogic.verdictText, checkLogic.nextSteps,
      checkLogic.sortPythons, checkLogic.missedText, checkLogic.privatePyprojectReason, checkLogic.privateText,
      checkLogic.cliCommand, () => {},
    );
    renderVerdict();
    return element("alerts").children.map((c) => c.attrs.text);
  };

  const extraAlerts = run({ extra_indexes: ["download.pytorch.org"] });
  assert.ok(
    extraAlerts.includes("requirements.txt also installs from download.pytorch.org (--extra-index-url). This page checked PyPI only."),
  );

  // The packages the extra index may serve are named, as unknown.
  const torch = { name: "torch", version: "2.6.0", gaps: [], unknown: ["3.13 linux-x86_64"], statuses: {}, reason: "may be installed from download.pytorch.org, which this page doesn't read" };
  const named = run({ extra_indexes: ["download.pytorch.org"], rows: [torch], packages: 3 });
  assert.ok(named.includes("requirements.txt also installs from download.pytorch.org (--extra-index-url). This page checked PyPI only. torch may be installed from there instead, so it's shown as unknown."), named.join("\n"));

  const privAlerts = run({ private: ["corp-pkg"], skipped: ["corp-pkg"], reader: "requirements" });
  // Never a plain command: the CLI would look these names up on PyPI without --index-url.
  assert.ok(privAlerts.some((t) => t.includes("run uvx wheelgap --index-url with its URL in a checkout")));
  assert.ok(privAlerts.every((t) => !t.includes("checks them on PyPI's files")));
  // A Pipfile.lock that pins a package to its own index: the CLI skips that one too, so --index-url is still the way.
  const pipfile = run({ lockfile: "Pipfile.lock", private: ["corp-pkg"], skipped: ["corp-pkg"], reader: "pipfile", looked_up: true });
  assert.ok(pipfile.some((t) => t.includes("run uvx wheelgap --index-url with its URL in a checkout")));
  // A settings file that couldn't be read keeps its own reason and advice, whatever the reader (#235).
  const unread = run({ private: ["corp-pkg"], skipped: ["corp-pkg"], reader: "requirements", configs: [], configs_unread: ["pyproject.toml"], configs_unknown: false });
  assert.ok(unread.some((t) => t.includes("pyproject.toml (beside requirements.txt or in a folder above it) couldn't be read here") && t.endsWith("Check again to retry, or in a checkout run uvx wheelgap --index-url with your project's index and requirements.txt: without --index-url it would look these names up on PyPI.")));
  assert.ok(unread.every((t) => !t.includes("checks them on PyPI's files")));
});

test("an unknown row's reason is its Fix text, so a touch screen shows it too", () => {
  const source = read("check.mjs");
  assert.match(source, /f\.kind === "unchecked" && row\.reason \? row\.reason : FIX_TEXT\[f\.kind\]\(f\)/);
  assert.equal(checkLogic.fixFor({ name: "torch", gaps: [], unknown: ["3.14 linux-x86_64"], lookup: false }, {}).kind, "unchecked");
});

test("a truncated file list passes no settings-file list, so an extra-index file stays private", () => {
  // The driver refuses a null list (test_check_driver.py); a partial one could miss a root uv.toml or pyproject.toml.
  assert.match(read("worker.mjs"), /configs: tree\.truncated \? null : plan\.configs/);
});

test("renderTargets passes the distinct private package count to the platform tiles", () => {
  const source = read("check.mjs").match(/^function renderTargets\(\) \{.*?^}$/ms);
  const nc = read("check.mjs").match(/^function notCheckedCount\(.*?^}$/ms);
  assert.ok(source && nc, "renderTargets and notCheckedCount are in check.mjs");
  const made = new Map();
  const element = (key) => {
    if (!made.has(key)) made.set(key, { replaceChildren() {} });
    return made.get(key);
  };
  const nodes = [];
  const el = (tag, attrs = {}, ...kids) => {
    const node = { tag, attrs, kids };
    nodes.push(node);
    return node;
  };
  const label = "3.14 linux-x86_64";
  const state = {
    python: "3.14", platform: null,
    result: {
      lockfile: "Pipfile.lock", private: ["corp"], private_count: 2, outside_lock: [],
      targets: [{ label, python: "3.14", platform: "linux-x86_64" }],
      per_target: { [label]: { wheel: 1, source: 0, missing: 0, unknown: 0 } },
    },
  };
  const renderTargets = new Function("$", "state", "el", "sortPythons", "tileText", "NAMES", `${nc[0]}\n${source[0]}; return renderTargets;`)(
    element, state, el, checkLogic.sortPythons, checkLogic.tileText, {},
  );
  renderTargets();
  const tile = nodes.find((node) => node.attrs.class === "pct");
  assert.equal(tile.kids[0], "?");
  assert.equal(tile.kids[1].attrs.text, "1 not checked");
});

test("the gaps note never says every package has a wheel when a private index's packages weren't checked", () => {
  const source = read("check.mjs");
  const rt = source.match(/^function renderTable\(\) \{.*?^}$/ms);
  const nc = source.match(/^function notCheckedCount\(.*?^}$/ms);
  assert.ok(rt && nc, "renderTable and notCheckedCount are in check.mjs");
  const note = (extra) => {
    const made = new Map();
    const $ = (id) => {
      if (!made.has(id)) made.set(id, { textContent: "", hidden: false, replaceChildren() {} });
      return made.get(id);
    };
    const state = {
      python: "3.14", platform: null, showAll: false, fixes: {}, gapWheels: null, lab: {},
      result: {
        lockfile: "Pipfile.lock", outside_lock: [], rows: [],
        targets: [{ label: "3.14 linux-x86_64", python: "3.14", platform: "linux-x86_64" }], ...extra,
      },
    };
    new Function(
      "$", "state", "el", "rowsFor", "fixFor", "gapWheelFixText", "canonicalName", "fixLinks", "labFor", "labVerdict",
      "gapWheelFor", "gapWheelTitle", "notCheckedTail", "NAMES", "STATUS_TEXT", "LAB_SHORT", "FIX_TEXT", "SHOWN",
      `${nc[0]}\n${rt[0]}; return renderTable;`,
    )(
      $, state, () => ({}), checkLogic.rowsFor, checkLogic.fixFor, checkLogic.gapWheelFixText, checkLogic.canonicalName,
      checkLogic.fixLinks, checkLogic.labFor, checkLogic.labVerdict, checkLogic.gapWheelFor, checkLogic.gapWheelTitle, checkLogic.notCheckedTail,
      {}, {}, {}, {}, 60,
    )();
    return $("gaps-note").textContent;
  };
  assert.equal(note({}), "None: every package installs from a wheel here.");
  assert.equal(
    note({ private: ["corp-utils"], private_count: 2 }),
    "None among the checked packages; 1 more wasn't checked (its index isn't known to be PyPI).",
  );
  assert.equal(
    note({ private: ["corp-a", "corp-b"] }),
    "None among the checked packages; 2 more weren't checked (their index isn't known to be PyPI).",
  );
});
