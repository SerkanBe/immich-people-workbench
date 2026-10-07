// Copyright (C) 2026 Serkan Bekdemir
// SPDX-License-Identifier: AGPL-3.0-only
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

const source = readFileSync(new URL("../web/face-review.js", import.meta.url), "utf8");
const { createFaceReview, showFaceInReview } = await import(`data:text/javascript;base64,${Buffer.from(source).toString("base64")}`);

function node() {
  const listeners = new Map();
  const classes = new Set();
  return {
    listeners, children: [], value: "", dataset: {}, style: {}, scrollTop: 0, clientHeight: 600, clientWidth: 600, offsetHeight: 600,
    classList: {
      add: name => classes.add(name), remove: name => classes.delete(name), contains: name => classes.has(name),
      toggle: (name, force) => { if (force ?? !classes.has(name)) classes.add(name); else classes.delete(name); },
    },
    append(...items) { for (const item of items) { item.parent = this; this.children.push(item); } },
    replaceChildren(...items) { this.children = []; this.append(...items); },
    insertBefore(item, following) {
      item.parent = this;
      const index = following ? this.children.indexOf(following) : -1;
      if (index < 0) this.children.push(item); else this.children.splice(index, 0, item);
    },
    remove() { if (this.parent) this.parent.children = this.parent.children.filter(item => item !== this); },
    querySelectorAll(selector) {
      if (!selector.startsWith(".")) return [];
      const matches = [];
      const visit = parent => {
        for (const item of parent.children) {
          if (item.className?.split(" ").includes(selector.slice(1))) matches.push(item);
          visit(item);
        }
      };
      visit(this);
      return matches;
    },
    querySelector(selector) { return this.querySelectorAll(selector)[0] || null; },
    addEventListener: (event, handler) => listeners.set(event, handler),
    setAttribute() {}, removeAttribute() {}, focus() {}, closest: () => null,
    close() { this.open = false; this.listeners.get("close")?.(); },
  };
}

test("Face review hides submitted faces while leaving new drafts available", () => {
  const checked = { reviewed: true, queued: false };
  const newFace = { reviewed: false, queued: false };
  assert.equal(showFaceInReview(checked, false, false, null), false);
  assert.equal(showFaceInReview(newFace, false, false, "correct"), true);
  assert.equal(showFaceInReview(checked, false, false, "wrong"), true);
  assert.equal(showFaceInReview(checked, true, false, null), true);
  assert.equal(showFaceInReview(checked, true, true, null), true);
  assert.equal(showFaceInReview({ reviewed: false, queued: true }, true, false, null), false);
});

test("Face review sidebar shows photo counts before face inventory is complete", async () => {
  const previous = { document: globalThis.document, window: globalThis.window, localStorage: globalThis.localStorage };
  const elements = new Map();
  globalThis.document = {
    getElementById: id => { if (!elements.has(id)) elements.set(id, node()); return elements.get(id); },
    createElement: () => node(), addEventListener() {},
  };
  globalThis.window = { addEventListener() {} };
  globalThis.localStorage = { getItem: () => null };
  const people = [
    { id: "unscanned", name: "First", assetCount: 8, inventoryComplete: false, reviewedCount: 0 },
    { id: "partial", name: "Second", assetCount: 4, inventoryComplete: false, reviewedCount: 2 },
    { id: "complete", name: "Third", assetCount: 1, inventoryComplete: true, reviewedCount: 1, reviewTotal: 2 },
  ];
  const list = node();
  try {
    const review = createFaceReview({
      api: async () => ({ people, page: 1 }),
      state: { faceReviewPeople: [], pages: { facePeople: 1 }, pageSize: 10, sort: "most" },
      ui: { facePersonList: list, faceReviewGrid: node() },
      assetThumb: () => "", personThumb: () => "", refreshSummary: async () => {}, showToast: () => {},
      renderPagination: () => {},
    });
    await review.loadFaceReviewPeople();
    const labels = list.children.map(button => button.children[1].children.slice(1).map(item => item.textContent));
    assert.deepEqual(labels, [
      ["8 photos"],
      ["4 photos", "2 faces checked so far"],
      ["1 photo", "1/2 faces checked"],
    ]);
  } finally {
    Object.assign(globalThis, previous);
  }
});

