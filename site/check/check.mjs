// The repo checker's page: the form, the target chips and the results. worker.mjs does the fetching and checking.
// Everything shown from the repo or PyPI goes in with textContent, never as HTML.

import {
  PLATFORMS, PYTHONS, parseRepo, inputHint, pickRequestedFile, autoPick, toQuery, fromQuery, sortPythons, freeThreaded,
  fixFor, rowsFor, headline, cliCommand, packageCliCommand, fixPrompt, verdictText, shareText, nextSteps, validRef, labVerdict, labFor, canonicalName, gapWheelFor,
  gapWheelTitle, gapWheelsNote, gapWheelFixText, withoutUnreadable, unreadableReason, privatePyprojectReason, tileText, missedText, privateText, badgeMarkdown, fixLinks,
  notCheckedTail, targetOutsideLock, environmentsText, nextSection, NESTED_DEPTH,
} from "./check-logic.mjs";

const $ = (id) => document.getElementById(id);
const NAMES = Object.fromEntries(PLATFORMS);
const STATUS_TEXT = { wheel: "✓", source: "source", missing: "can't install", unknown: "unknown", "n/a": "–" };
const STAGES = {
  python: "Starting Python in your browser (about 10 MB the first time)…",
  wheelgap: "Installing wheelgap…",
  tree: "Listing the repository's files on GitHub…",
  files: "Reading its workflows and Dockerfiles…",
  lockfile: "Fetching the lockfile…",
  lookups: "Looking packages up on PyPI…",
  checking: "Checking every package…",
};
const SHOWN = 60;
const LAB_SHORT = { works: "builds ✓", gil: "builds, GIL on", pure: "builds, pure Python", unsupported: "unsupported here", partial: "no compiled part", broken: "builds, won't load", fails: "won't build", slow: "too slow" };
const LAB_LOOKUPS = 120;

// The tracker's build lab results for the gap rows' packages, from its own per-package files (same origin). Only rows
// the driver marked as PyPI packages (`lookup`): a private index's package name never leaves the browser, here too.
async function loadLab(result) {
  const names = [...new Set(result.rows.filter((r) => r.gaps.length && r.lookup).map((r) => canonicalName(r.name)))]
    .filter((n) => /^[a-z0-9]+(-[a-z0-9]+)*$/.test(n) && !(n in state.lab))
    .slice(0, LAB_LOOKUPS);
  let next = 0;
  const lane = async () => {
    while (next < names.length) {
      const n = names[next++];
      try {
        const r = await fetch(new URL(`../data/packages/${n}.json`, location.href));
        if (r.ok) {
          const doc = await r.json();
          state.lab[n] = { ok: true, builds: doc.builds ?? [], issues_url: doc.issues_url ?? null };
        } else {
          state.lab[n] = { ok: false, builds: [], issues_url: null };
        }
      } catch {
        state.lab[n] = { ok: false, builds: [], issues_url: null };
      }
    }
  };
  await Promise.all(Array.from({ length: 6 }, lane));
  if (state.result === result) renderTable();
}
const FIX_TEXT = {
  all: (f) => `→ ${f.text}`,
  some: (f) => `→ ${f.text}`,
  loses: () => "only by losing a wheel elsewhere",
  none: () => "no newer wheel",
  private: () => "not looked up (index not known to be PyPI)",
  unchecked: () => "",
};

function el(tag, attrs = {}, ...kids) {
  const e = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (v === null || v === undefined || v === false) continue;
    if (k === "class") e.className = v;
    else if (k === "text") e.textContent = v;
    else if (k.startsWith("on")) e.addEventListener(k.slice(2), v);
    else e.setAttribute(k, v === true ? "" : v);
  }
  for (const kid of kids.flat()) if (kid !== null && kid !== undefined) e.append(kid);
  return e;
}

const worker = new Worker(new URL("./worker.mjs", import.meta.url), { type: "module" });
let seq = 0;
let wheelgapVersion = null;
const state = {
  repo: null, ref: null, root: null, plan: null, detected: null, projectName: null, lockfile: null, auto: false, fileNotice: null, pythons: [], platforms: [],
  pickContext: null, subfolderPick: null,
  result: null, fixes: null, gapWheels: null, fixesDone: false, python: null, platform: null, showAll: false, truncated: false, lab: {},
  inputError: false,
};

function status(content, error = false) {
  if (Array.isArray(content)) $("status").replaceChildren(...content);
  else $("status").textContent = content;
  $("status").classList.toggle("error", error);
}

// Shown until the next check starts: the worker's start-up progress and "Ready." lines mustn't replace it.
function showInputError(raw) {
  state.inputError = true;
  const hint = inputHint(raw);
  if (hint?.kind === "package") {
    status([
      `${raw.trim()} looks like a package, not a repository. `,
      el("a", { href: `../p/${encodeURIComponent(hint.name)}/` }, "See its wheel coverage"),
      ", or ",
      el("a", { href: "../" }, "search packages"),
      ".",
    ], true);
    return;
  }
  if (hint?.kind === "other-host") {
    status([
      "This page reads public GitHub repositories only. For a GitLab or other checkout, run ",
      el("code", { text: "uvx wheelgap" }),
      " in it (",
      el("a", { href: "https://github.com/wheelgap/wheelgap/blob/main/docs/getting-started.md", rel: "noopener" }, "getting started"),
      ").",
    ], true);
    return;
  }
  status("That doesn't look like a GitHub repository. Try owner/name or a github.com URL.", true);
}

