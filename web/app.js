"use strict";

const BROWSER_SETTINGS_KEY = "immichPeopleWorkbench.connection.v1";
const MERGE_CANVAS_KEY = "immichPeopleWorkbench.mergeCanvas.v1";
const PAGINATION_SETTINGS_KEY = "immichPeopleWorkbench.pagination.v1";
const LEGACY_BROWSER_SETTINGS_KEY = "immichPeopleConsole.connection.v1";
const LEGACY_MERGE_CANVAS_KEY = "immichPeopleConsole.mergeCanvas.v1";
const LEGACY_PAGINATION_SETTINGS_KEY = "immichPeopleConsole.pagination.v1";

function readStoredSetting(key, legacyKey) {
  const current = localStorage.getItem(key);
  if (current !== null) return current;
  const legacy = localStorage.getItem(legacyKey);
  if (legacy !== null) localStorage.setItem(key, legacy);
  return legacy;
}
const MERGE_MIN_WORLD_WIDTH = 12000, MERGE_MIN_WORLD_HEIGHT = 12000;
const MERGE_CARD_WIDTH = 220, MERGE_CARD_HEIGHT = 260, MERGE_MIN_ZOOM = .005, MERGE_MAX_ZOOM = 4;

function readMergeCanvasState() {
  const fallback = { positions: {}, groups: {}, buckets: {}, bucketNames: {}, zoom: 1, scrollLeft: 0, scrollTop: 0, worldWidth: MERGE_MIN_WORLD_WIDTH, worldHeight: MERGE_MIN_WORLD_HEIGHT };
  try {
    const value = JSON.parse(readStoredSetting(MERGE_CANVAS_KEY, LEGACY_MERGE_CANVAS_KEY) || "null");
    if (!value || typeof value.positions !== "object") return fallback;
    const positions = {};
    for (const [id, point] of Object.entries(value.positions)) {
      const x = Number(point?.x), y = Number(point?.y);
      if (Number.isFinite(x) && Number.isFinite(y)) positions[id] = { x, y };
    }
    const groups = {};
    for (const [id, groupId] of Object.entries(value.groups || {})) {
      if (typeof groupId === "string" && groupId) groups[id] = groupId;
    }
    const buckets = {};
    for (const [id, bucketId] of Object.entries(value.buckets || {})) {
      if (/^slot-[1-9]$/.test(bucketId)) buckets[id] = bucketId;
    }
    const bucketNames = {};
    for (const [bucketId, name] of Object.entries(value.bucketNames || value.groupNames || {})) {
      if (/^slot-[1-9]$/.test(bucketId) && typeof name === "string" && name.trim()) bucketNames[bucketId] = name.trim();
    }
    return {
      positions,
      groups,
      buckets,
      bucketNames,
      zoom: Math.max(MERGE_MIN_ZOOM, Math.min(MERGE_MAX_ZOOM, Number(value.zoom) || 1)),
      scrollLeft: Math.max(0, Number(value.scrollLeft) || 0),
      scrollTop: Math.max(0, Number(value.scrollTop) || 0),
      worldWidth: Math.max(MERGE_MIN_WORLD_WIDTH, Number(value.worldWidth) || MERGE_MIN_WORLD_WIDTH),
      worldHeight: Math.max(MERGE_MIN_WORLD_HEIGHT, Number(value.worldHeight) || MERGE_MIN_WORLD_HEIGHT),
    };
  } catch { return fallback; }
}

function readPageSize() {
  try {
    const value = JSON.parse(readStoredSetting(PAGINATION_SETTINGS_KEY, LEGACY_PAGINATION_SETTINGS_KEY) || "null");
    return [12, 24, 48, 60].includes(Number(value?.pageSize)) ? Number(value.pageSize) : 24;
  } catch { return 24; }
}

function savePageSize(pageSize) {
  try { localStorage.setItem(PAGINATION_SETTINGS_KEY, JSON.stringify({ pageSize })); }
  catch { showToast("The browser could not save the pagination setting."); }
}

const ui = {
  connect: document.querySelector("#connect-dialog"), duplicate: document.querySelector("#duplicate-dialog"),
  detail: document.querySelector("#detail-dialog"), toast: document.querySelector("#toast"),
  grid: document.querySelector("#unnamed-grid"), namedGrid: document.querySelector("#named-grid"),
  ignoredGrid: document.querySelector("#ignored-grid"), investigateGrid: document.querySelector("#investigate-grid"), pendingList: document.querySelector("#pending-list"),
  mergeGrid: document.querySelector("#merge-grid"), mergeSelected: document.querySelector("#merge-selected"),
  mergeViewport: document.querySelector("#merge-canvas-viewport"), mergeSurface: document.querySelector("#merge-canvas-surface"),
  facePersonList: document.querySelector("#face-person-list"), faceReviewGrid: document.querySelector("#face-review-grid"),
};

const state = {
  view: "unnamed", summary: null, names: [], sort: "most", duplicateAction: null,
  duplicateInput: null, detailItem: null, detailTarget: null, toastTimer: null,
  pageSize: readPageSize(), pages: { unnamed: 1, investigate: 1, pending: 1, named: 1, ignored: 1, facePeople: 1 },
  mergePeople: [], mergeSelected: new Set(), mergeFocusedId: null, mergeHoveredId: null,
  mergeSelectedBucketId: null, mergeSelectedGroupId: null, mergeTrayTab: "groups",
  mergeCanvas: readMergeCanvasState(), mergeCanvasRestored: false, mergeSaveTimer: null,
  mergeZen: false, mergeTrayHidden: false, mergeAreaSelect: false, mergeHighlightTimer: null,
  mergeReviewGroups: [], mergeReviewIndex: 0, mergeReviewBusy: false, mergeReviewQueued: 0, mergeReviewSkipped: 0, mergeReviewSupported: false,
  faceReviewPeople: [], faceReviewPersonId: null, faceReviewCurrentPerson: null, faceReviewPage: 1,
  faceReviewPageSize: 20, faceReviewLayout: { columns: 5, rows: 4 }, faceReviewResult: null, faceReviewResizeTimer: null,
  faceReviewSelected: new Set(), faceReviewSelectedData: new Map(), faceReviewLoading: false,
};

function saveMergeCanvasState() {
  clearTimeout(state.mergeSaveTimer);
  state.mergeSaveTimer = setTimeout(() => {
    try { localStorage.setItem(MERGE_CANVAS_KEY, JSON.stringify(state.mergeCanvas)); }
    catch { showToast("The browser could not save the canvas layout."); }
  }, 120);
}

function readBrowserSettings() {
  try {
    const raw = readStoredSetting(BROWSER_SETTINGS_KEY, LEGACY_BROWSER_SETTINGS_KEY);
    if (!raw) return null;
    const value = JSON.parse(raw);
    if (!value || typeof value.url !== "string" || typeof value.apiKey !== "string") return null;
    return { url: value.url, apiKey: value.apiKey, insecureTls: Boolean(value.insecureTls) };
  } catch { return null; }
}

function saveBrowserSettings(settings) {
  try { localStorage.setItem(BROWSER_SETTINGS_KEY, JSON.stringify(settings)); return true; }
  catch { return false; }
}

function forgetBrowserSettings() {
  try {
    localStorage.removeItem(BROWSER_SETTINGS_KEY);
    localStorage.removeItem(LEGACY_BROWSER_SETTINGS_KEY);
  } catch { /* Storage may be disabled. */ }
  document.querySelector("#forget-settings").disabled = true;
}

function fillConnectionForm(settings) {
  if (!settings) return;
  document.querySelector("#immich-url").value = settings.url;
  document.querySelector("#immich-key").value = settings.apiKey;
  document.querySelector("#insecure-tls").checked = settings.insecureTls;
  document.querySelector("#remember-connection").checked = true;
  document.querySelector("#forget-settings").disabled = false;
}

function connectionFormSettings() {
  return {
    url: document.querySelector("#immich-url").value.trim(),
    apiKey: document.querySelector("#immich-key").value,
    insecureTls: document.querySelector("#insecure-tls").checked,
  };
}

async function connectWithSettings(settings) {
  return api("/api/connect", { method: "POST", body: settings });
}

async function api(path, options = {}) {
  const init = { method: options.method || "GET", headers: {} };
  if (options.body !== undefined) {
    init.headers["Content-Type"] = "application/json";
    init.body = JSON.stringify(options.body);
  }
  const response = await fetch(path, init);
  const data = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(data.error || `${response.status} ${response.statusText}`);
  return data;
}

function showToast(message) {
  clearTimeout(state.toastTimer);
  ui.toast.textContent = message;
  ui.toast.hidden = false;
  state.toastTimer = setTimeout(() => { ui.toast.hidden = true; }, 4200);
}

function personThumb(id) { return `/media/person/${encodeURIComponent(id)}`; }
function assetThumb(id) { return `/media/asset/${encodeURIComponent(id)}`; }
function cleanName(value) { return value.trim().replace(/\s+/g, " "); }

function paginationPages(current, total) {
  const pages = new Set([1, total]);
  for (let page = current - 2; page <= current + 2; page += 1) {
    if (page > 0 && page <= total) pages.add(page);
  }
  return [...pages].sort((a, b) => a - b);
}

function renderPagination(elementId, result, onPage) {
  const container = document.querySelector(`#${elementId}`);
  if (!container) return;
  container.replaceChildren();
  container.hidden = !result?.total || result.pages <= 1;
  if (!result?.total) return;

  const addButton = (label, page, disabled = false, current = false, title = "") => {
    const button = document.createElement("button"); button.type = "button"; button.textContent = label; button.tabIndex = -1;
    button.disabled = disabled; button.classList.toggle("current", current); if (title) button.title = title;
    if (!disabled && !current) button.addEventListener("click", () => Promise.resolve(onPage(page)).catch(error => showToast(error.message)));
    return button;
  };
  container.append(addButton("«", 1, !result.hasPrevious, false, "First page"));
  container.append(addButton("‹", result.page - 1, !result.hasPrevious, false, "Previous page"));
  const pages = document.createElement("span"); pages.className = "pagination-pages";
  let previous = 0;
  for (const page of paginationPages(result.page, result.pages)) {
    if (previous && page > previous + 1) { const gap = document.createElement("span"); gap.textContent = "…"; pages.append(gap); }
    pages.append(addButton(String(page), page, false, page === result.page, `Page ${page}`)); previous = page;
  }
  container.append(pages);
  const info = document.createElement("span"); info.className = "pagination-info";
  info.textContent = `${result.from}–${result.to} of ${result.total} · page ${result.page}/${result.pages}`;
  container.append(info);
  container.append(addButton("›", result.page + 1, !result.hasNext, false, "Next page"));
  container.append(addButton("»", result.pages, !result.hasNext, false, "Last page"));
}

function resetListPages() {
  for (const key of Object.keys(state.pages)) state.pages[key] = 1;
  state.faceReviewPage = 1;
}

function matchingNames(fragment, excludeId = "") {
  const needle = cleanName(fragment).toLocaleLowerCase();
  if (!needle) return [];
  return state.names
    .filter(person => person.id !== excludeId && person.name.toLocaleLowerCase().includes(needle))
    .sort((a, b) => {
      const ap = a.name.toLocaleLowerCase().startsWith(needle) ? 0 : 1;
      const bp = b.name.toLocaleLowerCase().startsWith(needle) ? 0 : 1;
      return ap - bp || a.name.localeCompare(b.name);
    }).slice(0, 10);
}

function exactMatches(name, excludeId = "") {
  const needle = cleanName(name).toLocaleLowerCase();
  return state.names.filter(person => person.id !== excludeId && person.name.toLocaleLowerCase() === needle);
}

async function refreshSummary() {
  const summary = await api("/api/state");
  state.summary = summary;
  state.sort = summary.sort || state.sort;
  document.querySelector("#sort-select").value = state.sort;
  document.querySelector("#connection-status").textContent = summary.connected ? `Immich ${summary.version} · local queue enabled` : "Not connected";
  document.querySelector("#connect-button").textContent = summary.connected ? "Settings" : "Connect";
  document.querySelector("#reload-button").hidden = !summary.connected;
  for (const key of ["unnamed", "investigate", "faces", "pending", "named", "ignored"]) {
    document.querySelector(`#${key}-count`).textContent = summary.counts[key] ?? 0;
  }
  document.querySelector("#merge-count").textContent = summary.counts.unnamed ?? 0;
  document.querySelector("#pending-count").textContent = summary.counts.pending ?? 0;
  document.querySelector("#reset-button").disabled = !summary.connected || !(summary.counts.pending > 0);
  document.querySelector("#investigate-return-all").disabled = !summary.connected || !(summary.counts.investigate > 0);
  document.querySelector("#merge-sort-select").value = state.sort;
  const working = ["loading", "statistics"].includes(summary.phase);
  const panel = document.querySelector("#progress-panel");
  panel.hidden = !working;
  if (working) {
    document.querySelector("#progress-message").textContent = summary.message;
    const { current, total } = summary.progress;
    document.querySelector("#progress-numbers").textContent = total ? `${current} / ${total}` : "";
    const progress = document.querySelector("#progress-bar");
    if (total) { progress.max = total; progress.value = current; } else { progress.removeAttribute("value"); }
  }
  if (summary.phase === "error") showToast(summary.message);
  return summary;
}

async function loadNames() {
  if (!state.summary?.connected || state.summary.phase !== "ready") return;
  state.names = (await api("/api/names")).people;
}

async function waitUntilReady() {
  for (;;) {
    const summary = await refreshSummary();
    if (["ready", "error"].includes(summary.phase)) break;
    await new Promise(resolve => setTimeout(resolve, 500));
  }
  if (state.summary.phase === "ready") {
    await loadNames();
    await renderCurrentView(true);
  }
}

function switchView(view) {
  state.view = view;
  document.querySelectorAll(".nav-button").forEach(button => button.classList.toggle("active", button.dataset.view === view));
  document.querySelectorAll(".view").forEach(section => section.classList.toggle("active", section.id === `${view}-view`));
  renderCurrentView(true).catch(error => showToast(error.message));
}

async function renderCurrentView(focusFirst = false, refresh = true) {
  if (refresh) await refreshSummary();
  if (state.summary.phase !== "ready") return;
  if (state.view === "unnamed") await renderPeopleGrid("unnamed", ui.grid, focusFirst);
  if (state.view === "merge") await renderMergeWorkbench(focusFirst);
  if (state.view === "investigate") await renderInvestigate();
  if (state.view === "faces") await renderFaceReview();
  if (state.view === "pending") await renderPending();
  if (state.view === "named") await renderPeopleGrid("named", ui.namedGrid, focusFirst);
  if (state.view === "ignored") await renderIgnored();
}

function setGhost(ghost, typed, candidate) {
  ghost.replaceChildren();
  if (!typed || !candidate || !candidate.name.toLocaleLowerCase().startsWith(typed.toLocaleLowerCase()) || candidate.name.length === typed.length) return;
  const hidden = document.createElement("span"); hidden.className = "typed"; hidden.textContent = typed;
  const suffix = document.createElement("span"); suffix.textContent = candidate.name.slice(typed.length);
  ghost.append(hidden, suffix);
}

