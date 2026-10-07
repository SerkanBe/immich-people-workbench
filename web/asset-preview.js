// Copyright (C) 2026 Serkan Bekdemir
// SPDX-License-Identifier: AGPL-3.0-only

export function projectFaceBox(box, imageWidth, imageHeight, frameWidth, frameHeight, quarterTurns = 0) {
  if (!box) return null;
  if ([box.x1, box.y1, box.x2, box.y2, box.width, box.height].some(value => value == null || value === "")) return null;
  const [x1, y1, x2, y2, sourceWidth, sourceHeight] = [box.x1, box.y1, box.x2, box.y2, box.width, box.height].map(Number);
  if (![x1, y1, x2, y2, sourceWidth, sourceHeight, imageWidth, imageHeight, frameWidth, frameHeight].every(Number.isFinite) ||
      sourceWidth <= 0 || sourceHeight <= 0 || imageWidth <= 0 || imageHeight <= 0 || frameWidth <= 0 || frameHeight <= 0 ||
      x2 <= x1 || y2 <= y1) return null;
  const [left, top, right, bottom] = [x1 / sourceWidth, y1 / sourceHeight, x2 / sourceWidth, y2 / sourceHeight];
  const turns = ((quarterTurns % 4) + 4) % 4;
  const [u1, v1, u2, v2] = turns === 1 ? [1 - bottom, left, 1 - top, right]
    : turns === 2 ? [1 - right, 1 - bottom, 1 - left, 1 - top]
      : turns === 3 ? [top, 1 - right, bottom, 1 - left] : [left, top, right, bottom];
  const scale = Math.min(frameWidth / imageWidth, frameHeight / imageHeight);
  const shownWidth = imageWidth * scale, shownHeight = imageHeight * scale;
  return {
    left: (frameWidth - shownWidth) / 2 + u1 * shownWidth,
    top: (frameHeight - shownHeight) / 2 + v1 * shownHeight,
    width: (u2 - u1) * shownWidth,
    height: (v2 - v1) * shownHeight,
  };
}