// Re-rendering replaces buttons; this puts keyboard focus back on the one with the same data-key.
function keepFocus(fn) {
  const key = document.activeElement?.dataset?.key;
  fn();
  if (key) document.querySelector(`[data-key="${CSS.escape(key)}"]`)?.focus();
}

function workerFailed(message) {
  status(message, true);
  $("go").disabled = false;
}
worker.onerror = (e) => {
  e.preventDefault();
  workerFailed("The checker stopped (Python couldn't load, or the browser ran out of memory). Reload the page to try again.");
};
worker.onmessageerror = () => workerFailed("The checker sent something the page couldn't read. Reload the page to try again.");

worker.onmessage = ({ data }) => {
  if (data.type === "progress") {
    if (data.id === undefined ? !state.inputError : data.id === seq) status(STAGES[data.stage] || "Working…"); // not a dropped request's
    return;
  }
  if (data.type === "ready") {
    wheelgapVersion = data.wheelgap;
    if (!state.repo && !state.inputError) status(`Ready. wheelgap ${data.wheelgap} is running in your browser.`);
    return;
  }
  if (data.id !== undefined && data.id !== seq) return; // an answer to a request the page has moved on from
  if (data.type === "error") {
    status(data.message, true);
    $("go").disabled = false;
    return;
  }
  if (data.type === "loaded") return onLoaded(data);
  if (data.type === "result") return onResult(data.result);
  if (data.type === "gap-wheels") {
    state.gapWheels = data.gapWheels;
    // Said on the page, not only in the tag's tooltip: a touch screen or a keyboard never shows a title.
    $("gap-wheels-note").textContent = gapWheelsNote(state.gapWheels);
    $("gap-wheels-text").textContent = state.gapWheels.text;
    $("gap-wheels").hidden = !state.gapWheels.text;
    renderTable();
    return;
  }
  if (data.type === "fixes") {
    state.fixes = data.fixes;
    state.fixesDone = true;
    renderTable();
  }
};

function start(repo, query = null) {
  state.inputError = false;
  state.repo = repo;
  state.query = query;
  state.result = null;
  state.fileNotice = null;
  $("out").hidden = true;
  $("go").disabled = true;
  $("repo").value = `${repo.owner}/${repo.name}${repo.ref ? `@${repo.ref}` : ""}`;
  status(wheelgapVersion ? STAGES.tree : STAGES.python);
  worker.postMessage({ id: ++seq, type: "load", repo, file: query?.lockfile || repo.file || null });
}

function onLoaded({ plan, detected, truncated, ref, root, fileInTree, projectName = null }) {
  Object.assign(state, { plan, detected, truncated, ref, root, projectName });
  // What autoPick() needs to follow `wheelgap DIR` into the subfolders when the root has no input: whether the root
  // is a project of its own, and whether the whole file list is known (GitHub's tree, and plan()'s list, can be cut).
  // Inputs in folders plan() skips (build/, dist/) that `wheelgap DIR` searches are known by name only (unlistedInputs).
  state.pickContext = {
    rootProjectFiles: plan.root_project_files || [], pyprojects: {}, partial: Boolean(truncated || plan.more_lockfiles),
    unlistedInputs: plan.unlisted_inputs || [],
  };
  const q = state.query;
  const requested = q?.lockfile || state.repo.file || null;
  const picked = pickRequestedFile(plan.lockfiles, requested, Boolean(fileInTree), state.repo.dir, state.pickContext);
  plan.lockfiles = picked.lockfiles;
  state.auto = picked.auto; // the page's own pick, not one a link or the visitor named
  state.subfolderPick = picked.why === "subfolder" ? picked.lock.path : null; // said in the verdict
  state.fileNotice = picked.notice;
  // Set before the no-lockfile answer too: its `uvx wheelgap --deps` command uses them, never the last repo's.
  state.pythons = sortPythons(q?.pythons?.length ? q.pythons : detected.pythons);
  state.platforms = q?.platforms?.length ? q.platforms : detected.platforms.filter((p) => NAMES[p]);
  if (!state.platforms.length) state.platforms = ["linux-x86_64"];
  if (!picked.lock && !picked.choose) {
    status("");
    $("go").disabled = false;
    return noLockfile();
  }
  state.query = null;
  if (!picked.lock) return chooseFile(picked.choose, picked);
  state.lockfile = picked.lock.path;
  runCheck();
}

function runCheck() {
  // No file picked yet (chooseFile()): a platform or Python chip changes the selection, and the check waits for one.
  if (!state.lockfile) return keepFocus(renderSetup);
  state.fixes = null;
  state.fixesDone = false;
  state.showAll = false;
  const fromCi = state.detected.platforms_from === "ci";
  const options = { pythons: state.pythons, platforms: state.platforms };
  for (const k of ["glibc", "musl", "macos"]) if (fromCi && state.detected[k]) options[k] = state.detected[k];
  history.replaceState(null, "", `?${toQuery({ ...state, repo: { ...state.repo, ref: state.repo.ref } })}`);
  const lock = state.plan.lockfiles.find((l) => l.path === state.lockfile);
  status(lock.offline ? STAGES.checking : STAGES.lookups);
  worker.postMessage({ id: ++seq, type: "check", root: state.root, lockfile: state.lockfile, options });
}

