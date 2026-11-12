"use strict";
// Uses the helpers in logic.js (installable, platformStats, bundledDetail, ...), loaded before this file.

const PLATFORM_NAMES = {
  "linux-x86_64": ["Linux x86_64", "glibc · manylinux"],
  "linux-aarch64": ["Linux ARM64", "glibc · manylinux"],
  "musl-x86_64": ["Alpine x86_64", "musl · musllinux"],
  "musl-aarch64": ["Alpine ARM64", "musl · musllinux"],
  "macos-arm64": ["macOS Apple silicon", "macosx arm64"],
  "macos-x86_64": ["macOS Intel", "macosx x86_64"],
  "win-amd64": ["Windows x86_64", "win_amd64"],
  "win-arm64": ["Windows ARM64", "win_arm64"],
  "pyodide": ["Pyodide (browser)", "wasm32 · PyPI wheels only"],
};
const platformName = (p) => (Object.hasOwn(PLATFORM_NAMES, p) ? PLATFORM_NAMES[p] : [p, ""]);
const STATUS_TEXT = { wheel: "wheel", bundled: "bundled", source: "from source", missing: "can't install", unknown: "unknown" };
const PAGE = 50;

const $ = (id) => document.getElementById(id);
const fmt = (n) => (typeof n === "number" ? n.toLocaleString("en-GB") : String(n ?? ""));
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);

// pyodide: data/pyodide.json through pyodideReleases(), {python: {pyodide, lock}} for each pinned Pyodide release.
const bundledCache = new Map(); // canonical name -> bundledInfo() of its data/packages/<name>.json
const state = { data: null, history: [], pyodide: {}, python: null, platform: "musl-aarch64", shown: PAGE };

function pythons() {
  return [...new Set(state.data.targets.map((t) => t.python))];
}

function platformDetail(t) {
  const release = t.platform === "pyodide" ? state.pyodide[t.python] : null;
  if (release) return `wasm32 · PyPI wheels and Pyodide ${release.pyodide}'s builds`;
  return platformName(t.platform)[1];
}

function targetIndex(python, platform) {
  return state.data.targets.findIndex((t) => t.python === python && t.platform === platform);
}

function renderMeta() {
  const d = state.data;
  const date = new Date(d.generated);
  $("n-packages").textContent = fmt(d.packages.length);
  $("meta").textContent =
    `Latest scan ${date.toLocaleDateString("en-GB", { day: "numeric", month: "long", year: "numeric" })} · ` +
    `wheelgap ${d.wheelgap} · download ranking from ${d.top_packages.last_update?.slice(0, 10) ?? "top-pypi-packages"}`;
  const pure = d.packages.filter((p) => p.pure).length;
  $("pure-note").textContent =
    `${fmt(pure)} of the ${fmt(d.packages.length)} are pure Python and install everywhere; ` +
    `the gaps are among the ${fmt(d.packages.length - pure)} with compiled code.`;
  const anyBundled = Object.values(d.summary ?? {}).some((s) => s && s.bundled > 0);
  const footer = pyodideFooter(state.pyodide, anyBundled);
  if (footer) {
    $("legend-bundled").hidden = false;
    $("pyodide-note").textContent = footer;
  }
  $("package-names").innerHTML = d.packages.map((p) => `<option value="${esc(p.name)}">`).join("");
}

// data/agreement.json, from scripts/check_pip.py: how often pip agrees with the scan. Built only with textContent,
// so nothing in the file is read as HTML, and shown only while it's fresh and well-formed.
const AGREEMENT_MAX_AGE_DAYS = 3;
const isCount = (n) => Number.isInteger(n) && n >= 0;
// Round down, so a single mismatch never shows as 100%.
const rateText = (matched, checks) => (matched === checks ? "100" : (Math.floor((1000 * matched) / checks) / 10).toFixed(1));

function agreementText(a, now = Date.now()) {
  if (!a || typeof a !== "object") return null;
  const { checks, matched, compiled, skipped, errors } = a;
  if (![checks, matched, skipped, errors].every(isCount) || checks === 0 || matched > checks) return null;
  if (typeof a.date !== "string" || !/^\d{4}-\d{2}-\d{2}$/.test(a.date)) return null;
  const age = (now - Date.parse(`${a.date}T00:00:00Z`)) / 86400000;
  if (!(age >= -1 && age <= AGREEMENT_MAX_AGE_DAYS)) return null; // a stale check says nothing about today's scan
  let text =
    `Cross-checked against pip on ${a.date}, with wheelgap's own tag lists on both sides: ` +
    `they agree on ${rateText(matched, checks)}% of ${fmt(checks)} checks`;
  if (compiled && typeof compiled === "object" && [compiled.packages, compiled.checks, compiled.matched].every(isCount) &&
      compiled.checks > 0 && compiled.matched <= compiled.checks) {
    text +=
      `, and on ${rateText(compiled.matched, compiled.checks)}% of the ${fmt(compiled.checks)} for the ` +
      `${fmt(compiled.packages)} packages without a pure-Python wheel (the rest install everywhere)`;
  }
  return `${text}. ${fmt(skipped)} skipped, ${fmt(errors)} without an answer from pip. `;
}

