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
let gapWheelDoc = null; // wheelgap/wheels' data/gap-wheels.json, once fetched (null if it couldn't be read)
let gapWheelPromise = null;
// The pip index is absolute, but the list is fetched as ../wheels/data/gap-wheels.json: that relative path assumes
// this site and the wheels site share wheelgap.github.io (tracker at /tracker/, wheels at /wheels/).
const GAP_WHEEL_INDEX = "https://wheelgap.github.io/wheels/simple/";
const lookupDetails = new Map(); // canonical name -> its data/packages/<name>.json, once fetched
const pendingDetails = new Map(); // canonical name -> the fetch in flight (or done); dropped on failure so it can retry
function fetchLookupDetails(name) {
  if (!pendingDetails.has(name)) {
    const request = fetch(`data/packages/${encodeURIComponent(name)}.json`)
      .then((r) => (r.ok ? r.json() : null))
      .catch(() => null)
      .then((data) => {
        if (data) lookupDetails.set(name, data);
        else pendingDetails.delete(name);
        return data;
      });
    pendingDetails.set(name, request);
  }
  return pendingDetails.get(name);
}

const state = {
  data: null,
  history: [],
  pyodide: {},
  changes: [],
  blockers: null,
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
  const dropped = d.dropped_names?.count ?? 0;
  if (dropped > 0) {
    $("meta").textContent += ` · ${fmt(dropped)} ${dropped === 1 ? "name" : "names"} PyPI doesn't have ` +
      `left out of the top ${fmt(d.packages.length + dropped)}`;
  }
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

// The Python selector is a radio group (#163): one tab stop, on the selected Python, and arrow keys move the
// choice, like the status pills (#146).
function renderTabs() {
  $("python-tabs").innerHTML = pythons()
    .map((py) => {
      const on = py === state.python;
      return `<button type="button" role="radio" aria-checked="${on}" tabindex="${on ? 0 : -1}" data-python="${esc(py)}">${esc(py)}</button>`;
    })
    .join("");
}

// Switches the page to Python py from the selector itself, then puts focus back on the selected button, which
// renderTabs() has just replaced (otherwise focus drops to <body>).
function selectPython(py) {
  state.python = py;
  state.currentPage = 1;
  renderAll();
  renderLookup($("search").value);
  $("python-tabs").querySelector('button[aria-checked="true"]')?.focus();
}

// The night before the latest scan in data/history.json, or null.
function previousScan() {
  const h = state.history;
  return h.length > 1 ? h[h.length - 2] : null;
}

// Sparklines helper
function coverageSeries(label) {
  return chartSeries(state.history, label);
}

// Why a point isn't like for like with the one before it, or "".
function sparkMark(p) {
  return [p.upgrade ? `wheelgap changed to ${p.upgrade}` : "", p.bundledStart ? "Pyodide's own builds counted from here" : ""]
    .filter(Boolean).join("; ");
}

function sparkline(points, name) {
  const W = 160, H = 36, PAD_LEFT = 4, PAD_RIGHT = 34, PAD_Y = 4;
  const values = points.map((p) => p.value);
  let lo = Math.min(...values), hi = Math.max(...values);
  if (hi - lo < 0.5) {
    const mid = (hi + lo) / 2;
    lo = mid - 0.25;
    hi = mid + 0.25;
  }
  const x = (i) => PAD_LEFT + (i * (W - PAD_LEFT - PAD_RIGHT)) / (points.length - 1);
  const y = (v) => H - PAD_Y - ((v - lo) * (H - 2 * PAD_Y)) / (hi - lo);
  const path = points.map((p, i) => `${i ? "L" : "M"}${x(i).toFixed(1)},${y(p.value).toFixed(1)}`).join(" ");

  const upgradeLines = points
    .map((p, i) => (sparkMark(p) ? `<line class="spark-upgrade-line" x1="${x(i).toFixed(1)}" y1="${PAD_Y}" x2="${x(i).toFixed(1)}" y2="${(H - PAD_Y).toFixed(1)}"><title>${esc(p.date)}: ${esc(sparkMark(p))}</title></line>` : ""))
    .join("");

  const shown = points.length <= 31 ? points.map((p, i) => [p, i]) : [[points[points.length - 1], points.length - 1]];
  const dots = shown
    .map(([p, i]) => {
      const mark = sparkMark(p);
      const upgradeClass = mark ? ' class="spark-upgrade"' : "";
      const upgradeNote = mark ? ` (${esc(mark)})` : "";
      const r = mark ? 3.5 : 2;
      return `<circle${upgradeClass} cx="${x(i).toFixed(1)}" cy="${y(p.value).toFixed(1)}" r="${r}"><title>${esc(p.date)}: ${p.value.toFixed(1)}%${upgradeNote}</title></circle>`;
    })
    .join("");

  const first = points[0], last = points[points.length - 1];
  const lastX = x(points.length - 1);
  const lastY = y(last.value);
  const labelY = Math.max(PAD_Y + 7, Math.min(H - PAD_Y, lastY + 3));
  const latestText = `<text class="spark-label" x="${(lastX + 4).toFixed(1)}" y="${labelY.toFixed(1)}">${last.value.toFixed(1)}%</text>`;

  const marks = points.filter((p) => sparkMark(p));
  const upgradeLabel = marks.length ? `; ${marks.map((m) => `${sparkMark(m)} on ${m.date}`).join(", ")}` : "";
  const label = `${name}: ${first.value.toFixed(1)}% on ${first.date}, ${last.value.toFixed(1)}% on ${last.date} (latest)${upgradeLabel}`;

  return `<svg class="spark" viewBox="0 0 ${W} ${H}" role="img" aria-label="${esc(label)}">
    ${upgradeLines}<path d="${path}"></path>${dots}${latestText}</svg>`;
}

function renderTrends() {
  // The sparklines are on the leaderboard cards; this is the note under them, saying what they plot.
  const seriesList = state.data.targets
    .filter((t) => t.python === state.python)
    .map((t) => coverageSeries(t.label));
  const dates = seriesList.flatMap((s) => s.map((p) => p.date)).sort();
  const hasUpgrades = seriesList.some((s) => s.some((p) => sparkMark(p)));
  const upgradeNote = hasUpgrades
    ? " Marked points aren't like for like with the night before: a new wheelgap version, or Pyodide's own builds counted from then on."
    : "";
  const pyodideBundled = (state.data.summary?.[`${state.python} pyodide`]?.bundled ?? 0) > 0;
  $("trend-note").textContent = dates.length > 1 && !state.showCompiledOnly
    ? `Sparklines: the share of all packages with a wheel for Python ${state.python}, one point per nightly scan from ${dates[0]} to ${dates[dates.length - 1]}, with the latest point labelled. Each is scaled to its own range.${upgradeNote}` +
      (pyodideBundled ? " On Pyodide they also count Pyodide's own builds, like the percentages." : "")
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

function scrollBehavior() {
  return window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches
    ? "auto" : "smooth";
}

function scrollToGaps() {
  $("gaps-title")?.scrollIntoView({ behavior: scrollBehavior(), block: "start" });
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
      res = (a.downloads ?? 0) - (b.downloads ?? 0);
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

    // First & Prev. The name is the aria-label: a button is named by its content before its title, and the
    // content here is a glyph a screen reader would read as "black right-pointing triangle".
    btns.push(`<button type="button" class="page-btn" data-page="1" ${state.currentPage === 1 ? "disabled" : ""} aria-label="First page" title="First page"><span aria-hidden="true">⏮</span></button>`);
    btns.push(`<button type="button" class="page-btn" data-page="${state.currentPage - 1}" ${state.currentPage === 1 ? "disabled" : ""} aria-label="Previous page" title="Previous page"><span aria-hidden="true">◀</span></button>`);

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
        btns.push(`<button type="button" class="page-btn${isActive ? " active" : ""}" aria-label="Page ${p}" data-page="${p}" aria-current="${isActive ? "page" : "false"}">${p}</button>`);
      }
    }

    // Next & Last
    btns.push(`<button type="button" class="page-btn" data-page="${state.currentPage + 1}" ${state.currentPage === totalPages ? "disabled" : ""} aria-label="Next page" title="Next page"><span aria-hidden="true">▶</span></button>`);
    btns.push(`<button type="button" class="page-btn" data-page="${totalPages}" ${state.currentPage === totalPages ? "disabled" : ""} aria-label="Last page" title="Last page"><span aria-hidden="true">⏭</span></button>`);

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
        <button type="button" class="row-expand-btn" data-pkg="${esc(p.name)}" aria-label="Details for ${esc(p.name)}" aria-expanded="${isExpanded}"><span aria-hidden="true">${isExpanded ? "▲" : "▼"}</span></button>
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

// Packages blocking the most other top-500 installs (#93), from data/blockers.json.
function renderBlockers() {
  const container = $("blockers-feed");
  const badge = $("blockers-target-badge");
  const note = $("blockers-note");
  if (!container || !note) return;
  const placeholder = (text) => `<li class="feed-placeholder" style="color:var(--text-muted);padding:14px">${esc(text)}</li>`;

  // Whole-tree installs are only scanned for some Pythons, so fall back to the newest one checked on this platform.
  const label = blockersLabel(state.blockers, state.python, state.platform);
  if (badge) badge.textContent = label || state.platform;
  if (!label) {
    note.textContent = state.blockers
      ? `Whole-tree installs aren't checked on ${state.platform}, so there's nothing to rank here.`
      : "The blockers list isn't available right now.";
    container.innerHTML = "";
    return;
  }
  const { checked, blockers } = blockersForTarget(state.blockers, label);
  const python = label.split(" ")[0];
  note.textContent =
    `The packages whose missing wheel stops the most other top-500 packages installing from wheels alone on ${label}, ` +
    `of ${fmt(checked)} checked.` +
    (python !== state.python ? ` Installs aren't checked for Python ${state.python} here, so this is Python ${python}.` : "");
  if (!blockers.length) {
    container.innerHTML = placeholder("No missing wheel here holds back another top-500 package.");
    return;
  }

  const link = (name, cls) => `<a href="p/${esc(canonicalName(name))}/" class="${cls}">${esc(name)}</a>`;
  container.innerHTML = blockers
    .slice(0, 15)
    .map((item) => {
      const others = `${fmt(item.count)} other top-500 install${item.count === 1 ? "" : "s"}`;
      const examples = item.examples.length ? `, e.g. ${item.examples.map((ex) => link(ex, "blocker-ex")).join(", ")}` : "";
      return `<li>
        <div class="pulse-content">
          <div class="blocker-row">
            <strong>${link(item.name, "pulse-pkg")}</strong>
            <span class="blocker-count-badge" title="Blocks ${others}">${fmt(item.count)}</span>
          </div>
          <small class="pulse-meta">Blocks ${others}${examples}</small>
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
function buildPackageCardHTML(p, extra = null, pinNote = "", details = null) {
  const pys = pythons();
  const installData = !pinNote ? lookupInstallData(details, p, state.data.targets) : [];
  const byTarget = new Map(installData.map((row) => [row.label, row]));
  // Same pills and words as the package pages' install section (INSTALL_STATUS in scripts/build_pages.py).
  const installStatus = { wheel: ["wheel", "Wheels for the whole tree"], source: ["source", "Builds from source"],
    missing: ["missing", "Missing distribution"], not_checked: ["unknown", "Not checked"] };
  const installRows = installData.filter((row) => row.status !== "not_checked" || details?.install?.[row.label])
    .map((row) => {
      const blockers = row.blockers.length ? `<ul>${row.blockers.map((b) => `
        <li><code>${esc(b.name)}${b.version ? "==" + esc(b.version) : ""}</code>
        ${b.status === "source" ? "builds from source" : "has no distribution"}
        ${b.via.length > 1 ? " via <code>" + b.via.map(esc).join(" → ") + "</code>" : ""}</li>`).join("")}</ul>` : "";
      const [style, label] = installStatus[row.status];
      return `<tr><th scope="row">${esc(row.label)}</th><td><span class="status s-${style}">${esc(label)}</span>${blockers}</td></tr>`;
    }).join("");
  const installSection = installRows
    ? `<section aria-label="Install coverage with dependencies" style="margin-top:12px"><h4>Install coverage with dependencies</h4>
      <p class="sub">For the scanned release without extras. "Not checked" means wheelgap could not verify the whole tree.</p>
      <div class="matrix-scroll"><table class="matrix"><thead><tr><th>Target</th><th>Install result</th></tr></thead>
      <tbody>${installRows}</tbody></table></div></section>` : "";
  const matches = !pinNote ? gapWheelTargets(gapWheelDoc, p, state.data.targets) : [];
  const available = new Set(matches);
  // Same words as the package pages' "Gap wheels" section (scripts/build_pages.py).
  const gapHint = matches.length
    ? `<section class="card-gap-wheel" aria-label="Gap wheels" style="margin-top:12px"><h4>Gap wheels</h4>
        <p class="sub">wheelgap publishes an unofficial wheel of ${esc(p.name)} ${esc(p.version)} for ${matches.map(esc).join(", ")},
        built from the PyPI sdist. It isn't from the package's maintainers.</p>
        <p class="sub"><code>pip install --extra-index-url ${GAP_WHEEL_INDEX} ${esc(canonicalName(p.name))}==${esc(p.version)}</code></p>
        <p class="sub"><a href="https://wheelgap.github.io/wheels/" target="_blank" rel="noopener">About the gap wheels</a></p>
      </section>` : "";
  const platforms = Object.keys(PLATFORM_NAMES).filter((pl) => state.data.targets.some((t) => t.platform === pl));
  const cell = (py, pl) => {
    const i = targetIndex(py, pl);
    if (i < 0) return `<td><span class="status s-none">n/a</span></td>`;
    const s = p.results[i];
    const detail = s === "bundled" && extra ? bundledDetail(extra[state.data.targets[i].label]) : "";
    const hint = byTarget.get(state.data.targets[i].label)?.wayOut;
    const wayOut = hint ? `<small class="bver" style="display:block;margin-top:2px;font-size:0.72rem;color:var(--text-muted)">${hint.kind === "older" ? esc(hint.release) + " is the last release with a wheel here." : "Upgrade to " + esc(hint.release) + " for a wheel here."}</small>` : "";
    const small = detail ? `<small class="bver" style="display:block;margin-top:2px;font-size:0.72rem;color:var(--text-muted)">${esc(detail)}</small>` : "";
    const wheelHint = available.has(state.data.targets[i].label) ? '<small class="bver" style="display:block;margin-top:2px;font-size:0.72rem;color:var(--text-muted)">Unofficial gap wheel available below</small>' : "";
    return `<td>${statusBadge(s)}${small}${wayOut}${wheelHint}</td>`;
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
    ${installSection}
    ${gapHint}
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

  // extra: the package's data/packages/<name>.json when re-rendering after the fetch ({} if it failed).
  const documentData = extra ?? lookupDetails.get(q) ?? null;
  $("lookup").innerHTML = buildPackageCardHTML(p, bundledInfo(documentData), pinNote, documentData);

  // One optional same-origin request, only when the selected release has an actual PyPI gap.
  if (!gapWheelPromise && !pinNote && isPackageSpec(spec) && !p.error &&
      p.results.some((status) => status === "source" || status === "missing")) {
    gapWheelPromise = fetch("../wheels/data/gap-wheels.json")
      .then((response) => (response.ok ? response.json() : null))
      .catch(() => null)
      .then((data) => {
        gapWheelDoc = data;
        // Re-render whatever the lookup shows now (the list covers every package), with its package details if
        // they've arrived since (renderLookup reads them from lookupDetails), never this call's possibly stale `extra`.
        const current = $("search").value.trim();
        if (data && current && $("lookup").innerHTML) {
          renderLookup(current);
        }
      })
      .catch(() => {});
  }

  if (extra === null && !lookupDetails.has(q) && !p.error && isPackageSpec(spec)) {
    fetchLookupDetails(q)
      .then((doc) => {
        // Still this package, even if the box now holds another version of it: re-render what it says now.
        const current = $("search").value.trim();
        if (canonicalName(splitSpec(current)[0]) === q) renderLookup(current, doc || {});
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
  const doc = lookupDetails.get(q) ?? null;
  $("drawer-content").innerHTML = buildPackageCardHTML(p, bundledInfo(doc), "", doc);


  if ($("drawer").hidden) drawerReturnFocus = document.activeElement;
  $("drawer").hidden = false;
  $("drawer-backdrop").hidden = false;
  document.body.style.overflow = "hidden";
  $("drawer-close")?.focus();

  if (!p.error && !doc) {
    fetchLookupDetails(q)
      .then((fetched) => {
        if (fetched && $("drawer-title").textContent === p.name) {
          $("drawer-content").innerHTML = buildPackageCardHTML(p, bundledInfo(fetched), "", fetched);
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
      p.results[i] ?? "unknown",
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

// Dialog keyboard navigation. Query on each Tab so asynchronously loaded drawer
// links participate, while hidden and disabled controls do not.
function trapTab(container, event) {
  if (event.key !== "Tab" || event.ctrlKey || event.altKey || event.metaKey) return;
  const focusable = [...container.querySelectorAll(
    'a[href], button:not(:disabled), input:not(:disabled), select:not(:disabled), textarea:not(:disabled), [tabindex]'
  )].filter((node) => node.tabIndex >= 0 && node.getClientRects().length);
  if (!focusable.length) return;
  const first = focusable[0], last = focusable[focusable.length - 1];
  if (event.shiftKey && document.activeElement === first) {
    event.preventDefault();
    last.focus();
  } else if (!event.shiftKey && document.activeElement === last) {
    event.preventDefault();
    first.focus();
  }
}

let exportReturnFocus = null;

function openExport() {
  if ($("export-backdrop").hidden) exportReturnFocus = document.activeElement;
  $("export-backdrop").hidden = false;
  $("btn-export-csv").focus();
}

function closeExport() {
  $("export-backdrop").hidden = true;
  exportReturnFocus?.focus?.();
  exportReturnFocus = null;
}

// Both package searches follow the WAI-ARIA combobox pattern with list autocomplete (#167): focus stays in the
// input, aria-activedescendant names the highlighted option, and option ids come from the index, not the name.
function showActiveOption(input, prefix, count, active) {
  for (let k = 0; k < count; k++) {
    const opt = $(`${prefix}-${k}`);
    if (!opt) continue;
    opt.setAttribute("aria-selected", String(k === active));
    opt.classList.toggle("selected", k === active);
  }
  if (active >= 0) {
    input.setAttribute("aria-activedescendant", `${prefix}-${active}`);
    $(`${prefix}-${active}`)?.scrollIntoView?.({ block: "nearest" });
  } else {
    input.removeAttribute("aria-activedescendant");
  }
}

// Says how many suggestions there are once typing pauses, in the search's own polite live region, so a
// screen reader hears one count rather than one per keystroke. The quick search has its own region inside
// the dialog: aria-modal hides everything outside it from assistive technology. A count is dropped if the list
// has closed by then (Enter, Escape or a click within the pause), so it never talks over what replaced it.
const ANNOUNCE_DELAY_MS = 500;
const ANNOUNCE_CLEAR_MS = 50; // between clearing the region and writing the new text
const announceTimers = {};
function announceSuggestions(regionId, count, isOpen) {
  clearTimeout(announceTimers[regionId]);
  announceTimers[regionId] = setTimeout(() => {
    const region = $(regionId);
    if (!region || (count && isOpen && !isOpen())) return;
    // Clear the region, then write: a screen reader may skip a live region whose text didn't change, and most
    // short queries give the same "8 suggestions" as the one before.
    const text = count ? `${count} suggestion${count === 1 ? "" : "s"}. Use Up and Down to choose.` : "No packages match.";
    region.textContent = "";
    announceTimers[regionId] = setTimeout(() => { region.textContent = text; }, ANNOUNCE_CLEAR_MS);
  }, ANNOUNCE_DELAY_MS);
}

// Command Palette (Cmd+K)
let commandReturnFocus = null;
let cmdMatches = []; // names of the quick search's results, in order
let cmdActive = -1; // index of the highlighted one

function openCommandPalette() {
  if ($("cmd-overlay").hidden) commandReturnFocus = document.activeElement;
  $("cmd-overlay").hidden = false;
  $("cmd-input").value = "";
  $("cmd-input").focus();
  renderCmdResults("");
}

function closeCommandPalette() {
  $("cmd-overlay").hidden = true;
  commandReturnFocus?.focus?.();
  commandReturnFocus = null;
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

  cmdMatches = matches.map((p) => p.name);
  cmdActive = matches.length ? 0 : -1;
  const input = $("cmd-input");
  input.setAttribute("aria-expanded", String(matches.length > 0));
  announceSuggestions("cmd-status", matches.length, () => !$("cmd-overlay").hidden);
  if (!matches.length) {
    input.removeAttribute("aria-activedescendant");
    container.innerHTML = `<div style="padding:14px;text-align:center;color:var(--text-muted);font-size:0.86rem">No packages match "${esc(query)}".</div>`;
    return;
  }

  const i = targetIndex(state.python, state.platform);
  container.innerHTML = matches
    .map((p, idx) => {
      const s = i >= 0 ? p.results[i] : "unknown";
      return `<div class="cmd-result-item ${idx === 0 ? "selected" : ""}" id="cmd-opt-${idx}" data-pkg="${esc(p.name)}" role="option" aria-selected="${idx === 0}">
        <span class="cmd-res-rank">#${esc(p.rank)}</span>
        <span class="cmd-res-name">${esc(p.name)} <small style="color:var(--text-faint)">${esc(p.version ?? "")}</small></span>
        ${statusBadge(s)}
      </div>`;
    })
    .join("");
  input.setAttribute("aria-activedescendant", "cmd-opt-0");
}

// The gaps table's settings a shared link carries, each only when it differs from the default in `state`.
// Anything else in the hash is ignored; the page number is never shared, so a link opens on page 1.
const HASH_STATUS = ["source", "missing"];
const HASH_SORT = ["name", "status", "downloads"];
const HASH_SIZE = ["50", "100", "all"];
const HASH_QUERY_MAX = 100;

function readHash() {
  const params = new URLSearchParams(location.hash.slice(1));
  if (params.get("python") && pythons().includes(params.get("python"))) state.python = params.get("python");
  if (params.get("platform") && Object.hasOwn(PLATFORM_NAMES, params.get("platform"))) state.platform = params.get("platform");
  if (HASH_STATUS.includes(params.get("status"))) state.filterStatus = params.get("status");
  const query = (params.get("q") ?? "").trim().slice(0, HASH_QUERY_MAX);
  if (query) state.filterQuery = query;
  if (HASH_SORT.includes(params.get("sort"))) state.sortCol = params.get("sort");
  if (params.get("dir") === "desc") state.sortDir = "desc";
  if (HASH_SIZE.includes(params.get("size"))) state.pageSize = params.get("size");
  if (params.get("compiled") === "1") state.showCompiledOnly = true;
  return params.get("package") ?? "";
}

function writeHash() {
  const params = new URLSearchParams({ python: state.python, platform: state.platform });
  const pkg = $("search").value.trim();
  if (pkg) params.set("package", pkg);
  if (state.filterStatus !== "all") params.set("status", state.filterStatus);
  if (state.filterQuery) params.set("q", state.filterQuery.slice(0, HASH_QUERY_MAX));
  if (state.sortCol !== "rank") params.set("sort", state.sortCol);
  if (state.sortDir === "desc") params.set("dir", "desc");
  if (String(state.pageSize) !== "25") params.set("size", String(state.pageSize));
  if (state.showCompiledOnly) params.set("compiled", "1");
  history.replaceState(null, "", `#${params}`);
}

// Point the status pills, sort headings, universe toggle and name filter at `state`, after a hash set it.
// (renderGaps() sets both page-size selects.)
function markStatusPill() {
  document.querySelectorAll(".pill-filter").forEach((b) => {
    const selected = b.dataset.filter === state.filterStatus;
    b.classList.toggle("active", selected);
    b.setAttribute("aria-checked", String(selected));
    b.tabIndex = selected ? 0 : -1;
  });
}

function markSortHeading() {
  document.querySelectorAll(".packages-table thead th").forEach((h) => {
    const icon = h.querySelector(".sort-icon");
    if (h.dataset.sort === state.sortCol) {
      h.setAttribute("aria-sort", state.sortDir === "asc" ? "ascending" : "descending");
      if (icon) icon.textContent = state.sortDir === "asc" ? "▲" : "▼";
    } else {
      h.removeAttribute("aria-sort");
      if (icon) icon.textContent = "⇅";
    }
  });
}

function markUniverse() {
  const all = $("btn-show-all");
  const compiled = $("btn-show-compiled");
  all?.classList.toggle("active", !state.showCompiledOnly);
  all?.setAttribute("aria-pressed", String(!state.showCompiledOnly));
  compiled?.classList.toggle("active", state.showCompiledOnly);
  compiled?.setAttribute("aria-pressed", String(state.showCompiledOnly));
}

function syncGapsControls() {
  markStatusPill();
  markSortHeading();
  markUniverse();
  if ($("gaps-search")) $("gaps-search").value = state.filterQuery;
}

function renderAll() {
  renderTabs();
  populateOmniSelects();
  renderOmniAnswer();
  renderLeaderboard();
  renderTrends();
  renderGaps();
  renderPulse();
  renderBlockers();
  writeHash();
}

// Autocomplete Menu for Look Up input (top 8 matches, lightweight & fast)
let searchMatches = []; // names of the lookup's suggestions, in order
let searchActive = -1; // index of the highlighted one; none until Up or Down, so Enter still submits what was typed
function updateSearchAutocomplete(value) {
  const menu = $("search-autocomplete");
  if (!menu) return;
  const q = value.trim().toLowerCase();
  if (!q || q.length < 1) {
    closeSearchSuggestions();
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

  searchMatches = matches.map((p) => p.name);
  searchActive = -1;
  if (!matches.length) {
    closeSearchSuggestions();
    announceSuggestions("search-status", 0);
    return;
  }
  const i = targetIndex(state.python, state.platform);
  menu.innerHTML = matches
    .map(
      (p, idx) => `<div class="autocomplete-item" id="search-opt-${idx}" data-name="${esc(p.name)}" role="option" aria-selected="false">
      <span class="autocomplete-name">${esc(p.name)} <small style="color:var(--text-faint)">${esc(p.version ?? "")}</small></span>
      <span>${statusBadge(i >= 0 ? p.results[i] : "unknown")}</span>
    </div>`
    )
    .join("");
  menu.hidden = false;
  const input = $("search");
  input.setAttribute("aria-expanded", "true");
  input.removeAttribute("aria-activedescendant");
  announceSuggestions("search-status", matches.length, () => !menu.hidden);
}

// Hides the lookup's suggestions and tells assistive technology the list is gone. A pending announcement is
// dropped too, so clearing, submitting or picking within the pause never hears a stale "No packages match."
function closeSearchSuggestions() {
  clearTimeout(announceTimers["search-status"]);
  const menu = $("search-autocomplete");
  if (menu) menu.hidden = true;
  searchActive = -1;
  $("search").setAttribute("aria-expanded", "false");
  $("search").removeAttribute("aria-activedescendant");
}

// Theme management
function initTheme() {
  let stored = null;
  try {
    stored = localStorage.getItem("wheelgap-theme");
  } catch {}
  const prefersDark = window.matchMedia && window.matchMedia("(prefers-color-scheme: dark)").matches;
  const isDark = stored ? stored === "dark" : prefersDark;

  // The toggle's name stays "Dark theme"; aria-pressed says whether it is on.
  const apply = (dark) => {
    document.documentElement.setAttribute("data-theme", dark ? "dark" : "light");
    if ($("theme-icon-sun")) $("theme-icon-sun").style.display = dark ? "none" : "block";
    if ($("theme-icon-moon")) $("theme-icon-moon").style.display = dark ? "block" : "none";
    $("theme-toggle")?.setAttribute("aria-pressed", String(dark));
  };
  apply(isDark);

  $("theme-toggle")?.addEventListener("click", () => {
    const cur = document.documentElement.getAttribute("data-theme");
    const next = cur === "dark" ? "light" : "dark";
    apply(next === "dark");
    try {
      localStorage.setItem("wheelgap-theme", next);
    } catch {}
  });
}

async function main() {
  initTheme();

  const [data, hist, pyodide, agreement, changes, blockers] = await Promise.all([
    fetch("data/coverage.json").then((r) => r.json()),
    fetch("data/history.json").then((r) => (r.ok ? r.json() : [])).catch(() => []),
    fetch("data/pyodide.json").then((r) => (r.ok ? r.json() : null)).catch(() => null),
    fetch("data/agreement.json").then((r) => (r.ok ? r.json() : null)).catch(() => null),
    fetch("data/changes.json").then((r) => (r.ok ? r.json() : [])).catch(() => []),
    fetch("data/blockers.json").then((r) => (r.ok ? r.json() : null)).catch(() => null),
  ]);

  state.data = data;
  state.pyodide = pyodideReleases(pyodide);
  state.history = Array.isArray(hist) ? hist.filter((h) => h && typeof h === "object") : [];
  state.changes = Array.isArray(changes) ? changes.filter((c) => c && typeof c === "object") : [];
  state.blockers = blockers && typeof blockers === "object" ? blockers : null;
  state.python = pythons()[0];

  const pkg = readHash();
  if (pkg) $("search").value = pkg;
  syncGapsControls();

  renderKPIs();
  renderMeta();
  renderAgreement(agreement);
  renderAll();
  if (pkg) renderLookup(pkg);

  // Python version tabs
  $("python-tabs").addEventListener("click", (e) => {
    const b = e.target.closest("button[data-python]");
    if (!b) return;
    selectPython(b.dataset.python);
  });
  $("python-tabs").addEventListener("keydown", (e) => {
    if (e.altKey || e.ctrlKey || e.metaKey) return; // Alt+Left still goes back, as everywhere else
    const list = pythons();
    const b = e.target.closest("button[data-python]");
    const index = b ? list.indexOf(b.dataset.python) : -1;
    if (index < 0) return;
    const step = { ArrowRight: 1, ArrowDown: 1, ArrowLeft: -1, ArrowUp: -1 }[e.key];
    const next = e.key === "Home" ? 0 : e.key === "End" ? list.length - 1 : step ? (index + step + list.length) % list.length : -1;
    if (next < 0) return;
    e.preventDefault();
    selectPython(list[next]);
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
    renderBlockers();
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
    renderBlockers();
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
    state.showCompiledOnly = false;
    markUniverse();
    renderLeaderboard();
    renderTrends();
    writeHash();
  });
  $("btn-show-compiled")?.addEventListener("click", () => {
    state.showCompiledOnly = true;
    markUniverse();
    renderLeaderboard();
    renderTrends();
    writeHash();
  });

  // Table filters
  const statusFilters = document.querySelector(".status-filter-pills");
  const selectStatus = (btn) => {
    state.filterStatus = btn.dataset.filter;
    markStatusPill();
    btn.focus();
    state.currentPage = 1;
    renderGaps();
    writeHash();
  };
  statusFilters?.addEventListener("click", (e) => {
    const btn = e.target.closest(".pill-filter");
    if (btn) selectStatus(btn);
  });
  statusFilters?.addEventListener("keydown", (e) => {
    if (e.altKey || e.ctrlKey || e.metaKey) return; // Alt+Left still goes back, as everywhere else
    const step = { ArrowRight: 1, ArrowDown: 1, ArrowLeft: -1, ArrowUp: -1 }[e.key];
    if (!step) return;
    const buttons = Array.from(statusFilters.querySelectorAll(".pill-filter"));
    const index = buttons.indexOf(e.target.closest(".pill-filter"));
    if (index < 0) return;
    e.preventDefault();
    selectStatus(buttons[(index + step + buttons.length) % buttons.length]);
  });

  $("gaps-search")?.addEventListener("input", (e) => {
    state.filterQuery = e.target.value.trim();
    state.currentPage = 1;
    renderGaps();
    writeHash();
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
      state.sortDir = col === "downloads" ? "desc" : "asc";
    }
    markSortHeading();
    state.currentPage = 1;
    renderGaps();
    writeHash();
  });

  // Pagination click delegators (Top & Bottom)
  const handlePageClick = (e) => {
    const btn = e.target.closest("button.page-btn");
    if (!btn || btn.disabled) return;
    const p = Number(btn.dataset.page);
    if (!Number.isFinite(p) || p === state.currentPage) return;
    state.currentPage = p;
    renderGaps();
    $("gaps-title").scrollIntoView({ behavior: scrollBehavior(), block: "start" });
  };
  $("pagination-buttons-top")?.addEventListener("click", handlePageClick);
  $("pagination-buttons-bottom")?.addEventListener("click", handlePageClick);

  // Page size select (Top & Bottom)
  const handlePageSizeChange = (e) => {
    const val = e.target.value;
    state.pageSize = val;
    state.currentPage = 1;
    renderGaps();
    writeHash();
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
    closeSearchSuggestions();
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
    closeSearchSuggestions();
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

  $("search").addEventListener("keydown", (e) => {
    const menu = $("search-autocomplete");
    if (!menu) return;
    if (e.key === "ArrowDown" || e.key === "ArrowUp") {
      if (e.altKey || e.ctrlKey || e.metaKey || e.shiftKey) return; // Shift+Up/Down still extends the text selection
      e.preventDefault();
      if (menu.hidden) {
        updateSearchAutocomplete(e.target.value);
        if (menu.hidden) return;
      }
      const n = searchMatches.length;
      if (e.key === "ArrowDown") searchActive = (searchActive + 1) % n;
      else searchActive = searchActive <= 0 ? n - 1 : searchActive - 1;
      showActiveOption($("search"), "search-opt", n, searchActive);
    } else if (e.key === "Enter" && !menu.hidden && searchActive >= 0) {
      e.preventDefault(); // picks the highlighted suggestion; with none, Enter submits the form as before
      pickSearchSuggestion(searchMatches[searchActive]);
    } else if ((e.key === "Escape" || e.key === "Tab") && !menu.hidden) {
      closeSearchSuggestions(); // Tab moves focus on as usual; the list closes rather than stay open behind it
    }
  });
  function pickSearchSuggestion(name) {
    $("search").value = name;
    updateClearBtn();
    closeSearchSuggestions();
    renderOmniAnswer();
    renderLookup(name);
    writeHash();
  }
  // A press on the list would otherwise move focus off the input (the options aren't focusable): keep it there.
  $("search-autocomplete")?.addEventListener("mousedown", (e) => e.preventDefault());
  $("search-autocomplete")?.addEventListener("click", (e) => {
    const item = e.target.closest(".autocomplete-item");
    if (!item) return;
    pickSearchSuggestion(item.dataset.name);
  });

  // Omni Checker Clear All & Reset
  $("omni-reset-btn")?.addEventListener("click", () => {
    $("search").value = "";
    state.python = pythons()[0];
    state.platform = "musl-aarch64";
    state.currentPage = 1;
    populateOmniSelects();
    updateClearBtn();
    closeSearchSuggestions();
    renderAll();
    renderLookup("");
    writeHash();
  });

  // Close search autocomplete on click outside
  document.addEventListener("click", (e) => {
    if ($("search-autocomplete") && !e.target.closest(".autocomplete-wrapper")) closeSearchSuggestions();
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
    const n = cmdMatches.length;
    if (!n || cmdActive < 0) return;
    if (e.key === "ArrowDown" || e.key === "ArrowUp") {
      if (e.altKey || e.ctrlKey || e.metaKey || e.shiftKey) return; // as in the lookup: modified arrows stay the input's
      e.preventDefault();
      cmdActive = (cmdActive + (e.key === "ArrowDown" ? 1 : n - 1)) % n;
      showActiveOption($("cmd-input"), "cmd-opt", n, cmdActive);
    } else if (e.key === "Enter") {
      e.preventDefault();
      closeCommandPalette();
      openDrawer(cmdMatches[cmdActive]);
    }
  });
  $("cmd-results")?.addEventListener("click", (e) => {
    const item = e.target.closest(".cmd-result-item");
    if (!item) return;
    closeCommandPalette();
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
      if (!$("export-backdrop")?.hidden) closeExport();
      closeSearchSuggestions();
    }
  });

  // Export Modal
  $("export-btn")?.addEventListener("click", openExport);
  $("export-close")?.addEventListener("click", closeExport);
  $("export-backdrop")?.addEventListener("click", (e) => {
    if (e.target === $("export-backdrop")) closeExport();
  });
  $("btn-export-csv")?.addEventListener("click", () => {
    exportCSV();
    closeExport();
  });
  $("btn-export-json")?.addEventListener("click", () => {
    exportJSON();
    closeExport();
  });
  $("export-full-csv")?.addEventListener("click", closeExport);

  for (const id of ["cmd-overlay", "drawer", "export-backdrop"]) {
    $(id)?.addEventListener("keydown", (event) => trapTab($(id), event));
  }

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
