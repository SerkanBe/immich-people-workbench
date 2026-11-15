// Copyright (C) 2026 Serkan Bekdemir
// SPDX-License-Identifier: AGPL-3.0-only
import test from "node:test";
import assert from "node:assert/strict";
import { createNaming } from "../web/naming.js";

const deferred = () => {
  let resolve, reject;
  const promise = new Promise((done, fail) => { resolve = done; reject = fail; });
  return { promise, resolve, reject };
};

function setup({ queue = async () => ({}), load = async () => {}, refresh = async () => {} } = {}) {
  const previous = { document: globalThis.document, requestAnimationFrame: globalThis.requestAnimationFrame };
  const events = [], calls = [], toasts = [], cards = [];
  const state = { names: [], pages: { investigate: 1 }, sort: "most", pageSize: 24 };
  const elements = new Map();
  const element = () => ({ children: [], listeners: new Map(), value: "", classList: { remove() {}, toggle() {} },
    append(...children) { this.children.push(...children); }, replaceChildren() { this.children = []; },
    addEventListener(name, handler) { this.listeners.set(name, handler); }, focus() {}, setAttribute() {},
    closest() { return { append() {} }; } });
  globalThis.document = {
    querySelector(selector) { if (!elements.has(selector)) elements.set(selector, element()); return elements.get(selector); },
    createElement: () => element(),
    dispatchEvent(event) { if (event.type === "workbench-save-state") events.push(state.namingSaving); },
  };
  globalThis.requestAnimationFrame = callback => callback();
  const grid = {
    querySelectorAll: () => cards.filter(card => !card.removed).map(card => card.input),
    querySelector: () => cards.some(card => !card.removed),
  };
  const controls = createNaming({
    api: async (path, options) => { calls.push([path, options]); return queue(path, options); },
    cleanName: value => value.trim(), loadNames: load, refreshSummary: refresh,
    renderInvestigate: async () => {}, renderPagination() {}, showToast: message => toasts.push(message),
    personThumb: id => `/person/${id}`, state, ui: { grid, investigateGrid: grid, namedGrid: grid },
  });
  function model(id) {
    const card = { removed: false, remove() { this.removed = true; } }, input = element();
    input.value = `Synthetic ${id}`; card.input = input; cards.push(card);
    const value = { mode: "investigate", person: { id }, input, card, list: element(), ghost: element(),
      matches: [], selectedIndex: -1, selectedTarget: null, draft: input.value, slides: [], slideIndex: 0 };
    controls.attachNameBehavior(value);
    return value;
  }
  const press = (value, key, altKey = false) => value.input.listeners.get("keydown")({ key, altKey, preventDefault() {} });
  return { state, calls, toasts, events, model, press, dispose: () => Object.assign(globalThis, previous) };
}

test("Naming capture gate counts concurrent queued saves until each response returns", async () => {
  const first = deferred(), second = deferred(); let index = 0;
  const run = setup({ queue: () => [first, second][index++].promise });
  try {
    const one = run.model("one"), two = run.model("two");
    const savingOne = run.press(one, "Enter"), savingTwo = run.press(two, "Enter");
    assert.equal(run.state.namingSaving, 2);
    assert.equal(one.card.removed, true); assert.equal(two.card.removed, true);
    first.resolve({}); await savingOne;
    assert.equal(run.state.namingSaving, 1);
    assert.equal(run.events.includes(0), false, "a completed earlier save does not enable capture during a later save");
    second.resolve({}); await savingTwo;
    assert.equal(run.state.namingSaving, 0);
    assert.equal(run.events.at(-1), 0);
    assert.equal(run.calls.every(([path]) => path === "/api/queue"), true);
  } finally { run.dispose(); }
});

test("Naming save failure keeps capture busy through recovery and clears it if recovery also fails", async () => {
  const save = deferred(), recovery = deferred();
  const run = setup({ queue: () => save.promise, load: () => recovery.promise });
  try {
    const naming = run.press(run.model("failure"), "Enter");
    save.reject(new Error("Synthetic local save failure"));
    await new Promise(resolve => setImmediate(resolve));
    assert.equal(run.state.namingSaving, 1);
    assert.equal(run.events.includes(0), false);
    recovery.reject(new Error("Synthetic recovery read failure"));
    await naming;
    assert.equal(run.state.namingSaving, 0);
    assert.match(run.toasts.join(" "), /Synthetic local save failure/);
    assert.match(run.toasts.join(" "), /Synthetic recovery read failure/);
    assert.equal(run.calls.some(([path]) => path === "/api/sync"), false);
  } finally { run.dispose(); }
});
