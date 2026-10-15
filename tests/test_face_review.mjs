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
  const attributes = new Map();
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
    setAttribute: (name, value) => attributes.set(name, String(value)),
    getAttribute: name => attributes.get(name) ?? null,
    removeAttribute: name => attributes.delete(name),
    focus() { globalThis.document.activeElement = this; }, closest: () => null,
    contains(target) { for (let current = target; current; current = current.parent) if (current === this) return true; return false; },
    showModal() { this.open = true; },
    close() { this.open = false; this.listeners.get("close")?.(); },
  };
}

async function submitHarness({ people, pages, submit, peoplePages = null, selectedIndex = 0 }) {
  const previous = { document: globalThis.document, window: globalThis.window, localStorage: globalThis.localStorage,
    requestAnimationFrame: globalThis.requestAnimationFrame };
  const elements = new Map(), windowListeners = new Map(), documentListeners = new Map();
  globalThis.document = {
    activeElement: null,
    getElementById: id => { if (!elements.has(id)) elements.set(id, node()); return elements.get(id); },
    createElement: () => node(), addEventListener: (event, handler) => documentListeners.set(event, handler),
  };
  globalThis.window = { addEventListener: (event, handler) => windowListeners.set(event, handler) };
  globalThis.localStorage = { getItem: () => null };
  globalThis.requestAnimationFrame = callback => setImmediate(callback);
  const list = node(), grid = node();
  const calls = [];
  const pagination = [];
  const state = { view: "faces", faceReviewPeople: [], pages: { facePeople: 1 }, pageSize: 10, sort: "most",
    faceReviewSelected: new Set(), faceReviewSelectedData: new Map() };
  const review = createFaceReview({
    api: async (path, options) => {
      calls.push([path, options]);
      if (path.startsWith("/api/people?")) {
        const query = new URL(`http://local${path}`).searchParams;
        const page = Number(query.get("page")) || 1;
        if (peoplePages) return peoplePages(page, query);
        return { people, page: 1, pages: 1, total: people.length };
      }
      if (path.startsWith("/api/person-faces/")) {
        const page = Number(new URL(`http://local${path}`).searchParams.get("page"));
        const result = pages[page - 1];
        if (result instanceof Error) throw result;
        return result;
      }
      if (path === "/api/face-review/submit") return submit(options);
      throw new Error(`Unexpected request: ${path}`);
    }, state, ui: { facePersonList: list, faceReviewGrid: grid }, assetThumb: () => "", personThumb: () => "",
    drawFace: async () => {}, refreshSummary: async () => {}, showToast: () => {},
    renderPagination: (containerId, data, onPage) => pagination.push({ containerId, data, onPage }),
  });
  review.bindFaceReviewControls();
  await review.renderFaceReview();
  const selectPerson = async index => { await list.children[index].listeners.get("click")(); };
  await selectPerson(selectedIndex);
  const focus = target => { target.focus(); documentListeners.get("focusin")?.({ target }); };
  const fireSubmit = () => windowListeners.get("keydown")({ key: "Enter", ctrlKey: true, metaKey: false, altKey: false,
    shiftKey: false, target: { closest: () => null }, preventDefault() {} });
  const firstCard = grid.querySelector(".face-review-card");
  if (firstCard) { firstCard.listeners.get("focus")(); document.activeElement = firstCard; }
  return { calls, documentListeners, elements, fireSubmit, focus, grid, keydown: windowListeners.get("keydown"), list, pagination, review, selectPerson, state,
    dispose: () => Object.assign(globalThis, previous) };
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

test("Face review shortcut help opens and closes as a dialog", () => {
  const previous = { document: globalThis.document, window: globalThis.window, localStorage: globalThis.localStorage };
  const elements = new Map();
  let keydown;
  globalThis.document = {
    getElementById: id => { if (!elements.has(id)) elements.set(id, node()); return elements.get(id); },
    addEventListener() {},
  };
  globalThis.window = { addEventListener: (event, handler) => { if (event === "keydown") keydown = handler; } };
  globalThis.localStorage = { getItem: () => null };
  try {
    const review = createFaceReview({
      state: { view: "faces", faceReviewCurrentPerson: { id: "synthetic" } },
      ui: { facePersonList: node(), faceReviewGrid: node() },
    });
    review.bindFaceReviewControls();
    const dialog = document.getElementById("face-review-help-dialog");
    document.getElementById("face-review-help").listeners.get("click")();
    assert.equal(dialog.open, true);
    let prevented = false;
    keydown({ key: "?", target: node(), altKey: false, ctrlKey: false, metaKey: false,
      preventDefault: () => { prevented = true; } });
    assert.equal(prevented, true);
    assert.equal(dialog.open, false);
  } finally {
    Object.assign(globalThis, previous);
  }
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

test("Face review icon controls keep search, name, order, and completion independent", async () => {
  const previous = { document: globalThis.document, window: globalThis.window, localStorage: globalThis.localStorage };
  const elements = new Map(), documentListeners = new Map(), windowListeners = new Map();
  globalThis.document = {
    getElementById: id => { if (!elements.has(id)) elements.set(id, node()); return elements.get(id); },
    createElement: () => node(),
    addEventListener: (event, handler) => documentListeners.set(event, handler),
  };
  globalThis.window = { addEventListener: (event, handler) => windowListeners.set(event, [...(windowListeners.get(event) || []), handler]) };
  globalThis.localStorage = { getItem: () => null };
  const state = { view: "faces", pages: { facePeople: 3 }, pageSize: 10, sort: "most", faceReviewPeople: [] };
  const paths = [];
  let review;
  try {
    review = createFaceReview({
      api: async path => { paths.push(path); return { people: [], page: 1 }; },
      changePeopleSort: async value => { state.sort = value; state.pages.facePeople = 1; await review.loadFaceReviewPeople(); },
      state, ui: { facePersonList: node(), faceReviewGrid: node() },
      assetThumb: () => "", personThumb: () => "", refreshSummary: async () => {}, showToast: () => {},
      renderPagination: () => {},
    });
    review.bindFaceReviewControls();
    const get = id => document.getElementById(id);
    get("face-person-search-toggle").listeners.get("click")();
    assert.equal(get("face-person-search-drawer").classList.contains("open"), true);
    assert.equal(get("face-person-search-toggle").getAttribute("aria-expanded"), "true");
    assert.equal(document.activeElement, get("face-person-search"));
    get("face-person-search").value = "Synthetic";
    get("face-person-search").listeners.get("input")();
    await new Promise(resolve => setTimeout(resolve, 200));
    assert.match(paths.at(-1), /page=1&size=10&q=Synthetic&hideCompleted=0&nameFilter=all$/);
    get("face-person-search-toggle").listeners.get("click")();
    assert.equal(get("face-person-search").value, "Synthetic");
    assert.equal(get("face-person-search-toggle").classList.contains("active"), true);
    assert.equal(get("face-person-search-drawer").classList.contains("open"), true);
    assert.equal(get("face-person-search-toggle").getAttribute("aria-expanded"), "true");

    get("face-person-filter-toggle").listeners.get("click")();
    assert.equal(get("face-person-filter-drawer").classList.contains("open"), true);
    assert.equal(get("face-person-search-drawer").classList.contains("open"), true);
    await get("face-person-name-filter").listeners.get("change")({
      target: Object.assign(get("face-person-name-named"), { value: "named" }),
    });
    assert.match(paths.at(-1), /hideCompleted=0&nameFilter=named$/);
    assert.equal(get("face-person-filter-toggle").classList.contains("active"), true);

    get("face-person-sort-toggle").listeners.get("click")();
    await get("face-person-sort-options").listeners.get("change")({
      target: Object.assign(get("face-person-sort-fewest"), { value: "fewest" }),
    });
    assert.match(paths.at(-1), /sort=fewest.*hideCompleted=0&nameFilter=named$/);
    assert.equal(get("face-person-sort-toggle").classList.contains("active"), true);
    await get("face-person-toggle-completed").listeners.get("click")();
    assert.match(paths.at(-1), /sort=fewest.*q=Synthetic&hideCompleted=1&nameFilter=named$/);
    assert.equal(get("face-person-toggle-completed").getAttribute("aria-pressed"), "true");

    get("face-person-filter-toggle").listeners.get("click")();
    let prevented = false;
    for (const handler of windowListeners.get("keydown")) handler({ key: "Escape", target: node(), preventDefault: () => { prevented = true; } });
    assert.equal(prevented, true);
    assert.equal(get("face-person-filter-drawer").classList.contains("open"), false);
    assert.equal(get("face-person-search-drawer").classList.contains("open"), true);
    assert.equal(document.activeElement, get("face-person-filter-toggle"));
    get("face-person-sort-toggle").listeners.get("click")();
    documentListeners.get("pointerdown")({ target: node() });
    assert.equal(get("face-person-sort-drawer").classList.contains("open"), false);
    assert.equal(get("face-person-search-drawer").classList.contains("open"), true);
    get("face-person-search-toggle").listeners.get("click")();
    get("face-person-search-clear").listeners.get("click")();
    await new Promise(resolve => setTimeout(resolve, 200));
    assert.match(paths.at(-1), /sort=fewest.*q=&hideCompleted=1&nameFilter=named$/);
    get("face-person-search-toggle").listeners.get("click")();
    assert.equal(get("face-person-search-toggle").classList.contains("active"), false);
    assert.equal(get("face-person-search-drawer").classList.contains("open"), false);
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
    assert.deepEqual(calls.filter(([path]) => path === "/api/face-review/reopen")[0], ["/api/face-review/reopen", { personId: person.id, reopened: true }]);
    assert.equal(button.textContent, "Finish review");
    await click();
    assert.deepEqual(calls.filter(([path]) => path === "/api/face-review/reopen")[1], ["/api/face-review/reopen", { personId: person.id, reopened: false }]);
    assert.equal(button.textContent, "Reopen review");

    document.getElementById("face-person-search").value = "Synthetic";
    const filter = document.getElementById("face-person-name-filter");
    await filter.listeners.get("change")({ target: Object.assign(document.getElementById("face-person-name-named"), { value: "named" }) });
    assert.match(calls.at(-1)[0], /page=1&size=10&q=Synthetic&hideCompleted=0&nameFilter=named$/);
    await document.getElementById("face-person-toggle-completed").listeners.get("click")();
    assert.match(calls.at(-1)[0], /hideCompleted=1&nameFilter=named$/);
    assert.equal(document.getElementById("face-person-toggle-completed").title, "Show completed people");
    await filter.listeners.get("change")({ target: Object.assign(document.getElementById("face-person-name-unnamed"), { value: "unnamed" }) });
    assert.match(calls.at(-1)[0], /hideCompleted=1&nameFilter=unnamed$/);
    assert.equal(state.faceReviewPersonId, person.id);
  } finally {
    Object.assign(globalThis, previous);
  }
});

test("successful submit focuses the next untouched virtualized face", async () => {
  const previous = { document: globalThis.document, window: globalThis.window, localStorage: globalThis.localStorage };
  const elements = new Map(), windowListeners = new Map();
  globalThis.document = {
    activeElement: null,
    getElementById: id => { if (!elements.has(id)) elements.set(id, node()); return elements.get(id); },
    createElement: () => node(), addEventListener() {},
  };
  globalThis.window = { addEventListener: (event, handler) => windowListeners.set(event, handler) };
  globalThis.localStorage = { getItem: () => null };
  const person = { id: "11111111-1111-4111-8111-111111111111", name: "Synthetic person", assetCount: 2 };
  const faces = ["c1", "c2"].map(suffix => ({
    faceId: `${suffix}1111111-1111-4111-8111-111111111111`, assetId: `a${suffix}111111-1111-4111-8111-111111111111`,
    fileName: "synthetic.jpg", reviewed: false, queued: false, box: null,
  }));
  const list = node(), grid = node();
  const state = { view: "faces", faceReviewPeople: [], pages: { facePeople: 1 }, pageSize: 10, sort: "most",
    faceReviewSelected: new Set(), faceReviewSelectedData: new Map() };
  try {
    const review = createFaceReview({
      api: async (path) => {
        if (path.startsWith("/api/people?")) return { people: [person], page: 1 };
        if (path.startsWith("/api/person-faces/")) return { faces, page: 1, hasMore: false, draftCount: 1,
          reviewedCount: 0, reviewTotal: 2, untouchedCount: 1, inventoryComplete: true };
        if (path === "/api/face-review/submit") return { submitted: { correct: 1, wrong: 0 },
          submittedFaces: [{ faceId: faces[0].faceId, verdict: "correct" }], draftCount: 0, reviewedCount: 1,
          reviewTotal: 2, untouchedCount: 1, inventoryComplete: true, personReviewed: false };
        throw new Error(`Unexpected request: ${path}`);
      }, state, ui: { facePersonList: list, faceReviewGrid: grid }, assetThumb: () => "", personThumb: () => "",
      drawFace: async () => {}, refreshSummary: async () => {}, showToast: () => {}, renderPagination: () => {},
    });
    review.bindFaceReviewControls();
    await review.renderFaceReview();
    await list.children[0].listeners.get("click")();
    document.getElementById("face-review-show-checked").listeners.get("click")();
    const card = grid.querySelector(".face-review-card");
    assert.ok(card);
    card.listeners.get("focus")();
    const shortcut = windowListeners.get("keydown");
    shortcut({ key: "Enter", ctrlKey: true, metaKey: false, altKey: false, shiftKey: false,
      target: { closest: () => null }, preventDefault() {} });
    await new Promise(resolve => setImmediate(resolve));
    assert.equal(document.activeElement?.dataset.faceId, faces[1].faceId);
    assert.equal(state.faceReviewPersonId, person.id);
  } finally { Object.assign(globalThis, previous); }
});

test("completed submission opens the next unreviewed person and focuses their first face", async () => {
  const faces = [{ faceId: "c2111111-1111-4111-8111-111111111111", assetId: "a2111111-1111-4111-8111-111111111111",
    fileName: "next.jpg", reviewed: false, queued: false }];
  const people = [
    { id: "11111111-1111-4111-8111-111111111111", name: "Finished", reviewed: false },
    { id: "22222222-2222-4222-8222-222222222222", name: "Next", reviewed: false },
  ];
  const h = await submitHarness({ people, pages: [{ faces, page: 1, hasMore: false, draftCount: 1,
    reviewedCount: 0, reviewTotal: 1, untouchedCount: 1, inventoryComplete: true }], submit: async () => ({
    submitted: { correct: 1, wrong: 0 }, submittedFaces: [], draftCount: 0, reviewedCount: 1,
    reviewTotal: 1, untouchedCount: 0, inventoryComplete: true, personReviewed: true,
  }) });
  try {
    h.fireSubmit(); await new Promise(resolve => setTimeout(resolve, 10));
    assert.equal(h.state.faceReviewPersonId, people[1].id);
    assert.equal(document.activeElement?.dataset.faceId, faces[0].faceId);
  } finally { h.dispose(); }
});

test("completed-list filtering removes the current person before choosing the next", async () => {
  const person = { id: "11111111-1111-4111-8111-111111111111", name: "Finished", reviewed: false };
  const next = { id: "22222222-2222-4222-8222-222222222222", name: "Next", reviewed: false };
  const face = { faceId: "c2111111-1111-4111-8111-111111111111", assetId: "a2111111-1111-4111-8111-111111111111",
    fileName: "next.jpg", reviewed: false, queued: false };
  const h = await submitHarness({ people: [person, next], peoplePages: (_page, query) => {
    const shown = query.get("hideCompleted") === "1" ? [person, next].filter(item => !item.reviewed) : [person, next];
    return { people: shown, page: 1, pages: 1, total: shown.length };
  }, pages: [{ faces: [face], page: 1, hasMore: false, draftCount: 1, reviewedCount: 0, reviewTotal: 1,
    untouchedCount: 1, inventoryComplete: true }], submit: async () => ({ submitted: { correct: 1, wrong: 0 },
    submittedFaces: [], draftCount: 0, reviewedCount: 1, reviewTotal: 1, untouchedCount: 0,
    inventoryComplete: true, personReviewed: true }) });
  try {
    await h.elements.get("face-person-toggle-completed").listeners.get("click")();
    h.fireSubmit(); await new Promise(resolve => setTimeout(resolve, 10));
    assert.equal(h.state.faceReviewPersonId, next.id);
    assert.equal(document.activeElement?.dataset.faceId, face.faceId);
  } finally { h.dispose(); }
});

test("completed submission scans later people pages and wraps to earlier eligible people", async t => {
  const face = { faceId: "c3111111-1111-4111-8111-111111111111", assetId: "a3111111-1111-4111-8111-111111111111",
    fileName: "next.jpg", reviewed: false, queued: false };
  const people = Array.from({ length: 11 }, (_, index) => ({
    id: `${String(index + 1).padStart(8, "0")}-1111-4111-8111-111111111111`, name: `Person ${index + 1}`, reviewed: true,
  }));
  people[0].reviewed = false;
  const current = people[9]; current.reviewed = false;
  people[10].reviewed = false;
  const pageResult = { faces: [face], page: 1, hasMore: false, draftCount: 0, reviewedCount: 0,
    reviewTotal: 1, untouchedCount: 1, inventoryComplete: true };
  await t.test("later page is considered before wrapping", async () => {
    const h = await submitHarness({ people, pages: [{ ...pageResult, draftCount: 1 }], peoplePages: (page) => ({
      people: people.slice((page - 1) * 10, page * 10), page, pages: 2, total: people.length,
    }), selectedIndex: 9, submit: async () => ({ submitted: { correct: 1, wrong: 0 }, submittedFaces: [], draftCount: 0,
      reviewedCount: 1, reviewTotal: 1, untouchedCount: 0, inventoryComplete: true, personReviewed: true }) });
    try {
      h.state.pages.facePeople = 1;
      h.fireSubmit(); await new Promise(resolve => setTimeout(resolve, 10));
      assert.equal(h.state.faceReviewPersonId, people[10].id);
    } finally { h.dispose(); }
  });
  await t.test("earlier eligible person is considered after later pages", async () => {
    const h = await submitHarness({ people, pages: [{ ...pageResult, draftCount: 1 }], peoplePages: (page) => ({
      people: people.slice((page - 1) * 10, page * 10), page, pages: 2, total: people.length,
    }), selectedIndex: 9, submit: async () => ({ submitted: { correct: 1, wrong: 0 }, submittedFaces: [], draftCount: 0,
      reviewedCount: 1, reviewTotal: 1, untouchedCount: 0, inventoryComplete: true, personReviewed: true }) });
    try {
      h.state.pages.facePeople = 1;
      people[10].reviewed = true;
      h.fireSubmit(); await new Promise(resolve => setTimeout(resolve, 10));
      assert.equal(h.state.faceReviewPersonId, people[0].id, JSON.stringify(h.calls.map(([path]) => path)));
      assert.equal(h.state.pages.facePeople, 1);
      assert.ok(h.state.faceReviewPeople.some(item => item.id === people[0].id));
      assert.equal(h.pagination.at(-1).data.page, 1);
    } finally { h.dispose(); }
  });
});

test("completed person with no candidate shows filter guidance and a retryable next-person action", async () => {
  const person = { id: "11111111-1111-4111-8111-111111111111", name: "Finished", reviewed: false };
  const face = { faceId: "c1111111-1111-4111-8111-111111111111", assetId: "a1111111-1111-4111-8111-111111111111",
    fileName: "checked.jpg", reviewed: false, queued: false };
  let failRefresh = false;
  const h = await submitHarness({ people: [person], pages: [{ faces: [face], page: 1, hasMore: false, draftCount: 1,
    reviewedCount: 0, reviewTotal: 1, untouchedCount: 0, inventoryComplete: true }], peoplePages: (page, query) => {
      if (failRefresh && query.get("page") === "1") throw new Error("synthetic list failure");
      return { people: [person], page, pages: 1, total: 1 };
    }, submit: async () => ({ submitted: { correct: 1, wrong: 0 }, submittedFaces: [{ faceId: face.faceId, verdict: "correct" }], draftCount: 0,
      reviewedCount: 1, reviewTotal: 1, untouchedCount: 0, inventoryComplete: true, personReviewed: true }) });
  try {
    failRefresh = true;
    h.fireSubmit(); await new Promise(resolve => setTimeout(resolve, 10));
    assert.equal(h.state.faceReviewPersonId, person.id);
    assert.match(h.grid.querySelector(".face-review-empty")?.children[0]?.textContent || "", /Could not load the next person/, JSON.stringify(h.calls.map(([path]) => path)));
    const retry = h.grid.querySelector("#face-review-next-person") || h.grid.querySelector(".face-review-empty")?.children.find(item => item.id === "face-review-next-person");
    assert.ok(retry);
    failRefresh = false;
    await retry.listeners.get("click")();
    assert.match(h.grid.querySelector(".face-review-empty")?.children[0]?.textContent || "", /No more people need review with these filters/);
    assert.equal(h.grid.getAttribute("role"), "group");
    assert.equal(h.grid.getAttribute("aria-multiselectable"), null);
    h.elements.get("face-review-show-checked").listeners.get("click")();
    assert.equal(h.grid.getAttribute("role"), "listbox");
    assert.equal(h.grid.getAttribute("aria-multiselectable"), "true");
    const peopleCalls = h.calls.filter(([path]) => path.startsWith("/api/people?")).length;
    h.keydown({ key: "n", ctrlKey: false, metaKey: false, altKey: false, shiftKey: false, target: node(), preventDefault() {} });
    await new Promise(resolve => setTimeout(resolve, 10));
    assert.ok(h.calls.filter(([path]) => path.startsWith("/api/people?")).length > peopleCalls);
  } finally { h.dispose(); }
});

test("later-page failure restores the original people list and pagination state", async () => {
  const people = Array.from({ length: 11 }, (_, index) => ({
    id: `${String(index + 1).padStart(8, "0")}-1111-4111-8111-111111111111`, name: `Person ${index + 1}`, reviewed: true,
  }));
  people[9].reviewed = false;
  const face = { faceId: "c3111111-1111-4111-8111-111111111111", assetId: "a3111111-1111-4111-8111-111111111111",
    fileName: "reviewed.jpg", reviewed: false, queued: false };
  const h = await submitHarness({ people, selectedIndex: 9, pages: [{ faces: [face], page: 1, hasMore: false,
    draftCount: 1, reviewedCount: 0, reviewTotal: 1, untouchedCount: 0, inventoryComplete: true }],
    peoplePages: (page) => {
      if (page === 2) throw new Error("synthetic later-page failure");
      return { people: people.slice(0, 10), page: 1, pages: 2, total: people.length };
    }, submit: async () => ({ submitted: { correct: 1, wrong: 0 },
      submittedFaces: [{ faceId: face.faceId, verdict: "correct" }], draftCount: 0, reviewedCount: 1,
      reviewTotal: 1, untouchedCount: 0, inventoryComplete: true, personReviewed: true }) });
  try {
    h.fireSubmit(); await new Promise(resolve => setTimeout(resolve, 10));
    assert.equal(h.state.faceReviewPersonId, people[9].id);
    assert.equal(h.state.pages.facePeople, 1);
    assert.deepEqual(h.state.faceReviewPeople.map(item => item.id), people.slice(0, 10).map(item => item.id));
    assert.equal(h.pagination.at(-1).data.page, 1);
    assert.equal(h.pagination.at(-1).data.pages, 2);
  } finally { h.dispose(); }
});

test("submission keeps a surviving untouched anchor available when it is the only candidate", async () => {
  const faces = ["c1", "c2"].map((suffix, index) => ({ faceId: `${suffix}1111111-1111-4111-8111-111111111111`,
    assetId: `a${suffix}111111-1111-4111-8111-111111111111`, fileName: `synthetic-${index}.jpg`, reviewed: false, queued: false }));
  const person = { id: "11111111-1111-4111-8111-111111111111", name: "Synthetic", assetCount: 2 };
  const h = await submitHarness({ people: [person], pages: [{ faces, page: 1, hasMore: false, draftCount: 1, reviewedCount: 0,
    reviewTotal: 2, untouchedCount: 1, inventoryComplete: true }], submit: async () => ({ submitted: { correct: 1, wrong: 0 },
    submittedFaces: [{ faceId: faces[1].faceId, verdict: "correct" }], draftCount: 0, reviewedCount: 1, reviewTotal: 2,
    untouchedCount: 1, inventoryComplete: true, personReviewed: false }) });
  try {
    h.fireSubmit(); await new Promise(resolve => setImmediate(resolve));
    assert.equal(document.activeElement?.dataset.faceId, faces[0].faceId);
  } finally { h.dispose(); }
});

test("delayed submission respects a new focus target and a newly selected person", async t => {
  const faces = ["c1", "c2"].map(suffix => ({ faceId: `${suffix}1111111-1111-4111-8111-111111111111`,
    assetId: `a${suffix}111111-1111-4111-8111-111111111111`, fileName: "synthetic.jpg", reviewed: false, queued: false }));
  const people = [{ id: "11111111-1111-4111-8111-111111111111", name: "Synthetic one", assetCount: 2 },
    { id: "22222222-2222-4222-8222-222222222222", name: "Synthetic two", assetCount: 2 }];
  const page = { faces, page: 1, hasMore: false, draftCount: 1, reviewedCount: 0, reviewTotal: 2, untouchedCount: 1, inventoryComplete: true };
  const success = { submitted: { correct: 1, wrong: 0 }, submittedFaces: [{ faceId: faces[0].faceId, verdict: "correct" }],
    draftCount: 0, reviewedCount: 1, reviewTotal: 2, untouchedCount: 1, inventoryComplete: true, personReviewed: false };
  await t.test("focus moved to another control", async () => {
    let resolveSubmit;
    const h = await submitHarness({ people: [people[0]], pages: [page], submit: () => new Promise(resolve => { resolveSubmit = resolve; }) });
    try {
      h.fireSubmit();
      const search = document.getElementById("face-person-search"); h.focus(search);
      resolveSubmit(success); await new Promise(resolve => setImmediate(resolve));
      assert.equal(document.activeElement, search);
    } finally { h.dispose(); }
  });
  await t.test("person changed", async () => {
    let resolveSubmit;
    const h = await submitHarness({ people, pages: [page], submit: () => new Promise(resolve => { resolveSubmit = resolve; }) });
    try {
      h.fireSubmit();
      h.focus(h.list.children[1]); await h.selectPerson(1);
      const newPersonId = h.state.faceReviewPersonId;
      const newPersonFocus = document.activeElement;
      resolveSubmit(success); await new Promise(resolve => setImmediate(resolve));
      assert.equal(h.state.faceReviewPersonId, people[1].id);
      assert.equal(h.state.faceReviewPersonId, newPersonId);
      assert.equal(document.activeElement, newPersonFocus);
    } finally { h.dispose(); }
  });
});

test("delayed completed submission preserves a newer focus or person selection", async t => {
  const people = [{ id: "11111111-1111-4111-8111-111111111111", name: "Finished", reviewed: false },
    { id: "22222222-2222-4222-8222-222222222222", name: "Chosen", reviewed: false }];
  const face = { faceId: "c1111111-1111-4111-8111-111111111111", assetId: "a1111111-1111-4111-8111-111111111111",
    fileName: "synthetic.jpg", reviewed: false, queued: false };
  const page = { faces: [face], page: 1, hasMore: false, draftCount: 1, reviewedCount: 0,
    reviewTotal: 1, untouchedCount: 1, inventoryComplete: true };
  const completed = { submitted: { correct: 1, wrong: 0 }, submittedFaces: [{ faceId: face.faceId, verdict: "correct" }], draftCount: 0,
    reviewedCount: 1, reviewTotal: 1, untouchedCount: 0, inventoryComplete: true, personReviewed: true };
  await t.test("focus moved while next-person refresh is pending", async () => {
    let resolvePeople;
    let listLoads = 0;
    const h = await submitHarness({ people, pages: [page], submit: async () => completed,
      peoplePages: (pageNumber) => {
        listLoads += 1;
        if (listLoads > 1) return new Promise(resolve => { resolvePeople = resolve; });
        return { people, page: pageNumber, pages: 1, total: people.length };
      } });
    try {
      h.fireSubmit();
      for (let attempt = 0; !resolvePeople && attempt < 10; attempt += 1) await new Promise(resolve => setImmediate(resolve));
      assert.equal(typeof resolvePeople, "function");
      const search = h.elements.get("face-person-search"); h.focus(search);
      resolvePeople({ people, page: 1, pages: 1, total: people.length });
      await new Promise(resolve => setTimeout(resolve, 10));
      assert.equal(h.state.faceReviewPersonId, people[0].id);
      assert.equal(document.activeElement, search);
      const empty = h.grid.querySelector(".face-review-empty");
      assert.match(empty.children[0].textContent, /Review complete for this person/);
      assert.equal(empty.children[1].textContent, "Next person (N)");
      assert.equal(empty.children[1].disabled, false);
    } finally { h.dispose(); }
  });
  await t.test("person changed while next-person refresh is pending", async () => {
    let resolvePeople;
    let listLoads = 0;
    const h = await submitHarness({ people, pages: [page], submit: async () => completed,
      peoplePages: (pageNumber) => {
        listLoads += 1;
        if (listLoads > 1) return new Promise(resolve => { resolvePeople = resolve; });
        return { people, page: pageNumber, pages: 1, total: people.length };
      } });
    try {
      h.fireSubmit();
      for (let attempt = 0; !resolvePeople && attempt < 10; attempt += 1) await new Promise(resolve => setImmediate(resolve));
      assert.equal(typeof resolvePeople, "function");
      h.focus(h.list.children[1]); await h.selectPerson(1);
      const focused = document.activeElement;
      resolvePeople({ people, page: 1, pages: 1, total: people.length });
      await new Promise(resolve => setTimeout(resolve, 10));
      assert.equal(h.state.faceReviewPersonId, people[1].id);
      assert.equal(document.activeElement, focused);
    } finally { h.dispose(); }
  });
});

test("incomplete inventory does not trigger completed-person advance", async () => {
  const people = [{ id: "11111111-1111-4111-8111-111111111111", name: "Current", reviewed: false },
    { id: "22222222-2222-4222-8222-222222222222", name: "Next", reviewed: false }];
  const face = { faceId: "c1111111-1111-4111-8111-111111111111", assetId: "a1111111-1111-4111-8111-111111111111",
    fileName: "synthetic.jpg", reviewed: false, queued: false };
  const h = await submitHarness({ people, pages: [{ faces: [face], page: 1, hasMore: false, draftCount: 1,
    reviewedCount: 0, reviewTotal: 2, untouchedCount: 1, inventoryComplete: false }], submit: async () => ({
    submitted: { correct: 1, wrong: 0 }, submittedFaces: [], draftCount: 0, reviewedCount: 1,
    reviewTotal: 2, untouchedCount: 1, inventoryComplete: false, personReviewed: true,
  }) });
  try {
    const peopleCalls = h.calls.filter(([path]) => path.startsWith("/api/people?")).length;
    h.fireSubmit(); await new Promise(resolve => setTimeout(resolve, 10));
    assert.equal(h.state.faceReviewPersonId, people[0].id);
    assert.equal(h.calls.filter(([path]) => path.startsWith("/api/people?")).length, peopleCalls);
  } finally { h.dispose(); }
});

test("failed submission restores the original face without clearing its draft", async () => {
  const face = { faceId: "c1111111-1111-4111-8111-111111111111", assetId: "a1111111-1111-4111-8111-111111111111",
    fileName: "synthetic.jpg", reviewed: false, queued: false };
  const person = { id: "11111111-1111-4111-8111-111111111111", name: "Synthetic", assetCount: 1 };
  const h = await submitHarness({ people: [person], pages: [{ faces: [face], page: 1, hasMore: false, draftCount: 1,
    reviewedCount: 0, reviewTotal: 1, untouchedCount: 1, inventoryComplete: true }], submit: async () => { throw new Error("busy"); } });
  try {
    h.fireSubmit(); await new Promise(resolve => setImmediate(resolve));
    assert.equal(document.activeElement?.dataset.faceId, face.faceId);
    assert.equal(h.state.faceReviewResult.draftCount, 1);
  } finally { h.dispose(); }
});

test("continuation loads the next page and focuses Retry loading after a page error", async t => {
  const face = (suffix, index) => ({ faceId: `${suffix}1111111-1111-4111-8111-111111111111`,
    assetId: `a${suffix}111111-1111-4111-8111-111111111111`, fileName: `synthetic-${index}.jpg`, reviewed: false, queued: false });
  const first = face("c1", 1), next = face("c2", 2);
  const person = { id: "11111111-1111-4111-8111-111111111111", name: "Synthetic", assetCount: 2 };
  const firstPage = { faces: [first], page: 1, hasMore: true, draftCount: 1, reviewedCount: 0, reviewTotal: 2, untouchedCount: 1, inventoryComplete: false };
  const response = { submitted: { correct: 1, wrong: 0 }, submittedFaces: [{ faceId: first.faceId, verdict: "correct" }],
    draftCount: 0, reviewedCount: 1, reviewTotal: 2, untouchedCount: 1, inventoryComplete: false, personReviewed: false };
  await t.test("next page contains the next untouched face", async () => {
    const secondPage = { faces: [next], page: 2, hasMore: false, draftCount: 0, reviewedCount: 1, reviewTotal: 2, untouchedCount: 1, inventoryComplete: false };
    const h = await submitHarness({ people: [person], pages: [firstPage, secondPage], submit: async () => response });
    try {
      h.fireSubmit(); await new Promise(resolve => setImmediate(resolve));
      assert.equal(document.activeElement?.dataset.faceId, next.faceId);
    } finally { h.dispose(); }
  });
  await t.test("page error focuses the visible retry control", async () => {
    const h = await submitHarness({ people: [person], pages: [firstPage, new Error("synthetic page failure")], submit: async () => response });
    try {
      h.fireSubmit(); await new Promise(resolve => setImmediate(resolve));
      const retry = h.elements.get("face-review-load-more");
      assert.equal(retry.textContent, "Retry loading");
      assert.equal(document.activeElement, retry);
    } finally { h.dispose(); }
  });
});
