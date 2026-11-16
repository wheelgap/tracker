"use strict";
// Design: "Precision UI" by Kunal Shah (@kunalkshah1), the winner of the tracker's redesign challenge.
// Uses the helpers in logic.js (isPackageSpec, splitSpec, installable, platformStats, bundledDetail, ...), loaded before
// this file. The build lab section is site/lab.js.

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

function formatDownloads(n) {
  if (typeof n !== "number" || !Number.isFinite(n)) return "";
  if (n >= 1e9) return (n / 1e9).toFixed(1) + "B";
  if (n >= 1e6) return (n / 1e6).toFixed(1) + "M";
  if (n >= 1e3) return (n / 1e3).toFixed(1) + "K";
  return String(n);
}

// Dual-cue status badge with inline SVG icon for accessibility & clarity
function statusBadge(status) {
  const s = STATUS_TEXT[status] ?? status;
  let icon = "";
  if (status === "wheel") {
    icon = `<svg class="status-icon" viewBox="0 0 16 16" fill="currentColor" aria-hidden="true"><path d="M6.5 12.5l-4-4 1.4-1.4L6.5 9.7l5.6-5.6 1.4 1.4z"/></svg>`;
  } else if (status === "bundled") {
    icon = `<svg class="status-icon" viewBox="0 0 16 16" stroke="currentColor" stroke-width="2" fill="none" stroke-linecap="round" aria-hidden="true"><path d="M8 2v12M2 8h12"/></svg>`;
  } else if (status === "source") {
    icon = `<svg class="status-icon" viewBox="0 0 16 16" stroke="currentColor" stroke-width="2" fill="none" aria-hidden="true"><circle cx="8" cy="8" r="3"/><path d="M8 2v2M8 12v2M2 8h2M12 8h2" stroke-width="1.5"/></svg>`;
  } else if (status === "missing") {
    icon = `<svg class="status-icon" viewBox="0 0 16 16" stroke="currentColor" stroke-width="2" stroke-linecap="round" aria-hidden="true"><path d="M4 4l8 8M12 4l-8 8"/></svg>`;
  } else {
    icon = `<svg class="status-icon" viewBox="0 0 16 16" fill="currentColor" aria-hidden="true"><circle cx="8" cy="8" r="3"/></svg>`;
  }
  return `<span class="status s-${esc(status)}">${icon}<span>${esc(s)}</span></span>`;
}

// pyodide: data/pyodide.json through pyodideReleases(), {python: {pyodide, lock}} for each pinned Pyodide release.
const bundledCache = new Map(); // canonical name -> bundledInfo() of its data/packages/<name>.json
const state = {
  data: null,
  history: [],
  pyodide: {},
  changes: [],
  python: null,
  platform: "musl-aarch64",
  shown: PAGE,
  currentPage: 1,
  pageSize: 25,
  filterStatus: "all",
  filterQuery: "",
  sortCol: "rank",
  sortDir: "asc",
  showCompiledOnly: false,
  expandedPlatform: null,
};

function pythons() {
  return [...new Set(state.data.targets.map((t) => t.python))];
}

// Whether the scan checked Pyodide's own builds for this Python (wheelgap --pyodide-lock): coverage.json lists them.
function pyodideLockChecked(python) {
  return Array.isArray(state.data?.pyodide_locks) && state.data.pyodide_locks.some((l) => l && l.python === python);
}

function platformDetail(t) {
  const release = t.platform === "pyodide" ? state.pyodide[t.python] : null;
  if (release) return `wasm32 · PyPI wheels and Pyodide ${release.pyodide}'s builds`;
  // data/pyodide.json didn't load, but the counts still include Pyodide's builds
  if (t.platform === "pyodide" && (pyodideLockChecked(t.python) || (state.data.summary?.[t.label]?.bundled ?? 0) > 0)) {
    return "wasm32 · PyPI wheels and Pyodide's builds";
  }
  return platformName(t.platform)[1];
}

function targetIndex(python, platform) {
  return state.data.targets.findIndex((t) => t.python === python && t.platform === platform);
}

// 100% Dynamic KPI Calculation
function renderKPIs() {
  const d = state.data;
  const n = d.packages.length;
  const pure = d.packages.filter((p) => p.pure).length;
  const compiled = n - pure;
  const purePct = ((100 * pure) / n).toFixed(1);

  if ($("kpi-packages")) $("kpi-packages").textContent = fmt(n);
  if ($("kpi-targets")) $("kpi-targets").textContent = fmt(d.targets.length);
  const nPlatforms = new Set(d.targets.map((t) => t.platform)).size;
  if ($("kpi-targets-sub")) $("kpi-targets-sub").textContent = `${nPlatforms} platforms across ${pythons().length} Pythons`;
  if ($("kpi-pure")) $("kpi-pure").textContent = fmt(pure);
  if ($("kpi-pure-pct")) $("kpi-pure-pct").textContent = `${purePct}% · ship a pure Python wheel`;
  if ($("kpi-compiled")) $("kpi-compiled").textContent = fmt(compiled);
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
    `${fmt(pure)} of the ${fmt(d.packages.length)} ship a pure Python wheel; ` +
    `the gaps are among the ${fmt(d.packages.length - pure)} with compiled code.`;
  const anyBundled = Object.values(d.summary ?? {}).some((s) => s && s.bundled > 0);
  const footer = pyodideFooter(state.pyodide, anyBundled);
  if (footer) {
    $("legend-bundled").hidden = false;
    $("pyodide-note").textContent = footer;
  }
  if ($("package-names")) $("package-names").innerHTML = "";
}

// data/agreement.json cross-check
const AGREEMENT_MAX_AGE_DAYS = 3;
const isWholeCount = (n) => Number.isInteger(n) && n >= 0;
const rateText = (matched, checks) => (matched === checks ? "100" : (Math.floor((1000 * matched) / checks) / 10).toFixed(1));

