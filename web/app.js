// Copyright (C) 2026 Serkan Bekdemir
// SPDX-License-Identifier: AGPL-3.0-only
import { createMergeWorkbench } from "./merge.js";
import { createFaceReview } from "./face-review.js";
import { createNaming } from "./naming.js";
import { createInvestigate } from "./investigate.js";
import { createPending } from "./pending.js";
import { MERGE_MIN_WORLD_WIDTH, MERGE_MIN_WORLD_HEIGHT, MERGE_MIN_ZOOM, MERGE_MAX_ZOOM, readMergeCanvasState, readPageSize, savePageSize, saveMergeCanvasState } from "./browser-storage.js";

"use strict";

const MERGE_CARD_WIDTH = 220, MERGE_CARD_HEIGHT = 260;
const VIEW_ROUTES = new Set(["unnamed", "merge", "investigate", "faces", "pending", "named", "ignored"]);

function viewFromPath(path) {
  const view = path.replace(/^\/|\/$/g, "");
  return VIEW_ROUTES.has(view) ? view : "unnamed";
}

const ui = {
  duplicate: document.querySelector("#duplicate-dialog"),
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

async function api(path, options = {}) {
  const init = { method: options.method || "GET", headers: {} };
  if (options.body !== undefined) {
    init.headers["Content-Type"] = "application/json";
    init.body = JSON.stringify(options.body);
  }
  const response = await fetch(path, init);
  const data = await response.json().catch(() => ({}));
  if (response.status === 401) {
    window.location.assign(`/login?next=${encodeURIComponent(window.location.pathname)}`);
    throw new Error("Sign in required");
  }
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

const { matchingNames, drawFace, moveSlide, showSlide, attachNameBehavior, renderPeopleGrid, bindNamingControls } = createNaming({ api, assetThumb, cleanName, loadNames, personThumb, refreshSummary, renderInvestigate: (...args) => renderInvestigate(...args), renderPagination, showToast, state, ui });

const { renderMergeWorkbench, initializeMergeCanvas, bindMergeControls } = createMergeWorkbench({ api, cleanName, moveSlide, personThumb, refreshSummary, saveMergeCanvasState: () => saveMergeCanvasState(state, showToast), showSlide, showToast, resetListPages, state, ui, MERGE_CARD_WIDTH, MERGE_CARD_HEIGHT, MERGE_MIN_WORLD_WIDTH, MERGE_MIN_WORLD_HEIGHT, MERGE_MIN_ZOOM, MERGE_MAX_ZOOM });

function openAssetPreview(assetId, fileName = "Photo preview") {
  const dialog = document.querySelector("#asset-preview-dialog");
  document.querySelector("#asset-preview-title").textContent = fileName || "Photo preview";
  document.querySelector("#asset-preview-caption").textContent = "Immich preview — the original file is not downloaded.";
  const image = document.querySelector("#asset-preview-image");
  image.src = assetThumb(assetId); image.alt = fileName || "Photo preview";
  dialog.showModal();
}

const { renderInvestigate, bindInvestigateControls } = createInvestigate({ api, assetThumb, attachNameBehavior, moveSlide, openAssetPreview, personThumb, refreshSummary, renderPagination, showSlide, showToast, state, ui });

const { renderFaceReview, bindFaceReviewControls } = createFaceReview({ api, assetThumb, drawFace, openAssetPreview, personThumb, refreshSummary, renderPagination, showToast, state, ui });

const { renderPending, bindPendingControls } = createPending({ api, assetThumb, cleanName, drawFace, loadNames, matchingNames, openAssetPreview, personThumb, refreshSummary, renderPagination, showToast, state, ui, waitUntilReady });

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
document.querySelectorAll("[data-close]").forEach(button => button.addEventListener("click", () => button.closest("dialog").close()));
document.querySelector("#reload-button").addEventListener("click", async () => { resetListPages(); await api("/api/reload", { method: "POST", body: {} }); await waitUntilReady(); });
document.querySelector("#logout-button").addEventListener("click", async () => {
  try {
    await api("/api/logout", { method: "POST", body: {} });
    window.location.assign("/login");
  } catch (error) { showToast(error.message); }
});
document.querySelector("#page-size-select").addEventListener("change", event => {
  state.pageSize = Number(event.target.value) || 24; savePageSize(state.pageSize, showToast); resetListPages(); renderCurrentView(true).catch(error => showToast(error.message));
});
document.querySelector("#sort-select").addEventListener("change", event => { state.sort = event.target.value; resetListPages(); renderCurrentView(true, false).catch(error => showToast(error.message)); });
document.querySelector("#asset-preview-dialog").addEventListener("close", () => document.querySelector("#asset-preview-image").removeAttribute("src"));

initializeMergeCanvas();
bindMergeControls();
bindNamingControls();
bindFaceReviewControls();
bindInvestigateControls();
bindPendingControls();
switchView(state.view, false, false);
refreshSummary().then(async summary => {
  state.sort = summary.sort || "most";
  if (summary.connected) await waitUntilReady();
}).catch(error => showToast(error.message));