export function createAssetPreview({ assetThumb }) {
  const dialog = document.querySelector("#asset-preview-dialog");
  const viewport = document.querySelector("#asset-preview-viewport");
  const stage = document.querySelector("#asset-preview-stage");
  const image = document.querySelector("#asset-preview-image");
  const marker = document.querySelector("#asset-preview-face-box");
  const caption = document.querySelector("#asset-preview-caption");
  const toggleBox = document.querySelector("#asset-preview-toggle-box");
  const toggleLabel = document.querySelector("#asset-preview-toggle-label");
  const rotateBox = document.querySelector("#asset-preview-rotate-box");
  const rotateLabel = document.querySelector("#asset-preview-rotate-label");
  const zoomLabel = document.querySelector("#asset-preview-zoom-level");
  const reviewControls = document.querySelector("#asset-preview-review");
  const zoomLevels = [1, 1.5, 2, 3, 4];
  let activeFaceBox = null;
  let boxVisible = true;
  let boxRotation = 0;
  let zoomIndex = 0;
  let wheelRemainder = 0;
  let drag = null;
  let suppressClick = false;
  let suppressClickTimer = 0;
  let ignoredTouchClickId = null;
  let zoomFrame = 0;

  function updateZoomState() {
    zoomLabel.textContent = `${Math.round(zoomLevels[zoomIndex] * 100)}%`;
    viewport.classList.toggle("is-zoomed", zoomIndex > 0);
    if (!zoomIndex) viewport.classList.remove("is-dragging");
  }

  function fittedHeight() {
    const review = dialog.classList.contains("has-review");
    return Math.max(100, Math.min(window.innerHeight * (review ? 0.55 : 0.68), window.innerHeight - (review ? 400 : 270)));
  }

  function positionFaceBox() {
    marker.hidden = true;
    toggleBox.hidden = true;
    rotateBox.hidden = true;
    caption.textContent = "Immich preview — the original file is not downloaded.";
    if (!activeFaceBox || !image.complete || !image.naturalWidth || !image.naturalHeight) return;
    const frameWidth = image.clientWidth, frameHeight = image.clientHeight;
    const position = projectFaceBox(activeFaceBox, image.naturalWidth, image.naturalHeight, frameWidth, frameHeight, boxRotation);
    if (!position) {
      caption.textContent = "Immich preview — face coordinates unavailable for this image.";
      return;
    }
    marker.style.left = `${position.left}px`;
    marker.style.top = `${position.top}px`;
    marker.style.width = `${position.width}px`;
    marker.style.height = `${position.height}px`;
    marker.hidden = !boxVisible;
    toggleBox.hidden = false;
    rotateBox.hidden = false;
    toggleLabel.textContent = boxVisible ? "Hide box" : "Show box";
    rotateLabel.textContent = `${boxRotation * 90}°`;
    caption.textContent = boxVisible
      ? "Highlighted rectangle: Immich face coordinates on this preview. Rotate the box if the photo orientation differs. Original file not downloaded."
      : "Face rectangle hidden. Immich preview; original file not downloaded.";
  }

  function setZoom(nextIndex, pointer = null) {
    const clamped = Math.max(0, Math.min(nextIndex, zoomLevels.length - 1));
    if (clamped === zoomIndex) return;
    const viewportRect = viewport.getBoundingClientRect();
    const stageRect = stage.getBoundingClientRect();
    const x = pointer?.x ?? viewportRect.left + viewportRect.width / 2;
    const y = pointer?.y ?? viewportRect.top + viewportRect.height / 2;
    const offsetX = Math.max(0, Math.min(x - viewportRect.left, viewportRect.width));
    const offsetY = Math.max(0, Math.min(y - viewportRect.top, viewportRect.height));
    const fractionX = Math.max(0, Math.min((x - stageRect.left) / Math.max(stageRect.width, 1), 1));
    const fractionY = Math.max(0, Math.min((y - stageRect.top) / Math.max(stageRect.height, 1), 1));
    zoomIndex = clamped;
    const zoom = zoomLevels[zoomIndex];
    marker.hidden = true;
    stage.style.width = zoomIndex ? `${zoom * 100}%` : "";
    image.style.maxHeight = zoomIndex ? `${fittedHeight() * zoom}px` : "";
    updateZoomState();
    if (zoomFrame) cancelAnimationFrame(zoomFrame);
    zoomFrame = requestAnimationFrame(() => {
      zoomFrame = 0;
      viewport.scrollLeft = fractionX * stage.clientWidth - offsetX;
      viewport.scrollTop = fractionY * stage.clientHeight - offsetY;
      positionFaceBox();
    });
  }

  function openAssetPreview(assetId, fileName = "Photo preview", faceBox = null, { review = false } = {}) {
    const continueReview = review && dialog.open && dialog.classList.contains("has-review");
    activeFaceBox = faceBox;
    if (!continueReview) boxVisible = true;
    boxRotation = 0;
    zoomIndex = 0;
    wheelRemainder = 0;
    drag = null;
    suppressClick = false;
    clearTimeout(suppressClickTimer);
    suppressClickTimer = 0;
    ignoredTouchClickId = null;
    if (zoomFrame) cancelAnimationFrame(zoomFrame);
    zoomFrame = 0;
    stage.style.width = "";
    image.style.maxHeight = "";
    marker.hidden = true;
    toggleBox.hidden = true;
    rotateBox.hidden = true;
    reviewControls.hidden = !review;
    dialog.classList.toggle("has-review", review);
    viewport.setAttribute("aria-label", review
      ? "Photo preview. Press Space to close; Enter to zoom in or fit; use plus and minus keys to adjust zoom."
      : "Photo preview. Press Enter or Space to zoom in or fit; use plus and minus keys to adjust zoom.");
    caption.textContent = "Immich preview — the original file is not downloaded.";
    document.querySelector("#asset-preview-title").textContent = fileName || "Photo preview";
    image.src = assetThumb(assetId);
    image.alt = fileName || "Photo preview";
    if (!dialog.open) dialog.showModal();
    viewport.scrollLeft = 0;
    viewport.scrollTop = 0;
    updateZoomState();
    viewport.focus({ preventScroll: true });
    requestAnimationFrame(positionFaceBox);
  }

  toggleBox.addEventListener("click", () => { boxVisible = !boxVisible; positionFaceBox(); });
  rotateBox.addEventListener("click", () => { boxRotation = (boxRotation + 1) % 4; positionFaceBox(); });
  viewport.addEventListener("click", event => {
    if (event.pointerId === ignoredTouchClickId) { ignoredTouchClickId = null; return; }
    if (suppressClick) { suppressClick = false; return; }
    setZoom(zoomIndex ? 0 : 2, event.detail ? { x: event.clientX, y: event.clientY } : null);
  });
  viewport.addEventListener("wheel", event => {
    if (!event.ctrlKey) return;
    event.preventDefault();
    const delta = event.deltaY * (event.deltaMode === 1 ? 16 : event.deltaMode === 2 ? viewport.clientHeight : 1);
    if (!delta) return;
    if (Math.sign(delta) !== Math.sign(wheelRemainder)) wheelRemainder = 0;
    wheelRemainder += delta;
    if (Math.abs(wheelRemainder) < 80) return;
    wheelRemainder = 0;
    setZoom(zoomIndex + (delta < 0 ? 1 : -1), { x: event.clientX, y: event.clientY });
  }, { passive: false });
  viewport.addEventListener("pointerdown", event => {
    if (!zoomIndex || event.button !== 0) return;
    drag = { id: event.pointerId, x: event.clientX, y: event.clientY, left: viewport.scrollLeft, top: viewport.scrollTop, moved: false };
  });
  viewport.addEventListener("pointermove", event => {
    if (!drag || drag.id !== event.pointerId) return;
    const dx = event.clientX - drag.x, dy = event.clientY - drag.y;
    if (!drag.moved && Math.hypot(dx, dy) > 4) {
      drag.moved = true;
      viewport.setPointerCapture(event.pointerId);
    }
    if (!drag.moved) return;
    viewport.classList.add("is-dragging");
    viewport.scrollLeft = drag.left - dx;
    viewport.scrollTop = drag.top - dy;
  });
  function finishDrag(event, canceled = false) {
    if (!drag || drag.id !== event.pointerId) return;
    const moved = drag.moved;
    suppressClick = !canceled && event.pointerType === "mouse" && moved;
    if (suppressClick) {
      clearTimeout(suppressClickTimer);
      suppressClickTimer = setTimeout(() => { suppressClick = false; suppressClickTimer = 0; }, 0);
    }
    if (event.pointerType !== "mouse") ignoredTouchClickId = event.pointerId;
    drag = null;
    viewport.classList.remove("is-dragging");
    if (moved && viewport.hasPointerCapture(event.pointerId)) viewport.releasePointerCapture(event.pointerId);
    if (!canceled && !moved && event.pointerType !== "mouse") setZoom(0, { x: event.clientX, y: event.clientY });
  }
  viewport.addEventListener("pointerup", finishDrag);
  viewport.addEventListener("pointercancel", event => finishDrag(event, true));
  dialog.addEventListener("keydown", event => {
    if (event.altKey || event.ctrlKey || event.metaKey) return;
    const key = event.key.toLowerCase();
    if (key === " " && dialog.classList.contains("has-review")) return;
    if (key === "h" && !toggleBox.hidden) { event.preventDefault(); boxVisible = !boxVisible; positionFaceBox(); }
    else if (key === "r" && !rotateBox.hidden) { event.preventDefault(); boxRotation = (boxRotation + 1) % 4; positionFaceBox(); }
    else if (event.target === viewport && (key === "enter" || key === " ")) { event.preventDefault(); setZoom(zoomIndex ? 0 : 2); }
    else if (key === "+" || key === "=") { event.preventDefault(); setZoom(zoomIndex + 1); }
    else if (key === "-" || key === "_") { event.preventDefault(); setZoom(zoomIndex - 1); }
    else if (key === "0") { event.preventDefault(); setZoom(0); }
  });
  image.addEventListener("load", positionFaceBox);
  window.addEventListener("resize", () => {
    if (zoomIndex) image.style.maxHeight = `${fittedHeight() * zoomLevels[zoomIndex]}px`;
    positionFaceBox();
  });
  dialog.addEventListener("close", () => {
    if (zoomFrame) cancelAnimationFrame(zoomFrame);
    zoomFrame = 0;
    clearTimeout(suppressClickTimer);
    suppressClickTimer = 0;
    suppressClick = false;
    ignoredTouchClickId = null;
    drag = null;
    viewport.classList.remove("is-dragging");
    activeFaceBox = null;
    reviewControls.hidden = true;
    dialog.classList.remove("has-review");
    marker.hidden = true;
    image.removeAttribute("src");
  });
  updateZoomState();
  return openAssetPreview;
}