function showSuggestions(model) {
  const { input, list, ghost } = model;
  list.replaceChildren();
  model.matches.forEach((person, index) => {
    const button = document.createElement("button");
    button.type = "button"; button.className = "suggestion" + (index === model.selectedIndex ? " selected" : "");
    button.tabIndex = -1;
    button.setAttribute("role", "option"); button.setAttribute("aria-selected", String(index === model.selectedIndex));
    const image = document.createElement("img"); image.src = personThumb(person.id); image.alt = "";
    const name = document.createElement("strong"); name.textContent = person.name;
    const count = document.createElement("small");
    count.textContent = person.isPending ? "pending in this run" : person.assetCount == null ? "" : `${person.assetCount} photos`;
    button.append(image, name, count);
    button.addEventListener("mousedown", event => event.preventDefault());
    button.addEventListener("click", () => { selectSuggestion(model, index); input.focus(); });
    list.append(button);
  });
  list.classList.toggle("open", model.matches.length > 0 && document.activeElement === input);
  setGhost(ghost, model.draft, model.ghostCandidate);
}

function updateSuggestions(model, keepDraft = false) {
  const typed = cleanName(model.input.value);
  if (!keepDraft) model.draft = typed;
  model.matches = matchingNames(typed, model.person.id);
  model.ghostCandidate = model.matches.find(person => person.name.toLocaleLowerCase().startsWith(typed.toLocaleLowerCase())) || null;
  if (!keepDraft) { model.selectedIndex = -1; model.selectedTarget = null; }
  showSuggestions(model);
}

function selectSuggestion(model, index) {
  if (index < 0) {
    model.selectedIndex = -1; model.selectedTarget = null; model.input.value = model.draft;
  } else {
    model.selectedIndex = Math.min(index, model.matches.length - 1);
    model.selectedTarget = model.matches[model.selectedIndex];
    model.input.value = model.selectedTarget.name;
  }
  model.ghostCandidate = null;
  showSuggestions(model);
  model.onSelectionChanged?.();
}

async function drawFace(canvas, sample) {
  const image = new Image();
  image.src = assetThumb(sample.assetId);
  await image.decode();
  const box = sample.box || {};
  const sx = Number(box.x1), sy = Number(box.y1), ex = Number(box.x2), ey = Number(box.y2);
  const originalWidth = Number(box.width), originalHeight = Number(box.height);
  const scaleX = image.naturalWidth / (originalWidth || image.naturalWidth);
  const scaleY = image.naturalHeight / (originalHeight || image.naturalHeight);
  const faceWidth = Math.max(1, (ex - sx) * scaleX), faceHeight = Math.max(1, (ey - sy) * scaleY);
  const size = Math.min(Math.max(faceWidth, faceHeight) * 2.05, image.naturalWidth, image.naturalHeight);
  const cx = ((sx + ex) / 2) * scaleX, cy = ((sy + ey) / 2) * scaleY;
  const sourceX = Math.max(0, Math.min(image.naturalWidth - size, cx - size / 2));
  const sourceY = Math.max(0, Math.min(image.naturalHeight - size, cy - size / 2));
  const context = canvas.getContext("2d");
  context.clearRect(0, 0, canvas.width, canvas.height);
  context.drawImage(image, sourceX, sourceY, size, size, 0, 0, canvas.width, canvas.height);
}

async function ensureSamples(model) {
  if (model.samplesLoaded) return;
  model.samplesLoaded = true;
  try {
    const data = await api(`/api/samples/${encodeURIComponent(model.person.id)}`);
    model.slides.push(...data.samples.map(sample => ({ type: "sample", assetId: sample.assetId, sample })));
  } catch (error) {
    showToast(`Could not load additional faces: ${error.message}`);
  }
}

async function showSlide(model) {
  const slide = model.slides[model.slideIndex];
  model.counter.textContent = `${model.slideIndex + 1} / ${model.slides.length}${model.samplesLoaded ? "" : "+"}`;
  if (slide.type === "person") {
    model.image.hidden = false; model.canvas.hidden = true;
  } else {
    model.image.hidden = true; model.canvas.hidden = false;
    try { await drawFace(model.canvas, slide.sample); } catch { model.canvas.hidden = true; model.image.hidden = false; }
  }
}

async function moveSlide(model, direction) {
  if (!model.samplesLoaded) await ensureSamples(model);
  model.slideIndex = (model.slideIndex + direction + model.slides.length) % model.slides.length;
  await showSlide(model);
}

function currentFeature(model) {
  return model.slides[model.slideIndex]?.assetId || null;
}

async function openDuplicate(model, name, target, onConfirm) {
  state.duplicateAction = onConfirm;
  state.duplicateInput = model.input;
  document.querySelector("#duplicate-source").src = personThumb(model.person.id);
  document.querySelector("#duplicate-target").src = personThumb(target.id);
  document.querySelector("#duplicate-name").textContent = target.name;
  ui.duplicate.showModal();
  document.querySelector("#duplicate-confirm").focus();
}

function updateOptimisticNames(model, operation, extra) {
  state.names = state.names.filter(person => person.id !== model.person.id);
  if (operation === "rename") {
    state.names.push({ ...model.person, name: extra.name, isPending: true });
    state.names.sort((a, b) => a.name.localeCompare(b.name));
  }
}

function currentCardIndex(model) {
  const container = model.mode === "unnamed" ? ui.grid : model.mode === "investigate" ? ui.investigateGrid : ui.namedGrid;
  const index = [...container.querySelectorAll(".name-input")].indexOf(model.input);
  return Math.max(0, index);
}

function removeCardAndFocusNext(model) {
  const container = model.mode === "unnamed" ? ui.grid : model.mode === "investigate" ? ui.investigateGrid : ui.namedGrid;
  const inputs = [...container.querySelectorAll(".name-input")];
  const index = inputs.indexOf(model.input);
  const next = inputs[index + 1] || inputs.find(input => input !== model.input) || null;
  model.card.remove();
  if (model.mode === "unnamed" && !container.querySelector(".person-card")) document.querySelector("#unnamed-empty").hidden = false;
  if (model.mode === "investigate" && !container.querySelector(".investigate-card")) document.querySelector("#investigate-empty").hidden = false;
  requestAnimationFrame(() => next?.focus());
}

function queueCard(model, operation, extra = {}) {
  const focusIndex = currentCardIndex(model);
  const payload = { personId: model.person.id, operation, featureAssetId: currentFeature(model), ...extra };
  updateOptimisticNames(model, operation, extra);
  removeCardAndFocusNext(model);
  showToast(operation === "merge" ? `Merge into ${extra.targetName} queued` : operation === "hide" ? "Person queued as ignored" : `${extra.name} queued`);
  api("/api/queue", { method: "POST", body: payload })
    .then(() => refreshSummary())
    .catch(async error => {
      showToast(`Could not save the pending change: ${error.message}`);
      await loadNames();
      if (model.mode === "investigate") await renderInvestigate(true, focusIndex);
      else await renderPeopleGrid(model.mode, model.mode === "unnamed" ? ui.grid : ui.namedGrid, true, focusIndex);
      await refreshSummary();
    });
}

async function submitCard(model) {
  const name = cleanName(model.input.value);
  if (!name) return;
  model.list.classList.remove("open");
  if (model.selectedTarget && model.selectedTarget.name === name) {
    await queueCard(model, "merge", { name, targetPersonId: model.selectedTarget.id, targetName: model.selectedTarget.name });
    return;
  }
  if (["named", "investigate"].includes(model.mode) && name === model.person.name) {
    showToast("Name is unchanged"); return;
  }
  const exact = exactMatches(name, model.person.id);
  const saveSeparate = () => queueCard(model, "rename", { name });
  if (exact.length) { await openDuplicate(model, name, exact[0], saveSeparate); return; }
  await saveSeparate();
}

async function skipCard(model) {
  const focusIndex = currentCardIndex(model);
  await api("/api/skip", { method: "POST", body: { personId: model.person.id } });
  await renderPeopleGrid("unnamed", ui.grid, true, focusIndex);
}

async function investigateCard(model) {
  await api("/api/investigate/add", { method: "POST", body: { personId: model.person.id } });
  removeCardAndFocusNext(model);
  showToast("Moved to Investigate");
  await refreshSummary();
}

function attachNameBehavior(model) {
  const { input } = model;
  input.addEventListener("focus", () => updateSuggestions(model));
  input.addEventListener("input", () => updateSuggestions(model));
  input.addEventListener("blur", () => setTimeout(() => model.list.classList.remove("open"), 120));
  input.addEventListener("keydown", async event => {
    try {
      if (model.mode === "unnamed" && event.altKey && event.key.toLocaleLowerCase() === "i") {
        event.preventDefault(); await queueCard(model, "hide"); return;
      }
      if (model.mode === "unnamed" && event.altKey && event.key.toLocaleLowerCase() === "s") {
        event.preventDefault(); await skipCard(model); return;
      }
      if (model.mode === "unnamed" && event.altKey && event.key.toLocaleLowerCase() === "d") {
        event.preventDefault(); await investigateCard(model); return;
      }
      if (event.key === "PageDown" || event.key === "PageUp") {
        event.preventDefault(); await moveSlide(model, event.key === "PageDown" ? 1 : -1); return;
      }
      if (event.key === "ArrowDown" && model.matches.length) {
        event.preventDefault();
        if (model.selectedIndex < 0) model.draft = cleanName(input.value);
        selectSuggestion(model, Math.min(model.selectedIndex + 1, model.matches.length - 1)); return;
      }
      if (event.key === "ArrowUp" && model.selectedIndex >= 0) {
        event.preventDefault(); selectSuggestion(model, model.selectedIndex - 1); return;
      }
      if (event.key === "Tab" && !event.shiftKey && model.ghostCandidate) {
        event.preventDefault();
        const index = model.matches.findIndex(person => person.id === model.ghostCandidate.id);
        selectSuggestion(model, index); return;
      }
      if (event.key === "Enter") { event.preventDefault(); await submitCard(model); }
    } catch (error) { showToast(error.message); }
  });
}

function createPersonCard(person, mode, index) {
  const card = document.querySelector("#person-card-template").content.firstElementChild.cloneNode(true);
  const image = card.querySelector(".person-thumb"), canvas = card.querySelector(".sample-thumb"), input = card.querySelector(".name-input");
  image.src = personThumb(person.id); image.alt = person.name ? person.name : "Unnamed person";
  input.placeholder = mode === "unnamed" ? "Type a name…" : person.name;
  input.value = mode === "named" ? person.name : "";
  card.querySelector(".photo-count").textContent = person.assetCount == null ? "Photo count unavailable" : `${person.assetCount} photos`;
  card.querySelector(".round-count").textContent = person.skipCount ? `Round ${person.skipCount + 1}` : "";
  const model = {
    person, mode, cardIndex: index, card, image, canvas, input, list: card.querySelector(".suggestions"), ghost: card.querySelector(".ghost"),
    counter: card.querySelector(".carousel-count"), matches: [], ghostCandidate: null, selectedIndex: -1, selectedTarget: null,
    draft: input.value, slides: [{ type: "person", assetId: null }], slideIndex: 0, samplesLoaded: false,
  };
  const previous = card.querySelector(".previous"), next = card.querySelector(".next");
  previous.tabIndex = -1; next.tabIndex = -1;
  previous.addEventListener("click", () => moveSlide(model, -1));
  next.addEventListener("click", () => moveSlide(model, 1));
  if (mode === "unnamed") card.querySelector(".investigate-button").addEventListener("click", () => investigateCard(model).catch(error => showToast(error.message)));
  else card.querySelector(".card-actions").remove();
  attachNameBehavior(model); showSlide(model);
  return card;
}

async function renderPeopleGrid(kind, target, focusFirst = false, focusIndex = 0) {
  const query = kind === "named" ? document.querySelector("#named-search").value.trim() : "";
  const data = await api(`/api/people?kind=${encodeURIComponent(kind)}&sort=${encodeURIComponent(state.sort)}&page=${state.pages[kind] || 1}&size=${state.pageSize}&q=${encodeURIComponent(query)}`);
  const people = data.people;
  state.pages[kind] = data.page;
  target.replaceChildren(...people.map((person, index) => createPersonCard(person, kind, index)));
  renderPagination(`${kind}-pagination`, data, page => {
    state.pages[kind] = page; return renderPeopleGrid(kind, target, true);
  });
  if (kind === "unnamed") {
    document.querySelector("#unnamed-empty").hidden = data.total > 0;
    const banner = document.querySelector("#round-banner");
    const round = people.length ? (people[0].skipCount || 0) + 1 : 1;
    banner.hidden = round === 1;
    banner.textContent = `Round ${round} — only previously skipped clusters remain.`;
  }
  if (focusFirst && people.length) requestAnimationFrame(() => target.querySelectorAll(".name-input")[Math.min(focusIndex, people.length - 1)]?.focus());
}

function selectedMergePeople() {
  const byId = new Map(state.mergePeople.map(person => [person.id, person]));
  return [...state.mergeSelected].map(id => byId.get(id)).filter(Boolean);
}

function clearMergeSelectionContext() {
  state.mergeSelectedBucketId = null;
  state.mergeSelectedGroupId = null;
}

function mergeSurvivor(people) {
  return people.reduce((best, person) => !best || (person.assetCount || 0) > (best.assetCount || 0) ? person : best, null);
}

function mergeGroupColor(groupId) {
  let hash = 0;
  for (const character of groupId) hash = ((hash << 5) - hash + character.charCodeAt(0)) | 0;
  return `hsl(${Math.abs(hash) % 360} 72% 55%)`;
}

function mergeBucketLabel(bucketId) {
  const match = /^slot-([1-9])$/.exec(bucketId || "");
  return match ? match[1] : "•";
}

function nextMergeGroupId() {
  return globalThis.crypto?.randomUUID?.() || `group-${Date.now()}-${Math.random().toString(16).slice(2)}`;
}

function isMergeCardProtected(personId) {
  return Boolean(state.mergeCanvas.groups[personId] || state.mergeCanvas.buckets[personId]);
}

function applyMergeCardGroup(card, personId) {
  const groupId = state.mergeCanvas.groups[personId] || null;
  const bucketId = state.mergeCanvas.buckets[personId] || null;
  card.classList.toggle("canvas-grouped", Boolean(groupId));
  card.classList.toggle("canvas-bucketed", Boolean(bucketId));
  card.dataset.canvasGroup = groupId || "";
  card.dataset.canvasBucket = bucketId || "";
  const groupMark = card.querySelector(".merge-group-mark");
  const bucketMark = card.querySelector(".merge-bucket-mark");
  groupMark.hidden = !groupId;
  if (groupId) {
    const color = mergeGroupColor(groupId);
    card.style.setProperty("--canvas-group-color", color);
    groupMark.style.background = color;
    groupMark.textContent = "G";
    groupMark.title = "Persistent person group";
  } else {
    card.style.removeProperty("--canvas-group-color");
    groupMark.style.removeProperty("background");
  }
  bucketMark.hidden = !bucketId;
  if (bucketId) {
    const color = mergeGroupColor(bucketId);
    card.style.setProperty("--canvas-bucket-color", color);
    bucketMark.style.background = color;
    bucketMark.textContent = mergeBucketLabel(bucketId);
    bucketMark.title = `Mental sorting bucket ${mergeBucketLabel(bucketId)}`;
  } else {
    card.style.removeProperty("--canvas-bucket-color");
    bucketMark.style.removeProperty("background");
  }
}