function onResult(result) {
  state.gapWheels = null;
  $("gap-wheels").hidden = true;
  $("gap-wheels-note").textContent = "";
  $("gap-wheels-text").textContent = "";
  $("go").disabled = false;
  if (result.kind === "needs-index") return needsIndex(state.lockfile);
  const reason = unreadableReason(result);
  let rest = reason ? withoutUnreadable(state.plan.lockfiles, state.lockfile, state.auto) : null;
  if (rest && privatePyprojectReason(result)) {
    // Its index or source settings apply to the files that look packages up in its folder and below (uv reads
    // [tool.uv] from there), so those names are as private as its own: move on only to a lockfile read offline, or a
    // file elsewhere in the repo.
    const dir = state.lockfile.includes("/") ? state.lockfile.slice(0, state.lockfile.lastIndexOf("/") + 1) : "";
    rest = rest.filter((l) => l.offline || !l.path.startsWith(dir));
    if (!rest.length) rest = null;
  }
  if (rest) {
    // A pyproject.toml the page picked itself, with no dependencies the browser can check (or none at all): the repo's
    // next file is checked instead, and "What was read" says why this one wasn't.
    state.detected.unreadable.push({ path: state.lockfile, line: null, reason });
    // And what it was, for autoPick(): a root pyproject.toml that is a project of its own keeps the one input in a
    // subfolder from being taken for the project's. The driver says whether one wheelgap refused describes a project
    // (`project`); without that answer it's taken for one.
    const ctx = state.pickContext ?? {};
    const was = result.kind === "source" ? (result.project === false ? "settings" : "unread") : privatePyprojectReason(result) ? "private" : "empty";
    state.pickContext = { ...ctx, pyprojects: { ...ctx.pyprojects, [state.lockfile]: was } };
    state.plan.lockfiles = rest;
    const pick = autoPick(rest, state.repo.dir, state.pickContext);
    state.subfolderPick = pick.why === "subfolder" ? pick.lock.path : null;
    if (!pick.lock) return chooseFile(pick.choose, pick);
    state.lockfile = pick.lock.path;
    return runCheck();
  }
  if (result.error) {
    const notice = state.fileNotice ? `${state.fileNotice} Checked ${state.lockfile} instead, and ` : "";
    status(result.kind === "source" ? `${notice}${notice ? "w" : "W"}heelgap couldn't read ${state.lockfile}: ${result.error}` : `${notice}${result.error}`, true);
    if (result.kind === "source" && state.projectName && state.lockfile === "pyproject.toml") {
      showPackageFallback(state.projectName);
    }
    return;
  }
  state.result = result;
  const pys = sortPythons(result.targets.map((t) => t.python));
  if (!pys.includes(state.python)) state.python = pys.find((p) => result.targets.some((t) => t.python === p && !targetOutsideLock(result, t) && !result.unknown_environments?.[t.label])) ?? pys[0];
  if (state.platform && !state.platforms.includes(state.platform)) state.platform = null;
  status(`Checked with wheelgap ${result.wheelgap}, in your browser.`);
  loadLab(result);
  keepFocus(render);
  $("out").hidden = false;
}

function showPackageFallback(packageName) {
  const pythons = state.pythons?.length ? state.pythons : sortPythons(state.detected?.pythons || []);
  const platforms = state.platforms?.length
    ? state.platforms
    : (state.detected?.platforms || []).filter((p) => NAMES[p]);
  const cmd = packageCliCommand(packageName, pythons, platforms.length ? platforms : ["linux-x86_64"]);
  $("out").hidden = false;
  $("verdict").textContent = `No lockfile found, but this looks like the PyPI package ${packageName}.`;
  $("verdict-detail").textContent = "wheelgap can check the published package's dependencies directly:";
  const items = [];
  if (cmd) {
    items.push(
      el("pre", {}, el("code", { text: cmd })),
      el("button", { type: "button", class: "action", text: "Copy command", onclick: (e) => copy(e.currentTarget, cmd, "Command copied") }),
    );
  }
  const trackerSlot = el("p", { class: "note" });
  items.push(trackerSlot);
  // A link named a file this page can't check: that comes first.
  const notice = state.fileNotice ? [el("p", { class: "alert warn", text: state.fileNotice })] : [];
  $("alerts").replaceChildren(...notice, ...items);
  hideResultSections(true);
  const canonical = canonicalName(packageName);
  if (/^[a-z0-9]+(-[a-z0-9]+)*$/.test(canonical) && typeof fetch === "function") {
    fetch(new URL(`../data/packages/${canonical}.json`, location.href), { method: "HEAD" })
      .then((r) => {
        if (r.ok && state.projectName === packageName) {
          trackerSlot.replaceChildren(
            "Or view ",
            el("a", { href: `../p/${encodeURIComponent(canonical)}/` }, `${packageName} on the wheelgap tracker`),
            ".",
          );
        }
      })
      .catch(() => {});
  }
}

