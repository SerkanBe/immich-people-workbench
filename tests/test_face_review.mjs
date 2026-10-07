// Copyright (C) 2026 Serkan Bekdemir
// SPDX-License-Identifier: AGPL-3.0-only
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

const source = readFileSync(new URL("../web/face-review.js", import.meta.url), "utf8");
const { createFaceReview } = await import(`data:text/javascript;base64,${Buffer.from(source).toString("base64")}`);

function node() {
  const listeners = new Map();
  return {
    listeners, classList: { toggle() {} }, style: {}, scrollTop: 0, clientHeight: 600,
    append() {}, replaceChildren() {}, addEventListener: (event, handler) => listeners.set(event, handler),
    setAttribute() {}, querySelectorAll: () => [], focus() {},
  };
}

test("Face review submits directly, then reopens and finishes without changing decisions", async () => {
  const previous = { document: globalThis.document, window: globalThis.window, localStorage: globalThis.localStorage };
  const elements = new Map();
  globalThis.document = {
    getElementById: id => { if (!elements.has(id)) elements.set(id, node()); return elements.get(id); },
    createElement: () => node(), addEventListener() {},
  };
  globalThis.window = { addEventListener() {} };
  globalThis.localStorage = { getItem: () => null };
  const person = { id: "11111111-1111-4111-8111-111111111111", name: "Synthetic person", assetCount: 1 };
  const state = {
    faceReviewCurrentPerson: person, faceReviewPersonId: person.id, faceReviewPeople: [person],
    faceReviewResult: { draftCount: 1, reviewedCount: 1, reviewTotal: 1, untouchedCount: 0 },
    faceReviewSelected: new Set(), faceReviewSelectedData: new Map(), view: "faces",
  };
  const calls = [];
  const api = async (path, options) => {
    calls.push([path, options.body]);
    if (path === "/api/face-review/submit") return {
      submitted: { correct: 1, wrong: 0 }, submittedFaces: [], draftCount: 0,
      reviewedCount: 1, reviewTotal: 1, untouchedCount: 0, personReviewed: true, reviewReopened: false,
    };
    if (path === "/api/face-review/reopen") return {
      draftCount: 0, reviewedCount: 1, reviewTotal: 1, untouchedCount: 0,
      personReviewed: !options.body.reopened, reviewReopened: options.body.reopened,
    };
    throw new Error(`Unexpected request: ${path}`);
  };
  try {
    const review = createFaceReview({
      api, state, ui: { facePersonList: node(), faceReviewGrid: node() },
      assetThumb: () => "", personThumb: () => "", refreshSummary: async () => {}, showToast: () => {},
    });
    review.bindFaceReviewControls();
    const button = document.getElementById("face-review-submit");
    const click = async () => { button.listeners.get("click")(); await new Promise(resolve => setImmediate(resolve)); };

    await click();
    assert.deepEqual(calls[0], ["/api/face-review/submit", { personId: person.id }]);
    assert.equal(button.textContent, "Reopen review");
    await click();
    assert.deepEqual(calls[1], ["/api/face-review/reopen", { personId: person.id, reopened: true }]);
    assert.equal(button.textContent, "Finish review");
    await click();
    assert.deepEqual(calls[2], ["/api/face-review/reopen", { personId: person.id, reopened: false }]);
    assert.equal(button.textContent, "Reopen review");
  } finally {
    Object.assign(globalThis, previous);
  }
});