function makeMergeCanvasGroup() {
  const selected = [...state.mergeSelected];
  if (selected.length < 2) { showToast("Select at least two faces for a person group."); return; }
  const existing = new Set(selected.map(id => state.mergeCanvas.groups[id]).filter(Boolean));
  if (existing.size === 1 && selected.every(id => state.mergeCanvas.groups[id] === [...existing][0])) {
    state.mergeSelectedBucketId = null;
    state.mergeSelectedGroupId = [...existing][0];
    state.mergeTrayTab = "groups";
    renderMergeTray();
    showToast("Selection is already one person group."); return;
  }
  const groupId = nextMergeGroupId();
  for (const id of selected) state.mergeCanvas.groups[id] = groupId;
  state.mergeSelectedBucketId = null;
  state.mergeSelectedGroupId = groupId;
  state.mergeTrayTab = "groups";
  ui.mergeGrid.querySelectorAll(".merge-cluster-card").forEach(card => applyMergeCardGroup(card, card.dataset.personId));
  saveMergeCanvasState();
  renderMergeTray();
  showToast(`Created a person group with ${selected.length} faces.`);
}

function assignMergeNumberGroup(number) {
  const personId = state.mergeHoveredId || state.mergeFocusedId;
  if (!personId || !state.mergePeople.some(person => person.id === personId)) { showToast("Hover or focus a face first."); return; }
  const previousBucket = state.mergeCanvas.buckets[personId] || null;
  if (number === 0) {
    if (!state.mergeCanvas.buckets[personId]) { showToast("That face is not in a canvas bucket."); return; }
    delete state.mergeCanvas.buckets[personId];
    showToast("Removed the face from its canvas bucket.");
  } else {
    const targetBucket = `slot-${number}`;
    if (previousBucket === targetBucket) {
      delete state.mergeCanvas.buckets[personId];
      showToast(`Removed the face from canvas bucket ${number}.`);
    } else {
      state.mergeCanvas.buckets[personId] = targetBucket;
      showToast(`Assigned the face to canvas bucket ${number}.`);
    }
  }
  const card = mergeCardForId(personId); if (card) applyMergeCardGroup(card, personId);
  if (state.mergeSelectedBucketId && previousBucket === state.mergeSelectedBucketId && state.mergeCanvas.buckets[personId] !== previousBucket) {
    state.mergeSelected.delete(personId);
    if (!state.mergeSelected.size) state.mergeSelectedBucketId = null;
    syncMergeCardSelection();
  }
  renderMergeTray(); saveMergeCanvasState();
}

function ungroupMergeSelection() {
  let removed = 0;
  for (const id of state.mergeSelected) {
    if (state.mergeCanvas.groups[id]) { delete state.mergeCanvas.groups[id]; removed += 1; }
  }
  if (state.mergeSelectedGroupId) {
    state.mergeSelectedGroupId = null;
  }
  ui.mergeGrid.querySelectorAll(".merge-cluster-card").forEach(card => applyMergeCardGroup(card, card.dataset.personId));
  saveMergeCanvasState();
  renderMergeTray();
  if (removed) showToast(`Removed ${removed} faces from their person groups.`);
}

function syncMergeCardSelection() {
  const order = new Map([...state.mergeSelected].map((id, index) => [id, index + 1]));
  ui.mergeGrid.querySelectorAll(".merge-cluster-card").forEach(card => {
    const selected = order.has(card.dataset.personId);
    card.classList.toggle("selected", selected);
    card.setAttribute("aria-selected", String(selected));
    card.querySelector(".merge-selection-mark").textContent = selected ? String(order.get(card.dataset.personId)) : "+";
  });
}

function toggleMergePerson(person) {
  clearMergeSelectionContext();
  if (state.mergeSelected.has(person.id)) state.mergeSelected.delete(person.id);
  else state.mergeSelected.add(person.id);
  syncMergeCardSelection();
  renderMergeTray();
}

function collectMergeCollections(property) {
  const collections = new Map();
  for (const person of state.mergePeople) {
    const collectionId = state.mergeCanvas[property][person.id];
    if (!collectionId) continue;
    if (!collections.has(collectionId)) collections.set(collectionId, []);
    collections.get(collectionId).push(person);
  }
  return collections;
}

function setMergeTrayTab(tab) {
  state.mergeTrayTab = tab === "buckets" ? "buckets" : "groups";
  const groupsActive = state.mergeTrayTab === "groups";
  const groupsTab = document.querySelector("#merge-groups-tab");
  const bucketsTab = document.querySelector("#merge-buckets-tab");
  groupsTab.classList.toggle("active", groupsActive);
  bucketsTab.classList.toggle("active", !groupsActive);
  groupsTab.setAttribute("aria-selected", String(groupsActive));
  bucketsTab.setAttribute("aria-selected", String(!groupsActive));
  document.querySelector("#merge-groups-panel").hidden = !groupsActive;
  document.querySelector("#merge-buckets-panel").hidden = groupsActive;
}

function selectMergeCollection(kind, collectionId, people) {
  state.mergeSelected = new Set(people.map(person => person.id));
  state.mergeSelectedGroupId = kind === "group" ? collectionId : null;
  state.mergeSelectedBucketId = kind === "bucket" ? collectionId : null;
  state.mergeTrayTab = kind === "group" ? "groups" : "buckets";
  syncMergeCardSelection(); renderMergeTray();
  requestAnimationFrame(() => focusMergeCollection(people));
}

function renderMergeOrganizerLists() {
  const bucketContainer = document.querySelector("#merge-canvas-buckets");
  const groupContainer = document.querySelector("#merge-person-groups");
  const bucketEntries = [...collectMergeCollections("buckets").entries()].sort(([first], [second]) => {
    const firstNumber = Number(mergeBucketLabel(first)), secondNumber = Number(mergeBucketLabel(second));
    if (Number.isFinite(firstNumber) !== Number.isFinite(secondNumber)) return Number.isFinite(firstNumber) ? -1 : 1;
    return Number.isFinite(firstNumber) ? firstNumber - secondNumber : first.localeCompare(second);
  });
  const groupEntries = [...collectMergeCollections("groups").entries()].sort(([first], [second]) => first.localeCompare(second));
  document.querySelector("#merge-bucket-count").textContent = bucketEntries.length;
  document.querySelector("#merge-person-group-count").textContent = groupEntries.length;
  document.querySelector("#merge-review-groups").disabled = groupEntries.length === 0;
  bucketContainer.replaceChildren(); groupContainer.replaceChildren();

  if (!bucketEntries.length) {
    const empty = document.createElement("p"); empty.className = "muted"; empty.textContent = "Hover a face and press 1–9 to create a mental sorting bucket."; bucketContainer.append(empty);
  }
  for (const [groupId, people] of bucketEntries) {
    const row = document.createElement("article"); row.className = "canvas-group-row";
    row.classList.toggle("active", state.mergeSelectedBucketId === groupId);
    row.style.setProperty("--canvas-group-color", mergeGroupColor(groupId));
    const select = document.createElement("button"); select.type = "button"; select.className = "canvas-group-select";
    select.title = `Select all ${people.length} faces in canvas bucket ${mergeBucketLabel(groupId)}`;
    select.setAttribute("aria-pressed", String(state.mergeSelectedBucketId === groupId));
    const key = document.createElement("span"); key.className = "canvas-group-key"; key.textContent = mergeBucketLabel(groupId);
    const thumbs = document.createElement("span"); thumbs.className = "canvas-group-thumbs";
    for (const person of people.slice(0, 4)) {
      const image = document.createElement("img"); image.src = personThumb(person.id); image.alt = ""; thumbs.append(image);
    }
    const count = document.createElement("span"); count.className = "canvas-group-count"; count.textContent = String(people.length);
    select.append(key, thumbs, count);
    select.addEventListener("click", () => selectMergeCollection("bucket", groupId, people));
    const name = document.createElement("input"); name.className = "canvas-group-name"; name.type = "text";
    name.value = state.mergeCanvas.bucketNames[groupId] || ""; name.placeholder = `Name bucket ${mergeBucketLabel(groupId)}`;
    name.setAttribute("aria-label", `Name canvas bucket ${mergeBucketLabel(groupId)}`);
    name.addEventListener("input", () => {
      const value = name.value.trim();
      if (value) state.mergeCanvas.bucketNames[groupId] = value; else delete state.mergeCanvas.bucketNames[groupId];
      saveMergeCanvasState();
    });
    row.append(select, name); bucketContainer.append(row);
  }

  if (!groupEntries.length) {
    const empty = document.createElement("p"); empty.className = "muted"; empty.textContent = "Select faces and press G to create a person group."; groupContainer.append(empty);
  }
  groupEntries.forEach(([groupId, people], index) => {
    const row = document.createElement("article"); row.className = "canvas-group-row";
    row.classList.toggle("active", state.mergeSelectedGroupId === groupId);
    row.style.setProperty("--canvas-group-color", mergeGroupColor(groupId));
    const select = document.createElement("button"); select.type = "button"; select.className = "canvas-group-select";
    select.title = `Select all ${people.length} faces in person group ${index + 1}`;
    select.setAttribute("aria-pressed", String(state.mergeSelectedGroupId === groupId));
    const key = document.createElement("span"); key.className = "canvas-group-key"; key.textContent = `G${index + 1}`;
    const thumbs = document.createElement("span"); thumbs.className = "canvas-group-thumbs";
    for (const person of people.slice(0, 4)) {
      const image = document.createElement("img"); image.src = personThumb(person.id); image.alt = ""; thumbs.append(image);
    }
    const count = document.createElement("span"); count.className = "canvas-group-count"; count.textContent = String(people.length);
    select.append(key, thumbs, count);
    select.addEventListener("click", () => selectMergeCollection("group", groupId, people));
    row.append(select); groupContainer.append(row);
  });

  setMergeTrayTab(state.mergeTrayTab);
}

function removeMergeTrayPerson(person) {
  const bucketId = state.mergeSelectedBucketId;
  const groupId = state.mergeSelectedGroupId;
  if (bucketId && state.mergeCanvas.buckets[person.id] === bucketId) {
    delete state.mergeCanvas.buckets[person.id];
    state.mergeSelected.delete(person.id);
    const card = mergeCardForId(person.id); if (card) applyMergeCardGroup(card, person.id);
    if (!state.mergeSelected.size) state.mergeSelectedBucketId = null;
    saveMergeCanvasState();
  } else if (groupId && state.mergeCanvas.groups[person.id] === groupId) {
    delete state.mergeCanvas.groups[person.id];
    state.mergeSelected.delete(person.id);
    const card = mergeCardForId(person.id); if (card) applyMergeCardGroup(card, person.id);
    if (!state.mergeSelected.size) {
      state.mergeSelectedGroupId = null;
    }
    saveMergeCanvasState();
  } else {
    state.mergeSelected.delete(person.id);
  }
  syncMergeCardSelection(); renderMergeTray();
}

function renderMergeTray() {
  const people = selectedMergePeople();
  const bucketId = state.mergeSelectedBucketId;
  const bucketMode = Boolean(bucketId);
  const groupId = state.mergeSelectedGroupId;
  const groupMode = Boolean(groupId);
  const survivor = mergeSurvivor(people);
  const bucketName = bucketId ? cleanName(state.mergeCanvas.bucketNames[bucketId] || "") : "";
  const groupIds = [...collectMergeCollections("groups").keys()].sort((first, second) => first.localeCompare(second));
  const groupNumber = groupMode ? Math.max(1, groupIds.indexOf(groupId) + 1) : null;
  document.querySelector("#merge-selection-kicker").textContent = bucketMode
    ? `BUCKET ${mergeBucketLabel(bucketId)}${bucketName ? ` · ${bucketName}` : ""}`
    : groupMode ? `PERSON GROUP G${groupNumber}` : "LOOSE SELECTION";
  document.querySelector("#merge-operation-panel").hidden = !groupMode;
  document.querySelector("#merge-selected-count").textContent = people.length;
  ui.mergeSelected.replaceChildren();
  if (!people.length) {
    const empty = document.createElement("p"); empty.className = "muted"; empty.textContent = bucketMode
      ? "This mental sorting bucket is empty."
      : groupMode ? "This person group is empty." : "Mark faces, then press G to create a person group.";
    ui.mergeSelected.append(empty);
  } else {
    for (const person of people) {
      const row = document.createElement("div"); row.className = "merge-selected-person";
      if (person.id === survivor?.id && people.length >= 2) row.classList.add("survivor");
      const image = document.createElement("img"); image.src = personThumb(person.id); image.alt = "";
      const text = document.createElement("div");
      const strong = document.createElement("strong");
      strong.textContent = bucketMode
        ? `Bucket ${mergeBucketLabel(bucketId)} member`
        : groupMode ? (person.id === survivor?.id && people.length >= 2 ? "Surviving cluster" : "Person-group member") : "Marked face";
      const small = document.createElement("small"); small.textContent = person.assetCount == null ? "Photo count unavailable" : `${person.assetCount} photos`;
      const remove = document.createElement("button"); remove.type = "button"; remove.textContent = "×";
      remove.title = bucketMode ? "Remove from this canvas bucket" : groupMode ? "Remove from this person group" : "Remove from selection";
      remove.addEventListener("click", () => removeMergeTrayPerson(person));
      text.append(strong, small); row.append(image, text, remove); ui.mergeSelected.append(row);
    }
  }

  const targetBox = document.querySelector("#merge-target");
  targetBox.className = "merge-target muted";
  targetBox.textContent = people.length >= 2
    ? `The largest cluster (${survivor?.assetCount ?? "?"} photos) survives unnamed; ${people.length - 1} ${people.length === 2 ? "cluster is" : "clusters are"} merged into it.`
    : "A person group needs at least two clusters.";
  const canQueue = groupMode && people.length >= 2;
  const queue = document.querySelector("#merge-queue"); queue.disabled = !canQueue;
  queue.textContent = `Queue merge · ${Math.max(0, people.length - 1)} ${people.length === 2 ? "merge" : "merges"}`;
  renderMergeOrganizerLists();
}

function moveMergeFocus(card, key) {
  const cards = [...ui.mergeGrid.querySelectorAll(".merge-cluster-card")];
  const current = card.getBoundingClientRect();
  const candidates = cards.filter(candidate => candidate !== card).map(candidate => ({ card: candidate, rect: candidate.getBoundingClientRect() }));
  const directional = candidates.filter(({ rect }) => key === "ArrowLeft" ? rect.right <= current.left + 2 : key === "ArrowRight" ? rect.left >= current.right - 2 : key === "ArrowUp" ? rect.bottom <= current.top + 2 : rect.top >= current.bottom - 2);
  directional.sort((a, b) => {
    const primary = key === "ArrowLeft" ? current.left - a.rect.right : key === "ArrowRight" ? a.rect.left - current.right : key === "ArrowUp" ? current.top - a.rect.bottom : a.rect.top - current.bottom;
    const primaryB = key === "ArrowLeft" ? current.left - b.rect.right : key === "ArrowRight" ? b.rect.left - current.right : key === "ArrowUp" ? current.top - b.rect.bottom : b.rect.top - current.bottom;
    const cross = ["ArrowLeft", "ArrowRight"].includes(key) ? Math.abs(a.rect.top - current.top) : Math.abs(a.rect.left - current.left);
    const crossB = ["ArrowLeft", "ArrowRight"].includes(key) ? Math.abs(b.rect.top - current.top) : Math.abs(b.rect.left - current.left);
    return primary - primaryB || cross - crossB;
  });
  directional[0]?.card.focus();
}

function mergeCardForId(personId) {
  return [...ui.mergeGrid.querySelectorAll(".merge-cluster-card")].find(card => card.dataset.personId === personId) || null;
}

