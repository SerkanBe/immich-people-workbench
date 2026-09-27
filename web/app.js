// Copyright (C) 2026 Serkan Bekdemir
// SPDX-License-Identifier: AGPL-3.0-only
import { createMergeWorkbench } from "./merge.js";
import { createFaceReview } from "./face-review.js";
import { createNaming } from "./naming.js";
import { createInvestigate } from "./investigate.js";

"use strict";

const BROWSER_SETTINGS_KEY = "immichPeopleWorkbench.connection.v1";
const MERGE_CANVAS_KEY = "immichPeopleWorkbench.mergeCanvas.v1";
const PAGINATION_SETTINGS_KEY = "immichPeopleWorkbench.pagination.v1";
const LEGACY_BROWSER_SETTINGS_KEY = "immichPeopleConsole.connection.v1";
const LEGACY_MERGE_CANVAS_KEY = "immichPeopleConsole.mergeCanvas.v1";
const LEGACY_PAGINATION_SETTINGS_KEY = "immichPeopleConsole.pagination.v1";
const VIEW_ROUTES = new Set(["unnamed", "merge", "investigate", "faces", "pending", "named", "ignored"]);

function viewFromPath(path) {
  const view = path.replace(/^\/|\/$/g, "");
  return VIEW_ROUTES.has(view) ? view : "unnamed";
}

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
  view: viewFromPath(window.location.pathname), summary: null, names: [], sort: "most", duplicateAction: null,
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

function switchView(view, pushHistory = true, render = true) {
  if (!VIEW_ROUTES.has(view)) return;
  if (pushHistory && window.location.pathname !== `/${view}`) window.history.pushState(null, "", `/${view}`);
  state.view = view;
  document.querySelectorAll(".nav-button").forEach(button => {
    const active = button.dataset.view === view;
    button.classList.toggle("active", active);
    if (active) button.setAttribute("aria-current", "page"); else button.removeAttribute("aria-current");
  });
  document.querySelectorAll(".view").forEach(section => section.classList.toggle("active", section.id === `${view}-view`));
  if (render) renderCurrentView(true).catch(error => showToast(error.message));
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

const { matchingNames, drawFace, moveSlide, showSlide, attachNameBehavior, renderPeopleGrid } = createNaming({ api, assetThumb, cleanName, loadNames, personThumb, refreshSummary, renderInvestigate: (...args) => renderInvestigate(...args), renderPagination, showToast, state, ui });

const { renderMergeWorkbench, clearMergeGroup, queueMergeGroup, openMergeGroupReview, confirmMergeReviewGroup, skipMergeReviewGroup, finishMergeGroupReview, initializeMergeCanvas } = createMergeWorkbench({ api, cleanName, moveSlide, personThumb, refreshSummary, saveMergeCanvasState, showSlide, showToast, state, ui, MERGE_CARD_WIDTH, MERGE_CARD_HEIGHT, MERGE_MIN_WORLD_WIDTH, MERGE_MIN_WORLD_HEIGHT, MERGE_MIN_ZOOM, MERGE_MAX_ZOOM });

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

const { renderInvestigate } = createInvestigate({ api, assetThumb, attachNameBehavior, moveSlide, openAssetPreview, personThumb, refreshSummary, renderPagination, showSlide, showToast, state, ui });

const { renderFaceReview, currentFaceReviewPerson, applyFaceReviewCapacity, loadFaceReviewPage, loadFaceReviewPeople, queueSelectedFacesForUnnamed } = createFaceReview({ api, assetThumb, drawFace, openAssetPreview, personThumb, refreshSummary, renderPagination, showToast, state, ui });

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
document.querySelectorAll(".nav-button").forEach(button => button.addEventListener("click", event => {
  if (event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
  event.preventDefault(); switchView(button.dataset.view);
}));
window.addEventListener("popstate", () => switchView(viewFromPath(window.location.pathname), false));
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
switchView(state.view, false, false);
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