function renderAgreement(a, now = Date.now()) {
  const el = $("agreement");
  const text = agreementText(a, now);
  el.hidden = text === null;
  if (text === null) return;
  el.textContent = text;
  const link = document.createElement("a");
  link.href = "data/agreement.json";
  link.textContent = "Details";
  el.append(link);
}

function renderTabs() {
  $("python-tabs").innerHTML = pythons()
    .map((py) => `<button role="tab" aria-selected="${py === state.python}" data-python="${esc(py)}">${esc(py)}</button>`)
    .join("");
}

function previousSummary(label) {
  const h = state.history;
  return h.length > 1 ? h[h.length - 2].summary[label] : null;
}

function renderPlatforms() {
  const d = state.data;
  const n = d.packages.length;
  const rows = d.targets
    .filter((t) => t.python === state.python)
    .map((t) => {
      const s = d.summary[t.label];
      const [name] = platformName(t.platform);
      const { bundled, ok, delta, percent, widths } = platformStats(s, previousSummary(t.label), n);
      const trend = delta ? ` <small>${delta > 0 ? "+" : ""}${delta} since last scan</small>` : "";
      const seg = (k) => `<span class="b-${k}" style="width:${widths[k].toFixed(1)}%"></span>`;
      const counts = [`${fmt(s.wheel)} wheel`, ...(bundled ? [`${fmt(bundled)} bundled with Pyodide`] : []),
        `${fmt(s.source)} from source`, `${fmt(s.missing)} can't install`].join(" · ");
      return `<li><button data-platform="${esc(t.platform)}" aria-pressed="${t.platform === state.platform}"
          aria-label="${esc(name)}: ${percent.toFixed(1)}% have a wheel${bundled ? " or a Pyodide build" : ""}">
        <span class="pname">${esc(name)}<small>${esc(platformDetail(t))}</small></span>
        <span class="bar" title="${esc(counts)}">
          ${seg("wheel")}${seg("bundled")}${seg("source")}${seg("missing")}${seg("unknown")}</span>
        <span class="pct">${percent.toFixed(1)}%<small>${fmt(n - ok)} without${trend}</small></span>
      </button></li>`;
    });
  $("platforms").innerHTML = rows.join("");
}

// Share of packages with a wheel for one target label, per history entry that has it.
function coverageSeries(label) {
  return state.history
    .filter((h) => h.summary && h.summary[label] && Number.isFinite(h.summary[label].wheel) && h.packages)
    .map((h) => ({ date: h.date, value: (100 * h.summary[label].wheel) / h.packages }));
}

function sparkline(points, name) {
  const W = 240, H = 64, PAD = 6;
  const values = points.map((p) => p.value);
  let lo = Math.min(...values), hi = Math.max(...values);
  if (hi - lo < 0.5) {
    // A flat line would otherwise fill the chart's height with rounding noise.
    const mid = (hi + lo) / 2;
    lo = mid - 0.25;
    hi = mid + 0.25;
  }
  const x = (i) => PAD + (i * (W - 2 * PAD)) / (points.length - 1);
  const y = (v) => H - PAD - ((v - lo) * (H - 2 * PAD)) / (hi - lo);
  const path = points.map((p, i) => `${i ? "L" : "M"}${x(i).toFixed(1)},${y(p.value).toFixed(1)}`).join(" ");
  // One dot per scan while they fit; after a month only the latest, so the line stays readable.
  const shown = points.length <= 31 ? points.map((p, i) => [p, i]) : [[points[points.length - 1], points.length - 1]];
  const dots = shown
    .map(([p, i]) => `<circle cx="${x(i).toFixed(1)}" cy="${y(p.value).toFixed(1)}" r="2.5"><title>${esc(p.date)}: ${p.value.toFixed(1)}%</title></circle>`)
    .join("");
  const first = points[0], last = points[points.length - 1];
  const label = `${name}: ${first.value.toFixed(1)}% on ${first.date}, ${last.value.toFixed(1)}% on ${last.date}`;
  return `<svg class="spark" viewBox="0 0 ${W} ${H}" role="img" aria-label="${esc(label)}">
    <path d="${path}"></path>${dots}</svg>`;
}