function agreementText(a, now = Date.now()) {
  if (!a || typeof a !== "object") return null;
  const { checks, matched, compiled, skipped, errors } = a;
  if (![checks, matched, skipped, errors].every(isWholeCount) || checks === 0 || matched > checks) return null;
  if (typeof a.date !== "string" || !/^\d{4}-\d{2}-\d{2}$/.test(a.date)) return null;
  const age = (now - Date.parse(`${a.date}T00:00:00Z`)) / 86400000;
  if (!(age >= -1 && age <= AGREEMENT_MAX_AGE_DAYS)) return null;
  let text =
    `Cross-checked against pip on ${a.date}, with wheelgap's own tag lists on both sides: ` +
    `they agree on ${rateText(matched, checks)}% of ${fmt(checks)} checks`;
  if (compiled && typeof compiled === "object" && [compiled.packages, compiled.checks, compiled.matched].every(isWholeCount) &&
      compiled.checks > 0 && compiled.matched <= compiled.checks) {
    text +=
      `, and on ${rateText(compiled.matched, compiled.checks)}% of the ${fmt(compiled.checks)} for the ` +
      `${fmt(compiled.packages)} packages without a pure-Python wheel (the rest ship one)`;
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

// The night before the latest scan in data/history.json, or null.
function previousScan() {
  const h = state.history;
  return h.length > 1 ? h[h.length - 2] : null;
}

// Sparklines helper
function coverageSeries(label) {
  return state.history
    .filter((h) => h.summary && h.summary[label] && Number.isFinite(h.summary[label].wheel) && h.packages)
    .map((h) => ({ date: h.date, value: (100 * h.summary[label].wheel) / h.packages }));
}

function sparkline(points, name) {
  const W = 160, H = 36, PAD = 4;
  const values = points.map((p) => p.value);
  let lo = Math.min(...values), hi = Math.max(...values);
  if (hi - lo < 0.5) {
    const mid = (hi + lo) / 2;
    lo = mid - 0.25;
    hi = mid + 0.25;
  }
  const x = (i) => PAD + (i * (W - 2 * PAD)) / (points.length - 1);
  const y = (v) => H - PAD - ((v - lo) * (H - 2 * PAD)) / (hi - lo);
  const path = points.map((p, i) => `${i ? "L" : "M"}${x(i).toFixed(1)},${y(p.value).toFixed(1)}`).join(" ");
  const shown = points.length <= 31 ? points.map((p, i) => [p, i]) : [[points[points.length - 1], points.length - 1]];
  const dots = shown
    .map(([p, i]) => `<circle cx="${x(i).toFixed(1)}" cy="${y(p.value).toFixed(1)}" r="2"><title>${esc(p.date)}: ${p.value.toFixed(1)}%</title></circle>`)
    .join("");
  const first = points[0], last = points[points.length - 1];
  const label = `${name}: ${first.value.toFixed(1)}% on ${first.date}, ${last.value.toFixed(1)}% on ${last.date}`;
  return `<svg class="spark" viewBox="0 0 ${W} ${H}" role="img" aria-label="${esc(label)}">
    <path d="${path}"></path>${dots}</svg>`;
}

function renderTrends() {
  // The sparklines are on the leaderboard cards; this is the note under them, saying what they plot.
  const dates = state.data.targets
    .filter((t) => t.python === state.python)
    .flatMap((t) => coverageSeries(t.label))
    .map((p) => p.date)
    .sort();
  const pyodideBundled = (state.data.summary?.[`${state.python} pyodide`]?.bundled ?? 0) > 0;
  $("trend-note").textContent = dates.length > 1 && !state.showCompiledOnly
    ? `Sparklines: the share of all packages with a wheel for Python ${state.python}, one point per nightly scan from ${dates[0]} to ${dates[dates.length - 1]}. Each is scaled to its own range.` +
      (pyodideBundled ? " On Pyodide that's PyPI wheels only: the percentages also count Pyodide's own builds." : "")
    : "";
}

// ============================================================================
// 1. INSTANT ANSWER OMNI-CHECKER (Zero Eye-Darting Question Answerer)
// "Does numpy have a wheel for Alpine ARM on Python 3.13?" in < 2 seconds
// ============================================================================

function populateOmniSelects() {
  const pySelect = $("omni-python-select");
  const plSelect = $("omni-platform-select");
  if (!pySelect || !plSelect) return;

  const pys = pythons();
  const platforms = Object.keys(PLATFORM_NAMES).filter((pl) => state.data.targets.some((t) => t.platform === pl));

  pySelect.innerHTML = pys.map((py) => `<option value="${esc(py)}"${py === state.python ? " selected" : ""}>Python ${esc(py)}</option>`).join("");
  plSelect.innerHTML = platforms.map((pl) => {
    const [name, desc] = platformName(pl);
    const shortAbi = desc ? desc.split(" · ")[0] : "";
    const label = shortAbi ? `${name} (${shortAbi})` : name;
    return `<option value="${esc(pl)}"${pl === state.platform ? " selected" : ""}>${esc(label)}</option>`;
  }).join("");
}

function renderOmniAnswer() {
  const container = $("omni-answer");
  const miniContainer = $("omni-minimized-summary");
  if (!container) return;

  const pkgName = $("search").value.trim();
  const py = $("omni-python-select")?.value || state.python;
  const pl = $("omni-platform-select")?.value || state.platform;
  const [pname] = platformName(pl);

  if (!pkgName) {
    container.innerHTML = `
      <div class="verdict-bar verdict-placeholder">
        <span class="placeholder-icon">⚡</span>
        <span class="placeholder-text">Type a package or pick a quick check above for instant one-line compatibility.</span>
      </div>`;
    if (miniContainer) {
      miniContainer.innerHTML = `<span class="mini-summary-text text-muted">No package checked. Click Expand to search.</span>`;
    }
    return;
  }

  const [base, pinned] = splitSpec(pkgName);
  const q = canonicalName(base);
  const p = state.data.packages.find((x) => canonicalName(x.name) === q);

  if (!p && !isPackageSpec(pkgName)) {
    // Never echo arbitrary text (it can come from a shared link) or put it in a command someone might paste.
    container.innerHTML = `
      <div class="verdict-bar verdict-notfound">
        <div class="verdict-text-block">
          <span class="verdict-summary">That isn't a valid package name.</span>
        </div>
      </div>`;
    if (miniContainer) miniContainer.innerHTML = `<span class="mini-summary-text text-muted">That isn't a valid package name.</span>`;
    return;
  }

  if (!p) {
    const cliFallback = `uvx wheelgap ${pkgName} -p ${py} -t ${pl} --explain`;
    container.innerHTML = `
      <div class="verdict-bar verdict-notfound">
        <div class="verdict-pill-wrap">
          <span class="verdict-status-pill badge-missing">Not in Top ${fmt(state.data.packages.length)}</span>
        </div>
        <div class="verdict-text-block">
          <span class="verdict-summary"><strong>${esc(base)}</strong> isn't in the top ${fmt(state.data.packages.length)} PyPI packages the scan checks. Check it, or any package at a pinned version, locally: <code>${esc(cliFallback)}</code></span>
        </div>
        <div class="verdict-bar-actions">
          <button type="button" class="vbtn vbtn-repro btn-copy-repro" data-cmd="${esc(cliFallback)}">Copy CLI</button>
          <a href="https://pypi.org/search/?q=${encodeURIComponent(base)}" target="_blank" rel="noopener" class="vbtn vbtn-ext">Search PyPI ↗</a>
        </div>
      </div>`;
    if (miniContainer) {
      miniContainer.innerHTML = `
        <span class="mini-summary-pill badge-missing">Not in Top ${fmt(state.data.packages.length)}</span>
        <span class="mini-summary-text"><strong>${esc(base)}</strong></span>
        <div class="mini-summary-actions">
          <a href="https://pypi.org/search/?q=${encodeURIComponent(base)}" target="_blank" rel="noopener" class="vbtn vbtn-ext mini-btn">Search PyPI ↗</a>
        </div>`;
    }
    return;
  }

  const idx = targetIndex(py, pl);
  if (idx < 0) {
    container.innerHTML = `
      <div class="verdict-bar verdict-na">
        <span class="verdict-status-pill badge-unknown">Not Scanned</span>
        <div class="verdict-text-block">
          <span class="verdict-summary"><strong>${esc(p.name)}</strong> on <strong>${esc(pname)}</strong> (Python ${esc(py)}) is not a targeted combination.</span>
        </div>
      </div>`;
    if (miniContainer) {
      miniContainer.innerHTML = `
        <span class="mini-summary-pill badge-unknown">Not Scanned</span>
        <span class="mini-summary-text"><strong>${esc(p.name)}</strong> on ${esc(pname)} (${esc(py)})</span>`;
    }
    return;
  }

  const res = p.results[idx];
  let badgeClass = "badge-wheel";
  let badgeLabel = "✓ Prebuilt Wheel";
  let cardClass = "verdict-wheel";
  let summary = "";

  if (res === "wheel") {
    cardClass = "verdict-wheel";
    badgeClass = "badge-wheel";
    badgeLabel = "✓ Prebuilt Wheel";
    summary = `has a prebuilt wheel for <strong>${esc(pname)}</strong> on <strong>Python ${esc(py)}</strong>`;
  } else if (res === "bundled") {
    cardClass = "verdict-bundled";
    badgeClass = "badge-bundled";
    badgeLabel = "📦 Bundled Pyodide";
    summary = `has no wheel on PyPI for Pyodide, but Pyodide's own distribution builds it for <strong>Python ${esc(py)}</strong>, often an older version than the one shown`;
  } else if (res === "source") {
    cardClass = "verdict-source";
    badgeClass = "badge-source";
    badgeLabel = "⚡ Builds from Source";
    summary = `has no wheel for <strong>${esc(pname)}</strong> (Python ${esc(py)}), so pip builds it from source: that needs a compiler and the package's build dependencies`;
  } else if (res === "missing") {
    cardClass = "verdict-missing";
    badgeClass = "badge-missing";
    badgeLabel = "✕ Cannot Install";
    // "No Pyodide build" only when the scan checked Pyodide's own builds for this Python.
    summary = pl === "pyodide"
      ? `has no wheel on PyPI for <strong>${esc(pname)}</strong> (Python ${esc(py)})${pyodideLockChecked(py) ? " and no Pyodide build, so it can't be installed there" : ""}`
      : `has no wheel for <strong>${esc(pname)}</strong> (Python ${esc(py)}) and no source distribution, so pip can't install it`;
  } else {
    cardClass = "verdict-unknown";
    badgeClass = "badge-unknown";
    badgeLabel = "? Unknown";
    summary = `status unknown for <strong>${esc(pname)}</strong> on <strong>Python ${esc(py)}</strong>`;
  }

  const cliCmd = whyCommand(p.name, p.version, py, pl, state.pyodide);
  // A pinned version the scan didn't check: the verdict is for the latest release, so say that.
  // The command only for a valid name==version (#57): the text can come from a shared link.
  const pinNote = pinned === null || pinned === p.version
    ? ""
    : isPackageSpec(pkgName)
      ? ` · The scan checks the latest release, ${esc(p.version ?? "")}, not ${esc(pinned)}: run <code>uvx wheelgap ${esc(pkgName)} -p ${esc(py)} -t ${esc(pl)} --explain</code> for that one.`
      : ` · The scan checks the latest release, ${esc(p.version ?? "")}.`;
  const pypiUrl = `https://pypi.org/project/${encodeURIComponent(p.name)}/${encodeURIComponent(p.version ?? "")}/`;

  container.innerHTML = `
    <div class="verdict-bar ${cardClass}">
      <div class="verdict-pill-wrap">
        <span class="verdict-status-pill ${badgeClass}">${badgeLabel}</span>
      </div>
      <div class="verdict-text-block">
        <strong class="verdict-pkg-title">${esc(p.name)} <small>${esc(p.version ?? "")}</small></strong>
        <span class="verdict-summary">${summary}${pinNote}</span>
        <span class="verdict-meta-inline">${p.pure ? "Pure Python" : "Compiled"} · #${esc(p.rank)} · ${formatDownloads(p.downloads)} dl/mo</span>
      </div>
      <div class="verdict-bar-actions">
        <a href="${pypiUrl}" target="_blank" rel="noopener" class="vbtn vbtn-ext" title="View release on PyPI">PyPI ↗</a>
        <button type="button" class="vbtn vbtn-drawer" data-pkg="${esc(p.name)}" title="Inspect all ${fmt(state.data.targets.length)} platform targets in side drawer">Inspect ${fmt(state.data.targets.length)} Targets ⤢</button>
        <button type="button" class="vbtn vbtn-filter" data-pl="${esc(pl)}" data-py="${esc(py)}" title="Filter gap packages on this platform below">Filter Gaps ↓</button>
        <button type="button" class="vbtn vbtn-repro btn-copy-repro" data-cmd="${esc(cliCmd)}" title="Copy CLI reproduction command">Copy CLI</button>
      </div>
    </div>`;

  if (miniContainer) {
    miniContainer.innerHTML = `
      <span class="mini-summary-pill ${badgeClass}">${badgeLabel}</span>
      <span class="mini-summary-text"><strong>${esc(p.name)}</strong> on <strong>${esc(pname)}</strong> (Python ${esc(py)})</span>
      <div class="mini-summary-actions">
        <button type="button" class="vbtn vbtn-drawer mini-btn" data-pkg="${esc(p.name)}" title="Inspect all ${fmt(state.data.targets.length)} platform targets in side drawer">Inspect ${fmt(state.data.targets.length)} ⤢</button>
        <button type="button" class="vbtn vbtn-filter mini-btn" data-pl="${esc(pl)}" data-py="${esc(py)}" title="Filter gap packages on this platform below">Filter Gaps ↓</button>
        <a href="${pypiUrl}" target="_blank" rel="noopener" class="vbtn vbtn-ext mini-btn" title="View release on PyPI">PyPI ↗</a>
      </div>`;
  }
}

// ============================================================================
// 2. ECOSYSTEM HEALTH HUB & WORST-SERVED LEADERBOARD
// "See at a glance which platforms are worst served"
// ============================================================================

function renderLeaderboard() {
  const container = $("platform-leaderboard");
  if (!container) return;

  const platforms = Object.keys(PLATFORM_NAMES).filter((pl) => state.data.targets.some((t) => t.platform === pl));
  const pool = state.showCompiledOnly ? state.data.packages.filter((p) => !p.pure) : state.data.packages;
  const total = pool.length;

  // Gather stats for all platforms for the active Python version
  const list = [];
  for (const pl of platforms) {
    const [name] = platformName(pl);
    const idx = targetIndex(state.python, pl);
    if (idx < 0) continue;
    const desc = platformDetail(state.data.targets[idx]);

    let wheel = 0, bundled = 0, source = 0, missing = 0, unknown = 0;
    for (const p of pool) {
      const res = p.results[idx];
      if (res === "wheel") wheel++;
      else if (res === "bundled") bundled++;
      else if (res === "source") source++;
      else if (res === "missing") missing++;
      else unknown++;
    }

    const ok = wheel + bundled;
    const gaps = total - ok;
    const percent = total ? (100 * ok) / total : 0;
    const widths = {
      wheel: total ? (100 * wheel) / total : 0,
      bundled: total ? (100 * bundled) / total : 0,
      source: total ? (100 * source) / total : 0,
      missing: total ? (100 * missing) / total : 0,
      unknown: total ? (100 * unknown) / total : 0,
    };

    // Change since the previous scan: only over all packages (history has no compiled-only counts), and not across a
    // wheelgap upgrade (platformStats leaves it at 0 then), so the number is like for like.
    const targetLabel = state.data.targets[idx].label;
    const prev = previousScan();
    const delta = state.showCompiledOnly
      ? 0
      : platformStats(state.data.summary[targetLabel], prev?.summary?.[targetLabel] ?? null, total, prev?.wheelgap, state.data.wheelgap).delta;

    const series = coverageSeries(targetLabel);

    list.push({
      platform: pl,
      name,
      desc,
      wheel,
      bundled,
      source,
      missing,
      unknown,
      ok,
      gaps,
      percent,
      widths,
      delta,
      series,
    });
  }

  // Sort: WORST SERVED FIRST (lowest percent / highest gaps)
  list.sort((a, b) => a.percent - b.percent || b.gaps - a.gaps);

  // Render cards. The tag compares each platform's gaps with the best-served platform's for this Python, so it says
  // how far behind a platform is, not just where it ranks.
  const fewest = Math.max(1, Math.min(...list.map((x) => x.gaps)));
  const cardsHtml = list.map((item, index) => {
    const rank = index + 1;
    const isSelected = item.platform === state.platform;
    const selectedClass = isSelected ? " active-platform-card" : "";

    const behind = item.gaps / fewest;
    let severityClass = "sev-good";
    let severityTag = `✅ #${rank} · Close to the best`;
    if (behind >= 2) {
      severityClass = "sev-critical";
      severityTag = rank === 1 ? `🚨 #1 · Worst Served` : `🚨 #${rank} · ${behind.toFixed(1)}× the gaps`;
    } else if (behind >= 1.3) {
      severityClass = "sev-warning";
      severityTag = `⚠️ #${rank} · ${behind.toFixed(1)}× the gaps`;
    }

    const deltaBadge = item.delta
      ? `<span class="board-delta ${item.delta > 0 ? "delta-up" : "delta-down"}">${item.delta > 0 ? "+" : ""}${item.delta} this scan</span>`
      : "";

    // History has all packages' coverage only, so the compiled-only view shows no sparkline rather than a different number.
    const sparkHtml = !state.showCompiledOnly && item.series.length >= 2 ? sparkline(item.series, item.name) : "";

    const countsTitle = `${fmt(item.wheel)} wheel · ${item.bundled ? fmt(item.bundled) + " bundled · " : ""}${fmt(item.source)} source · ${fmt(item.missing)} missing`;

    return `
      <div class="leaderboard-card ${severityClass}${selectedClass}" data-pl="${esc(item.platform)}" tabindex="0" role="button" aria-pressed="${isSelected}">
        <div class="card-top-row">
          <span class="board-rank-pill">${severityTag}</span>
          ${isSelected ? `<span class="active-indicator-pill">Active Target</span>` : ""}
        </div>

        <div class="board-platform-meta">
          <h3 class="board-pname">${esc(item.name)}</h3>
          <span class="board-pdesc">${esc(item.desc)}</span>
        </div>

        <div class="board-stat-row">
          <div class="board-pct-block">
            <span class="board-pct">${item.percent.toFixed(1)}%</span>
            <span class="board-pct-sub">prebuilt coverage</span>
          </div>
          <div class="board-gaps-block">
            <span class="board-gaps">${fmt(item.gaps)}</span>
            <span class="board-gaps-sub">gap packages</span>
          </div>
        </div>

        <!-- Segmented bar -->
        <div class="bar board-bar" title="${esc(countsTitle)}" aria-hidden="true">
          <span class="b-wheel" style="width:${item.widths.wheel.toFixed(1)}%"></span>
          <span class="b-bundled" style="width:${item.widths.bundled.toFixed(1)}%"></span>
          <span class="b-source" style="width:${item.widths.source.toFixed(1)}%"></span>
          <span class="b-missing" style="width:${item.widths.missing.toFixed(1)}%"></span>
        </div>

        <div class="board-footer-row">
          <div class="board-spark-wrap">${sparkHtml}</div>
          ${deltaBadge}
        </div>
      </div>`;
  }).join("");

  container.innerHTML = cardsHtml;
}

function scrollToGaps() {
  $("gaps-title")?.scrollIntoView({ behavior: "smooth", block: "start" });
}

// ============================================================================
// PACKAGE EXPLORER & 2026 ENTERPRISE PAGINATION
// ============================================================================

function currentGapsList() {
  const i = targetIndex(state.python, state.platform);
  if (i < 0) return [];
  let list = gapsAt(state.data.packages, i);

  if (state.filterStatus === "source") {
    list = list.filter((p) => p.results[i] === "source");
  } else if (state.filterStatus === "missing") {
    list = list.filter((p) => p.results[i] === "missing");
  }

  if (state.filterQuery) {
    const q = state.filterQuery.toLowerCase();
    list = list.filter((p) => p.name.toLowerCase().includes(q));
  }

  list.sort((a, b) => {
    let res = 0;
    if (state.sortCol === "rank") {
      res = a.rank - b.rank;
    } else if (state.sortCol === "name") {
      res = a.name.localeCompare(b.name);
    } else if (state.sortCol === "downloads") {
      res = (b.downloads ?? 0) - (a.downloads ?? 0);
    } else if (state.sortCol === "status") {
      const sa = a.results[i] ?? "";
      const sb = b.results[i] ?? "";
      res = sa.localeCompare(sb);
    }
    return state.sortDir === "asc" ? res : -res;
  });

  return list;
}

function renderPaginationControls(totalItems) {
  const pageSize = state.pageSize === "all" ? totalItems : Number(state.pageSize);
  const totalPages = Math.max(1, Math.ceil(totalItems / pageSize));
  if (state.currentPage > totalPages) state.currentPage = totalPages;

  const start = totalItems === 0 ? 0 : (state.currentPage - 1) * pageSize + 1;
  const end = state.pageSize === "all" ? totalItems : Math.min(totalItems, state.currentPage * pageSize);
  const infoText = `Showing ${fmt(start)}–${fmt(end)} of ${fmt(totalItems)} packages`;

  if ($("pagination-info-top")) $("pagination-info-top").textContent = infoText;
  if ($("pagination-info-bottom")) $("pagination-info-bottom").textContent = infoText;

  // Build pagination buttons
  const buildButtons = () => {
    if (totalPages <= 1) return "";
    let btns = [];

    // First & Prev
    btns.push(`<button type="button" class="page-btn" data-page="1" ${state.currentPage === 1 ? "disabled" : ""} title="First page">⏮</button>`);
    btns.push(`<button type="button" class="page-btn" data-page="${state.currentPage - 1}" ${state.currentPage === 1 ? "disabled" : ""} title="Previous page">◀</button>`);

    // Visible window of page pills
    let pages = [];
    if (totalPages <= 7) {
      pages = Array.from({ length: totalPages }, (_, i) => i + 1);
    } else {
      if (state.currentPage <= 4) {
        pages = [1, 2, 3, 4, 5, "…", totalPages];
      } else if (state.currentPage >= totalPages - 3) {
        pages = [1, "…", totalPages - 4, totalPages - 3, totalPages - 2, totalPages - 1, totalPages];
      } else {
        pages = [1, "…", state.currentPage - 1, state.currentPage, state.currentPage + 1, "…", totalPages];
      }
    }

    for (const p of pages) {
      if (p === "…") {
        btns.push(`<span class="page-ellipsis">…</span>`);
      } else {
        const isActive = p === state.currentPage;
        btns.push(`<button type="button" class="page-btn${isActive ? " active" : ""}" data-page="${p}" aria-current="${isActive ? "page" : "false"}">${p}</button>`);
      }
    }

    // Next & Last
    btns.push(`<button type="button" class="page-btn" data-page="${state.currentPage + 1}" ${state.currentPage === totalPages ? "disabled" : ""} title="Next page">▶</button>`);
    btns.push(`<button type="button" class="page-btn" data-page="${totalPages}" ${state.currentPage === totalPages ? "disabled" : ""} title="Last page">⏭</button>`);

    return btns.join("");
  };

  const buttonsHtml = buildButtons();
  if ($("pagination-buttons-top")) $("pagination-buttons-top").innerHTML = buttonsHtml;
  if ($("pagination-buttons-bottom")) $("pagination-buttons-bottom").innerHTML = buttonsHtml;

  // Sync size selects
  if ($("page-size-top")) $("page-size-top").value = String(state.pageSize);
  if ($("page-size-bottom")) $("page-size-bottom").value = String(state.pageSize);
}

function renderGaps() {
  const i = targetIndex(state.python, state.platform);
  const [name] = platformName(state.platform);
  $("gaps-platform").textContent = `${name}, Python ${state.python}`;

  if (i < 0) {
    $("gaps").innerHTML = `<tr><td colspan="5" class="empty" style="padding:18px;text-align:center;color:var(--text-muted)">Not scanned for Python ${esc(state.python)} (no Pyodide build for this version).</td></tr>`;
    renderPaginationControls(0);
    return;
  }

  const gaps = currentGapsList();
  const total = gaps.length;
  renderPaginationControls(total);

  if (!total) {
    const msg = state.filterQuery
      ? `No matching gap packages for "${esc(state.filterQuery)}".`
      : state.filterStatus !== "all"
        ? `No packages ${state.filterStatus === "missing" ? "that can't be installed" : "that build from source"} here.`
        : "Every package has a prebuilt wheel for this platform.";
    $("gaps").innerHTML = `<tr><td colspan="5" class="empty" style="padding:18px;text-align:center;color:var(--text-muted)">${msg}</td></tr>`;
    return;
  }

  const pageSize = state.pageSize === "all" ? total : Number(state.pageSize);
  const startIdx = (state.currentPage - 1) * pageSize;
  const visible = gaps.slice(startIdx, startIdx + pageSize);

  const rows = visible.map((p) => {
    const s = p.results[i];
    const pureBadge = p.pure ? ` <span class="status s-wheel" style="font-size:0.68rem;padding:1px 6px">pure</span>` : "";
    const isExpanded = state.expandedPkg === p.name;
    const cliCmd = whyCommand(p.name, p.version, state.python, state.platform, state.pyodide);
    const pypiUrl = `https://pypi.org/project/${encodeURIComponent(p.name)}/${encodeURIComponent(p.version ?? "")}/`;

    let html = `<tr class="gap-row ${isExpanded ? "row-expanded" : ""}" data-pkg="${esc(p.name)}">
      <td class="pkg-rank">#${esc(p.rank)}</td>
      <td>
        <a href="#package=${encodeURIComponent(p.name)}" class="pkg-name-link" data-pkg="${esc(p.name)}" title="Inspect compatibility">${esc(p.name)}</a>
        <span class="pkg-version">${esc(p.version ?? "")}</span>${pureBadge}
      </td>
      <td>${statusBadge(s)}</td>
      <td style="text-align:right" class="pkg-downloads" title="${esc(fmt(p.downloads))} downloads">${formatDownloads(p.downloads)}</td>
      <td style="text-align:right;white-space:nowrap">
        <a href="${pypiUrl}" target="_blank" rel="noopener" class="table-ext-link" title="Open package release on PyPI">PyPI ↗</a>
        <button type="button" class="row-expand-btn" data-pkg="${esc(p.name)}" aria-label="Toggle details">${isExpanded ? "▲" : "▼"}</button>
      </td>
    </tr>`;

    if (isExpanded) {
      html += `<tr class="gap-inline-row">
        <td colspan="5">
          <div class="inline-compat-card">
            <div class="inline-compat-head">
              <strong>${esc(p.name)}</strong> compatibility summary:
            </div>
            <div class="inline-targets-pills">
              <span class="inline-target-pill ${s === "missing" ? "pill-missing" : s === "source" ? "pill-source" : ""}">This Platform (${esc(state.platform)}): ${s === "missing" ? "✕ cannot install" : s === "source" ? "⚡ builds from source" : "? unknown (the lookup failed)"}</span>
              <span class="inline-target-pill pill-wheel">Pure Python wheel: ${p.pure ? "yes" : "no (compiled code)"}</span>
              <span class="inline-target-pill pill-wheel">Rank: #${esc(p.rank)}</span>
            </div>
            <div class="inline-actions-row">
              <code class="inline-cli-cmd">${esc(cliCmd)}</code>
              <div class="inline-btns">
                <button type="button" class="vbtn vbtn-repro btn-copy-repro" data-cmd="${esc(cliCmd)}">Copy CLI</button>
                <a href="${pypiUrl}" target="_blank" rel="noopener" class="vbtn vbtn-ext">PyPI ↗</a>
                <button type="button" class="vbtn vbtn-drawer" data-pkg="${esc(p.name)}">All ${fmt(state.data.targets.length)} Targets ⤢</button>
              </div>
            </div>
          </div>
        </td>
      </tr>`;
    }

    return html;
  });

  $("gaps").innerHTML = rows.join("");
}

// Ecosystem Pulse (Recent Changes Feed)
function renderPulse() {
  const container = $("pulse-feed");
  if (!container) return;

  if (!Array.isArray(state.changes) || !state.changes.length) {
    container.innerHTML = `<li class="feed-placeholder" style="color:var(--text-muted);padding:14px">No wheel changes recorded yet.</li>`;
    return;
  }

  // Newest scans first; within a scan, wheels lost before wheels gained (losses are rarer, and the ones to act on).
  // Up to 15, but every loss in that window is kept rather than crowded out by gains.
  const items = [];
  for (let idx = state.changes.length - 1; idx >= 0 && items.length < 15; idx--) {
    const day = state.changes[idx];
    if (!day || typeof day !== "object") continue;
    const lost = (Array.isArray(day.lost) ? day.lost : []).map((l) => ({ type: "loss", ...l, date: day.date }));
    const gained = (Array.isArray(day.gained) ? day.gained : []).map((g) => ({ type: "gain", ...g, date: day.date }));
    items.push(...lost, ...gained.slice(0, Math.max(0, 15 - items.length - lost.length)));
  }

  if (!items.length) {
    container.innerHTML = `<li class="feed-placeholder" style="color:var(--text-muted);padding:14px">No wheel transitions in recent scans.</li>`;
    return;
  }

  container.innerHTML = items
    .map((item) => {
      const isGain = item.type === "gain";
      const icon = isGain ? "✓" : "✕";
      const iconClass = isGain ? "icon-gain" : "icon-loss";
      const actionText = isGain ? "gained wheel" : "lost wheel";
      return `<li>
        <span class="pulse-icon ${iconClass}" aria-hidden="true">${icon}</span>
        <div class="pulse-content">
          <a href="#package=${encodeURIComponent(item.name)}" class="pulse-pkg" data-pkg="${esc(item.name)}">${esc(item.name)}</a>
          <span>${actionText}</span>
          <small class="pulse-meta">${esc(item.target)} · ${esc(item.date)}</small>
        </div>
      </li>`;
    })
    .join("");
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

// Deep Package Inspection (used in drawer and card)
function buildPackageCardHTML(p, extra = null, pinNote = "") {
  const pys = pythons();
  const platforms = Object.keys(PLATFORM_NAMES).filter((pl) => state.data.targets.some((t) => t.platform === pl));
  const cell = (py, pl) => {
    const i = targetIndex(py, pl);
    if (i < 0) return `<td><span class="status s-none">n/a</span></td>`;
    const s = p.results[i];
    const detail = s === "bundled" && extra ? bundledDetail(extra[state.data.targets[i].label]) : "";
    const small = detail ? `<small class="bver" style="display:block;margin-top:2px;font-size:0.72rem;color:var(--text-muted)">${esc(detail)}</small>` : "";
    return `<td>${statusBadge(s)}${small}</td>`;
  };

  return `<div class="card">
    <div style="display:flex;align-items:baseline;justify-content:space-between;flex-wrap:wrap;gap:8px">
      <h3>${esc(p.name)} <span class="ver" style="font-weight:normal;color:var(--text-muted);font-size:0.9rem">${esc(p.version ?? "")}</span></h3>
      <span style="font:600 0.8rem var(--mono);color:var(--text-muted)">#${esc(p.rank)} · ${formatDownloads(p.downloads)} downloads</span>
    </div>${pinNote}
    <p class="sub">${p.pure ? "Ships a pure Python wheel." : "Contains compiled extensions."}${p.error ? ` · lookup error: ${esc(p.error)}` : ""}
      · <a href="https://pypi.org/project/${encodeURIComponent(p.name)}/${encodeURIComponent(p.version ?? "")}/" target="_blank" rel="noopener">PyPI release ↗</a>
      · <a href="p/${esc(canonicalName(p.name))}/">Package page ↗</a>
    </p>
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
      <p class="sub" style="margin:0 0 6px">Badge for your repository README</p>
      <pre class="badge-md"><code>${esc(badgeMarkdown(p))}</code></pre>
      <button type="button" class="copy-badge" data-md="${esc(badgeMarkdown(p))}">Copy badge</button>
    </div>
  </div>`;
}

function renderLookup(query, extra = null) {
  const [base, pinned] = splitSpec(query);
  const q = canonicalName(base);
  if (!q) {
    $("lookup").innerHTML = "";
    return;
  }
  const p = state.data.packages.find((x) => canonicalName(x.name) === q);
  if (!p) {
    const name = query.trim();
    $("lookup").innerHTML = isPackageSpec(name)
      ? `<div class="card"><p class="sub">${esc(base.trim())} isn't in the top ${fmt(state.data.packages.length)}.
      Check it, or any package at a pinned version, locally: <code>uvx wheelgap ${esc(name)} -t all</code></p></div>`
      : `<div class="card"><p class="sub">That isn't a valid package name.</p></div>`; // don't echo arbitrary URL text
    return;
  }
  // A pinned version the scan didn't check: say which release the card is for, and how to check the pinned one.
  const spec = query.trim();
  const pinNote = pinned !== null && pinned !== p.version && isPackageSpec(spec)
    ? `<p class="sub">The scan checks the latest release, ${esc(p.version ?? "")}, not ${esc(pinned)}. Check that one locally:
      <code>uvx wheelgap ${esc(spec)} -t all</code></p>`
    : "";

  $("lookup").innerHTML = buildPackageCardHTML(p, extra, pinNote);


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
      .catch(() => {});
  }
}

// Deep Inspection Drawer
function openDrawer(packageName) {
  const q = canonicalName(packageName.trim());
  const p = state.data.packages.find((x) => canonicalName(x.name) === q);
  if (!p) return;

  $("drawer-title").textContent = p.name;
  $("drawer-subtitle").textContent = `#${fmt(p.rank)} by downloads · ${formatDownloads(p.downloads)} dl/mo`;
  $("drawer-content").innerHTML = buildPackageCardHTML(p, null);


  if ($("drawer").hidden) drawerReturnFocus = document.activeElement;
  $("drawer").hidden = false;
  $("drawer-backdrop").hidden = false;
  document.body.style.overflow = "hidden";
  $("drawer-close")?.focus();

  if (!p.error && p.results.includes("bundled")) {
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
        if ($("drawer-title").textContent === p.name) {
          $("drawer-content").innerHTML = buildPackageCardHTML(p, info);
        }
      })
      .catch(() => {});
  }
}

