// Copyright (C) 2026 Serkan Bekdemir
// SPDX-License-Identifier: AGPL-3.0-only
import test from "node:test";
import assert from "node:assert/strict";
import { createGroupPhotos } from "../web/group-photos.js";

class Element {
  constructor() {
    this.children = []; this.listeners = new Map(); this.style = {}; this.dataset = {};
    this.className = ""; this.value = ""; this.textContent = ""; this.hidden = false; this.disabled = false;
    this.classList = {
      contains: name => this.className.split(" ").includes(name),
      toggle: (name, on) => { this.className = [...new Set(this.className.split(" ").filter(part => part && part !== name).concat(on ? [name] : []))].join(" "); },
    };
  }
  addEventListener(type, listener) { this.listeners.set(type, [...(this.listeners.get(type) || []), listener]); }
  dispatch(type, props = {}) {
    const event = { key: "", shiftKey: false, ctrlKey: false, metaKey: false, preventDefault() { this.defaultPrevented = true; }, ...props };
    for (const listener of this.listeners.get(type) || []) listener(event);
    return event;
  }
  append(...children) { this.children.push(...children); }
  replaceChildren(...children) { this.children = children; }
  setAttribute(name, value) { this[name] = value; }
  querySelectorAll(selector) { return selector === "button" ? this.children.filter(child => child.tagName === "button") : []; }
  focus() { document.activeElement = this; this.dispatch("focus"); }
  click() { this.dispatch("click"); }
  showModal() { this.open = true; }
  getContext() { return { clearRect() {}, drawImage() {} }; }
}

function setup(photo, scanOverride = {}, nextPhoto = null, mode = {}) {
  const elements = new Map();
  const selectors = ["group-name", "group-suggestions", "group-ghost", "group-progress", "group-scan-error",
    "group-face-position", "group-face-note", "group-markers", "group-face-crop", "group-workspace",
    "group-checkpoint", "group-photo-image", "group-photo-caption", "group-min-faces", "group-archived",
    "group-order", "group-checkpoint-text", "group-review-deferred", "group-advance", "group-help",
    "group-help-dialog", "duplicate-dialog", "group-retry"];
  for (const name of selectors) elements.set(`#${name}`, new Element());
  globalThis.document = {
    activeElement: null, listeners: new Map(),
    querySelector: selector => elements.get(selector),
    createElement: tag => { const item = new Element(); item.tagName = tag; return item; },
    addEventListener(type, listener) { this.listeners.set(type, [...(this.listeners.get(type) || []), listener]); },
    dispatch(type, props = {}) {
      const event = { preventDefault() { this.defaultPrevented = true; }, ...props };
      for (const listener of this.listeners.get(type) || []) listener(event);
      return event;
    },
  };
  globalThis.requestAnimationFrame = callback => callback();
  let current = photo, deferred = 0;
  const calls = [], drafts = new Map(photo?.drafts.map(row => [row.person_id, row]) || []);
  const state = { view: "group-photos", names: [{ id: "known", name: "Known Person", assetCount: 4 }] };
  const api = async (path, options = {}) => {
    calls.push([path, options.body]);
    if (path === "/api/group-photos") return {
      photo: current ? { ...current, drafts: [...drafts.values()] } : null,
      settings: { minFaces: 5, includeArchived: true, order: "most" },
      scan: { scanning: false, complete: true, indexed: 1, examined: 1, failures: 0, provisional: false,
        sources: 1, discovered: 1, filtered: 0, belowMinimum: 0, withoutActionable: 0, ...scanOverride },
      deferred, checkpoint: !current,
    };
    if (path === "/api/group-photos/draft") {
      const row = options.body;
      if (mode.draft) await mode.draft(row);
      drafts.set(row.personId, { person_id: row.personId, name: row.name,
        target_person_id: row.targetPersonId, operation: row.operation });
      return { ok: true };
    }
    if (path === "/api/group-photos/advance") {
      current = nextPhoto; deferred = options.body.decisions.length ? 0 : 1;
      return { queued: options.body.decisions.length, status: deferred ? "deferred" : "done" };
    }
    if (path === "/api/group-photos/deferred") { current = photo; deferred = 0; return { ok: true }; }
    return { ok: true };
  };
  let duplicateCount = 0;
  const controls = createGroupPhotos({
    api, assetThumb: id => `/media/asset/${id}`, cleanName: value => value.trim().replace(/\s+/g, " "),
    drawFace: async () => {}, loadNames: async () => {},
    matchingNames: value => state.names.filter(row => row.name.toLowerCase().includes(value.trim().toLowerCase())),
    openDuplicate: (_model, _name, _target, onConfirm) => { duplicateCount += 1; onConfirm(); },
    refreshSummary: async () => {}, showToast: message => mode.toast?.(message), state,
  });
  controls.bindGroupPhotos();
  return { controls, elements, calls, drafts, state, duplicateCount: () => duplicateCount };
}