function renderTrends() {
  const dates = [];
  const rows = state.data.targets
    .filter((t) => t.python === state.python)
    .map((t) => {
      const points = coverageSeries(t.label);
      if (points.length < 2) return "";
      dates.push(...points.map((p) => p.date));
      const [name] = platformName(t.platform);
      const first = points[0], last = points[points.length - 1];
      const delta = Number((last.value - first.value).toFixed(1));
      const change = `${delta >= 0 ? "+" : "−"}${Math.abs(delta).toFixed(1)} points since ${first.date}`;
      return `<li><span class="pname">${esc(name)}</span>
        <span class="pct">${last.value.toFixed(1)}%</span>
        ${sparkline(points, name)}
        <small class="change">${esc(change)}</small></li>`;
    })
    .join("");
  $("trends").innerHTML = rows;
  $("trend-section").hidden = !rows;
  dates.sort();
  $("trend-note").textContent = rows
    ? `Share of packages with a wheel for Python ${state.python}, one point per nightly scan from ${dates[0]} to ${dates[dates.length - 1]}. Each chart is scaled to its own range.` +
      // Wheels only, so the line doesn't jump on the night the scan started counting Pyodide's builds.
      (state.pyodide[state.python] ? " On Pyodide that's PyPI wheels only: the bars above also count Pyodide's own builds." : "")
    : "";
}

function renderGaps() {
  const i = targetIndex(state.python, state.platform);
  const [name] = platformName(state.platform);
  $("gaps-platform").textContent = `${name}, Python ${state.python}`;
  if (i < 0) {
    $("gaps").innerHTML = `<li class="empty">Not scanned for Python ${esc(state.python)} (no Pyodide build for this version).</li>`;
    $("more").hidden = true;
    return;
  }
  const gaps = gapsAt(state.data.packages, i);
  $("gaps").innerHTML =
    gaps
      .slice(0, state.shown)
      .map((p) => {
        const s = p.results[i];
        return `<li><span class="rank">#${esc(p.rank)}</span>
          <span><a href="https://pypi.org/project/${encodeURIComponent(p.name)}/${encodeURIComponent(p.version ?? "")}">${esc(p.name)}</a><span class="ver">${esc(p.version ?? "")}</span></span>
          <span class="status s-${esc(s)}">${esc(STATUS_TEXT[s] ?? s)}</span></li>`;
      })
      .join("") || `<li class="empty">Every package has a wheel here.</li>`;
  $("more").hidden = gaps.length <= state.shown;
  $("more").textContent = `Show more (${fmt(gaps.length - state.shown)} left)`;
}

function canonicalName(name) {
  return name.toLowerCase().replace(/[-_.]+/g, "-");
}

function badgeMarkdown(p) {
  const name = canonicalName(p.name);
  const badge = `https://img.shields.io/endpoint?url=https://wheelgap.github.io/tracker/badge/${name}.json`;
  const link = `https://wheelgap.github.io/tracker/#package=${encodeURIComponent(p.name)}`;
  return `[![wheels](${badge})](${link})`;
}