let drawerReturnFocus = null;

function closeDrawer() {
  $("drawer").hidden = true;
  $("drawer-backdrop").hidden = true;
  document.body.style.overflow = "";
  drawerReturnFocus?.focus?.();
  drawerReturnFocus = null;
}

// Export System
function exportCSV() {
  const gaps = currentGapsList();
  const headers = ["Rank", "Package", "Version", "Status", "Downloads", "Platform", "Python"];
  const rows = gaps.map((p) => {
    const i = targetIndex(state.python, state.platform);
    return [
      p.rank,
      `"${p.name.replace(/"/g, '""')}"`,
      `"${(p.version ?? "").replace(/"/g, '""')}"`,
      p.results[i] ?? "",
      p.downloads ?? 0,
      state.platform,
      state.python,
    ].join(",");
  });
  const csvContent = "data:text/csv;charset=utf-8," + encodeURIComponent([headers.join(","), ...rows].join("\n"));
  const a = document.createElement("a");
  a.href = csvContent;
  a.download = `wheelgap-${state.platform}-py${state.python}-gaps.csv`;
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
}

function exportJSON() {
  const gaps = currentGapsList();
  const i = targetIndex(state.python, state.platform);
  const payload = {
    target: { python: state.python, platform: state.platform },
    exported_at: new Date().toISOString(),
    count: gaps.length,
    packages: gaps.map((p) => ({
      rank: p.rank,
      name: p.name,
      version: p.version,
      status: p.results[i] ?? "unknown",
      downloads: p.downloads,
    })),
  };
  const jsonContent = "data:application/json;charset=utf-8," + encodeURIComponent(JSON.stringify(payload, null, 2));
  const a = document.createElement("a");
  a.href = jsonContent;
  a.download = `wheelgap-${state.platform}-py${state.python}-gaps.json`;
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
}