function ensureMergeCanvasPositions(people) {
  const occupied = Object.values(state.mergeCanvas.positions);
  let slot = 0;
  for (const person of people) {
    if (state.mergeCanvas.positions[person.id]) continue;
    let point;
    do {
      point = { x: 140 + (slot % 32) * 255, y: 140 + Math.floor(slot / 32) * 295 };
      slot += 1;
    } while (occupied.some(other => Math.abs(other.x - point.x) < 225 && Math.abs(other.y - point.y) < 265));
    state.mergeCanvas.positions[person.id] = point; occupied.push(point);
  }
  ensureMergeWorldContains(...occupied); saveMergeCanvasState();
}

function positionMergeCard(card, personId) {
  const point = state.mergeCanvas.positions[personId];
  if (point) card.style.transform = `translate(${point.x}px, ${point.y}px)`;
}

function applyMergeCanvasScale() {
  const zoom = state.mergeCanvas.zoom;
  ui.mergeGrid.style.transform = `scale(${zoom})`;
  ui.mergeGrid.style.width = `${state.mergeCanvas.worldWidth}px`;
  ui.mergeGrid.style.height = `${state.mergeCanvas.worldHeight}px`;
  ui.mergeSurface.style.width = `${state.mergeCanvas.worldWidth * zoom}px`;
  ui.mergeSurface.style.height = `${state.mergeCanvas.worldHeight * zoom}px`;
}

function ensureMergeWorldContains(...points) {
  const valid = points.filter(point => Number.isFinite(point?.x) && Number.isFinite(point?.y));
  if (!valid.length) return;
  const roundUp = value => Math.ceil(value / 2000) * 2000;
  const neededWidth = Math.max(...valid.map(point => point.x + MERGE_CARD_WIDTH + 1500));
  const neededHeight = Math.max(...valid.map(point => point.y + MERGE_CARD_HEIGHT + 1500));
  const width = Math.max(state.mergeCanvas.worldWidth, MERGE_MIN_WORLD_WIDTH, roundUp(neededWidth));
  const height = Math.max(state.mergeCanvas.worldHeight, MERGE_MIN_WORLD_HEIGHT, roundUp(neededHeight));
  if (width === state.mergeCanvas.worldWidth && height === state.mergeCanvas.worldHeight) return;
  state.mergeCanvas.worldWidth = width; state.mergeCanvas.worldHeight = height; applyMergeCanvasScale();
}

function setMergeCanvasZoom(nextZoom, clientX = null, clientY = null) {
  const viewport = ui.mergeViewport, oldZoom = state.mergeCanvas.zoom;
  const next = Math.max(MERGE_MIN_ZOOM, Math.min(MERGE_MAX_ZOOM, nextZoom));
  if (Math.abs(next - oldZoom) < .001) return;
  const rect = viewport.getBoundingClientRect();
  const focalX = clientX == null ? viewport.clientWidth / 2 : clientX - rect.left;
  const focalY = clientY == null ? viewport.clientHeight / 2 : clientY - rect.top;
  const worldX = (viewport.scrollLeft + focalX) / oldZoom, worldY = (viewport.scrollTop + focalY) / oldZoom;
  state.mergeCanvas.zoom = next; applyMergeCanvasScale();
  viewport.scrollLeft = worldX * next - focalX; viewport.scrollTop = worldY * next - focalY;
  state.mergeCanvas.scrollLeft = viewport.scrollLeft; state.mergeCanvas.scrollTop = viewport.scrollTop; saveMergeCanvasState();
}

function fitMergeCanvas() {
  const points = state.mergePeople.map(person => state.mergeCanvas.positions[person.id]).filter(Boolean);
  if (!points.length) return;
  const minX = Math.min(...points.map(point => point.x)), minY = Math.min(...points.map(point => point.y));
  const maxX = Math.max(...points.map(point => point.x + MERGE_CARD_WIDTH)), maxY = Math.max(...points.map(point => point.y + MERGE_CARD_HEIGHT));
  const padding = 100, width = maxX - minX + padding * 2, height = maxY - minY + padding * 2;
  const zoom = Math.max(MERGE_MIN_ZOOM, Math.min(1.2, Math.min(ui.mergeViewport.clientWidth / width, ui.mergeViewport.clientHeight / height)));
  state.mergeCanvas.zoom = zoom; applyMergeCanvasScale();
  ui.mergeViewport.scrollLeft = Math.max(0, (minX - padding) * zoom);
  ui.mergeViewport.scrollTop = Math.max(0, (minY - padding) * zoom);
  state.mergeCanvas.scrollLeft = ui.mergeViewport.scrollLeft; state.mergeCanvas.scrollTop = ui.mergeViewport.scrollTop; saveMergeCanvasState();
}

function focusMergeCollection(people) {
  const points = people.map(person => ({ id: person.id, point: state.mergeCanvas.positions[person.id] })).filter(item => item.point);
  if (!points.length) return;
  const minX = Math.min(...points.map(item => item.point.x));
  const minY = Math.min(...points.map(item => item.point.y));
  const maxX = Math.max(...points.map(item => item.point.x + MERGE_CARD_WIDTH));
  const maxY = Math.max(...points.map(item => item.point.y + MERGE_CARD_HEIGHT));
  const padding = 90;
  const width = Math.max(MERGE_CARD_WIDTH, maxX - minX) + padding * 2;
  const height = Math.max(MERGE_CARD_HEIGHT, maxY - minY) + padding * 2;
  const zoom = Math.max(MERGE_MIN_ZOOM, Math.min(1.15, Math.min(ui.mergeViewport.clientWidth / width, ui.mergeViewport.clientHeight / height)));
  state.mergeCanvas.zoom = zoom; applyMergeCanvasScale();
  const left = Math.max(0, (minX - padding) * zoom);
  const top = Math.max(0, (minY - padding) * zoom);
  state.mergeCanvas.scrollLeft = left; state.mergeCanvas.scrollTop = top; saveMergeCanvasState();
  ui.mergeViewport.scrollTo({ left, top, behavior: "smooth" });

  clearTimeout(state.mergeHighlightTimer);
  ui.mergeGrid.querySelectorAll(".merge-cluster-card.collection-highlight").forEach(card => card.classList.remove("collection-highlight"));
  for (const { id } of points) mergeCardForId(id)?.classList.add("collection-highlight");
  state.mergeHighlightTimer = setTimeout(() => {
    ui.mergeGrid.querySelectorAll(".merge-cluster-card.collection-highlight").forEach(card => card.classList.remove("collection-highlight"));
  }, 2900);
}

function focusMergeCard(personId, card = mergeCardForId(personId)) {
  state.mergeFocusedId = personId;
  ui.mergeGrid.querySelectorAll(".merge-cluster-card").forEach(item => item.classList.toggle("focused", item.dataset.personId === personId));
  document.querySelector("#merge-arrange-similar").disabled = !personId;
  card?.focus({ preventScroll: true });
}

async function arrangeSimilarNearby() {
  const anchorId = state.mergeFocusedId || (state.mergeSelected.size === 1 ? [...state.mergeSelected][0] : null);
  if (!anchorId || !state.mergeCanvas.positions[anchorId]) { showToast("Focus one cluster first."); return; }
  const button = document.querySelector("#merge-arrange-similar"), limit = Number(document.querySelector("#merge-similar-count").value) || 12;
  button.disabled = true;
  try {
    const ranked = (await api(`/api/similar/${encodeURIComponent(anchorId)}?limit=${Math.min(100, limit * 5)}`)).people;
    const protectedCount = ranked.filter(person => isMergeCardProtected(person.id)).length;
    const people = ranked.filter(person => !isMergeCardProtected(person.id)).slice(0, limit);
    const anchor = state.mergeCanvas.positions[anchorId];
    people.forEach((person, index) => {
      const ring = Math.floor(index / 8), indexInRing = index % 8, ringCount = Math.min(8, people.length - ring * 8);
      const angle = -Math.PI / 2 + indexInRing * Math.PI * 2 / ringCount, radius = 310 + ring * 260;
      state.mergeCanvas.positions[person.id] = {
        x: Math.max(20, anchor.x + Math.cos(angle) * radius),
        y: Math.max(20, anchor.y + Math.sin(angle) * radius),
      };
      const card = mergeCardForId(person.id); if (card) positionMergeCard(card, person.id);
    });
    ensureMergeWorldContains(...people.map(person => state.mergeCanvas.positions[person.id])); saveMergeCanvasState();
    showToast(`Arranged ${people.length} Immich-ranked similar clusters${protectedCount ? `; kept ${protectedCount} bucketed clusters in place` : ""}.`);
  } catch (error) { showToast(`Could not arrange similar clusters: ${error.message}`); }
  finally { button.disabled = !state.mergeFocusedId; }
}

function arrangeUngroupedGrid() {
  const grouped = state.mergePeople.filter(person => isMergeCardProtected(person.id));
  const loose = state.mergePeople.filter(person => !isMergeCardProtected(person.id));
  const occupied = grouped.map(person => state.mergeCanvas.positions[person.id]).filter(Boolean);
  let slot = 0;
  for (const person of loose) {
    let point;
    do {
      point = { x: 140 + (slot % 32) * 255, y: 140 + Math.floor(slot / 32) * 295 };
      slot += 1;
    } while (occupied.some(other => Math.abs(other.x - point.x) < 235 && Math.abs(other.y - point.y) < 275));
    state.mergeCanvas.positions[person.id] = point; occupied.push(point);
    const card = mergeCardForId(person.id); if (card) positionMergeCard(card, person.id);
  }
  ensureMergeWorldContains(...occupied); saveMergeCanvasState();
  showToast(`Returned ${loose.length} unbucketed clusters to the grid; ${grouped.length} bucketed clusters stayed in place.`);
}

function packMergeSelection() {
  const ids = [...state.mergeSelected].filter(id => state.mergeCanvas.positions[id]);
  if (ids.length < 2) { showToast("Select at least two faces to pack them."); return; }
  const centers = ids.map(id => state.mergeCanvas.positions[id]);
  const centerX = centers.reduce((sum, point) => sum + point.x + MERGE_CARD_WIDTH / 2, 0) / ids.length;
  const centerY = centers.reduce((sum, point) => sum + point.y + MERGE_CARD_HEIGHT / 2, 0) / ids.length;
  const columns = Math.ceil(Math.sqrt(ids.length)), rows = Math.ceil(ids.length / columns);
  const stepX = MERGE_CARD_WIDTH + 24, stepY = MERGE_CARD_HEIGHT + 24;
  const startX = Math.max(20, centerX - (columns * stepX - 24) / 2);
  const startY = Math.max(20, centerY - (rows * stepY - 24) / 2);
  ids.forEach((id, index) => {
    state.mergeCanvas.positions[id] = { x: startX + (index % columns) * stepX, y: startY + Math.floor(index / columns) * stepY };
    const card = mergeCardForId(id); if (card) positionMergeCard(card, id);
  });
  ensureMergeWorldContains(...ids.map(id => state.mergeCanvas.positions[id])); saveMergeCanvasState();
  showToast(`Packed ${ids.length} selected faces into a compact grid.`);
}

function animationFrame() {
  return new Promise(resolve => requestAnimationFrame(resolve));
}

function resolveMergeCardOverlaps(movingIds, desiredById, graphEdges) {
  const gap = 24, stepX = MERGE_CARD_WIDTH + gap, stepY = MERGE_CARD_HEIGHT + gap;
  const buckets = new Map(), rectangles = [];
  const bucketKeys = rectangle => {
    const left = Math.floor((rectangle.x - gap) / stepX), right = Math.floor((rectangle.x + MERGE_CARD_WIDTH + gap) / stepX);
    const top = Math.floor((rectangle.y - gap) / stepY), bottom = Math.floor((rectangle.y + MERGE_CARD_HEIGHT + gap) / stepY);
    const keys = [];
    for (let column = left; column <= right; column += 1) for (let row = top; row <= bottom; row += 1) keys.push(`${column}:${row}`);
    return keys;
  };
  const add = rectangle => {
    const index = rectangles.push(rectangle) - 1;
    for (const key of bucketKeys(rectangle)) {
      if (!buckets.has(key)) buckets.set(key, []);
      buckets.get(key).push(index);
    }
  };
  const collides = candidate => {
    const candidates = new Set(bucketKeys(candidate).flatMap(key => buckets.get(key) || []));
    for (const index of candidates) {
      const other = rectangles[index];
      if (candidate.x < other.x + MERGE_CARD_WIDTH + gap && candidate.x + MERGE_CARD_WIDTH + gap > other.x &&
          candidate.y < other.y + MERGE_CARD_HEIGHT + gap && candidate.y + MERGE_CARD_HEIGHT + gap > other.y) return true;
    }
    return false;
  };
  for (const person of state.mergePeople) {
    if (!isMergeCardProtected(person.id)) continue;
    const point = state.mergeCanvas.positions[person.id];
    if (point) add({ id: person.id, x: point.x, y: point.y });
  }
  const degree = new Map(movingIds.map(id => [id, 0]));
  for (const edge of graphEdges) {
    const weight = Number(edge.weight) || 0;
    if (degree.has(edge.source)) degree.set(edge.source, degree.get(edge.source) + weight);
    if (degree.has(edge.target)) degree.set(edge.target, degree.get(edge.target) + weight);
  }
  const ordered = [...movingIds].sort((first, second) => degree.get(second) - degree.get(first) || first.localeCompare(second));
  const resolved = new Map();
  for (const id of ordered) {
    const desired = desiredById.get(id);
    let chosen = { id, x: Math.max(20, desired.x), y: Math.max(20, desired.y) };
    if (collides(chosen)) {
      let found = null;
      for (let ring = 1; ring <= 80 && !found; ring += 1) {
        const candidates = [];
        for (let offset = -ring; offset <= ring; offset += 1) {
          candidates.push({ id, x: desired.x + offset * stepX, y: desired.y - ring * stepY });
          candidates.push({ id, x: desired.x + offset * stepX, y: desired.y + ring * stepY });
        }
        for (let offset = -ring + 1; offset < ring; offset += 1) {
          candidates.push({ id, x: desired.x - ring * stepX, y: desired.y + offset * stepY });
          candidates.push({ id, x: desired.x + ring * stepX, y: desired.y + offset * stepY });
        }
        candidates.sort((first, second) => (first.x - desired.x) ** 2 + (first.y - desired.y) ** 2 - ((second.x - desired.x) ** 2 + (second.y - desired.y) ** 2));
        found = candidates.find(candidate => candidate.x >= 20 && candidate.y >= 20 && !collides(candidate)) || null;
      }
      if (found) chosen = found;
      else chosen = { id, x: 20, y: Math.max(20, ...rectangles.map(rectangle => rectangle.y + MERGE_CARD_HEIGHT + gap)) };
    }
    add(chosen); resolved.set(id, { x: chosen.x, y: chosen.y });
  }
  return resolved;
}