// extra is bundledInfo() of the package's data/packages/<name>.json, fetched after a first render without it.
function renderLookup(query, extra = null) {
  const q = canonicalName(query.trim());
  if (!q) {
    $("lookup").innerHTML = "";
    return;
  }
  const p = state.data.packages.find((x) => canonicalName(x.name) === q);
  if (!p) {
    $("lookup").innerHTML = `<div class="card"><p class="sub">${esc(query)} isn't in the top ${fmt(state.data.packages.length)}.
      Check it, or any package at a pinned version, locally: <code>uvx wheelgap ${esc(query.trim())} -t all</code></p></div>`;
    return;
  }
  const pys = pythons();
  const platforms = Object.keys(PLATFORM_NAMES).filter((pl) => state.data.targets.some((t) => t.platform === pl));
  const cell = (py, pl) => {
    const i = targetIndex(py, pl);
    if (i < 0) return `<td><span class="status s-none">n/a</span></td>`;
    const s = p.results[i];
    const detail = s === "bundled" && extra ? bundledDetail(extra[state.data.targets[i].label]) : "";
    const small = detail ? `<small class="bver">${esc(detail)}</small>` : "";
    return `<td><span class="status s-${esc(s)}">${esc(STATUS_TEXT[s] ?? s)}</span>${small}</td>`;
  };
  $("lookup").innerHTML = `<div class="card">
    <h3>${esc(p.name)} <span class="ver">${esc(p.version ?? "")}</span></h3>
    <p class="sub">#${esc(p.rank)} by downloads${p.pure ? " · pure Python: installs everywhere" : ""}${p.error ? ` · lookup failed: ${esc(p.error)}` : ""} ·
      <a href="p/${esc(canonicalName(p.name))}/">Package page</a> ·
      <a href="https://pypi.org/project/${encodeURIComponent(p.name)}/">PyPI</a></p>
    <div class="matrix-scroll"><table class="matrix">
      <thead><tr><th>Platform</th>${pys.map((py) => `<th>${esc(py)}</th>`).join("")}</tr></thead>
      <tbody>${platforms
        .map((pl) => `<tr><th scope="row">${esc(PLATFORM_NAMES[pl][0])}</th>${pys.map((py) => cell(py, pl)).join("")}</tr>`)
        .join("")}</tbody>
    </table></div>
    ${!p.error && p.results.includes("bundled") ? `<p class="sub" style="margin:12px 0 0">${esc(BUNDLED_NOTE)}</p>` : ""}
    ${
      targetIndex(state.python, state.platform) < 0
        ? ""
        : `<p class="sub" style="margin:12px 0 0">Why? <code>${esc(whyCommand(p.name, p.version, state.python, state.platform, state.pyodide))}</code></p>`
    }
    <div class="badge-snippet">
      <p class="sub">Badge for your README</p>
      <pre class="badge-md"><code>${esc(badgeMarkdown(p))}</code></pre>
      <button type="button" class="copy-badge">Copy</button>
    </div>
  </div>`;
  const copy = $("lookup").querySelector(".copy-badge");
  if (copy) {
    const md = badgeMarkdown(p);
    copy.addEventListener("click", async () => {
      try {
        await navigator.clipboard.writeText(md);
        copy.textContent = "Copied";
        setTimeout(() => {
          copy.textContent = "Copy";
        }, 1500);
      } catch {
        copy.textContent = "Select and copy";
      }
    });
  }
  // Which version Pyodide ships is in the package's own file; show the card now and add it when it arrives.
  if (extra === null && !p.error && p.results.includes("bundled")) {
    const cached = bundledCache.get(q);
    const got = cached
      ? Promise.resolve(cached)
      : fetch(`data/packages/${encodeURIComponent(q)}.json`)
          .then((r) => (r.ok ? r.json() : null))
          .then((json) => {
            const info = bundledInfo(json);
            bundledCache.set(q, info);
            return info;
          });
    got
      .then((info) => {
        if (canonicalName($("search").value.trim()) === q) renderLookup(query, info);
      })
      .catch(() => {}); // the card already shows "bundled"; it just goes without the version
  }
}

function readHash() {
  const params = new URLSearchParams(location.hash.slice(1));
  if (params.get("python") && pythons().includes(params.get("python"))) state.python = params.get("python");
  if (params.get("platform") && Object.hasOwn(PLATFORM_NAMES, params.get("platform"))) state.platform = params.get("platform");
  return params.get("package") ?? "";
}

function writeHash() {
  const params = new URLSearchParams({ python: state.python, platform: state.platform });
  const pkg = $("search").value.trim();
  if (pkg) params.set("package", pkg);
  history.replaceState(null, "", `#${params}`);
}

function renderAll() {
  renderTabs();
  renderPlatforms();
  renderTrends();
  renderGaps();
  writeHash();
}

async function main() {
  const [data, hist, pyodide, agreement] = await Promise.all([
    fetch("data/coverage.json").then((r) => r.json()),
    fetch("data/history.json").then((r) => (r.ok ? r.json() : [])).catch(() => []),
    fetch("data/pyodide.json").then((r) => (r.ok ? r.json() : null)).catch(() => null),
    fetch("data/agreement.json").then((r) => (r.ok ? r.json() : null)).catch(() => null),
  ]);
  state.data = data;
  state.pyodide = pyodideReleases(pyodide);
  state.history = Array.isArray(hist) ? hist.filter((h) => h && typeof h === "object") : [];
  state.python = pythons()[0];
  const pkg = readHash();
  if (pkg) $("search").value = pkg; // before renderAll(), whose writeHash() would otherwise drop it from the URL
  renderMeta();
  renderAgreement(agreement);
  renderAll();
  if (pkg) renderLookup(pkg);

  $("python-tabs").addEventListener("click", (e) => {
    const b = e.target.closest("button[data-python]");
    if (!b) return;
    state.python = b.dataset.python;
    state.shown = PAGE;
    renderAll();
    renderLookup($("search").value);
  });
  $("platforms").addEventListener("click", (e) => {
    const b = e.target.closest("button[data-platform]");
    if (!b) return;
    state.platform = b.dataset.platform;
    state.shown = PAGE;
    renderAll();
    $("gaps-title").scrollIntoView({ behavior: "smooth", block: "start" });
  });
  $("more").addEventListener("click", () => {
    state.shown += PAGE;
    renderGaps();
  });
  $("search-form").addEventListener("submit", (e) => {
    e.preventDefault();
    renderLookup($("search").value);
    writeHash();
  });
  $("search").addEventListener("change", () => {
    renderLookup($("search").value);
    writeHash();
  });
}

main().catch((err) => {
  $("meta").textContent = `Couldn't load the data (${err.message}).`;
});