// Command Palette (Cmd+K)
function openCommandPalette() {
  $("cmd-overlay").hidden = false;
  $("cmd-input").value = "";
  $("cmd-input").focus();
  renderCmdResults("");
}

function closeCommandPalette() {
  $("cmd-overlay").hidden = true;
}

function renderCmdResults(query) {
  const container = $("cmd-results");
  const q = query.trim().toLowerCase();
  let matches = [];
  if (!q) {
    matches = state.data.packages.slice(0, 10);
  } else {
    matches = state.data.packages.filter((p) => p.name.toLowerCase().includes(q)).slice(0, 12);
  }

  if (!matches.length) {
    container.innerHTML = `<div style="padding:14px;text-align:center;color:var(--text-muted);font-size:0.86rem">No packages match "${esc(query)}".</div>`;
    return;
  }

  const i = targetIndex(state.python, state.platform);
  container.innerHTML = matches
    .map((p, idx) => {
      const s = i >= 0 ? p.results[i] : "unknown";
      return `<div class="cmd-result-item ${idx === 0 ? "selected" : ""}" data-pkg="${esc(p.name)}" role="option">
        <span class="cmd-res-rank">#${esc(p.rank)}</span>
        <span class="cmd-res-name">${esc(p.name)} <small style="color:var(--text-faint)">${esc(p.version ?? "")}</small></span>
        ${statusBadge(s)}
      </div>`;
    })
    .join("");
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
  populateOmniSelects();
  renderOmniAnswer();
  renderLeaderboard();
  renderTrends();
  renderGaps();
  renderPulse();
  writeHash();
}