// No file the page checks by itself (autoPick()): it doesn't guess which one is the project's (as `wheelgap DIR`
// doesn't), so it lists them, says why in `wheelgap DIR`'s words (`pick.why`), and the picker checks the one chosen.
// Without a `why`, they're #229's requirements files under other names.
function chooseFile(files, pick = {}) {
  state.lockfile = null;
  // Drop the lock= of a file the page tried and moved on from: a reload or a shared link would check it as named.
  history.replaceState(null, "", `?${toQuery({ ...state, repo: { ...state.repo, ref: state.repo.ref } })}`);
  state.result = null;
  status("");
  $("go").disabled = false;
  $("out").hidden = false;
  const names = files.map((l) => l.path);
  const some = `${names.slice(0, 8).join(", ")}${names.length > 8 ? `, and ${names.length - 8} more` : ""}`;
  const repoName = `${state.repo.owner}/${state.repo.name}`;
  const atRoot = `${repoName} has no lockfile, requirements.txt or pyproject.toml dependencies at its root`;
  $("verdict").textContent = "Pick the file to check";
  switch (pick.why) {
    case "subfolders": {
      // GitHub cut its file list short, plan() listed only its first files, or it has inputs in folders the page
      // doesn't list (build/, dist/): there may be more than these.
      const partial = state.pickContext?.partial || state.pickContext?.unlistedInputs?.length
        ? " (and maybe more: the page sees only part of this repo's files)" : "";
      $("verdict-detail").textContent = `${atRoot}, but inputs in its subfolders${partial}: ${some}. Which one holds the project's dependencies can't be told, so pick it under "Lockfile" below.`;
      break;
    }
    case "project":
      $("verdict-detail").textContent = `${atRoot}. ${pick.project}, so the one input in its subfolders, ${names[0]}, isn't taken for the project's. To check that one anyway, pick it under "Lockfile" below.`;
      break;
    case "rivals":
      $("verdict-detail").textContent = `${atRoot}, and more than one file that could hold the project's dependencies: ${some}. Pick the one to check under "Lockfile" below.`;
      break;
    case "side":
      $("verdict-detail").textContent = `${atRoot}, and the only inputs it has are, by their names, for development, tests, docs or examples, which wheelgap doesn't check on its own: ${some}. To check one anyway, pick it under "Lockfile" below.`;
      break;
    case "elsewhere":
      $("verdict-detail").textContent = `${repoName} has no input that wheelgap reads by itself at its root or in a folder up to ${NESTED_DEPTH} down, only ${some}. To check one anyway, pick it under "Lockfile" below.`;
      break;
    default:
      $("verdict-detail").textContent = names.length === 1
        ? `${repoName} has no lockfile the page reads by itself, only ${names[0]}, which by its name is for development, tests, docs or examples, so it isn't checked on its own. To check it anyway, pick it under "Lockfile" below.`
        : `${repoName} has no lockfile the page reads by itself, but ${names.length} requirements files: ${some}. Which one holds the project's dependencies can't be told from its name, so pick it under "Lockfile" below.`;
  }
  // A link named a file this page can't check: say so, as the verdict would after a fallback. And a repo that names
  // its PyPI package (setup.cfg, pyproject.toml) can also be checked as that package.
  const cmd = state.projectName ? packageCliCommand(state.projectName, state.pythons, state.platforms) : null;
  $("alerts").replaceChildren(
    ...(state.fileNotice ? [el("p", { class: "alert warn", text: `${state.fileNotice} Pick a file to check instead.` })] : []),
    ...(cmd ? [el("p", { class: "alert" }, `Or check the published package ${state.projectName}'s dependencies: `, el("code", { text: cmd }))] : []),
  );
  renderSetup();
  hideResultSections(true, true);
}

function noLockfile() {
  if (state.projectName) return showPackageFallback(state.projectName);
  $("out").hidden = false;
  $("verdict").textContent = "No lockfile found";
  const prefix = state.fileNotice ? `${state.fileNotice} ` : "";
  $("verdict-detail").textContent = `${prefix}The repo check reads a uv.lock, poetry.lock, pdm.lock, pylock.toml, Pipfile.lock, requirements.txt, pixi.lock or pyproject.toml, and ${state.repo.owner}/${state.repo.name} has none${state.truncated ? " in the part of its file list GitHub returned" : ""}.`;
  for (const id of ["alerts"]) $(id).replaceChildren();
  hideResultSections(true);
}

function needsIndex(path) {
  $("go").disabled = false;
  $("out").hidden = false;
  status("");
  $("verdict").textContent = `${path.split("/").pop()} needs PyPI lookups`;
  $("verdict-detail").textContent = `It doesn't list each package's files, so the browser check can't read it offline. Run uvx wheelgap ${path} in a checkout instead.`;
  $("alerts").replaceChildren(...(state.fileNotice ? [el("p", { class: "alert warn", text: `${state.fileNotice} Checked ${path} instead.` })] : []));
  renderSetup();
  hideResultSections(true, true);
}

function hideResultSections(hidden, keepSetup = false) {
  if (hidden) {
    $("next").hidden = true;
    $("verdict-badge").hidden = true;
    $("verdict").closest("section").classList.remove("clean");
  }
  for (const id of ["targets-title", "gaps-title", "next-title"]) $(id).closest("section").hidden = hidden;
  $("setup-title").closest("section").hidden = hidden && !keepSetup;
  $("star-line").hidden = hidden;  // shown under a result, with or without gaps
}

function render() {
  hideResultSections(false);
  renderVerdict();
  renderSetup();
  // No package checked: hide empty bars, unless the targets' ? marks need to explain undecidable environments.
  if (state.result.packages === 0 && !Object.keys(state.result.unknown_environments || {}).length) return hideResultSections(true, true);
  renderTargets();
  renderTable();
  renderNext();
}

function notCheckedCount(r) {
  return (r.private || []).length;
}