const box = { x1: 10, y1: 10, x2: 30, y2: 30, width: 100, height: 100 };
const photo = {
  id: "asset", fileName: "synthetic.jpg", faceCount: 4, unnamedCount: 2, drafts: [],
  faces: [
    { id: "one", personId: "cluster", status: "actionable", name: "", box },
    { id: "two", personId: "cluster", status: "actionable", name: "", box },
    { id: "three", personId: "known", status: "named", name: "Known Person", box },
    { id: "four", personId: null, status: "unassigned", name: "Unassigned", box },
  ],
};

test("markers, Tab completion, shared cluster draft, disabled context, and submission", async () => {
  const run = setup(photo);
  await run.controls.renderGroupPhotos();
  const input = run.elements.get("#group-name"), markers = run.elements.get("#group-markers").children;
  assert.equal(markers.length, 4);
  input.focus(); input.value = "Kno"; input.dispatch("input");
  input.dispatch("keydown", { key: "ArrowDown" });
  assert.equal(input.value, "Known Person");
  input.dispatch("keydown", { key: "ArrowUp" });
  assert.equal(input.value, "Kno", "moving above the first suggestion restores the draft");
  input.dispatch("keydown", { key: "Tab" });
  assert.equal(input.value, "Known Person");
  input.dispatch("keydown", { key: "Tab" });
  assert.equal(input.value, "Known Person", "second face in the same cluster shares its name");
  markers[2].click();
  assert.equal(input.disabled, true);
  assert.equal(input.value, "Known Person");
  assert.equal(document.activeElement, markers[2]);
  markers[3].click();
  assert.equal(input.value, "Unassigned");
  assert.match(run.elements.get("#group-face-note").textContent, /Immich returned this face/);
  markers[2].click();
  document.dispatch("keydown", { key: "Tab", shiftKey: true, preventDefault() {} });
  assert.equal(input.disabled, false);
  document.dispatch("keydown", { key: "?", preventDefault() {} });
  assert.equal(run.elements.get("#group-help-dialog").open, true);
  run.elements.get("#group-help-dialog").open = false;
  document.dispatch("keydown", { key: "Enter", ctrlKey: true, preventDefault() {} });
  await new Promise(resolve => setTimeout(resolve, 0));
  const advance = run.calls.find(([path]) => path === "/api/group-photos/advance")[1];
  assert.deepEqual(advance.decisions, [{ personId: "cluster", name: "Known Person", targetPersonId: "known", operation: "rename" }]);
  assert.equal(run.elements.get("#group-checkpoint").hidden, false);
});

test("Tab skips context faces and wraps between Submit Names and the first unnamed face", async () => {
  const run = setup(photo);
  await run.controls.renderGroupPhotos();
  const input = run.elements.get("#group-name");
  const markers = run.elements.get("#group-markers").children;
  const submit = run.elements.get("#group-advance");
  input.focus();
  input.dispatch("keydown", { key: "Tab" });
  assert.equal(document.activeElement, input, "second face shares the editable field");
  input.dispatch("keydown", { key: "Tab" });
  assert.equal(document.activeElement, submit);
  document.dispatch("keydown", { key: "Tab" });
  assert.equal(document.activeElement, input);
  assert.equal(run.elements.get("#group-face-position").textContent, "Face 1 of 4");
  input.dispatch("keydown", { key: "Tab", shiftKey: true });
  assert.equal(document.activeElement, submit);
  document.dispatch("keydown", { key: "Tab", shiftKey: true });
  assert.equal(document.activeElement, input);
  assert.equal(run.elements.get("#group-face-position").textContent, "Face 2 of 4");
  markers[2].click();
  document.dispatch("keydown", { key: "Tab" });
  assert.equal(document.activeElement, submit, "forward traversal from a clicked known face skips context faces");
  markers[3].click();
  document.dispatch("keydown", { key: "Tab", shiftKey: true });
  assert.equal(document.activeElement, input, "reverse traversal returns to the last nameable face");
  assert.equal(run.elements.get("#group-face-position").textContent, "Face 2 of 4");
});