async function arrangeGlobalSimilarity() {
  const button = document.querySelector("#merge-similarity-map");
  button.disabled = true; button.setAttribute("aria-busy", "true"); button.textContent = "Reading Immich ranks…";
  try {
    const graph = await api("/api/similarity-graph?neighbors=8");
    const available = new Set(state.mergePeople.map(person => person.id));
    const movingIds = graph.nodes.filter(id => available.has(id) && !isMergeCardProtected(id));
    if (movingIds.length < 2) { showToast("At least two unbucketed clusters are needed for a similarity map."); return; }

    const movingIndex = new Map(movingIds.map((id, index) => [id, index]));
    const fixed = new Map(state.mergePeople
      .filter(person => isMergeCardProtected(person.id) && state.mergeCanvas.positions[person.id])
      .map(person => {
        const point = state.mergeCanvas.positions[person.id];
        return [person.id, { x: point.x + MERGE_CARD_WIDTH / 2, y: point.y + MERGE_CARD_HEIGHT / 2 }];
      }));
    const count = movingIds.length, side = Math.max(6000, Math.sqrt(count) * 460);
    const centerX = 300 + side / 2, centerY = 300 + side / 2;
    const x = new Float64Array(count), y = new Float64Array(count);
    const goldenAngle = Math.PI * (3 - Math.sqrt(5));
    movingIds.forEach((id, index) => {
      let hash = 0; for (const character of id) hash = ((hash << 5) - hash + character.charCodeAt(0)) | 0;
      const radius = side * .44 * Math.sqrt((index + .5) / count), angle = index * goldenAngle + (Math.abs(hash) % 1000) / 1000;
      x[index] = centerX + Math.cos(angle) * radius; y[index] = centerY + Math.sin(angle) * radius;
    });
    const edges = graph.edges.map(edge => ({
      a: movingIndex.has(edge.source) ? movingIndex.get(edge.source) : null,
      b: movingIndex.has(edge.target) ? movingIndex.get(edge.target) : null,
      af: fixed.get(edge.source) || null, bf: fixed.get(edge.target) || null,
      strength: (.28 + Math.min(2, Number(edge.weight) || 0) * .42) * (edge.mutual ? 1.25 : 1),
    })).filter(edge => (edge.a != null || edge.af) && (edge.b != null || edge.bf) && !(edge.a == null && edge.b == null));
    const fixedPoints = [...fixed.values()], k = Math.max(330, Math.min(520, side / Math.sqrt(count)));
    const dx = new Float64Array(count), dy = new Float64Array(count), iterations = count > 1000 ? 100 : 150;
    for (let iteration = 0; iteration < iterations; iteration += 1) {
      dx.fill(0); dy.fill(0);
      for (let first = 0; first < count; first += 1) {
        for (let second = first + 1; second < count; second += 1) {
          let deltaX = x[second] - x[first], deltaY = y[second] - y[first];
          let distanceSquared = deltaX * deltaX + deltaY * deltaY;
          if (distanceSquared < 1) { deltaX = 1; deltaY = .5; distanceSquared = 1.25; }
          const force = k * k / distanceSquared;
          dx[first] -= deltaX * force; dy[first] -= deltaY * force;
          dx[second] += deltaX * force; dy[second] += deltaY * force;
        }
        for (const point of fixedPoints) {
          let deltaX = point.x - x[first], deltaY = point.y - y[first];
          let distanceSquared = Math.max(1, deltaX * deltaX + deltaY * deltaY);
          const force = k * k * 1.4 / distanceSquared;
          dx[first] -= deltaX * force; dy[first] -= deltaY * force;
        }
      }
      for (const edge of edges) {
        const ax = edge.a == null ? edge.af.x : x[edge.a], ay = edge.a == null ? edge.af.y : y[edge.a];
        const bx = edge.b == null ? edge.bf.x : x[edge.b], by = edge.b == null ? edge.bf.y : y[edge.b];
        const deltaX = bx - ax, deltaY = by - ay, distance = Math.max(1, Math.hypot(deltaX, deltaY));
        const force = distance * distance / k * edge.strength;
        const pullX = deltaX / distance * force, pullY = deltaY / distance * force;
        if (edge.a != null) { dx[edge.a] += pullX; dy[edge.a] += pullY; }
        if (edge.b != null) { dx[edge.b] -= pullX; dy[edge.b] -= pullY; }
      }
      const temperature = Math.max(4, side * .07 * (1 - iteration / iterations));
      for (let index = 0; index < count; index += 1) {
        dx[index] += (centerX - x[index]) * .018; dy[index] += (centerY - y[index]) * .018;
        const distance = Math.max(1, Math.hypot(dx[index], dy[index])), step = Math.min(distance, temperature);
        x[index] = Math.max(MERGE_CARD_WIDTH / 2 + 20, x[index] + dx[index] / distance * step);
        y[index] = Math.max(MERGE_CARD_HEIGHT / 2 + 20, y[index] + dy[index] / distance * step);
      }
      if (iteration % 10 === 9) {
        button.textContent = `Laying out faces… ${Math.round((iteration + 1) / iterations * 100)}%`;
        await animationFrame();
      }
    }
    button.textContent = "Removing overlaps…"; await animationFrame();
    const desiredById = new Map(movingIds.map((id, index) => [id, {
      x: x[index] - MERGE_CARD_WIDTH / 2, y: y[index] - MERGE_CARD_HEIGHT / 2,
    }]));
    const resolved = resolveMergeCardOverlaps(movingIds, desiredById, graph.edges);
    movingIds.forEach(id => {
      state.mergeCanvas.positions[id] = resolved.get(id);
      const card = mergeCardForId(id); if (card) positionMergeCard(card, id);
    });
    ensureMergeWorldContains(...movingIds.map(id => state.mergeCanvas.positions[id])); saveMergeCanvasState();
    showToast(`Mapped ${movingIds.length} unbucketed clusters without overlaps; canvas buckets and the current camera stayed fixed${graph.failed ? ` (${graph.failed} lookups failed)` : ""}. Press F to fit everything.`);
  } catch (error) { showToast(`Could not build similarity map: ${error.message}`); }
  finally { button.disabled = false; button.removeAttribute("aria-busy"); button.textContent = "Similarity map [M]"; }
}

function createMergeClusterCard(person) {
  const card = document.createElement("article"); card.className = "merge-cluster-card"; card.dataset.personId = person.id; card.tabIndex = 0; card.setAttribute("role", "option");
  const portrait = document.createElement("div"); portrait.className = "portrait-wrap";
  const image = document.createElement("img"); image.className = "portrait person-thumb"; image.src = personThumb(person.id); image.alt = "Unnamed cluster"; image.draggable = false;
  const canvas = document.createElement("canvas"); canvas.className = "portrait sample-thumb"; canvas.width = 480; canvas.height = 480; canvas.hidden = true;
  const previous = document.createElement("button"); previous.className = "carousel-button previous"; previous.type = "button"; previous.tabIndex = -1; previous.textContent = "‹"; previous.setAttribute("aria-label", "Previous face");
  const next = document.createElement("button"); next.className = "carousel-button next"; next.type = "button"; next.tabIndex = -1; next.textContent = "›"; next.setAttribute("aria-label", "Next face");
  const counter = document.createElement("span"); counter.className = "carousel-count";
  portrait.append(image, canvas, previous, next, counter);
  const meta = document.createElement("div"); meta.className = "merge-cluster-meta";
  const count = document.createElement("span"); count.textContent = person.assetCount == null ? "? photos" : `${person.assetCount} photos`;
  const groupMark = document.createElement("span"); groupMark.className = "merge-group-mark"; groupMark.textContent = "G"; groupMark.hidden = true;
  const bucketMark = document.createElement("span"); bucketMark.className = "merge-bucket-mark"; bucketMark.hidden = true;
  const mark = document.createElement("span"); mark.className = "merge-selection-mark";
  meta.append(count, groupMark, bucketMark, mark); card.append(portrait, meta); applyMergeCardGroup(card, person.id);
  const model = { person, image, canvas, counter, slides: [{ type: "person", assetId: null }], slideIndex: 0, samplesLoaded: false };
  previous.addEventListener("pointerdown", event => event.stopPropagation()); previous.addEventListener("click", event => { event.stopPropagation(); moveSlide(model, -1); });
  next.addEventListener("pointerdown", event => event.stopPropagation()); next.addEventListener("click", event => { event.stopPropagation(); moveSlide(model, 1); });
  card.addEventListener("pointerenter", () => { state.mergeHoveredId = person.id; });
  card.addEventListener("pointerleave", () => { if (state.mergeHoveredId === person.id) state.mergeHoveredId = null; });
  card.addEventListener("focus", () => focusMergeCard(person.id, null));
  card.addEventListener("pointerdown", event => {
    if (event.button !== 0 || event.target.closest("button")) return;
    event.stopPropagation(); focusMergeCard(person.id, null); card.setPointerCapture(event.pointerId);
    const originX = event.clientX, originY = event.clientY;
    const movingIds = state.mergeSelected.has(person.id) && state.mergeSelected.size > 1 ? [...state.mergeSelected] : [person.id];
    const starts = new Map(movingIds.map(id => [id, { ...state.mergeCanvas.positions[id] }]));
    let dragged = false;
    const move = moveEvent => {
      const dx = (moveEvent.clientX - originX) / state.mergeCanvas.zoom, dy = (moveEvent.clientY - originY) / state.mergeCanvas.zoom;
      if (!dragged && Math.hypot(dx, dy) < 4) return;
      dragged = true; card.classList.add("dragging");
      for (const id of movingIds) {
        const start = starts.get(id);
        state.mergeCanvas.positions[id] = {
          x: Math.max(0, start.x + dx),
          y: Math.max(0, start.y + dy),
        };
        const movingCard = mergeCardForId(id); if (movingCard) positionMergeCard(movingCard, id);
      }
    };
    const finish = () => {
      card.removeEventListener("pointermove", move); card.removeEventListener("pointerup", finish); card.removeEventListener("pointercancel", finish); card.classList.remove("dragging");
      if (dragged) { ensureMergeWorldContains(...movingIds.map(id => state.mergeCanvas.positions[id])); saveMergeCanvasState(); }
      else toggleMergePerson(person);
    };
    card.addEventListener("pointermove", move); card.addEventListener("pointerup", finish); card.addEventListener("pointercancel", finish);
  });
  card.addEventListener("keydown", async event => {
    if (["PageUp", "PageDown"].includes(event.key)) { event.preventDefault(); await moveSlide(model, event.key === "PageDown" ? 1 : -1); return; }
    if (event.key === " " || event.key === "Enter") { event.preventDefault(); toggleMergePerson(person); return; }
    if (["ArrowLeft", "ArrowRight", "ArrowUp", "ArrowDown"].includes(event.key)) { event.preventDefault(); moveMergeFocus(card, event.key); }
  });
  positionMergeCard(card, person.id); showSlide(model); return card;
}

async function renderMergeWorkbench(focusFirst = false) {
  const people = (await api(`/api/people?kind=unnamed&sort=${encodeURIComponent(state.sort)}`)).people;
  state.mergePeople = people;
  const available = new Set(people.map(person => person.id));
  for (const id of state.mergeSelected) if (!available.has(id)) state.mergeSelected.delete(id);
  if (state.mergeSelectedGroupId && ![...state.mergeSelected].some(id => state.mergeCanvas.groups[id] === state.mergeSelectedGroupId)) {
    state.mergeSelectedGroupId = null;
  }
  if (state.mergeSelectedBucketId && ![...state.mergeSelected].some(id => state.mergeCanvas.buckets[id] === state.mergeSelectedBucketId)) state.mergeSelectedBucketId = null;
  if (state.mergeFocusedId && !available.has(state.mergeFocusedId)) state.mergeFocusedId = null;
  if (state.mergeHoveredId && !available.has(state.mergeHoveredId)) state.mergeHoveredId = null;
  ensureMergeCanvasPositions(people); applyMergeCanvasScale();
  ui.mergeGrid.replaceChildren(...people.map(createMergeClusterCard));
  document.querySelector("#merge-empty").hidden = people.length > 0;
  syncMergeCardSelection(); renderMergeTray();
  requestAnimationFrame(() => {
    if (!state.mergeCanvasRestored) {
      ui.mergeViewport.scrollLeft = state.mergeCanvas.scrollLeft; ui.mergeViewport.scrollTop = state.mergeCanvas.scrollTop; state.mergeCanvasRestored = true;
    }
    if (state.mergeFocusedId) focusMergeCard(state.mergeFocusedId);
    else if (focusFirst && people.length) focusMergeCard(people[0].id);
  });
}

function clearMergeGroup() {
  state.mergeSelected.clear();
  clearMergeSelectionContext();
  syncMergeCardSelection(); renderMergeTray();
}

async function queueMergeGroup() {
  if (!state.mergeSelectedGroupId) { showToast("Select a person group before merging."); return; }
  const people = selectedMergePeople();
  if (people.length < 2) return;
  const survivor = mergeSurvivor(people);
  const button = document.querySelector("#merge-queue"); button.disabled = true;
  try {
    const result = await api("/api/queue/group", { method: "POST", body: { personIds: people.map(person => person.id), survivorPersonId: survivor.id } });
    for (const person of people) delete state.mergeCanvas.groups[person.id];
    saveMergeCanvasState();
    showToast(`Queued ${result.merged} ${result.merged === 1 ? "merge" : "merges"}; the surviving person remains unnamed.`);
    clearMergeGroup(); await renderMergeWorkbench(); await refreshSummary();
  } catch (error) {
    showToast(error.message); renderMergeTray();
  }
}

function currentMergeReviewGroup() {
  return state.mergeReviewGroups[state.mergeReviewIndex] || null;
}

function setMergeReviewStatus(message = "") {
  const status = document.querySelector("#group-review-status");
  status.textContent = message; status.hidden = !message;
}

function renderMergeGroupReview() {
  const entry = currentMergeReviewGroup();
  if (!entry) { finishMergeGroupReview(); return; }
  const survivor = mergeSurvivor(entry.people);
  document.querySelector("#group-review-progress").textContent = `Group ${state.mergeReviewIndex + 1} of ${state.mergeReviewGroups.length} · ${entry.people.length} clusters`;
  const gallery = document.querySelector("#group-review-faces"); gallery.replaceChildren();
  for (const person of entry.people) {
    const item = document.createElement("div"); item.className = "group-review-face";
    if (person.id === survivor?.id) item.classList.add("survivor");
    const image = document.createElement("img"); image.src = personThumb(person.id); image.alt = "Face cluster";
    const label = document.createElement("span");
    label.textContent = person.id === survivor?.id
      ? `Survives · ${person.assetCount ?? "?"} photos`
      : `${person.assetCount ?? "?"} photos`;
    item.append(image, label); gallery.append(item);
  }
  document.querySelector("#group-review-confirm").disabled = !state.mergeReviewSupported || state.mergeReviewBusy || entry.people.length < 2;
  document.querySelector("#group-review-skip").disabled = state.mergeReviewBusy;
  requestAnimationFrame(() => document.querySelector("#group-review-confirm").focus());
}

function openMergeGroupReview() {
  const groups = [...collectMergeCollections("groups").entries()]
    .sort(([first], [second]) => first.localeCompare(second))
    .map(([id, people]) => ({ id, people: [...people] }));
  if (!groups.length) { showToast("There are no person groups to review."); return; }
  state.mergeReviewGroups = groups; state.mergeReviewIndex = 0; state.mergeReviewBusy = false;
  state.mergeReviewQueued = 0; state.mergeReviewSkipped = 0;
  state.mergeReviewSupported = state.summary?.capabilities?.unnamedGroupMerge === true;
  document.querySelector("#group-review-dialog").showModal(); renderMergeGroupReview();
  setMergeReviewStatus(state.mergeReviewSupported ? "" : "The running People Workbench server is still using the old merge API. Restart people_workbench.py, then reload this page before confirming groups.");
}