function renderVerdict() {
  const r = state.result;
  const h = headline(r);
  const notChecked = notCheckedCount(r);
  const privateReason = privatePyprojectReason(r);
  const card = $("verdict").closest("section");
  const repoName = `${state.repo.owner}/${state.repo.name}`;
  const isClean = h.gaps === 0 && h.unknown === 0 && !h.unknownTargets && h.targets > 0 && h.packages > 0 && notChecked === 0;
  card.classList.toggle("clean", isClean);
  // Not offered while a private index's packages went unchecked: "keep it that way" would claim a pass for them too.
  $("verdict-badge").hidden = !isClean;
  $("verdict").textContent = privateReason ? `${r.lockfile} wasn't checked: ${privateReason}`
    : h.targets === 0 && !h.unknownTargets ? `${repoName}'s lockfile leaves out every target picked` : verdictText(h, notChecked);
  const steps = nextSteps(state.pythons, state.platforms);
  $("next").hidden = !steps.length;
  $("next-buttons").replaceChildren(...steps.map((s) => el("button", {
    type: "button", class: "chip shortcut", text: `+ ${s.label}`, "data-key": `next:${s.label}`,
    onclick: () => { state.pythons = s.pythons; state.platforms = s.platforms; runCheck(); },
  })));
  // A file the page picked from a subfolder, as `wheelgap DIR` does with the only one there: said, never implied.
  const subfolder = state.auto && state.subfolderPick === r.lockfile ? `. No input at the root: checked ${r.lockfile}, the only one in a subfolder.` : "";
  $("verdict-detail").textContent = `${repoName} · ${r.lockfile}${state.ref && state.ref !== "HEAD" ? ` at ${state.ref}` : ""} · Python ${sortPythons(state.pythons).join(", ")} on ${state.platforms.length} platform${state.platforms.length === 1 ? "" : "s"}${subfolder}`;
  const alerts = [];
  if (state.fileNotice) {
    alerts.push(el("p", { class: "alert warn", text: `${state.fileNotice} Checked ${r.lockfile} instead.` }));
  }
  if (r.outside_lock.length) {
    alerts.push(el("p", { class: "alert warn", text: `${r.lockfile} only allows Python ${r.requires_python}, so it has no wheels for ${r.outside_lock.join(", ")} and those aren't counted. Widening requires-python and re-locking is the first step there.` }));
  }
  const environments = environmentsText(r);
  if (environments) alerts.push(el("p", { class: "alert warn", text: environments }));
  if (h.unknown) {
    alerts.push(el("p", { class: "alert warn", text: `${h.unknown} package${h.unknown === 1 ? "" : "s"} couldn't be checked on some targets (marked unknown below), so they aren't counted as having a wheel or not.` }));
  }
  const missed = missedText(r.missed, r.lockfile);
  if (missed) alerts.push(el("p", { class: "alert warn", text: missed }));
  const some = (names) => `${names.slice(0, 8).join(", ")}${names.length > 8 ? `, and ${names.length - 8} more` : ""}`;
  for (const host of r.extra_indexes || []) {
    // The packages that index may serve instead of PyPI's are unknown, never PyPI's answer as if it were certain.
    const served = r.rows.filter((row) => row.reason && row.reason.includes(host)).map((row) => row.name);
    const why = served.length ? ` ${some(served)} may be installed from there instead, so ${served.length === 1 ? "it's" : "they're"} shown as unknown.` : "";
    alerts.push(el("p", { class: "alert warn", text: `${r.lockfile} also installs from ${host} (--extra-index-url). This page checked PyPI only.${why}` }));
  }
  const privateIndex = r.private || [];
  if (privateReason) {
    // The zero-package result hides "Fix it", so keep the local command here with the explanation.
    const command = cliCommand(r, state.pythons, state.platforms);
    if (command) alerts.push(el("p", { class: "alert warn" },
      "Check locally with ", el("code", { text: command }),
      " in a checkout (add --index-url for your index), or commit a uv.lock: this page reads its recorded files without PyPI lookups."));
  } else if (privateIndex.length) {
    alerts.push(el("p", { class: "alert warn", text: privateText(r) }));
  }
  const skipped = r.skipped.filter((name) => !privateIndex.includes(name));
  if (skipped.length) {
    alerts.push(el("p", { class: "alert", text: `Not checked, since they aren't from PyPI (the project itself, or path, Git and workspace packages): ${some(skipped)}.` }));
  }
  const base = r.lockfile.split("/").pop();
  if (!privateReason && r.looked_up && (base === "pyproject.toml" || base.startsWith("requirements"))) {
    alerts.push(el("p", { class: "alert", text: `${r.lockfile} is checked for its direct requirements, as uvx wheelgap ${r.lockfile} does; uvx wheelgap --deps ${r.lockfile} on the command line checks the whole dependency tree.` }));
  }
  $("alerts").replaceChildren(...alerts);
}

function chip(label, pressed, onclick, extra = {}) {
  return el("button", { type: "button", class: `chip${extra.shortcut ? " shortcut" : ""}`, "aria-pressed": String(pressed), title: extra.title, "data-key": `chip:${label}`, onclick },
    label, extra.ci ? el("span", { class: "ci", text: "CI" }) : null);
}