test("Face review counts people in background and refreshes the sidebar", async () => {
  const previous = { document: globalThis.document, window: globalThis.window, localStorage: globalThis.localStorage };
  const elements = new Map();
  globalThis.document = {
    getElementById: id => { if (!elements.has(id)) elements.set(id, node()); return elements.get(id); },
    createElement: () => node(), addEventListener() {},
  };
  globalThis.window = { addEventListener() {} };
  globalThis.localStorage = { getItem: () => null };
  const calls = [];
  const state = { view: "faces", faceReviewPeople: [], pages: { facePeople: 1 }, pageSize: 10, sort: "most" };
  const person = { id: "11111111-1111-4111-8111-111111111111", name: "Synthetic", assetCount: 2 };
  const api = async path => {
    calls.push(path);
    if (path.startsWith("/api/people?")) return { people: [person], page: 1 };
    if (path === "/api/face-review/scan-next") return {
      personId: person.id, complete: calls.filter(item => item === path).length === 1 ? 0 : 1,
      total: 1, failed: 0, done: calls.filter(item => item === path).length > 1,
    };
    throw new Error(`Unexpected request: ${path}`);
  };
  try {
    const review = createFaceReview({
      api, state, ui: { facePersonList: node(), faceReviewGrid: node() },
      assetThumb: () => "", personThumb: () => "", refreshSummary: async () => {}, showToast: () => {},
      renderPagination: () => {},
    });
    await review.renderFaceReview();
    await new Promise(resolve => setTimeout(resolve, 220));
    assert.equal(calls.filter(path => path === "/api/face-review/scan-next").length, 2);
    assert.equal(document.getElementById("face-person-scan-status").textContent, "Face counts ready · 1 person");
    assert.equal(calls.filter(path => path.startsWith("/api/people?")).length, 3);
  } finally {
    Object.assign(globalThis, previous);
  }
});

test("Space opens a focused face and closes its review lightbox", async () => {
  const previous = { document: globalThis.document, window: globalThis.window, localStorage: globalThis.localStorage };
  const elements = new Map();
  globalThis.document = {
    getElementById: id => { if (!elements.has(id)) elements.set(id, node()); return elements.get(id); },
    createElement: () => node(), addEventListener() {},
  };
  globalThis.window = { addEventListener() {} };
  globalThis.localStorage = { getItem: () => null };
  const person = { id: "11111111-1111-4111-8111-111111111111", name: "Synthetic", assetCount: 1 };
  const face = { faceId: "cccccccc-cccc-4ccc-8ccc-cccccccccccc", assetId: "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa", fileName: "synthetic.jpg", reviewed: false, queued: false, box: null };
  const list = node(), grid = node();
  const state = {
    view: "faces", faceReviewPeople: [], pages: { facePeople: 1 }, pageSize: 10, sort: "most",
    faceReviewSelected: new Set(), faceReviewSelectedData: new Map(),
  };
  const api = async path => {
    if (path.startsWith("/api/people?")) return { people: [person], page: 1 };
    if (path.startsWith("/api/person-faces/")) return {
      faces: [face], page: 1, hasMore: false, draftCount: 0, reviewedCount: 0,
      reviewTotal: 1, inventoryComplete: true, untouchedCount: 1,
    };
    if (path === "/api/face-review/scan-next") return { complete: 1, total: 1, failed: 0, done: true };
    throw new Error(`Unexpected request: ${path}`);
  };
  try {
    const review = createFaceReview({
      api, state, ui: { facePersonList: list, faceReviewGrid: grid },
      assetThumb: () => "", personThumb: () => "", drawFace: async () => {},
      openAssetPreview: () => {
        document.getElementById("asset-preview-dialog").open = true;
        document.getElementById("asset-preview-review").hidden = false;
      },
      refreshSummary: async () => {}, showToast: () => {}, renderPagination: () => {},
    });
    review.bindFaceReviewControls();
    await review.renderFaceReview();
    await list.children[0].listeners.get("click")();
    const card = grid.children[0].querySelector(".face-review-card");
    assert.ok(card);
    card.listeners.get("keydown")({ key: " ", target: card, preventDefault() {} });
    const dialog = document.getElementById("asset-preview-dialog");
    assert.equal(dialog.open, true);
    dialog.listeners.get("keydown")({
      key: " ", target: { closest: () => ({}) }, altKey: false, ctrlKey: false, metaKey: false, shiftKey: false,
      preventDefault() {},
    });
    assert.equal(dialog.open, true);
    let prevented = false;
    dialog.listeners.get("keydown")({
      key: " ", target: node(), altKey: false, ctrlKey: false, metaKey: false, shiftKey: false,
      preventDefault: () => { prevented = true; },
    });
    assert.equal(prevented, true);
    assert.equal(dialog.open, false);
  } finally {
    Object.assign(globalThis, previous);
  }
});