test("H toggles rectangles outside name fields and remembers the choice", async () => {
  const saved = new Map();
  globalThis.localStorage = { getItem: key => saved.get(key) ?? null, setItem: (key, value) => saved.set(key, value) };
  try {
    const first = setup(photo);
    await first.controls.renderGroupPhotos();
    const layer = first.elements.get("#group-markers");
    layer.children[2].click();
    assert.equal(document.dispatch("keydown", { key: "h" }).defaultPrevented, true);
    assert.equal(layer.hidden, true);
    assert.equal(layer.children.length, 4, "hiding keeps markers and the selected face intact");
    assert.equal(document.activeElement, first.elements.get("#group-advance"), "focus moves off the hidden marker");
    assert.equal(document.dispatch("keydown", { key: "h" }).defaultPrevented, true);
    assert.equal(layer.hidden, false, "H can restore rectangles from Submit Names");
    document.dispatch("keydown", { key: "h" });
    const second = setup(photo);
    await second.controls.renderGroupPhotos();
    const restoredLayer = second.elements.get("#group-markers");
    assert.equal(restoredLayer.hidden, true);
    second.elements.get("#group-name").focus();
    assert.equal(document.dispatch("keydown", { key: "h" }).defaultPrevented, undefined);
    assert.equal(restoredLayer.hidden, true, "typing a name does not toggle rectangles");
    second.elements.get("#group-advance").focus();
    document.dispatch("keydown", { key: "h" });
    assert.equal(restoredLayer.hidden, false);
  } finally {
    delete globalThis.localStorage;
  }
});

test("submitting a photo focuses the first nameable face in the next photo", async () => {
  const next = { ...photo, id: "next-asset" };
  const run = setup(photo, {}, next);
  await run.controls.renderGroupPhotos();
  run.elements.get("#group-advance").focus();
  run.elements.get("#group-advance").click();
  await new Promise(resolve => setTimeout(resolve, 0));
  assert.equal(run.elements.get("#group-photo-image").src, "/media/asset/next-asset");
  assert.equal(document.activeElement, run.elements.get("#group-name"));
});

test("typed name survives reload and duplicate confirmation precedes Pending", async () => {
  const first = setup(photo);
  await first.controls.renderGroupPhotos();
  const input = first.elements.get("#group-name");
  input.value = "Known Person"; input.dispatch("input");
  await new Promise(resolve => setTimeout(resolve, 0));
  const restored = { ...photo, drafts: [...first.drafts.values()] };
  const second = setup(restored);
  await second.controls.renderGroupPhotos();
  assert.equal(second.elements.get("#group-name").value, "Known Person");
  document.dispatch("keydown", { key: "Enter", ctrlKey: true, preventDefault() {} });
  await new Promise(resolve => setTimeout(resolve, 0));
  assert.equal(second.duplicateCount(), 1);
  assert.deepEqual(second.calls.find(([path]) => path === "/api/group-photos/advance")[1].decisions,
    [{ personId: "cluster", name: "Known Person", targetPersonId: null, operation: "rename" }]);
});

test("Ignore suggestion is an explicit Pending action and survives reload", async () => {
  const first = setup(photo);
  first.state.names.push({ id: "literal-ignore", name: "Ignore", assetCount: 1 });
  await first.controls.renderGroupPhotos();
  const input = first.elements.get("#group-name");
  input.focus(); input.value = "Ignore"; input.dispatch("input");
  assert.equal(first.elements.get("#group-suggestions").children[0].children[1].textContent, "Ignore this cluster");
  input.dispatch("keydown", { key: "Tab" });
  assert.equal(input.value, "Ignore this cluster");
  assert.equal(input.classList.contains("ignore-selected"), true);
  assert.match(first.elements.get("#group-face-note").textContent, /will enter Pending/);
  input.dispatch("keydown", { key: "Tab" });
  assert.equal(input.value, "Ignore this cluster", "the same cluster keeps the selected action on another face");
  first.elements.get("#group-markers").children[2].click();
  assert.equal(input.classList.contains("ignore-selected"), false);
  first.elements.get("#group-markers").children[0].click();
  assert.equal(input.value, "Ignore this cluster", "returning to the face restores the selected action");
  await new Promise(resolve => setTimeout(resolve, 0));
  const second = setup({ ...photo, drafts: [...first.drafts.values()] });
  second.state.names.push({ id: "literal-ignore", name: "Ignore", assetCount: 1 });
  await second.controls.renderGroupPhotos();
  assert.equal(second.elements.get("#group-name").value, "Ignore this cluster");
  assert.equal(second.elements.get("#group-name").classList.contains("ignore-selected"), true);
  assert.match(second.elements.get("#group-face-note").textContent, /will enter Pending/);
  document.dispatch("keydown", { key: "Enter", ctrlKey: true });
  await new Promise(resolve => setTimeout(resolve, 0));
  assert.equal(second.duplicateCount(), 0);
  assert.deepEqual(second.calls.find(([path]) => path === "/api/group-photos/advance")[1].decisions,
    [{ personId: "cluster", name: "", targetPersonId: null, operation: "hide" }]);
});