async function finishMergeGroupReview(cancelled = false) {
  const dialog = document.querySelector("#group-review-dialog");
  if (dialog.open) dialog.close();
  const queued = state.mergeReviewQueued, skipped = state.mergeReviewSkipped;
  state.mergeReviewGroups = []; state.mergeReviewIndex = 0; state.mergeReviewBusy = false;
  setMergeReviewStatus();
  if (queued) { await renderMergeWorkbench(); await refreshSummary(); }
  showToast(cancelled ? `Review closed · ${queued} queued, ${skipped} skipped.` : `Group review complete · ${queued} queued, ${skipped} skipped.`);
}

function skipMergeReviewGroup() {
  if (state.mergeReviewBusy || !currentMergeReviewGroup()) return;
  state.mergeReviewSkipped += 1; state.mergeReviewIndex += 1; renderMergeGroupReview();
}

async function confirmMergeReviewGroup() {
  const entry = currentMergeReviewGroup();
  if (!entry || entry.people.length < 2 || state.mergeReviewBusy || !state.mergeReviewSupported) return;
  state.mergeReviewBusy = true; renderMergeGroupReview();
  setMergeReviewStatus();
  const survivor = mergeSurvivor(entry.people);
  try {
    await api("/api/queue/group", { method: "POST", body: { personIds: entry.people.map(person => person.id), survivorPersonId: survivor.id } });
    for (const person of entry.people) delete state.mergeCanvas.groups[person.id];
    saveMergeCanvasState();
    state.mergeReviewQueued += 1; state.mergeReviewIndex += 1;
  } catch (error) {
    const message = /name is required/i.test(error.message)
      ? "The running server still expects a name. Restart people_workbench.py, reload the page, and try again."
      : `Could not queue this group: ${error.message}`;
    setMergeReviewStatus(message); showToast(message);
  } finally {
    state.mergeReviewBusy = false; renderMergeGroupReview();
  }
}

function mergeMarqueeHits(rect) {
  return [...ui.mergeGrid.querySelectorAll(".merge-cluster-card")].filter(card => {
    const cardRect = card.getBoundingClientRect();
    const centerX = cardRect.left + cardRect.width / 2, centerY = cardRect.top + cardRect.height / 2;
    return centerX >= rect.left && centerX <= rect.right && centerY >= rect.top && centerY <= rect.bottom;
  });
}

function startMergeMarquee(event) {
  const viewport = ui.mergeViewport, bounds = viewport.getBoundingClientRect();
  const clampX = value => Math.max(bounds.left, Math.min(bounds.right, value));
  const clampY = value => Math.max(bounds.top, Math.min(bounds.bottom, value));
  const startX = clampX(event.clientX), startY = clampY(event.clientY);
  const additive = event.shiftKey || event.ctrlKey || event.metaKey;
  const box = document.createElement("div"); box.className = "merge-selection-box"; box.hidden = true; document.body.append(box);
  let currentRect = { left: startX, right: startX, top: startY, bottom: startY }, moved = false;
  viewport.setPointerCapture(event.pointerId); viewport.classList.add("area-selecting");
  const move = moveEvent => {
    const endX = clampX(moveEvent.clientX), endY = clampY(moveEvent.clientY);
    moved = moved || Math.hypot(endX - startX, endY - startY) >= 4;
    currentRect = { left: Math.min(startX, endX), right: Math.max(startX, endX), top: Math.min(startY, endY), bottom: Math.max(startY, endY) };
    box.hidden = !moved; box.style.left = `${currentRect.left}px`; box.style.top = `${currentRect.top}px`;
    box.style.width = `${currentRect.right - currentRect.left}px`; box.style.height = `${currentRect.bottom - currentRect.top}px`;
    const hits = new Set(mergeMarqueeHits(currentRect));
    ui.mergeGrid.querySelectorAll(".merge-cluster-card").forEach(card => card.classList.toggle("marquee-hit", hits.has(card)));
  };
  const cleanup = () => {
    viewport.removeEventListener("pointermove", move); viewport.removeEventListener("pointerup", finish); viewport.removeEventListener("pointercancel", cancel);
    viewport.classList.remove("area-selecting"); box.remove();
    ui.mergeGrid.querySelectorAll(".merge-cluster-card.marquee-hit").forEach(card => card.classList.remove("marquee-hit"));
  };
  const finish = () => {
    const hits = moved ? mergeMarqueeHits(currentRect) : [];
    cleanup();
    clearMergeSelectionContext();
    if (!additive) state.mergeSelected.clear();
    for (const card of hits) state.mergeSelected.add(card.dataset.personId);
    syncMergeCardSelection(); renderMergeTray();
  };
  const cancel = () => cleanup();
  viewport.addEventListener("pointermove", move); viewport.addEventListener("pointerup", finish); viewport.addEventListener("pointercancel", cancel);
}

function initializeMergeCanvas() {
  const viewport = ui.mergeViewport;
  viewport.addEventListener("pointerdown", event => {
    if (event.button !== 0 || event.target.closest(".merge-cluster-card")) return;
    if (state.mergeAreaSelect || event.shiftKey) { event.preventDefault(); startMergeMarquee(event); return; }
    viewport.setPointerCapture(event.pointerId); viewport.classList.add("panning");
    const startX = event.clientX, startY = event.clientY, startLeft = viewport.scrollLeft, startTop = viewport.scrollTop;
    let moved = false;
    const move = moveEvent => {
      const dx = moveEvent.clientX - startX, dy = moveEvent.clientY - startY;
      moved = moved || Math.hypot(dx, dy) >= 4;
      if (!moved) return;
      viewport.scrollLeft = startLeft - dx; viewport.scrollTop = startTop - dy;
    };
    const cleanup = () => {
      viewport.removeEventListener("pointermove", move); viewport.removeEventListener("pointerup", finish); viewport.removeEventListener("pointercancel", cancel); viewport.classList.remove("panning");
    };
    const finish = () => {
      cleanup();
      if (moved) {
        state.mergeCanvas.scrollLeft = viewport.scrollLeft; state.mergeCanvas.scrollTop = viewport.scrollTop; saveMergeCanvasState();
      } else if (state.mergeSelected.size) clearMergeGroup();
    };
    const cancel = () => cleanup();
    viewport.addEventListener("pointermove", move); viewport.addEventListener("pointerup", finish); viewport.addEventListener("pointercancel", cancel);
  });
  viewport.addEventListener("wheel", event => {
    if (!event.ctrlKey) return;
    event.preventDefault(); setMergeCanvasZoom(state.mergeCanvas.zoom * (event.deltaY > 0 ? .9 : 1.1), event.clientX, event.clientY);
  }, { passive: false });
  viewport.addEventListener("scroll", () => {
    state.mergeCanvas.scrollLeft = viewport.scrollLeft; state.mergeCanvas.scrollTop = viewport.scrollTop; saveMergeCanvasState();
  });
  document.querySelector("#merge-zoom-out").addEventListener("click", () => setMergeCanvasZoom(state.mergeCanvas.zoom / 1.15));
  document.querySelector("#merge-zoom-in").addEventListener("click", () => setMergeCanvasZoom(state.mergeCanvas.zoom * 1.15));
  document.querySelector("#merge-zoom-reset").addEventListener("click", fitMergeCanvas);
  document.querySelector("#merge-arrange-similar").addEventListener("click", () => arrangeSimilarNearby());
  document.querySelector("#merge-similarity-map").addEventListener("click", () => arrangeGlobalSimilarity());
  document.querySelector("#merge-toggle-tray").addEventListener("click", () => toggleMergeTray());
  document.querySelector("#merge-zen").addEventListener("click", () => toggleMergeZen());
  document.querySelector("#merge-help").addEventListener("click", () => document.querySelector("#merge-help-dialog").showModal());
  document.querySelector("#merge-groups-tab").addEventListener("click", () => setMergeTrayTab("groups"));
  document.querySelector("#merge-buckets-tab").addEventListener("click", () => setMergeTrayTab("buckets"));
  document.addEventListener("keydown", event => {
    const editing = event.target instanceof Element && Boolean(event.target.closest("input, textarea, select, [contenteditable='true']"));
    if (event.key === "Escape" && !editing && !document.querySelector("dialog[open]")) {
      if (state.view === "merge" && state.mergeSelected.size) { event.preventDefault(); clearMergeGroup(); return; }
      if (state.mergeZen) { toggleMergeZen(false); return; }
    }
    if (state.view !== "merge" || document.querySelector("dialog[open]") || event.ctrlKey || event.metaKey || event.altKey) return;
    if (editing) return;
    const key = event.key.toLocaleLowerCase();
    if (/^[0-9]$/.test(key)) {
      if (event.repeat) return;
      event.preventDefault(); assignMergeNumberGroup(Number(key)); return;
    }
    if (key === "?" && !event.repeat) { event.preventDefault(); document.querySelector("#merge-help-dialog").showModal(); return; }
    const directAction = { a: toggleMergeAreaSelect, g: makeMergeCanvasGroup, u: ungroupMergeSelection, p: packMergeSelection, l: arrangeUngroupedGrid, v: openMergeGroupReview }[key];
    if (directAction) {
      if (event.repeat) return;
      event.preventDefault(); directAction(); return;
    }
    const selector = {
      r: "#merge-arrange-similar", m: "#merge-similarity-map",
      f: "#merge-zoom-reset", h: "#merge-toggle-tray",
      z: "#merge-zen", c: "#merge-clear", "-": "#merge-zoom-out", "+": "#merge-zoom-in", "=": "#merge-zoom-in",
    }[key];
    if (!selector || (event.repeat && !["-", "+", "="].includes(key))) return;
    const button = document.querySelector(selector);
    if (!button || button.disabled) return;
    event.preventDefault(); button.click();
  });
}

function toggleMergeAreaSelect(force = null) {
  const explicit = typeof force === "boolean" ? force : null;
  state.mergeAreaSelect = explicit == null ? !state.mergeAreaSelect : explicit;
  ui.mergeViewport.classList.toggle("area-select-mode", state.mergeAreaSelect);
  showToast(state.mergeAreaSelect ? "Area-select mode on." : "Area-select mode off.");
}

function toggleMergeTray(force = null) {
  const explicit = typeof force === "boolean" ? force : null;
  state.mergeTrayHidden = explicit == null ? !state.mergeTrayHidden : explicit;
  document.body.classList.toggle("merge-tray-hidden", state.mergeTrayHidden);
  const button = document.querySelector("#merge-toggle-tray");
  button.textContent = state.mergeTrayHidden ? "Show tray [H]" : "Hide tray [H]";
  button.setAttribute("aria-pressed", String(state.mergeTrayHidden));
  requestAnimationFrame(applyMergeCanvasScale);
}

function toggleMergeZen(force = null) {
  const explicit = typeof force === "boolean" ? force : null;
  state.mergeZen = explicit == null ? !state.mergeZen : explicit;
  document.body.classList.toggle("merge-zen", state.mergeZen);
  const button = document.querySelector("#merge-zen");
  button.textContent = state.mergeZen ? "Exit Zen [Z]" : "Zen [Z]";
  button.setAttribute("aria-pressed", String(state.mergeZen));
  requestAnimationFrame(applyMergeCanvasScale);
}

async function renderSampleStrip(container, personId, limit = 4) {
  container.replaceChildren();
  try {
    const samples = (await api(`/api/samples/${encodeURIComponent(personId)}`)).samples.slice(0, limit);
    if (!samples.length) {
      const image = document.createElement("img"); image.src = personThumb(personId); image.alt = ""; container.append(image); return;
    }
    for (const sample of samples) {
      const canvas = document.createElement("canvas"); canvas.width = 320; canvas.height = 320; container.append(canvas);
      drawFace(canvas, sample).catch(() => canvas.remove());
    }
  } catch { /* The main person thumbnail remains available. */ }
}

async function renderPending() {
  const data = await api(`/api/pending?page=${state.pages.pending}&size=${state.pageSize}`);
  const pending = data.pending;
  state.pages.pending = data.page;
  ui.pendingList.replaceChildren();
  document.querySelector("#pending-empty").hidden = data.total > 0;
  document.querySelector("#sync-button").disabled = !(state.summary?.counts?.included > 0);
  renderPagination("pending-pagination", data, page => { state.pages.pending = page; return renderPending(); });
  for (const item of pending) {
    const row = document.createElement("article"); row.className = "pending-item" + (item.included ? "" : " excluded");
    const checkbox = document.createElement("input"); checkbox.type = "checkbox"; checkbox.checked = item.included; checkbox.setAttribute("aria-label", "Include in next sync");
    checkbox.addEventListener("change", async () => {
      const path = item.kind === "face" ? "/api/face-detach/include" : "/api/pending/include";
      const body = item.kind === "face" ? { faceId: item.faceId, included: checkbox.checked } : { personId: item.personId, included: checkbox.checked };
      await api(path, { method: "POST", body }); await refreshSummary(); await renderPending();
    });
    const image = document.createElement("img"); image.src = item.kind === "face" ? assetThumb(item.assetId) : item.featureAssetId ? assetThumb(item.featureAssetId) : personThumb(item.personId); image.alt = "";
    const title = document.createElement("div"); title.className = "pending-title";
    const strong = document.createElement("strong");
    strong.textContent = item.kind === "face" ? `${item.currentName || "Unnamed person"}: move face to a new unnamed cluster` : item.operation === "merge" ? `${item.currentName || "Unnamed"} → ${item.targetName || "unnamed merged person"}` : item.operation === "hide" ? "Ignore this person" : item.operation === "unhide" ? `Restore ${item.currentName || "person"}` : item.name;
    const operation = document.createElement("span"); operation.className = "operation"; operation.textContent = item.operation;
    title.append(strong, operation);
    if (item.kind === "face" && item.fileName) { const file = document.createElement("code"); file.textContent = item.fileName; file.title = item.fileName; title.append(file); }
    if (item.lastError) { const error = document.createElement("div"); error.className = "error-text"; error.textContent = item.lastError; title.append(error); }
    const samples = document.createElement("div"); samples.className = "pending-samples";
    const actions = document.createElement("div"); actions.className = "pending-actions";
    if (item.kind === "face") {
      const preview = document.createElement("button"); preview.className = "quiet-button"; preview.textContent = "Preview"; preview.addEventListener("click", () => openAssetPreview(item.assetId, item.fileName));
      const remove = document.createElement("button"); remove.className = "quiet-button"; remove.textContent = "Remove";
      remove.addEventListener("click", async () => { await api("/api/face-detach/return", { method: "POST", body: { faceId: item.faceId } }); await refreshSummary(); await renderPending(); });
      actions.append(preview, remove);
    } else {
      const details = document.createElement("button"); details.className = "quiet-button"; details.textContent = "Inspect"; details.addEventListener("click", () => openDetails(item));
      actions.append(details); renderSampleStrip(samples, item.personId);
    }
    row.append(checkbox, image, title, samples, actions); ui.pendingList.append(row);
  }
}

function openAssetPreview(assetId, fileName = "Photo preview") {
  const dialog = document.querySelector("#asset-preview-dialog");
  document.querySelector("#asset-preview-title").textContent = fileName || "Photo preview";
  document.querySelector("#asset-preview-caption").textContent = "Immich preview — the original file is not downloaded.";
  const image = document.querySelector("#asset-preview-image");
  image.src = assetThumb(assetId); image.alt = fileName || "Photo preview";
  dialog.showModal();
}

