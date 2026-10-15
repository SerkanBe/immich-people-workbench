// Copyright (C) 2026 Serkan Bekdemir
// SPDX-License-Identifier: AGPL-3.0-only
import test from "node:test";
import assert from "node:assert/strict";
import { createNavigationDrawer, routeForView, updateNavigationState, viewFromPath } from "../web/navigation.js";

class Classes {
  values = new Set();
  add(value) { this.values.add(value); }
  remove(value) { this.values.delete(value); }
  contains(value) { return this.values.has(value); }
  toggle(value, on) { if (on) this.add(value); else this.remove(value); }
}

class Element {
  constructor(name) { this.name = name; this.listeners = new Map(); this.attributes = {}; this.classList = new Classes(); this.style = {}; this.hidden = false; this.inert = false; }
  addEventListener(type, listener) { this.listeners.set(type, [...(this.listeners.get(type) || []), listener]); }
  dispatch(type, props = {}) {
    const event = { type, key: "", button: 0, shiftKey: false, metaKey: false, ctrlKey: false, altKey: false, prevented: false, preventDefault() { this.prevented = true; }, ...props };
    for (const listener of this.listeners.get(type) || []) listener(event);
    return event;
  }
  setAttribute(name, value) { this.attributes[name] = value; }
  getAttribute(name) { return this.attributes[name] ?? null; }
  focus() { this.focusCount = (this.focusCount || 0) + 1; globalThis.document.activeElement = this; globalThis.document.dispatch("focusin", { target: this }); }
  closest(selector) { return selector === "a[data-view]" && this.dataset?.view ? this : null; }
  contains(element) { return element === this || (this.focusables || []).includes(element); }
  querySelector(selector) { return selector.startsWith("a[href") ? this.focusables?.find(element => element.name === "first-link") : null; }
  querySelectorAll() { return this.focusables || []; }
}

function setup(width = 390, afterNavigate = () => {}) {
  const sidebar = new Element("sidebar"), menu = new Element("menu"), close = new Element("close");
  const backdrop = new Element("backdrop"), main = new Element("main-column"), body = new Element("body");
  const first = new Element("first-link"), last = new Element("logout");
  const heading = new Element("heading"), link = new Element("pending-link");
  link.dataset = { view: "pending" };
  sidebar.focusables = [close, first, link, last];
  sidebar.addEventListener = Element.prototype.addEventListener;
  const skip = new Element("skip-link");
  const elements = { "#app-sidebar": sidebar, "#nav-menu": menu, "#nav-close": close, "#nav-backdrop": backdrop, "#app-main-column": main, ".skip-link": skip };
  const doc = globalThis.document = {
    body, activeElement: menu, listeners: new Map(),
    querySelector(selector) {
      if (selector === ".view.active h2") return heading;
      if (selector === '.nav-button[aria-current="page"]') return { dataset: { view: activeView } };
      return elements[selector];
    },
    querySelectorAll(selector) { return selector === "dialog" ? [] : []; },
    addEventListener(type, listener) { this.listeners.set(type, [...(this.listeners.get(type) || []), listener]); },
    dispatch(type, props = {}) {
      const event = { type, prevented: false, preventDefault() { this.prevented = true; }, ...props };
      for (const listener of this.listeners.get(type) || []) listener(event);
      return event;
    },
  };
  body.classList = new Classes();
  body.style.overflow = "clip";
  const mediaListeners = [];
  const media = { get matches() { return width <= 900; }, addEventListener(_type, listener) { mediaListeners.push(listener); } };
  const win = { innerWidth: width, matchMedia: () => media, addEventListener() {} };
  const selected = [];
  let activeView = "unnamed";
  const drawer = createNavigationDrawer({ document: doc, window: win, onNavigate: view => { selected.push(view); activeView = view; return afterNavigate(view); } });
  return { sidebar, menu, close, backdrop, main, body, skip, first, last, heading, link, doc, mediaListeners, setWidth(value) { width = value; win.innerWidth = value; mediaListeners.forEach(listener => listener()); }, selected, drawer, setActiveView(view) { activeView = view; } };
}

test("mobile drawer traps focus and Escape returns focus to Menu", () => {
  const ui = setup();
  ui.menu.dispatch("click");
  assert.equal(ui.drawer.isOpen(), true);
  assert.equal(ui.sidebar.inert, false);
  assert.equal(ui.menu.getAttribute("aria-expanded"), "true");
  assert.equal(ui.main.inert, true);
  assert.equal(ui.skip.inert, true);
  assert.equal(ui.backdrop.hidden, false);
  assert.equal(ui.body.classList.contains("nav-drawer-open"), true);
  assert.equal(ui.body.style.overflow, "clip");
  assert.equal(ui.doc.activeElement, ui.close);

  ui.doc.activeElement = ui.last;
  const tab = ui.doc.dispatch("keydown", { key: "Tab" });
  assert.equal(ui.doc.activeElement, ui.close);
  assert.equal(tab.prevented, true);
  ui.doc.activeElement = ui.close;
  ui.doc.dispatch("keydown", { key: "Tab", shiftKey: true });
  assert.equal(ui.doc.activeElement, ui.last);
  ui.doc.dispatch("keydown", { key: "Escape" });
  assert.equal(ui.drawer.isOpen(), false);
  assert.equal(ui.sidebar.inert, true);
  assert.equal(ui.main.inert, false);
  assert.equal(ui.skip.inert, false);
  assert.equal(ui.backdrop.hidden, true);
  assert.equal(ui.menu.getAttribute("aria-expanded"), "false");
  assert.equal(ui.doc.activeElement, ui.menu);
  assert.equal(ui.body.style.overflow, "clip");
});