// Autocomplete Menu for Look Up input (top 8 matches, lightweight & fast)
function updateSearchAutocomplete(value) {
  const menu = $("search-autocomplete");
  if (!menu) return;
  const q = value.trim().toLowerCase();
  if (!q || q.length < 1) {
    menu.hidden = true;
    return;
  }
  const matches = state.data.packages
    .filter((p) => p.name.toLowerCase().includes(q))
    .sort((a, b) => {
      const aStarts = a.name.toLowerCase().startsWith(q);
      const bStarts = b.name.toLowerCase().startsWith(q);
      if (aStarts && !bStarts) return -1;
      if (!aStarts && bStarts) return 1;
      return (a.rank ?? 9999) - (b.rank ?? 9999);
    })
    .slice(0, 8);

  if (!matches.length) {
    menu.hidden = true;
    return;
  }
  const i = targetIndex(state.python, state.platform);
  menu.innerHTML = matches
    .map(
      (p) => `<div class="autocomplete-item" data-name="${esc(p.name)}" role="button" tabindex="0">
      <span class="autocomplete-name">${esc(p.name)} <small style="color:var(--text-faint)">${esc(p.version ?? "")}</small></span>
      <span>${statusBadge(i >= 0 ? p.results[i] : "unknown")}</span>
    </div>`
    )
    .join("");
  menu.hidden = false;
}

