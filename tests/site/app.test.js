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
  assert.ok(miniHtml.includes("Inspect 42"), "minimized summary includes Inspect 42 button");
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