async function renderInvestigateMedia(container, personId) {
  try {
    const files = (await api(`/api/files/${encodeURIComponent(personId)}`)).files;
    container.replaceChildren();
    if (!files.length) {
      const empty = document.createElement("p"); empty.className = "muted"; empty.textContent = "No filenames returned by Immich."; container.append(empty); return;
    }
    let index = 0;
    const stage = document.createElement("div"); stage.className = "investigate-photo-stage"; stage.tabIndex = 0;
    const image = document.createElement("img"); image.className = "investigate-photo";
    const previous = document.createElement("button"); previous.type = "button"; previous.className = "carousel-button previous"; previous.tabIndex = -1; previous.textContent = "‹"; previous.setAttribute("aria-label", "Previous photo");
    const next = document.createElement("button"); next.type = "button"; next.className = "carousel-button next"; next.tabIndex = -1; next.textContent = "›"; next.setAttribute("aria-label", "Next photo");
    const counter = document.createElement("span"); counter.className = "carousel-count"; stage.append(image, previous, next, counter);
    const caption = document.createElement("div"); caption.className = "investigate-photo-caption";
    const filename = document.createElement("code");
    const copy = document.createElement("button"); copy.type = "button"; copy.className = "quiet-button"; copy.textContent = "Copy filename"; copy.tabIndex = -1;
    caption.append(filename, copy);
    const strip = document.createElement("div"); strip.className = "investigate-photo-strip";
    const thumbs = files.map((file, fileIndex) => {
      const button = document.createElement("button"); button.type = "button"; button.className = "investigate-photo-thumb"; button.tabIndex = -1; button.title = file.fileName;
      const thumb = document.createElement("img"); thumb.src = assetThumb(file.assetId); thumb.alt = ""; thumb.loading = "lazy";
      button.append(thumb); button.addEventListener("click", () => show(fileIndex)); strip.append(button); return button;
    });
    const show = nextIndex => {
      index = (nextIndex + files.length) % files.length;
      const file = files[index];
      image.src = assetThumb(file.assetId); image.alt = file.fileName; image.title = "Click for a larger preview";
      filename.textContent = file.fileName; filename.title = file.originalPath || file.fileName;
      counter.textContent = `${index + 1} / ${files.length}`;
      thumbs.forEach((button, thumbIndex) => button.classList.toggle("active", thumbIndex === index));
      thumbs[index]?.scrollIntoView({ block: "nearest", inline: "nearest" });
    };
    previous.addEventListener("click", () => show(index - 1)); next.addEventListener("click", () => show(index + 1));
    image.addEventListener("click", () => openAssetPreview(files[index].assetId, files[index].fileName));
    copy.addEventListener("click", async () => { try { await navigator.clipboard.writeText(files[index].fileName); showToast(`Copied ${files[index].fileName}`); } catch { showToast("Could not copy filename"); } });
    stage.addEventListener("keydown", event => {
      if (event.key === "ArrowLeft" || event.key === "ArrowRight") { event.preventDefault(); show(index + (event.key === "ArrowRight" ? 1 : -1)); }
    });
    container.append(stage, caption, strip); show(0);
  } catch (error) {
    container.textContent = `Could not load filenames: ${error.message}`;
  }
}

async function renderInvestigate(focusFirst = false, focusIndex = 0) {
  const data = await api(`/api/people?kind=investigate&sort=${encodeURIComponent(state.sort)}&page=${state.pages.investigate}&size=${state.pageSize}`);
  const people = data.people;
  state.pages.investigate = data.page;
  ui.investigateGrid.replaceChildren();
  document.querySelector("#investigate-empty").hidden = data.total > 0;
  renderPagination("investigate-pagination", data, page => { state.pages.investigate = page; return renderInvestigate(true); });
  for (const [index, person] of people.entries()) {
    const card = document.createElement("article"); card.className = "investigate-card";
    const head = document.createElement("div"); head.className = "investigate-head";
    const portrait = document.createElement("div"); portrait.className = "portrait-wrap investigate-person-portrait";
    const image = document.createElement("img"); image.className = "portrait person-thumb"; image.src = personThumb(person.id); image.alt = person.name || "Unnamed person";
    const canvas = document.createElement("canvas"); canvas.className = "portrait sample-thumb"; canvas.width = 480; canvas.height = 480; canvas.hidden = true;
    const previous = document.createElement("button"); previous.type = "button"; previous.className = "carousel-button previous"; previous.tabIndex = -1; previous.textContent = "‹"; previous.setAttribute("aria-label", "Previous face");
    const next = document.createElement("button"); next.type = "button"; next.className = "carousel-button next"; next.tabIndex = -1; next.textContent = "›"; next.setAttribute("aria-label", "Next face");
    const counter = document.createElement("span"); counter.className = "carousel-count"; portrait.append(image, canvas, previous, next, counter);
    const meta = document.createElement("div");
    const title = document.createElement("h3"); title.textContent = person.name || "Unnamed cluster";
    const count = document.createElement("p"); count.textContent = person.assetCount == null ? "Photo count unavailable" : `${person.assetCount} photos`;
    const nameWrap = document.createElement("div"); nameWrap.className = "autocomplete-wrap name-wrap investigate-name";
    const ghost = document.createElement("div"); ghost.className = "ghost"; ghost.setAttribute("aria-hidden", "true");
    const input = document.createElement("input"); input.className = "name-input"; input.autocomplete = "off"; input.spellcheck = false; input.setAttribute("aria-label", "Person name"); input.placeholder = "Type a name…"; input.value = person.name || "";
    const suggestions = document.createElement("div"); suggestions.className = "suggestions"; suggestions.setAttribute("role", "listbox");
    nameWrap.append(ghost, input, suggestions);
    const restore = document.createElement("button"); restore.type = "button"; restore.className = "quiet-button"; restore.textContent = "Return to naming queue"; restore.tabIndex = -1;
    restore.addEventListener("click", async () => { await api("/api/investigate/remove", { method: "POST", body: { personId: person.id } }); await renderInvestigate(); await refreshSummary(); });
    meta.append(title, count, nameWrap, restore); head.append(portrait, meta);
    const files = document.createElement("div"); files.className = "investigate-media"; files.textContent = "Loading photo previews…";
    card.append(head, files); ui.investigateGrid.append(card);
    const model = {
      person, mode: "investigate", cardIndex: index, card, image, canvas, counter, input, list: suggestions, ghost,
      matches: [], ghostCandidate: null, selectedIndex: -1, selectedTarget: null,
      draft: input.value, slides: [{ type: "person", assetId: null }], slideIndex: 0, samplesLoaded: false,
    };
    previous.addEventListener("click", () => moveSlide(model, -1)); next.addEventListener("click", () => moveSlide(model, 1));
    attachNameBehavior(model); showSlide(model); renderInvestigateMedia(files, person.id);
  }
  if (focusFirst && people.length) requestAnimationFrame(() => ui.investigateGrid.querySelectorAll(".name-input")[Math.min(focusIndex, people.length - 1)]?.focus());
}

function currentFaceReviewPerson() {
  return state.faceReviewCurrentPerson;
}

function calculateFaceReviewCapacity() {
  const fallback = { columns: 5, rows: 4, size: 20, height: 0 };
  const width = ui.faceReviewGrid.clientWidth;
  const top = ui.faceReviewGrid.getBoundingClientRect().top;
  if (!width || !Number.isFinite(top)) return fallback;
  const styles = getComputedStyle(ui.faceReviewGrid);
  const gap = Number.parseFloat(styles.columnGap) || 12;
  const columns = Math.max(1, Math.floor((width + gap) / (180 + gap)));
  const cardWidth = (width - gap * (columns - 1)) / columns;
  const cardHeight = cardWidth + 76;
  const available = Math.max(cardHeight, window.innerHeight - top - 76);
  const rows = Math.max(1, Math.min(4, Math.floor((available + gap) / (cardHeight + gap))));
  return { columns, rows, size: columns * rows, height: Math.ceil(rows * cardHeight + (rows - 1) * gap) };
}

function applyFaceReviewCapacity() {
  const layout = calculateFaceReviewCapacity();
  ui.faceReviewGrid.style.gridTemplateColumns = `repeat(${layout.columns}, minmax(0, 1fr))`;
  ui.faceReviewGrid.style.minHeight = layout.height ? `${layout.height}px` : "";
  const changed = layout.size !== state.faceReviewPageSize;
  state.faceReviewLayout = layout; state.faceReviewPageSize = layout.size;
  return changed;
}

function updateFaceReviewProgress(result = state.faceReviewResult) {
  const person = currentFaceReviewPerson();
  if (!person || !result) return;
  person.reviewedCount = result.reviewedCount || 0;
  person.reviewTotal = result.totalAssets ?? result.reviewTotal ?? person.assetCount ?? 0;
  person.reviewed = Boolean(result.personReviewed);
  const pagePerson = state.faceReviewPeople.find(item => item.id === person.id);
  if (pagePerson && pagePerson !== person) Object.assign(pagePerson, { reviewedCount: person.reviewedCount, reviewTotal: person.reviewTotal, reviewed: person.reviewed });
  const reviewed = person.reviewed ? `✓ Reviewed · ${person.reviewedCount}/${person.reviewTotal}` : `${person.reviewedCount}/${person.reviewTotal} reviewed`;
  document.querySelector("#face-review-meta").textContent = `${person.assetCount ?? result.totalAssets ?? "?"} photos · showing ${result.from}–${result.to} · ${reviewed}`;
  document.querySelector("#face-review-content").classList.toggle("person-reviewed", person.reviewed);
}

function renderFacePersonList() {
  ui.facePersonList.replaceChildren();
  for (const person of state.faceReviewPeople) {
    const button = document.createElement("button"); button.type = "button"; button.className = "face-person-button";
    button.classList.toggle("active", person.id === state.faceReviewPersonId);
    const image = document.createElement("img"); image.src = personThumb(person.id); image.alt = ""; image.loading = "lazy";
    const text = document.createElement("span");
    const name = document.createElement("strong"); name.textContent = person.name || "Unnamed person";
    const count = document.createElement("small");
    const reviewTotal = person.reviewTotal ?? person.assetCount ?? 0;
    count.textContent = person.reviewed ? `✓ Reviewed · ${person.assetCount ?? "?"} photos` : `${person.assetCount ?? "?"} photos · ${person.reviewedCount || 0}/${reviewTotal} reviewed`;
    button.classList.toggle("reviewed", Boolean(person.reviewed));
    text.append(name, count); button.append(image, text);
    button.addEventListener("click", () => selectFaceReviewPerson(person.id));
    ui.facePersonList.append(button);
  }
  if (!state.faceReviewPeople.length) { const empty = document.createElement("p"); empty.className = "muted"; empty.textContent = "No matching people."; ui.facePersonList.append(empty); }
}

async function loadFaceReviewPeople() {
  const query = document.querySelector("#face-person-search").value.trim();
  const data = await api(`/api/people?kind=review&sort=${encodeURIComponent(state.sort)}&page=${state.pages.facePeople}&size=${state.pageSize}&q=${encodeURIComponent(query)}`);
  state.faceReviewPeople = data.people; state.pages.facePeople = data.page;
  renderFacePersonList();
  renderPagination("face-person-pagination", data, page => {
    state.pages.facePeople = page; return loadFaceReviewPeople();
  });
}

function updateFaceReviewSelection() {
  ui.faceReviewGrid.querySelectorAll(".face-review-card").forEach(card => {
    const selected = state.faceReviewSelected.has(card.dataset.faceId); card.classList.toggle("selected", selected);
    const status = card.querySelector(".face-review-status");
    if (status && !card.classList.contains("queued")) status.textContent = selected ? "Selected for Unnamed" : "Select for Unnamed";
  });
  const button = document.querySelector("#face-review-queue"), count = state.faceReviewSelected.size;
  button.disabled = count === 0 || state.faceReviewLoading;
  button.textContent = count ? `Queue ${count} for Unnamed` : "Queue selected for Unnamed";
}

function createFaceReviewCard(face) {
  const card = document.createElement("article"); card.className = "face-review-card"; card.dataset.faceId = face.faceId; card.dataset.assetId = face.assetId; card.dataset.fileName = face.fileName; card.tabIndex = face.queued ? -1 : 0;
  card.classList.toggle("queued", Boolean(face.queued));
  card.classList.toggle("selected", state.faceReviewSelected.has(face.faceId));
  card.classList.toggle("reviewed", Boolean(face.reviewed));
  const crop = document.createElement("canvas"); crop.width = 280; crop.height = 280; crop.setAttribute("aria-label", `Face from ${face.fileName}`);
  drawFace(crop, face).catch(() => { crop.replaceWith(Object.assign(document.createElement("img"), { src: assetThumb(face.assetId), alt: face.fileName })); });
  const meta = document.createElement("div"); meta.className = "face-review-card-meta";
  const filename = document.createElement("code"); filename.textContent = face.fileName; filename.title = face.fileName;
  const status = document.createElement("span"); status.className = "face-review-status"; status.textContent = face.queued ? "Queued for Unnamed" : "Select for Unnamed";
  const preview = document.createElement("button"); preview.type = "button"; preview.className = "quiet-button"; preview.textContent = "Photo"; preview.tabIndex = -1;
  preview.addEventListener("click", event => { event.stopPropagation(); openAssetPreview(face.assetId, face.fileName); });
  const reviewedLabel = document.createElement("label"); reviewedLabel.className = "face-reviewed-check";
  const reviewed = document.createElement("input"); reviewed.type = "checkbox"; reviewed.checked = Boolean(face.reviewed); reviewed.disabled = Boolean(face.queued); reviewed.tabIndex = -1;
  reviewed.setAttribute("aria-label", `Reviewed face from ${face.fileName}`);
  reviewedLabel.append(reviewed, document.createTextNode("Reviewed"));
  reviewedLabel.addEventListener("click", event => event.stopPropagation());
  reviewed.addEventListener("change", async event => {
    event.stopPropagation(); const next = reviewed.checked; reviewed.disabled = true;
    try {
      const result = await api("/api/face-review/reviewed", { method: "POST", body: { personId: currentFaceReviewPerson().id, faceId: face.faceId, reviewed: next } });
      face.reviewed = next; card.classList.toggle("reviewed", next); Object.assign(state.faceReviewResult, result); updateFaceReviewProgress();
      const scrollTop = ui.facePersonList.scrollTop; renderFacePersonList(); ui.facePersonList.scrollTop = scrollTop;
      if (result.personReviewed) showToast(`${currentFaceReviewPerson().name || "This person"} is fully reviewed.`);
    } catch (error) { reviewed.checked = !next; showToast(`Could not save review state: ${error.message}`); }
    finally { reviewed.disabled = Boolean(face.queued); }
  });
  meta.append(filename, status, preview, reviewedLabel); card.append(crop, meta);
  const toggle = () => {
    if (face.queued) return;
    if (state.faceReviewSelected.has(face.faceId)) {
      state.faceReviewSelected.delete(face.faceId); state.faceReviewSelectedData.delete(face.faceId);
    } else {
      state.faceReviewSelected.add(face.faceId); state.faceReviewSelectedData.set(face.faceId, face);
    }
    updateFaceReviewSelection();
  };
  card.addEventListener("click", event => { if (!event.target.closest("button, label, input")) toggle(); });
  card.addEventListener("keydown", event => {
    if (event.key === " " || event.key === "Enter") { event.preventDefault(); toggle(); }
    else if (event.key.toLocaleLowerCase() === "r" && !face.queued) { event.preventDefault(); reviewed.click(); }
  });
  return card;
}

