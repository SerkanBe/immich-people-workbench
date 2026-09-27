// Copyright (C) 2026 Serkan Bekdemir
// SPDX-License-Identifier: AGPL-3.0-only

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

export const MERGE_MIN_WORLD_WIDTH = 12000, MERGE_MIN_WORLD_HEIGHT = 12000;
export const MERGE_MIN_ZOOM = .005, MERGE_MAX_ZOOM = 4;

export function readMergeCanvasState() {
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

export function readPageSize() {
  try {
    const value = JSON.parse(readStoredSetting(PAGINATION_SETTINGS_KEY, LEGACY_PAGINATION_SETTINGS_KEY) || "null");
    return [12, 24, 48, 60].includes(Number(value?.pageSize)) ? Number(value.pageSize) : 24;
  } catch { return 24; }
}

export function savePageSize(pageSize, showToast) {
  try { localStorage.setItem(PAGINATION_SETTINGS_KEY, JSON.stringify({ pageSize })); }
  catch { showToast("The browser could not save the pagination setting."); }
}

export function saveMergeCanvasState(state, showToast) {
  clearTimeout(state.mergeSaveTimer);
  state.mergeSaveTimer = setTimeout(() => {
    try { localStorage.setItem(MERGE_CANVAS_KEY, JSON.stringify(state.mergeCanvas)); }
    catch { showToast("The browser could not save the canvas layout."); }
  }, 120);
}

export function readBrowserSettings() {
  try {
    const raw = readStoredSetting(BROWSER_SETTINGS_KEY, LEGACY_BROWSER_SETTINGS_KEY);
    if (!raw) return null;
    const value = JSON.parse(raw);
    if (!value || typeof value.url !== "string" || typeof value.apiKey !== "string") return null;
    return { url: value.url, apiKey: value.apiKey, insecureTls: Boolean(value.insecureTls) };
  } catch { return null; }
}

export function saveBrowserSettings(settings) {
  try { localStorage.setItem(BROWSER_SETTINGS_KEY, JSON.stringify(settings)); return true; }
  catch { return false; }
}

export function forgetBrowserSettings() {
  try {
    localStorage.removeItem(BROWSER_SETTINGS_KEY);
    localStorage.removeItem(LEGACY_BROWSER_SETTINGS_KEY);
  } catch { /* Storage may be disabled. */ }
}
