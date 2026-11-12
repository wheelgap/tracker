"use strict";
// Pure helpers for app.js, kept apart so tests/site/logic.test.js can run them under node (no DOM).
// In the browser this is a plain script loaded before app.js; under node it exports the same functions.

// A wheel, or Pyodide's own build of the package (wheelgap --pyodide-lock): either way it installs without a build.
const INSTALLABLE = new Set(["wheel", "bundled"]);
// Same text as BUNDLED_NOTE in scripts/build_pages.py.
const BUNDLED_NOTE =
  "bundled: there's no wheel on PyPI for Pyodide, but Pyodide ships its own build of this package, often an " +
  "older version than the one shown here. The cell names the Pyodide release and the version it ships.";

const isText = (v) => typeof v === "string" && v.length > 0 && v.length < 200;
const isCount = (v) => typeof v === "number" && Number.isFinite(v) && v >= 0;

function installable(status) {
  return INSTALLABLE.has(status);
}

// The packages without a wheel or a Pyodide build on target index i.
function gapsAt(packages, i) {
  return packages.filter((p) => !installable(p.results[i]));
}

// One platform bar's numbers from a summary cell and the previous night's (or null).
function platformStats(s, prev, n) {
  const count = (cell, k) => (cell && isCount(cell[k]) ? cell[k] : 0);
  const bundled = count(s, "bundled");
  const ok = count(s, "wheel") + bundled; // installs without a build
  // A previous night that counted nothing as bundled here (no --pyodide-lock yet) isn't like for like.
  const comparable = Boolean(prev) && !(bundled && !count(prev, "bundled"));
  const delta = comparable ? ok - (count(prev, "wheel") + count(prev, "bundled")) : 0;
  const share = (k) => (n ? (100 * count(s, k)) / n : 0);
  return { bundled, ok, delta, percent: n ? (100 * ok) / n : 0, widths: Object.fromEntries(
    ["wheel", "bundled", "source", "missing", "unknown"].map((k) => [k, share(k)])) };
}

// data/pyodide.json -> {python: {pyodide, lock}}, keeping only well-formed entries; anything else is {}.
function pyodideReleases(raw) {
  const out = {};
  const pythons = raw && typeof raw === "object" && raw.pythons;
  if (!pythons || typeof pythons !== "object" || Array.isArray(pythons)) return out;
  for (const [python, r] of Object.entries(pythons)) {
    if (/^3\.\d+$/.test(python) && r && typeof r === "object" && isText(r.pyodide) && isText(r.lock) && r.lock.startsWith("https://")) {
      out[python] = { pyodide: r.pyodide, lock: r.lock };
    }
  }
  return out;
}

// The `bundled` map of data/packages/<name>.json -> {label: {pyodide, bundled_version, vs_latest}}, well-formed only.
function bundledInfo(raw) {
  const out = {};
  const bundled = raw && typeof raw === "object" && raw.bundled;
  if (!bundled || typeof bundled !== "object" || Array.isArray(bundled)) return out;
  for (const [label, b] of Object.entries(bundled)) {
    if (b && typeof b === "object" && isText(b.pyodide) && isText(b.bundled_version)) {
      out[label] = { pyodide: b.pyodide, bundled_version: b.bundled_version, vs_latest: b.vs_latest };
    }
  }
  return out;
}

// The line under a bundled cell; same text as describe() in scripts/pyodide_locks.py.
function bundledDetail(info) {
  if (!info || !isText(info.pyodide) || !isText(info.bundled_version)) return "";
  const text = `Pyodide ${info.pyodide} ships ${info.bundled_version}`;
  if (info.vs_latest === "older") return `${text}, older than the latest`;
  if (info.vs_latest === "different") return `${text}, not the latest`;
  return text;
}

// The "Why?" command for one target; a pyodide target with a pinned release gets its lock, so the command's
// answer matches the card.
function whyCommand(name, version, python, platform, releases) {
  let cmd = `uvx wheelgap ${name}==${version ?? ""} -p ${python} -t ${platform} --explain`;
  const release = platform === "pyodide" && releases ? releases[python] : null;
  if (release) cmd += ` --pyodide-lock ${release.lock}`;
  return cmd;
}

// The footer sentence when the scan counted Pyodide's builds, or "" to keep the page's default. anyBundled says the
// scan has bundled results even if data/pyodide.json couldn't name the releases.
function pyodideFooter(releases, anyBundled = false) {
  const named = Object.entries(releases).map(([py, r]) => `${py} (Pyodide ${r.pyodide})`);
  if (!named.length) {
    return anyBundled
      ? "Pyodide also ships its own builds of many popular packages, often older versions than the latest release; those count as bundled."
      : "";
  }
  return (
    "Pyodide also ships its own builds of many popular packages, often older versions than the latest release: " +
    `on Python ${named.join(" and ")}, those count as bundled.`
  );
}

if (typeof module !== "undefined" && module.exports) {
  module.exports = {
    BUNDLED_NOTE, installable, gapsAt, platformStats, pyodideReleases, bundledInfo, bundledDetail, whyCommand, pyodideFooter,
  };
}