test("Face review submits directly, then reopens and finishes without changing decisions", async () => {
  const previous = { document: globalThis.document, window: globalThis.window, localStorage: globalThis.localStorage };
  const elements = new Map();
  const windowListeners = new Map();
  globalThis.document = {
    getElementById: id => { if (!elements.has(id)) elements.set(id, node()); return elements.get(id); },
    createElement: () => node(), addEventListener() {},
  };
  globalThis.window = { addEventListener: (event, handler) => windowListeners.set(event, handler) };
  globalThis.localStorage = { getItem: () => null };
  const person = { id: "11111111-1111-4111-8111-111111111111", name: "Synthetic person", assetCount: 1 };
  const state = {
    faceReviewCurrentPerson: person, faceReviewPersonId: person.id, faceReviewPeople: [person],
    faceReviewResult: { draftCount: 1, reviewedCount: 1, reviewTotal: 1, untouchedCount: 0, inventoryComplete: true },
    faceReviewSelected: new Set(), faceReviewSelectedData: new Map(), view: "faces",
    pages: { facePeople: 3 }, pageSize: 10, sort: "most",
  };
  const calls = [];
  const api = async (path, options) => {
    calls.push([path, options?.body]);
    if (path.startsWith("/api/people?")) return { people: [person], page: 1 };
    if (path === "/api/face-review/submit") return {
      submitted: { correct: 1, wrong: 0 }, submittedFaces: [], draftCount: 0,
      reviewedCount: 1, reviewTotal: 1, untouchedCount: 0, inventoryComplete: true, personReviewed: true, reviewReopened: false,
    };
    if (path === "/api/face-review/reopen") return {
      draftCount: 0, reviewedCount: 1, reviewTotal: 1, untouchedCount: 0, inventoryComplete: true,
      personReviewed: !options.body.reopened, reviewReopened: options.body.reopened,
    };
    throw new Error(`Unexpected request: ${path}`);
  };
  try {
    const review = createFaceReview({
      api, state, ui: { facePersonList: node(), faceReviewGrid: node() },
      assetThumb: () => "", personThumb: () => "", refreshSummary: async () => {}, showToast: () => {},
      renderPagination: () => {},
    });
    review.bindFaceReviewControls();
    const button = document.getElementById("face-review-submit");
    const click = async () => { button.listeners.get("click")(); await new Promise(resolve => setImmediate(resolve)); };

    windowListeners.get("keydown")({
      key: "Enter", ctrlKey: true, metaKey: false, altKey: false, shiftKey: false,
      target: { closest: () => ({}) }, preventDefault() { throw new Error("Input shortcut was intercepted"); },
    });
    assert.equal(calls.length, 0);
    let prevented = false;
    windowListeners.get("keydown")({
      key: "Enter", ctrlKey: true, metaKey: false, altKey: false, shiftKey: false,
      target: { closest: () => null }, preventDefault: () => { prevented = true; },
    });
    await new Promise(resolve => setImmediate(resolve));
    assert.equal(prevented, true);
    assert.deepEqual(calls[0], ["/api/face-review/submit", { personId: person.id }]);
    assert.equal(button.textContent, "Reopen review");
    await click();
    assert.deepEqual(calls[1], ["/api/face-review/reopen", { personId: person.id, reopened: true }]);
    assert.equal(button.textContent, "Finish review");
    await click();
    assert.deepEqual(calls[2], ["/api/face-review/reopen", { personId: person.id, reopened: false }]);
    assert.equal(button.textContent, "Reopen review");

    document.getElementById("face-person-search").value = "Synthetic";
    const filter = document.getElementById("face-person-name-filter");
    filter.value = "named";
    await filter.listeners.get("change")({ target: filter });
    assert.match(calls.at(-1)[0], /page=1&size=10&q=Synthetic&hideCompleted=0&nameFilter=named$/);
    await document.getElementById("face-person-toggle-completed").listeners.get("click")();
    assert.match(calls.at(-1)[0], /hideCompleted=1&nameFilter=named$/);
    assert.equal(document.getElementById("face-person-toggle-completed").textContent, "Show completed people");
    filter.value = "unnamed";
    await filter.listeners.get("change")({ target: filter });
    assert.match(calls.at(-1)[0], /hideCompleted=1&nameFilter=unnamed$/);
  } finally {
    Object.assign(globalThis, previous);
  }
});