async function loadFaceReviewPage(page = state.faceReviewPage) {
  const person = currentFaceReviewPerson();
  if (!person || state.faceReviewLoading) return;
  state.faceReviewLoading = true; updateFaceReviewSelection();
  try {
    const result = await api(`/api/person-faces/${encodeURIComponent(person.id)}?page=${page}&size=${state.faceReviewPageSize}`);
    ui.faceReviewGrid.replaceChildren(...result.faces.map(createFaceReviewCard));
    state.faceReviewPage = result.page; state.faceReviewResult = result;
    updateFaceReviewProgress(result);
    renderPagination("face-review-pagination", result, nextPage => loadFaceReviewPage(nextPage));
    if (!ui.faceReviewGrid.children.length) {
      const empty = document.createElement("p"); empty.className = "muted"; empty.textContent = "Immich returned no assigned faces for this person."; ui.faceReviewGrid.append(empty);
    }
  } catch (error) {
    showToast(`Could not load faces: ${error.message}`);
  } finally {
    state.faceReviewLoading = false; updateFaceReviewSelection();
  }
}

async function selectFaceReviewPerson(personId) {
  const person = state.faceReviewPeople.find(item => item.id === personId) || (state.faceReviewPersonId === personId ? state.faceReviewCurrentPerson : null);
  if (!person) return;
  state.faceReviewPersonId = personId; state.faceReviewCurrentPerson = person; state.faceReviewPage = 1;
  state.faceReviewSelected.clear(); state.faceReviewSelectedData.clear();
  renderFacePersonList();
  document.querySelector("#face-review-placeholder").hidden = true;
  document.querySelector("#face-review-content").hidden = false;
  document.querySelector("#face-review-name").textContent = person?.name || "Unnamed person";
  document.querySelector("#face-review-meta").textContent = "Loading assigned faces…";
  ui.faceReviewGrid.replaceChildren(); applyFaceReviewCapacity(); await loadFaceReviewPage(1);
}

async function renderFaceReview() {
  await loadFaceReviewPeople();
  if (state.faceReviewPersonId) await selectFaceReviewPerson(state.faceReviewPersonId);
  else {
    document.querySelector("#face-review-placeholder").hidden = false;
    document.querySelector("#face-review-content").hidden = true;
  }
}

async function queueSelectedFacesForUnnamed() {
  const person = currentFaceReviewPerson();
  if (!person || !state.faceReviewSelected.size || state.faceReviewLoading) return;
  const selectedData = [...state.faceReviewSelectedData.values()]
    .map(face => ({ faceId: face.faceId, assetId: face.assetId, fileName: face.fileName }));
  state.faceReviewLoading = true; updateFaceReviewSelection();
  let queued = 0;
  try {
    for (const face of selectedData) {
      await api("/api/face-detach/queue", { method: "POST", body: { personId: person.id, ...face } });
      queued += 1;
    }
    state.faceReviewSelected.clear(); state.faceReviewSelectedData.clear();
    state.faceReviewLoading = false;
    showToast(`Queued ${queued} ${queued === 1 ? "face" : "faces"} to become unnamed on sync.`);
    await loadFaceReviewPage(state.faceReviewPage); await refreshSummary();
  } catch (error) {
    state.faceReviewLoading = false;
    showToast(`Queued ${queued}; stopped because: ${error.message}`);
    await loadFaceReviewPage(state.faceReviewPage); await refreshSummary();
  } finally {
    state.faceReviewLoading = false; updateFaceReviewSelection();
  }
}

async function renderGallery(title, personId) {
  const section = document.createElement("section"); section.className = "detail-gallery";
  const heading = document.createElement("h3"); heading.textContent = title;
  const grid = document.createElement("div"); grid.className = "gallery-grid"; section.append(heading, grid);
  await renderSampleStrip(grid, personId, 12); return section;
}

async function openDetails(item) {
  state.detailItem = item; state.detailTarget = null;
  document.querySelector("#detail-kind").textContent = item.operation === "merge" ? (item.targetName ? "Compare source and destination before syncing." : "Compare both clusters before syncing. The surviving person remains unnamed.") : "Inspect representative faces from this cluster.";
  const galleries = document.querySelector("#detail-galleries"); galleries.replaceChildren();
  galleries.append(await renderGallery("Current cluster", item.personId));
  if (item.targetPersonId) galleries.append(await renderGallery(item.targetName ? `Existing: ${item.targetName}` : "Surviving unnamed cluster", item.targetPersonId));
  const input = document.querySelector("#detail-name"); input.value = item.targetName || item.name || item.currentName || "";
  document.querySelector("#detail-suggestions").replaceChildren();
  ui.detail.showModal(); input.focus();
}

function updateDetailSuggestions() {
  const input = document.querySelector("#detail-name"), list = document.querySelector("#detail-suggestions");
  state.detailTarget = null; list.replaceChildren();
  const matches = matchingNames(input.value, state.detailItem?.personId).slice(0, 6);
  for (const person of matches) {
    const button = document.createElement("button"); button.type = "button"; button.className = "suggestion";
    button.tabIndex = -1;
    const image = document.createElement("img"); image.src = personThumb(person.id); image.alt = "";
    const name = document.createElement("strong"); name.textContent = person.name;
    const count = document.createElement("small");
    count.textContent = person.isPending ? "pending in this run" : person.assetCount == null ? "" : `${person.assetCount} photos`;
    button.append(image, name, count); button.addEventListener("click", () => { state.detailTarget = person; input.value = person.name; list.classList.remove("open"); }); list.append(button);
  }
  list.classList.toggle("open", matches.length > 0);
}

async function renderIgnored() {
  const data = await api(`/api/people?kind=ignored&sort=${encodeURIComponent(state.sort)}&page=${state.pages.ignored}&size=${state.pageSize}`);
  const people = data.people; state.pages.ignored = data.page;
  ui.ignoredGrid.replaceChildren();
  renderPagination("ignored-pagination", data, page => { state.pages.ignored = page; return renderIgnored(); });
  for (const person of people) {
    const card = document.createElement("article"); card.className = "person-card";
    const image = document.createElement("img"); image.className = "portrait"; image.src = personThumb(person.id); image.alt = person.name || "Ignored person";
    const title = document.createElement("p"); title.textContent = person.name || "Unnamed person";
    const button = document.createElement("button"); button.className = "primary-button"; button.textContent = "Restore";
    button.addEventListener("click", async () => { await api("/api/queue", { method: "POST", body: { personId: person.id, operation: "unhide" } }); await renderIgnored(); await refreshSummary(); });
    card.append(image, title, button); ui.ignoredGrid.append(card);
  }
}

document.querySelector("#page-size-select").value = String(state.pageSize);
document.querySelectorAll(".nav-button").forEach(button => button.addEventListener("click", () => switchView(button.dataset.view)));
document.querySelectorAll("[data-go]").forEach(button => button.addEventListener("click", () => switchView(button.dataset.go)));
document.querySelector("#connect-button").addEventListener("click", () => ui.connect.showModal());
document.querySelectorAll("[data-close]").forEach(button => button.addEventListener("click", () => button.closest("dialog").close()));
document.querySelector("#connect-form").addEventListener("submit", async event => {
  event.preventDefault();
  const settings = connectionFormSettings();
  const remember = document.querySelector("#remember-connection").checked;
  try {
    await connectWithSettings(settings);
    let stored = true;
    if (remember) stored = saveBrowserSettings(settings);
    else forgetBrowserSettings();
    document.querySelector("#forget-settings").disabled = !remember || !stored;
    if (!remember) document.querySelector("#immich-key").value = "";
    ui.connect.close(); await waitUntilReady();
    if (remember && !stored) showToast("Connected, but this browser did not allow the settings to be saved.");
  } catch (error) { showToast(error.message); }
});
document.querySelector("#forget-settings").addEventListener("click", () => {
  forgetBrowserSettings();
  document.querySelector("#remember-connection").checked = false;
  document.querySelector("#immich-key").value = "";
  showToast("Saved browser settings removed. The current server session remains connected until restart.");
});
document.querySelector("#reload-button").addEventListener("click", async () => { resetListPages(); await api("/api/reload", { method: "POST", body: {} }); await waitUntilReady(); });
document.querySelector("#page-size-select").addEventListener("change", event => {
  state.pageSize = Number(event.target.value) || 24; savePageSize(state.pageSize); resetListPages(); renderCurrentView(true).catch(error => showToast(error.message));
});
document.querySelector("#sort-select").addEventListener("change", event => { state.sort = event.target.value; resetListPages(); renderCurrentView(true, false).catch(error => showToast(error.message)); });
document.querySelector("#merge-sort-select").addEventListener("change", event => { state.sort = event.target.value; resetListPages(); document.querySelector("#sort-select").value = state.sort; renderMergeWorkbench(true).catch(error => showToast(error.message)); });
document.querySelector("#merge-clear").addEventListener("click", clearMergeGroup);
document.querySelector("#merge-queue").addEventListener("click", () => queueMergeGroup());
document.querySelector("#merge-review-groups").addEventListener("click", openMergeGroupReview);
document.querySelector("#group-review-confirm").addEventListener("click", () => confirmMergeReviewGroup());
document.querySelector("#group-review-skip").addEventListener("click", skipMergeReviewGroup);
document.querySelector("#group-review-close").addEventListener("click", () => finishMergeGroupReview(true).catch(error => showToast(error.message)));
document.querySelector("#group-review-dialog").addEventListener("cancel", event => {
  event.preventDefault(); finishMergeGroupReview(true).catch(error => showToast(error.message));
});
document.querySelector("#group-review-dialog").addEventListener("keydown", event => {
  if (event.repeat || state.mergeReviewBusy) return;
  if (event.key === "ArrowRight" || event.key === "Enter") { event.preventDefault(); confirmMergeReviewGroup(); }
  else if (event.key === "ArrowLeft") { event.preventDefault(); skipMergeReviewGroup(); }
});
let namedSearchTimer = null, faceSearchTimer = null;
document.querySelector("#named-search").addEventListener("input", () => {
  clearTimeout(namedSearchTimer); state.pages.named = 1;
  namedSearchTimer = setTimeout(() => renderPeopleGrid("named", ui.namedGrid).catch(error => showToast(error.message)), 180);
});
document.querySelector("#face-person-search").addEventListener("input", () => {
  clearTimeout(faceSearchTimer); state.pages.facePeople = 1;
  faceSearchTimer = setTimeout(() => loadFaceReviewPeople().catch(error => showToast(error.message)), 180);
});
document.querySelector("#face-review-queue").addEventListener("click", () => queueSelectedFacesForUnnamed());
window.addEventListener("resize", () => {
  clearTimeout(state.faceReviewResizeTimer);
  state.faceReviewResizeTimer = setTimeout(() => {
    if (state.view !== "faces" || !currentFaceReviewPerson()) return;
    const oldSize = state.faceReviewPageSize, firstIndex = (state.faceReviewPage - 1) * oldSize;
    if (!applyFaceReviewCapacity()) return;
    state.faceReviewPage = Math.floor(firstIndex / state.faceReviewPageSize) + 1;
    loadFaceReviewPage(state.faceReviewPage).catch(error => showToast(error.message));
  }, 180);
});
document.querySelector("#asset-preview-dialog").addEventListener("close", () => document.querySelector("#asset-preview-image").removeAttribute("src"));
document.querySelector("#sync-button").addEventListener("click", async () => {
  if (!confirm("Sync every checked pending change to Immich now?")) return;
  try { const result = await api("/api/sync", { method: "POST", body: { confirmation: "SYNC" } }); showToast(`Sync finished: ${result.results.length - result.failed} succeeded, ${result.failed} failed`); await loadNames(); await refreshSummary(); await renderPending(); } catch (error) { showToast(error.message); }
});
document.querySelector("#reset-button").addEventListener("click", async () => {
  const count = state.summary?.counts?.pending || 0;
  if (!count) return;
  if (!confirm(`Discard all ${count} unsynced local changes and reload the current names from Immich?\n\nAlready synced Immich changes are not affected.`)) return;
  const button = document.querySelector("#reset-button"); button.disabled = true;
  try {
    const result = await api("/api/reset", { method: "POST", body: { confirmation: "DISCARD" } });
    state.names = [];
    showToast(`Discarded ${result.discarded} local changes. Reloading from Immich…`);
    await waitUntilReady();
  } catch (error) {
    showToast(error.message);
  } finally {
    button.disabled = !(state.summary?.counts?.pending > 0);
  }
});
document.querySelector("#investigate-return-all").addEventListener("click", async () => {
  const count = state.summary?.counts?.investigate || 0;
  if (!count) return;
  if (!confirm(`Return all ${count} Investigate clusters to Unnamed?\n\nThis only changes the local queue; Immich is not modified.`)) return;
  const button = document.querySelector("#investigate-return-all"); button.disabled = true;
  try {
    const result = await api("/api/investigate/clear", { method: "POST", body: { confirmation: "RETURN_ALL" } });
    showToast(`Returned ${result.returned} clusters to Unnamed`);
    await renderInvestigate();
    await refreshSummary();
  } catch (error) {
    showToast(error.message);
  } finally {
    button.disabled = !(state.summary?.counts?.investigate > 0);
  }
});
document.querySelector("#duplicate-confirm").addEventListener("click", async () => { ui.duplicate.close(); try { await state.duplicateAction?.(); } catch (error) { showToast(error.message); } finally { state.duplicateAction = null; } });
document.querySelector("#duplicate-cancel").addEventListener("click", () => { ui.duplicate.close(); requestAnimationFrame(() => state.duplicateInput?.focus()); });
ui.duplicate.addEventListener("cancel", event => { event.preventDefault(); ui.duplicate.close(); requestAnimationFrame(() => state.duplicateInput?.focus()); });
ui.duplicate.addEventListener("keydown", event => { if (event.key === "Enter") { event.preventDefault(); document.querySelector("#duplicate-confirm").click(); } });
document.querySelector("#detail-name").addEventListener("input", updateDetailSuggestions);
document.querySelector("#detail-save").addEventListener("click", async () => {
  const item = state.detailItem, name = cleanName(document.querySelector("#detail-name").value); if (!item || !name) return;
  const body = state.detailTarget ? { personId: item.personId, operation: "merge", name: state.detailTarget.name, targetPersonId: state.detailTarget.id, targetName: state.detailTarget.name, featureAssetId: item.featureAssetId } : { personId: item.personId, operation: "rename", name, featureAssetId: item.featureAssetId };
  try { await api("/api/queue", { method: "POST", body }); ui.detail.close(); await renderPending(); } catch (error) { showToast(error.message); }
});
document.querySelector("#return-button").addEventListener("click", async () => { if (!state.detailItem) return; await api("/api/pending/return", { method: "POST", body: { personId: state.detailItem.personId } }); ui.detail.close(); await renderPending(); await refreshSummary(); });

initializeMergeCanvas();
const savedConnection = readBrowserSettings();
fillConnectionForm(savedConnection);
document.querySelector("#forget-settings").disabled = !savedConnection;
refreshSummary().then(async summary => {
  state.sort = summary.sort || "most";
  if (summary.connected) { await waitUntilReady(); return; }
  if (savedConnection?.apiKey) {
    try { await connectWithSettings(savedConnection); await waitUntilReady(); }
    catch (error) { showToast(`Automatic connection failed: ${error.message}`); ui.connect.showModal(); }
  } else ui.connect.showModal();
}).catch(error => showToast(error.message));