function renderSetup() {
  const d = state.detected;
  const ciTargets = new Set(d.found.filter((f) => f.kind === "target").map((f) => f.value));
  const ciPythons = new Set(d.found.filter((f) => f.kind === "python").map((f) => f.value));
  const from = d.platforms_from === "ci"
    ? "The platforms and Pythons marked CI come from the repo's workflows and Dockerfiles."
    : "The repo's workflows and Dockerfiles don't name a platform wheelgap can read, so this starts from the common ones.";
  $("setup-note").textContent = `${from} Add or remove any of them; the check reruns in a moment.`;

  const toggle = (list, v) => (list.includes(v) ? list.filter((x) => x !== v) : [...list, v]);
  $("platform-chips").replaceChildren(...PLATFORMS.map(([p, name]) => chip(name, state.platforms.includes(p), () => {
    const next = toggle(state.platforms, p);
    if (!next.length) return;
    state.platforms = PLATFORMS.map(([q]) => q).filter((q) => next.includes(q));
    runCheck();
  }, { ci: ciTargets.has(p), title: p })));

  const pys = sortPythons([...PYTHONS, ...state.pythons, ...ciPythons]);
  const ft = freeThreaded(state.pythons);
  $("python-chips").replaceChildren(
    ...pys.map((v) => chip(v.endsWith("t") ? `${v} free-threaded` : `Python ${v}`, state.pythons.includes(v), () => {
      const next = toggle(state.pythons, v);
      if (!next.length) return;
      state.pythons = sortPythons(next);
      runCheck();
    }, { ci: ciPythons.has(v) })),
  );
  if (ft && !pys.includes(ft)) {
    $("python-chips").append(chip(`+ ${ft} free-threaded`, false, () => { state.pythons = sortPythons([...state.pythons, ft]); runCheck(); }, { shortcut: true }));
  }

  const locks = state.plan.lockfiles;
  $("lock-pick").hidden = locks.length < 2 && Boolean(state.lockfile); // a chooser has a picker, even for one file
  $("lockfile").replaceChildren(
    ...(state.lockfile ? [] : [el("option", { value: "", disabled: true, selected: true, text: "Choose a file…" })]),
    ...locks.map((l) => el("option", { value: l.path, selected: l.path === state.lockfile, text: l.offline ? l.path : `${l.path} (looks packages up on PyPI)` })),
  );
  $("lockfile").onchange = () => { state.lockfile = $("lockfile").value; state.auto = false; state.fileNotice = null; runCheck(); };

  const repoUrl = `https://github.com/${state.repo.owner}/${state.repo.name}/blob/${encodeURIComponent(state.ref || "HEAD")}/`;
  const link = (path, line) => el("a", { href: `${repoUrl}${path.split("/").map(encodeURIComponent).join("/")}${line ? `#L${line}` : ""}`, rel: "noopener" }, el("code", { text: `${path}${line ? `:${line}` : ""}` }));
  const items = [
    state.lockfile ? el("li", {}, "Lockfile: ", link(state.lockfile)) : el("li", { text: "Lockfile: none picked yet." }),
    ...d.found.map((f) => el("li", {}, `${f.kind === "target" ? "Platform" : "Python"} ${f.value}, from `, link(f.path, f.line), ` (${f.source})`)),
    ...d.unreadable.map((u) => el("li", {}, "Not read: ", link(u.path, u.line), ` (${u.reason})`)),
  ];
  if (!state.plan.workflows.length) items.push(el("li", { text: "No GitHub Actions workflows." }));
  if (state.plan.more_dockerfiles) items.push(el("li", { text: `${state.plan.more_dockerfiles} more Dockerfiles weren't read.` }));
  if (state.truncated) items.push(el("li", { text: "GitHub listed only part of this large repo's files, so some may have been missed." }));
  $("read-list").replaceChildren(...items);
}

function renderTargets() {
  const r = state.result;
  const notChecked = notCheckedCount(r);
  const pys = sortPythons(r.targets.map((t) => t.python));
  $("python-tabs").replaceChildren(...pys.map((v) => el("button", {
    type: "button", "aria-pressed": String(v === state.python), text: v, "data-key": `py:${v}`,
    onclick: () => { state.python = v; state.platform = null; keepFocus(() => { renderTargets(); renderTable(); }); },
  })));
  const outside = r.outside_lock.includes(state.python);
  const rows = r.targets.filter((t) => t.python === state.python).map((t) => {
    const c = r.per_target[t.label];
    const total = c.wheel + c.source + c.missing + c.unknown;
    const excluded = targetOutsideLock(r, t);
    const unknown = excluded ? null : r.unknown_environments?.[t.label];
    const tile = unknown ? { mark: "?", note: "unknown" } : tileText(c, excluded, notChecked);
    const pct = (k) => (total ? (100 * c[k]) / total : 0);
    const pressed = state.platform === t.platform;
    return el("li", {}, el("button", {
      type: "button", "aria-pressed": String(pressed), "data-key": `platform:${t.platform}`,
      onclick: () => { state.platform = pressed ? null : t.platform; keepFocus(() => { renderTargets(); renderTable(); }); },
    },
    el("span", { class: "pname" }, NAMES[t.platform] || t.platform, el("small", { text: t.platform })),
    el("span", { class: "bar", title: unknown || (excluded ? "not locked for this target" : `${c.wheel} wheel, ${c.source} from source, ${c.missing} can't install${c.unknown ? `, ${c.unknown} unknown` : ""}`) },
      ...["wheel", "source", "missing", "unknown"].map((k) => el("span", { class: `b-${k}`, style: `width:${pct(k)}%` }))),
    el("span", { class: "pct" }, tile.mark, el("small", { text: tile.note }))));
  });
  $("platforms").replaceChildren(...rows);
  $("targets-note").textContent = outside
    ? `${r.lockfile} doesn't allow Python ${state.python}, so there's nothing to count.`
    : "Click a platform to list only its gaps.";
}

function renderTable() {
  const r = state.result;
  if (!r) return;
  const labels = r.targets.filter((t) => t.python === state.python && (!state.platform || t.platform === state.platform));
  const unknown = labels.find((t) => r.unknown_environments?.[t.label]);
  let rows = rowsFor(r, state.python);
  if (state.platform) rows = rows.filter((row) => row.here.some((l) => labels.some((t) => t.label === l)));
  $("gaps-title").textContent = `Packages without a wheel on Python ${state.python}${state.platform ? `, ${NAMES[state.platform]}` : ""}`;
  $("gaps-note").textContent = r.outside_lock.includes(state.python)
    ? `${r.lockfile} doesn't allow Python ${state.python} (requires-python ${r.requires_python}), so it has no wheels for it to check. Widen requires-python and re-lock first.`
    : labels.every((t) => targetOutsideLock(r, t))
      ? `${r.lockfile} is not locked for these targets. Add them to [tool.uv] environments and run uv lock first.`
    : unknown
      ? `Targets marked ? weren't checked: ${r.unknown_environments[unknown.label]}.`
    : rows.length
      ? `${rows.length} package${rows.length === 1 ? "" : "s"}. The fix column names the lowest newer release with a wheel for every gap that has one, without losing a wheel elsewhere.`
      : notCheckedCount(r)
        // Packages kept off pypi.org aren't in the rows: "every package" would claim a pass for them too.
        ? `None among the checked packages${notCheckedTail(notCheckedCount(r))}.`
        : labels.some((t) => targetOutsideLock(r, t))
          ? "None on the checked targets. Platforms marked not locked aren't counted."
          : "None: every package installs from a wheel here.";
  const shownLab = rows.some((row) => labels.some((t) => !targetOutsideLock(r, t) && !r.unknown_environments?.[t.label] && row.statuses[t.label] === "source" && labFor(state.lab[canonicalName(row.name)]?.builds, row.version, t.label)));
  if (shownLab) {
    $("gaps-note").textContent += " Under a source build, what the tracker's build lab saw building that release from source on GitHub's runners, which have compilers and many system libraries preinstalled (hover for details).";
  }
  const shown = state.showAll ? rows : rows.slice(0, SHOWN);
  const head = el("thead", {}, el("tr", {},
    el("th", { scope: "col", text: "Package" }),
    ...labels.map((t) => el("th", { scope: "col", text: NAMES[t.platform] || t.platform, title: t.label })),
    el("th", { scope: "col", text: "Fix" })));
  const body = el("tbody", {}, ...shown.map((row) => {
    const f = fixFor(row, state.fixes);
    const gapWheelFix = f.kind === "none" || f.kind === "loses" ? gapWheelFixText(row, state.gapWheels) : null;
    // An unknown-only row can say why (a package an extra index may serve instead of PyPI's).
    const fixText = gapWheelFix ?? (f.kind === "pending" ? (state.fixesDone ? "not looked up" : "…") : f.kind === "unchecked" && row.reason ? row.reason : FIX_TEXT[f.kind](f));
    const name = canonicalName(row.name);
    const labPkg = state.lab[name];
    const hasTracker = Boolean(labPkg?.ok && row.lookup !== false && f.kind !== "private" && f.kind !== "pending");
    const pypiUrl = `https://pypi.org/project/${encodeURIComponent(row.name)}/${encodeURIComponent(row.version || "")}`;

    const pkgKids = [];
    if (hasTracker) {
      pkgKids.push(
        el("a", { href: `../p/${name}/`, text: row.name }),
        el("span", { class: "ver", text: row.version || "" }),
        el("a", { class: "pypi", href: pypiUrl, rel: "noopener", text: "PyPI" })
      );
    } else if (row.lookup === false || f.kind === "private" || f.kind === "pending") {
      pkgKids.push(
        el("span", { text: row.name }),
        el("span", { class: "ver", text: row.version || "" })
      );
    } else {
      pkgKids.push(
        el("a", { href: pypiUrl, rel: "noopener", text: row.name }),
        el("span", { class: "ver", text: row.version || "" })
      );
    }

    const links = fixLinks(row, f, labPkg, state.gapWheels);
    const fixKids = [el("span", { text: fixText })];
    for (const link of links) {
      fixKids.push(el("span", { class: "fix-link" }, el("a", { href: link.href, rel: link.rel || "noopener", text: link.text })));
    }

    return el("tr", {},
      el("td", { class: "pkg" }, ...pkgKids),
      ...labels.map((t) => {
        if (targetOutsideLock(r, t)) return el("td", {}, el("span", { class: "na", text: "–", title: "not locked for this target" }));
        if (r.unknown_environments?.[t.label]) return el("td", {}, el("span", { class: "status s-unknown", text: "? unknown", title: r.unknown_environments[t.label] }));
        const s = row.statuses[t.label];
        if (s === "n/a") return el("td", {}, el("span", { class: "na", text: "–", title: "not installed on this target" }));
        const lab = s === "source" ? labFor(labPkg?.builds, row.version, t.label) : null;
        const v = lab ? labVerdict(lab) : null;
        return el("td", {}, el("span", { class: `status s-${s}`, text: STATUS_TEXT[s] || s }),
          v ? el("span", { class: `lab lab-${v.kind}`, text: LAB_SHORT[v.kind], title: `Build lab, ${lab.date} on ${lab.runner}: ${v.text}` }) : null,
          gapWheelFor(row, state.gapWheels, t.label)
            ? el("span", { class: "lab", text: "gap wheel", title: gapWheelTitle(row, state.gapWheels, t.label) })
            : null);
      }),
      el("td", { class: `fix fix-${f.kind}` }, ...fixKids));
  }));
  $("gap-table").replaceChildren(head, body);
  $("gap-table").hidden = !rows.length;
  $("more").hidden = state.showAll || rows.length <= SHOWN;
  $("more").textContent = `Show all ${rows.length}`;
}

function renderNext() {
  const r = state.result;
  const h = headline(r);
  const command = cliCommand(r, state.pythons, state.platforms);
  // A target the lock's environments can't decide is checked locally; with no covered target there's no command.
  const undecided = h.unknownTargets > 0 && h.gaps === 0;
  const next = nextSection(h, notCheckedCount(r));
  $("next-title").textContent = !command ? "Choose a covered target" : undecided ? "Check it locally" : next.title;
  const showPrompt = next.showPrompt && !undecided && !!command;
  $("copy-prompt").hidden = !showPrompt;
  $("prompt-note").hidden = !showPrompt;
  // A command for the visitor's own index needs the placeholder the private-index alert asks them to add.
  const indexFlag = notCheckedCount(r) ? " --index-url YOUR_INDEX_URL" : "";
  $("cli").textContent = command ? command + indexFlag
    : "No selected target is known to be covered by the lockfile's environments. Choose a covered target before checking again.";
  // The link names the lockfile only when a visit with the repo alone would start from another one.
  $("badge-md").textContent = badgeMarkdown(state.repo, r.lockfile, autoPick(state.plan.lockfiles.filter((l) => !l.added), null, state.pickContext ?? {}).lock?.path ?? null);
  const caveats = [
    "A wheel on PyPI means pip and uv install it without compiling; it doesn't test that the package works there.",
    r.looked_up
      ? `Checked from the files PyPI lists for each package in ${r.lockfile}: a pinned one at that version, any other at the newest release that fits each Python. Packages it leaves out on a platform show as –.`
      : "Checked from the files your lockfile lists, for the versions it pins. Packages it leaves out on a platform show as –.",
  ];
  if (state.pythons.some((v) => v.endsWith("t"))) {
    caveats.unshift("Free-threaded: a cp3XXt wheel means it installs; whether it's thread-safe without the GIL is up to the package.");
  }
  $("caveats").replaceChildren(...caveats.map((c) => el("li", { text: c })));
}

async function copy(button, text, done) {
  try {
    await navigator.clipboard.writeText(text);
    const old = button.textContent;
    button.textContent = done;
    button.classList.add("done");
    setTimeout(() => { button.textContent = old; button.classList.remove("done"); }, 1800);
  } catch {
    status("Couldn't copy: your browser blocked the clipboard.", true);
  }
}

$("copy-link").onclick = (e) => copy(e.currentTarget, location.href, "Link copied");
// The line names only what the shown result counted: its own targets, less those the lockfile leaves out,
// never the current selection (a new check may still be running for it), and how many it left off pypi.org.
const counted = (key) => [...new Set(state.result.targets.filter((t) => !targetOutsideLock(state.result, t) && !state.result.unknown_environments?.[t.label]).map((t) => t[key]))];
$("copy-share").onclick = (e) => copy(e.currentTarget, shareText(`${state.repo.owner}/${state.repo.name}`, headline(state.result), counted("python"), counted("platform"), location.href, notCheckedCount(state.result)), "Result copied");
$("copy-prompt").onclick = (e) => copy(e.currentTarget, fixPrompt(`${state.repo.owner}/${state.repo.name}`, state.result, state.fixes, state.pythons, state.platforms, state.gapWheels, state.lab, notCheckedCount(state.result)), "Prompt copied");
$("copy-badge").onclick = (e) => copy(e.currentTarget, $("badge-md").textContent, "Badge Markdown copied");
$("copy-verdict-badge").onclick = (e) => copy(e.currentTarget, $("badge-md").textContent, "Badge Markdown copied");
$("copy-gap-wheels").onclick = (e) => copy(e.currentTarget, state.gapWheels.text, "Gap wheel instructions copied");
$("more").onclick = () => { state.showAll = true; renderTable(); };

$("form").addEventListener("submit", (e) => {
  e.preventDefault();
  let text = $("repo").value.trim();
  let ref = null;
  const at = text.match(/^([^\s@]+\/[^\s@]+)@([A-Za-z0-9._\/-]+)$/); // owner/name@ref
  if (at) [, text, ref] = at;
  const repo = parseRepo(text);
  if (!repo) return showInputError(text);
  if (ref) {
    if (!validRef(ref)) {
      state.inputError = true; // like showInputError(): start-up progress mustn't replace it
      return status("That branch or tag name isn't one this page can read.", true);
    }
    repo.ref = ref;
  }
  start(repo);
});
for (const b of document.querySelectorAll("[data-repo]")) b.addEventListener("click", () => start(parseRepo(b.dataset.repo)));

const initial = fromQuery(location.search);
if (initial) start(initial.repo, initial);
else {
  const rawRepo = new URLSearchParams(location.search).get("repo");
  if (rawRepo) {
    $("repo").value = rawRepo;
    showInputError(rawRepo);
  }
}