test("drawer closes on backdrop and focused destination navigation", async () => {
  const ui = setup();
  ui.menu.dispatch("click");
  ui.sidebar.dispatch("click", { target: ui.first });
  assert.equal(ui.drawer.isOpen(), true);
  ui.backdrop.dispatch("click");
  assert.equal(ui.doc.activeElement, ui.menu);
  ui.menu.dispatch("click");
  const event = ui.sidebar.dispatch("click", { target: ui.link });
  assert.equal(event.prevented, true);
  assert.deepEqual(ui.selected, ["pending"]);
  await Promise.resolve();
  assert.equal(ui.doc.activeElement, ui.heading);
  assert.equal(ui.main.inert, false);
});

test("Close button returns focus to Menu", () => {
  const ui = setup();
  ui.menu.dispatch("click");
  ui.close.dispatch("click");
  assert.equal(ui.drawer.isOpen(), false);
  assert.equal(ui.sidebar.inert, true);
  assert.equal(ui.doc.activeElement, ui.menu);
});

test("modified clicks keep browser link behavior and desktop resize clears drawer state", () => {
  const ui = setup();
  ui.drawer.close();
  assert.equal(ui.body.style.overflow, "clip");
  ui.menu.dispatch("click");
  const modified = ui.sidebar.dispatch("click", { target: ui.link, ctrlKey: true });
  assert.equal(modified.prevented, false);
  assert.deepEqual(ui.selected, []);
  assert.equal(ui.drawer.isOpen(), true);
  ui.setWidth(1280);
  assert.equal(ui.drawer.isOpen(), false);
  assert.equal(ui.main.inert, false);
  assert.equal(ui.backdrop.hidden, true);
  assert.equal(ui.body.classList.contains("nav-drawer-open"), false);
  assert.equal(ui.body.style.overflow, "clip");
  assert.equal(ui.doc.activeElement, ui.first);
  ui.menu.dispatch("click");
  assert.equal(ui.drawer.isOpen(), false);
  ui.setWidth(390);
  assert.equal(ui.sidebar.inert, true);
  assert.equal(ui.doc.activeElement, ui.menu);
  ui.menu.dispatch("click");
  assert.equal(ui.drawer.isOpen(), true);
});

test("all current paths and legacy aliases update one exact active destination", () => {
  const views = ["unnamed", "group-photos", "investigate", "faces", "pending", "named", "ignored"];
  const paths = ["/naming/clusters", "/naming/group-photos", "/investigate", "/faces", "/pending", "/named", "/ignored"];
  const buttons = views.map(view => ({
    dataset: { view }, classList: new Classes(), attributes: {},
    setAttribute(name, value) { this.attributes[name] = value; },
    removeAttribute(name) { delete this.attributes[name]; },
  }));
  const navDoc = { querySelectorAll: selector => selector === ".nav-button" ? buttons : [] };
  assert.equal(routeForView("unnamed"), "/naming/clusters");
  assert.equal(routeForView("group-photos"), "/naming/group-photos");
  const historyTraversal = [0, 1, 2, 1, 0, 3, 4, 5, 6];
  historyTraversal.forEach(index => {
    const path = paths[index];
    const view = viewFromPath(path);
    updateNavigationState(navDoc, view);
    assert.equal(view, views[index]);
    assert.deepEqual(buttons.filter(button => button.attributes["aria-current"] === "page").map(button => button.dataset.view), [view]);
  });
  assert.equal(viewFromPath("/"), "unnamed");
  assert.equal(viewFromPath("/unnamed"), "unnamed");
  assert.equal(viewFromPath("/merge"), "unnamed");
  assert.equal(viewFromPath("/merge", true), "merge");
  assert.equal(routeForView("merge"), "/merge");
});

test("an open dialog prevents drawer stacking and closes a drawer opened earlier", () => {
  const ui = setup();
  let dialogOpen = true;
  const query = ui.doc.querySelector.bind(ui.doc);
  ui.doc.querySelector = selector => selector === "dialog[open]" && dialogOpen ? {} : query(selector);
  ui.menu.dispatch("click");
  assert.equal(ui.drawer.isOpen(), false);
  dialogOpen = false;
  ui.menu.dispatch("click");
  assert.equal(ui.drawer.isOpen(), true);
  ui.doc.dispatch("toggle", { target: { open: true } });
  assert.equal(ui.drawer.isOpen(), false);
  assert.equal(ui.main.inert, false);
  assert.equal(ui.sidebar.inert, true);
  assert.equal(ui.body.style.overflow, "clip");
});

test("a delayed navigation cannot steal focus after another focus or route change", async () => {
  let completeNavigation;
  const ui = setup(390, () => new Promise(resolve => { completeNavigation = resolve; }));
  ui.menu.dispatch("click");
  ui.sidebar.dispatch("click", { target: ui.link });
  assert.equal(ui.doc.activeElement, ui.heading);
  ui.first.focus();
  completeNavigation();
  await new Promise(resolve => setImmediate(resolve));
  assert.equal(ui.doc.activeElement, ui.first);
  assert.equal(ui.heading.focusCount, 1);

  let completeSecondNavigation;
  const next = setup(390, () => new Promise(resolve => { completeSecondNavigation = resolve; }));
  next.menu.dispatch("click");
  next.sidebar.dispatch("click", { target: next.link });
  next.setActiveView("faces");
  completeSecondNavigation();
  await new Promise(resolve => setImmediate(resolve));
  assert.equal(next.heading.focusCount, 1);
});