// Theme management
function initTheme() {
  let stored = null;
  try {
    stored = localStorage.getItem("wheelgap-theme");
  } catch {}
  const prefersDark = window.matchMedia && window.matchMedia("(prefers-color-scheme: dark)").matches;
  const isDark = stored ? stored === "dark" : prefersDark;

  if (isDark) {
    document.documentElement.setAttribute("data-theme", "dark");
    if ($("theme-icon-sun")) $("theme-icon-sun").style.display = "none";
    if ($("theme-icon-moon")) $("theme-icon-moon").style.display = "block";
  } else {
    document.documentElement.setAttribute("data-theme", "light");
    if ($("theme-icon-sun")) $("theme-icon-sun").style.display = "block";
    if ($("theme-icon-moon")) $("theme-icon-moon").style.display = "none";
  }

  $("theme-toggle")?.addEventListener("click", () => {
    const cur = document.documentElement.getAttribute("data-theme");
    const next = cur === "dark" ? "light" : "dark";
    document.documentElement.setAttribute("data-theme", next);
    try {
      localStorage.setItem("wheelgap-theme", next);
    } catch {}
    if ($("theme-icon-sun")) $("theme-icon-sun").style.display = next === "dark" ? "none" : "block";
    if ($("theme-icon-moon")) $("theme-icon-moon").style.display = next === "dark" ? "block" : "none";
  });
}

