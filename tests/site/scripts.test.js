// The site's scripts run as classic <script>s, which share one global scope: two files declaring the same top-level
// const pass `node --check` one by one but stop the second script in the browser. Compile them together, in the order
// index.html loads them, so a clash fails here instead. It can't catch a function or var declared twice (legal: the later
// one wins), or a name that clashes only with a browser global (`let location`). Run with
// `node --test tests/site/logic.test.js tests/site/scripts.test.js`.
"use strict";

const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

const SITE = path.join(__dirname, "..", "..", "site");

test("the home page's scripts compile together, as the browser runs them", () => {
  const html = fs.readFileSync(path.join(SITE, "index.html"), "utf8");
  const sources = [...html.matchAll(/<script\b(?![^>]*\btype=["']module)[^>]*\bsrc=["']([^"']+)["']/g)].map((m) => m[1]);
  assert.ok(sources.length >= 2, `expected index.html to load the site scripts, found ${sources}`);
  const code = sources.map((src) => fs.readFileSync(path.join(SITE, src), "utf8")).join("\n;\n");
  assert.doesNotThrow(() => new vm.Script(code, { filename: sources.join(" + ") }));
});