test("editing the selected ignore action restores literal name entry", async () => {
  const run = setup(photo);
  await run.controls.renderGroupPhotos();
  const input = run.elements.get("#group-name");
  input.focus(); input.value = "Ignore"; input.dispatch("input");
  input.dispatch("keydown", { key: "Tab" });
  assert.equal(input.value, "Ignore this cluster");
  input.value = "Ignore"; input.dispatch("input");
  assert.equal(input.classList.contains("ignore-selected"), false);
  document.dispatch("keydown", { key: "Enter", ctrlKey: true });
  await new Promise(resolve => setTimeout(resolve, 0));
  assert.deepEqual(run.calls.find(([path]) => path === "/api/group-photos/advance")[1].decisions,
    [{ personId: "cluster", name: "Ignore", targetPersonId: null, operation: "rename" }]);
});

test("typing Ignore without selecting the action still submits a literal name", async () => {
  const run = setup(photo);
  run.state.names.push({ id: "literal-ignore", name: "Ignore", assetCount: 1 });
  await run.controls.renderGroupPhotos();
  const input = run.elements.get("#group-name");
  input.value = "Ignore"; input.dispatch("input");
  document.dispatch("keydown", { key: "Enter", ctrlKey: true });
  await new Promise(resolve => setTimeout(resolve, 0));
  assert.equal(run.duplicateCount(), 1);
  assert.deepEqual(run.calls.find(([path]) => path === "/api/group-photos/advance")[1].decisions,
    [{ personId: "cluster", name: "Ignore", targetPersonId: null, operation: "rename" }]);
});

test("zero-name submission waits at a checkpoint until deferred review is chosen", async () => {
  const run = setup(photo);
  await run.controls.renderGroupPhotos();
  run.elements.get("#group-advance").click();
  await new Promise(resolve => setTimeout(resolve, 0));
  assert.deepEqual(run.calls.find(([path]) => path === "/api/group-photos/advance")[1].decisions, []);
  assert.equal(run.elements.get("#group-checkpoint").hidden, false);
  assert.equal(run.elements.get("#group-review-deferred").hidden, false);
  assert.equal(run.calls.some(([path]) => path === "/api/group-photos/deferred"), false);
  run.elements.get("#group-review-deferred").click();
  await new Promise(resolve => setTimeout(resolve, 0));
  assert.equal(run.calls.some(([path]) => path === "/api/group-photos/deferred"), true);
  assert.equal(run.elements.get("#group-workspace").hidden, false);
});

test("empty scan explains the face minimum", async () => {
  const run = setup(null, { indexed: 0, examined: 3, discovered: 3, belowMinimum: 3 });
  await run.controls.renderGroupPhotos();
  assert.match(run.elements.get("#group-progress").textContent, /3 photos checked from 3 found/);
  assert.match(run.elements.get("#group-checkpoint-text").textContent, /fewer than 5 detected faces/);
});

test("empty scan explains when every found asset is filtered", async () => {
  const run = setup(null, { indexed: 0, examined: 0, discovered: 3, filtered: 3 });
  await run.controls.renderGroupPhotos();
  assert.match(run.elements.get("#group-checkpoint-text").textContent, /All 3 linked assets were excluded/);
});


test("failed Group-photo draft stays visible and explicit Submit retries before advance", async () => {
  let failing = true; const toasts = [];
  const retryPhoto = structuredClone(photo);
  const run = setup(retryPhoto, {}, null, { draft: async () => { if (failing) throw Error("Local changes are busy during Sync"); }, toast: message => toasts.push(message) });
  await run.controls.renderGroupPhotos();
  const input = run.elements.get("#group-name"); input.value = "Synthetic retry name"; input.dispatch("input");
  await new Promise(resolve => setTimeout(resolve, 0));
  assert.equal(run.drafts.size, 0); assert.match(toasts.at(-1), /Submit Names will retry/);
  // A newly loaded face signature must not overwrite the unsaved local snapshot.
  retryPhoto.faces[3].name = "New context label";
  await run.controls.renderGroupPhotos();
  assert.equal(input.value, "Synthetic retry name");
  run.elements.get("#group-advance").click();
  await new Promise(resolve => setTimeout(resolve, 0));
  assert.equal(run.calls.some(([path]) => path === "/api/group-photos/advance"), false);
  assert.equal(input.value, "Synthetic retry name");
  failing = false; run.elements.get("#group-advance").click();
  await new Promise(resolve => setTimeout(resolve, 0));
  const advanceIndex = run.calls.findIndex(([path]) => path === "/api/group-photos/advance");
  assert.ok(advanceIndex > 0);
  assert.equal(run.calls[advanceIndex-1][0], "/api/group-photos/draft");
  assert.equal(run.calls[advanceIndex][1].decisions[0].name, "Synthetic retry name");
  assert.equal(run.drafts.get("cluster").name, "Synthetic retry name");
  assert.equal(run.calls.some(([path]) => path === "/api/sync"), false);
});