async function main() {
  initTheme();

  const [data, hist, pyodide, agreement, changes] = await Promise.all([
    fetch("data/coverage.json").then((r) => r.json()),
    fetch("data/history.json").then((r) => (r.ok ? r.json() : [])).catch(() => []),
    fetch("data/pyodide.json").then((r) => (r.ok ? r.json() : null)).catch(() => null),
    fetch("data/agreement.json").then((r) => (r.ok ? r.json() : null)).catch(() => null),
    fetch("data/changes.json").then((r) => (r.ok ? r.json() : [])).catch(() => []),
  ]);

  state.data = data;
  state.pyodide = pyodideReleases(pyodide);
  state.history = Array.isArray(hist) ? hist.filter((h) => h && typeof h === "object") : [];
  state.changes = Array.isArray(changes) ? changes.filter((c) => c && typeof c === "object") : [];
  state.python = pythons()[0];

  const pkg = readHash();
  if (pkg) $("search").value = pkg;

  renderKPIs();
  renderMeta();
  renderAgreement(agreement);
  renderAll();
  if (pkg) renderLookup(pkg);

  // Python version tabs
  $("python-tabs").addEventListener("click", (e) => {
    const b = e.target.closest("button[data-python]");
    if (!b) return;
    state.python = b.dataset.python;
    state.currentPage = 1;
    renderAll();
    renderLookup($("search").value);
  });

  // Platform Leaderboard card click (Ranked worst to best)
  $("platform-leaderboard")?.addEventListener("click", (e) => {
    const card = e.target.closest(".leaderboard-card");
    if (!card) return;
    state.platform = card.dataset.pl;
    state.currentPage = 1;
    populateOmniSelects();
    renderLeaderboard();
    renderGaps();
    renderOmniAnswer();
    renderLookup($("search").value); // its "Why?" command names the selected platform
    writeHash();
    scrollToGaps();
  });
  $("platform-leaderboard")?.addEventListener("keydown", (e) => {
    if (e.key === "Enter" || e.key === " ") {
      const card = e.target.closest(".leaderboard-card");
      if (card) {
        e.preventDefault();
        card.click();
      }
    }
  });

  // Omni Selects
  $("omni-python-select")?.addEventListener("change", (e) => {
    state.python = e.target.value;
    state.currentPage = 1;
    renderAll();
    renderLookup($("search").value);
  });
  $("omni-platform-select")?.addEventListener("change", (e) => {
    state.platform = e.target.value;
    state.currentPage = 1;
    renderLeaderboard();
    renderGaps();
    renderOmniAnswer();
    renderLookup($("search").value);
    writeHash();
  });

  // Fast Presets for Core Questions (e.g. numpy on Alpine ARM 3.13)
  $("omni-presets")?.addEventListener("click", (e) => {
    const chip = e.target.closest(".preset-chip");
    if (!chip) return;
    const { pkg, py, pl } = chip.dataset;
    if (pkg) $("search").value = pkg;
    if (py && pythons().includes(py)) state.python = py;
    if (pl && Object.hasOwn(PLATFORM_NAMES, pl)) state.platform = pl;
    state.currentPage = 1;
    renderAll();
    renderLookup($("search").value);
  });

  // Universe toggles (All 5,000 packages vs 953 Compiled Only)
  $("btn-show-all")?.addEventListener("click", () => {
    $("btn-show-all").classList.add("active");
    $("btn-show-all").setAttribute("aria-pressed", "true");
    $("btn-show-compiled").classList.remove("active");
    $("btn-show-compiled").setAttribute("aria-pressed", "false");
    state.showCompiledOnly = false;
    renderLeaderboard();
    renderTrends();
  });
  $("btn-show-compiled")?.addEventListener("click", () => {
    $("btn-show-compiled").classList.add("active");
    $("btn-show-compiled").setAttribute("aria-pressed", "true");
    $("btn-show-all").classList.remove("active");
    $("btn-show-all").setAttribute("aria-pressed", "false");
    state.showCompiledOnly = true;
    renderLeaderboard();
    renderTrends();
  });

  // Table filters
  document.querySelector(".status-filter-pills")?.addEventListener("click", (e) => {
    const btn = e.target.closest(".pill-filter");
    if (!btn) return;
    document.querySelectorAll(".pill-filter").forEach((b) => {
      b.classList.remove("active");
      b.setAttribute("aria-checked", "false");
    });
    btn.classList.add("active");
    btn.setAttribute("aria-checked", "true");
    state.filterStatus = btn.dataset.filter;
    state.currentPage = 1;
    renderGaps();
  });

  $("gaps-search")?.addEventListener("input", (e) => {
    state.filterQuery = e.target.value.trim();
    state.currentPage = 1;
    renderGaps();
  });

  // Table sorting
  document.querySelector(".packages-table thead")?.addEventListener("click", (e) => {
    const th = e.target.closest("th.sortable");
    if (!th) return;
    const col = th.dataset.sort;
    if (state.sortCol === col) {
      state.sortDir = state.sortDir === "asc" ? "desc" : "asc";
    } else {
      state.sortCol = col;
      state.sortDir = "asc";
    }
    document.querySelectorAll(".packages-table thead th").forEach((h) => {
      h.removeAttribute("aria-sort");
      const icon = h.querySelector(".sort-icon");
      if (icon) icon.textContent = "⇅";
    });
    th.setAttribute("aria-sort", state.sortDir === "asc" ? "ascending" : "descending");
    const icon = th.querySelector(".sort-icon");
    if (icon) icon.textContent = state.sortDir === "asc" ? "▲" : "▼";
    state.currentPage = 1;
    renderGaps();
  });

  // Pagination click delegators (Top & Bottom)
  const handlePageClick = (e) => {
    const btn = e.target.closest("button.page-btn");
    if (!btn || btn.disabled) return;
    const p = Number(btn.dataset.page);
    if (!Number.isFinite(p) || p === state.currentPage) return;
    state.currentPage = p;
    renderGaps();
    $("gaps-title").scrollIntoView({ behavior: "smooth", block: "start" });
  };
  $("pagination-buttons-top")?.addEventListener("click", handlePageClick);
  $("pagination-buttons-bottom")?.addEventListener("click", handlePageClick);

  // Page size select (Top & Bottom)
  const handlePageSizeChange = (e) => {
    const val = e.target.value;
    state.pageSize = val;
    state.currentPage = 1;
    renderGaps();
  };
  $("page-size-top")?.addEventListener("change", handlePageSizeChange);
  $("page-size-bottom")?.addEventListener("change", handlePageSizeChange);

  // Delegated clicks: Copy CLI repro command, verdict buttons, package drawers
  document.addEventListener("click", async (e) => {
    // Copy badge (lookup card and drawer)
    const copyBadge = e.target.closest(".copy-badge");
    if (copyBadge && copyBadge.dataset.md) {
      try {
        await navigator.clipboard.writeText(copyBadge.dataset.md);
        copyBadge.textContent = "Copied!";
        setTimeout(() => { copyBadge.textContent = "Copy badge"; }, 1500);
      } catch {
        copyBadge.textContent = "Select and copy";
      }
      return;
    }

    // Copy CLI repro command button
    const copyRepro = e.target.closest(".btn-copy-repro");
    if (copyRepro && copyRepro.dataset.cmd) {
      try {
        await navigator.clipboard.writeText(copyRepro.dataset.cmd);
        const orig = copyRepro.textContent;
        copyRepro.textContent = "Copied!";
        setTimeout(() => { copyRepro.textContent = orig; }, 1500);
      } catch {}
      return;
    }

    // Verdict Filter button
    const filterBtn = e.target.closest(".vbtn-filter, .verdict-btn-filter");
    if (filterBtn) {
      if (filterBtn.dataset.pl) state.platform = filterBtn.dataset.pl;
      if (filterBtn.dataset.py) state.python = filterBtn.dataset.py;
      state.currentPage = 1;
      populateOmniSelects();
      renderAll();
      renderLookup($("search").value);
      scrollToGaps();
      return;
    }

    // Verdict Drawer button
    const drawerBtn = e.target.closest(".vbtn-drawer, .verdict-btn-drawer");
    if (drawerBtn && drawerBtn.dataset.pkg) {
      openDrawer(drawerBtn.dataset.pkg);
      return;
    }

    // Row expand caret button (Inline expansion)
    const expandBtn = e.target.closest(".row-expand-btn");
    if (expandBtn && expandBtn.dataset.pkg) {
      e.preventDefault();
      state.expandedPkg = state.expandedPkg === expandBtn.dataset.pkg ? null : expandBtn.dataset.pkg;
      renderGaps();
      return;
    }

    // Existing package links (mobile uses inline accordion, desktop uses drawer)
    const link = e.target.closest("a[data-pkg], .pkg-name-link");
    if (link && link.dataset.pkg) {
      e.preventDefault();
      if (window.innerWidth <= 640 && !link.classList.contains("pulse-pkg")) {
        state.expandedPkg = state.expandedPkg === link.dataset.pkg ? null : link.dataset.pkg;
        renderGaps();
      } else {
        openDrawer(link.dataset.pkg);
      }
      return;
    }
  });

  // Drawer closing
  $("drawer-close")?.addEventListener("click", closeDrawer);
  $("drawer-backdrop")?.addEventListener("click", closeDrawer);

  // Clear search input button logic
  const updateClearBtn = () => {
    const clearBtn = $("search-clear-btn");
    if (!clearBtn) return;
    clearBtn.hidden = !$("search").value.trim();
  };

  updateClearBtn();

  $("search-clear-btn")?.addEventListener("click", () => {
    $("search").value = "";
    $("search").focus();
    updateClearBtn();
    if ($("search-autocomplete")) $("search-autocomplete").hidden = true;
    renderOmniAnswer();
    renderLookup("");
    writeHash();
  });

  // Omni Checker collapse / minimize toggle
  $("omni-toggle-btn")?.addEventListener("click", () => {
    const panel = $("omni-checker");
    const body = $("omni-body");
    const mini = $("omni-minimized-summary");
    if (!panel || !body) return;
    const willCollapse = !body.hidden;
    body.hidden = willCollapse;
    panel.classList.toggle("is-minimized", willCollapse);
    if (mini) mini.hidden = !willCollapse;
    $("omni-toggle-btn").setAttribute("aria-expanded", String(!willCollapse));
    const icon = $("omni-toggle-icon");
    const text = $("omni-toggle-text");
    if (icon) icon.textContent = willCollapse ? "▼" : "▲";
    if (text) text.textContent = willCollapse ? "Expand" : "Minimize";
  });

  // Search form & Autocomplete
  $("search-form").addEventListener("submit", (e) => {
    e.preventDefault();
    if ($("search-autocomplete")) $("search-autocomplete").hidden = true;
    renderOmniAnswer();
    renderLookup($("search").value);
    writeHash();
  });
  $("search").addEventListener("input", (e) => {
    updateClearBtn();
    // The card below is for the name last looked up: clear it while a new one is typed, so the two never disagree.
    if ($("lookup").innerHTML) renderLookup("");
    updateSearchAutocomplete(e.target.value);
    renderOmniAnswer();
  });
  $("search").addEventListener("focus", (e) => {
    if (e.target.value.trim().length >= 1) {
      updateSearchAutocomplete(e.target.value);
    }
  });

  $("search-autocomplete")?.addEventListener("keydown", (e) => {
    const item = e.target.closest(".autocomplete-item");
    if (!item) return;
    if (e.key === "Enter" || e.key === " ") {
      e.preventDefault();
      item.click();
    } else if (e.key === "ArrowDown" || e.key === "ArrowUp") {
      e.preventDefault();
      const sib = e.key === "ArrowDown" ? item.nextElementSibling : item.previousElementSibling;
      (sib ?? (e.key === "ArrowUp" ? $("search") : null))?.focus();
    }
  });
  $("search").addEventListener("keydown", (e) => {
    if (e.key === "ArrowDown" && !$("search-autocomplete")?.hidden) {
      const first = $("search-autocomplete").querySelector(".autocomplete-item");
      if (first) {
        e.preventDefault();
        first.focus();
      }
    }
  });
  $("search-autocomplete")?.addEventListener("click", (e) => {
    const item = e.target.closest(".autocomplete-item");
    if (!item) return;
    $("search").value = item.dataset.name;
    updateClearBtn();
    $("search-autocomplete").hidden = true;
    renderOmniAnswer();
    renderLookup(item.dataset.name);
    writeHash();
  });

  // Omni Checker Clear All & Reset
  $("omni-reset-btn")?.addEventListener("click", () => {
    $("search").value = "";
    state.python = pythons()[0];
    state.platform = "musl-aarch64";
    state.currentPage = 1;
    populateOmniSelects();
    updateClearBtn();
    if ($("search-autocomplete")) $("search-autocomplete").hidden = true;
    renderAll();
    renderLookup("");
    writeHash();
  });

  // Close search autocomplete on click outside
  document.addEventListener("click", (e) => {
    if ($("search-autocomplete") && !e.target.closest(".autocomplete-wrapper")) {
      $("search-autocomplete").hidden = true;
    }
  });

  // Command Palette
  $("cmd-trigger")?.addEventListener("click", openCommandPalette);
  $("cmd-close")?.addEventListener("click", closeCommandPalette);
  $("cmd-overlay")?.addEventListener("click", (e) => {
    if (e.target === $("cmd-overlay")) closeCommandPalette();
  });
  $("cmd-input")?.addEventListener("input", (e) => {
    renderCmdResults(e.target.value);
  });
  $("cmd-input")?.addEventListener("keydown", (e) => {
    const items = [...$("cmd-results").querySelectorAll(".cmd-result-item")];
    if (!items.length) return;
    const at = Math.max(0, items.findIndex((x) => x.classList.contains("selected")));
    if (e.key === "ArrowDown" || e.key === "ArrowUp") {
      e.preventDefault();
      const next = (at + (e.key === "ArrowDown" ? 1 : items.length - 1)) % items.length;
      items[at].classList.remove("selected");
      items[next].classList.add("selected");
      items[next].scrollIntoView?.({ block: "nearest" });
    } else if (e.key === "Enter") {
      e.preventDefault();
      closeCommandPalette();
      $("cmd-trigger")?.focus();
      openDrawer(items[at].dataset.pkg);
    }
  });
  $("cmd-results")?.addEventListener("click", (e) => {
    const item = e.target.closest(".cmd-result-item");
    if (!item) return;
    closeCommandPalette();
    $("cmd-trigger")?.focus();
    openDrawer(item.dataset.pkg);
  });

  // Global Keyboard shortcuts
  document.addEventListener("keydown", (e) => {
    if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "k") {
      e.preventDefault();
      if ($("cmd-overlay") && $("cmd-overlay").hidden) {
        openCommandPalette();
      } else {
        closeCommandPalette();
      }
    } else if (e.key === "Escape") {
      if (!$("cmd-overlay")?.hidden) closeCommandPalette();
      if (!$("drawer")?.hidden) closeDrawer();
      if (!$("export-backdrop")?.hidden) $("export-backdrop").hidden = true;
      if ($("search-autocomplete")) $("search-autocomplete").hidden = true;
    }
  });

  // Export Modal
  $("export-btn")?.addEventListener("click", () => {
    $("export-backdrop").hidden = false;
  });
  $("export-close")?.addEventListener("click", () => {
    $("export-backdrop").hidden = true;
  });
  $("export-backdrop")?.addEventListener("click", (e) => {
    if (e.target === $("export-backdrop")) $("export-backdrop").hidden = true;
  });
  $("btn-export-csv")?.addEventListener("click", () => {
    exportCSV();
    $("export-backdrop").hidden = true;
  });
  $("btn-export-json")?.addEventListener("click", () => {
    exportJSON();
    $("export-backdrop").hidden = true;
  });

  // CTA Copy
  $("cta-copy-btn")?.addEventListener("click", async () => {
    const code = document.querySelector(".command-block code")?.textContent;
    if (!code) return;
    try {
      await navigator.clipboard.writeText(code);
      const btn = $("cta-copy-btn");
      btn.textContent = "Copied!";
      setTimeout(() => { btn.textContent = "Copy"; }, 1500);
    } catch {}
  });
}

main().catch((err) => {
  $("meta").textContent = `Couldn't load the data (${err.message}).`;
});
