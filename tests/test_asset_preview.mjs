// Copyright (C) 2026 Serkan Bekdemir
// SPDX-License-Identifier: AGPL-3.0-only
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

const source = readFileSync(new URL("../web/asset-preview.js", import.meta.url), "utf8");
const { createAssetPreview, projectFaceBox } = await import(`data:text/javascript;base64,${Buffer.from(source).toString("base64")}`);

const box = { x1: 20, y1: 10, x2: 60, y2: 40, width: 100, height: 200 };
function assertBox(actual, expected) {
  assert.ok(actual);
  for (const [side, value] of Object.entries(expected)) {
    assert.ok(Math.abs(actual[side] - value) < 1e-9, `${side}: expected ${value}, got ${actual[side]}`);
  }
}

test("projects a face box when preview and coordinate dimensions disagree", () => {
  assertBox(projectFaceBox(box, 200, 100, 200, 100), { left: 40, top: 5, width: 80, height: 15 });
});

test("turns the projected box clockwise through all four orientations", () => {
  assertBox(projectFaceBox(box, 200, 100, 200, 100, 1), { left: 160, top: 20, width: 30, height: 40 });
  assertBox(projectFaceBox(box, 200, 100, 200, 100, 2), { left: 80, top: 80, width: 80, height: 15 });
  assertBox(projectFaceBox(box, 200, 100, 200, 100, 3), { left: 10, top: 40, width: 30, height: 40 });
  assert.deepEqual(projectFaceBox(box, 200, 100, 200, 100, 4), projectFaceBox(box, 200, 100, 200, 100));
});

test("accounts for empty space around a contained preview", () => {
  assertBox(projectFaceBox(box, 200, 100, 300, 300), { left: 60, top: 82.5, width: 120, height: 22.5 });
});

test("rejects missing dimensions or unusable coordinates", () => {
  assert.equal(projectFaceBox({ ...box, width: 0 }, 200, 100, 200, 100), null);
  assert.equal(projectFaceBox({ ...box, x2: 20 }, 200, 100, 200, 100), null);
  assert.equal(projectFaceBox({ ...box, x1: null }, 200, 100, 200, 100), null);
  assert.equal(projectFaceBox({ ...box, y1: NaN }, 200, 100, 200, 100), null);
});

test("preview shows a mismatched box and resets its rotation for the next photo", () => {
  const previous = { document: globalThis.document, window: globalThis.window, requestAnimationFrame: globalThis.requestAnimationFrame };
  const selectors = ["dialog", "viewport", "stage", "image", "face-box", "caption", "toggle-box", "toggle-label", "rotate-box", "rotate-label", "zoom-level", "review", "title"];
  const nodes = new Map(selectors.map(name => {
    const listeners = new Map(), classes = new Set();
    return [`#asset-preview-${name}`, {
      hidden: false, style: {}, textContent: "", listeners, scrollLeft: 0, scrollTop: 0,
      classList: {
        contains: name => classes.has(name),
        add: name => classes.add(name),
        remove: name => classes.delete(name),
        toggle: (name, force) => { if (force ?? !classes.has(name)) classes.add(name); else classes.delete(name); },
      },
      addEventListener: (event, handler) => listeners.set(event, handler),
      focus() {},
      showModal() { this.open = true; },
    }];
  }));
  const image = nodes.get("#asset-preview-image");
  Object.assign(image, { complete: true, naturalWidth: 200, naturalHeight: 100, clientWidth: 200, clientHeight: 100 });
  globalThis.document = { querySelector: selector => nodes.get(selector) };
  globalThis.window = { innerHeight: 900, addEventListener() {} };
  globalThis.requestAnimationFrame = callback => { callback(); return 1; };
  try {
    const open = createAssetPreview({ assetThumb: id => `/synthetic/${id}` });
    const marker = nodes.get("#asset-preview-face-box");
    const rotate = nodes.get("#asset-preview-rotate-box");
    open("first", "Synthetic photo", box);
    assert.equal(marker.hidden, false);
    assert.equal(rotate.hidden, false);
    assert.equal(marker.style.left, "40px");
    rotate.listeners.get("click")();
    assert.equal(marker.style.left, "160px");
    assert.equal(nodes.get("#asset-preview-rotate-label").textContent, "90°");
    nodes.get("#asset-preview-dialog").listeners.get("keydown")({ key: "r", altKey: false, ctrlKey: false, metaKey: false, preventDefault() {} });
    assert.equal(nodes.get("#asset-preview-rotate-label").textContent, "180°");
    open("second", "Another synthetic photo", box);
    assert.equal(marker.style.left, "40px");
    assert.equal(nodes.get("#asset-preview-rotate-label").textContent, "0°");
  } finally {
    Object.assign(globalThis, previous);
  }
});
