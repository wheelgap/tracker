// Offline checks for site/logic.js, the helpers app.js uses: run with `node --test tests/site/`.
"use strict";

const test = require("node:test");
const assert = require("node:assert/strict");
const path = require("node:path");

const logic = require(path.join(__dirname, "..", "..", "site", "logic.js"));

const LOCK = "https://cdn.jsdelivr.net/pyodide/v0.29.5/full/pyodide-lock.json";

test("bundled packages aren't gaps; source, missing and unknown are", () => {
  const packages = ["wheel", "bundled", "source", "missing", "unknown"].map((s, i) => ({ name: `p${i}`, results: [s] }));
  assert.deepEqual(logic.gapsAt(packages, 0).map((p) => p.results[0]), ["source", "missing", "unknown"]);
});

test("the bar's share counts wheel and bundled", () => {
  const s = { wheel: 70, bundled: 10, source: 0, missing: 19, unknown: 1 };
  const prev = { wheel: 69, bundled: 10, source: 0, missing: 20, unknown: 1 };
  const stats = logic.platformStats(s, prev, 100);
  assert.equal(stats.ok, 80);
  assert.equal(stats.percent, 80);
  assert.equal(stats.delta, 1);
  assert.equal(stats.widths.bundled, 10);
});

test("no delta on the night bundled is first counted", () => {
  const stats = logic.platformStats({ wheel: 70, bundled: 10, missing: 20 }, { wheel: 70, missing: 30 }, 100);
  assert.equal(stats.delta, 0);
  assert.equal(stats.percent, 80);
  const zero = logic.platformStats({ wheel: 70, bundled: 10, missing: 20 }, { wheel: 70, bundled: 0, missing: 30 }, 100);
  assert.equal(zero.delta, 0);
});

test("a scan without bundled counts reads as before", () => {
  const stats = logic.platformStats({ wheel: 90, source: 5, missing: 5, unknown: 0 }, { wheel: 88 }, 100);
  assert.deepEqual([stats.bundled, stats.ok, stats.delta, stats.percent], [0, 90, 2, 90]);
  assert.equal(stats.widths.bundled, 0);
});

test("malformed pyodide.json gives no releases instead of throwing", () => {
  for (const raw of [null, 3, "x", [], { pythons: null }, { pythons: [] }, { pythons: { "3.13": null } },
    { pythons: { "3.13": { pyodide: 1, lock: LOCK } } }, { pythons: { "3.13": { pyodide: "0.29.5", lock: "http://x" } } },
    { pythons: { "<b>": { pyodide: "0.29.5", lock: LOCK } } }]) {
    assert.deepEqual(logic.pyodideReleases(raw), {}, JSON.stringify(raw));
  }
  assert.deepEqual(logic.pyodideReleases({ pythons: { "3.13": { pyodide: "0.29.5", lock: LOCK, extra: 1 } } }), {
    "3.13": { pyodide: "0.29.5", lock: LOCK },
  });
  assert.equal(logic.pyodideFooter({}), "");
  assert.match(logic.pyodideFooter({}, true), /count as bundled/);
  assert.match(logic.pyodideFooter(logic.pyodideReleases({ pythons: { "3.13": { pyodide: "0.29.5", lock: LOCK } } })),
    /3\.13 \(Pyodide 0\.29\.5\)/);
});

test("the bundled line names the release and says when it isn't the latest", () => {
  const info = logic.bundledInfo({ bundled: {
    "3.13 pyodide": { pyodide: "0.29.5", bundled_version: "2.3.3", vs_latest: "older" },
    "3.14 pyodide": { pyodide: null, bundled_version: null, vs_latest: null },
  } });
  assert.deepEqual(Object.keys(info), ["3.13 pyodide"]);
  assert.equal(logic.bundledDetail(info["3.13 pyodide"]), "Pyodide 0.29.5 ships 2.3.3, older than the latest");
  assert.equal(logic.bundledDetail({ pyodide: "0.29.5", bundled_version: "6.0.3", vs_latest: "same" }), "Pyodide 0.29.5 ships 6.0.3");
  assert.equal(logic.bundledDetail({ pyodide: "0.29.5", bundled_version: "2.0rc1", vs_latest: "different" }),
    "Pyodide 0.29.5 ships 2.0rc1, not the latest");
  assert.equal(logic.bundledDetail(undefined), "");
  for (const raw of [null, [], { bundled: [] }, { bundled: { x: "y" } }]) assert.deepEqual(logic.bundledInfo(raw), {});
});

test("the Why command passes the pinned lock on pyodide only", () => {
  const releases = { "3.13": { pyodide: "0.29.5", lock: LOCK } };
  assert.equal(logic.whyCommand("pandas", "3.1.1", "3.13", "pyodide", releases),
    `uvx wheelgap pandas==3.1.1 -p 3.13 -t pyodide --explain --pyodide-lock ${LOCK}`);
  assert.equal(logic.whyCommand("pandas", "3.1.1", "3.13", "linux-x86_64", releases),
    "uvx wheelgap pandas==3.1.1 -p 3.13 -t linux-x86_64 --explain");
  assert.equal(logic.whyCommand("pandas", "3.1.1", "3.14", "pyodide", releases),
    "uvx wheelgap pandas==3.1.1 -p 3.14 -t pyodide --explain");
});
