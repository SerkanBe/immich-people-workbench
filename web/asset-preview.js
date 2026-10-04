// Copyright (C) 2026 Serkan Bekdemir
// SPDX-License-Identifier: AGPL-3.0-only

export function createAssetPreview({ assetThumb }) {
  const dialog = document.querySelector("#asset-preview-dialog");
  const viewport = document.querySelector("#asset-preview-viewport");
  const stage = document.querySelector("#asset-preview-stage");
  const image = document.querySelector("#asset-preview-image");
  const marker = document.querySelector("#asset-preview-face-box");
  const caption = document.querySelector("#asset-preview-caption");
  const toggleBox = document.querySelector("#asset-preview-toggle-box");
  const toggleLabel = document.querySelector("#asset-preview-toggle-label");
  const zoomOut = document.querySelector("#asset-preview-zoom-out");
  const zoomIn = document.querySelector("#asset-preview-zoom-in");
  const zoomLabel = document.querySelector("#asset-preview-zoom-level");
  const zoomLevels = [1, 1.5, 2, 3, 4];
  let activeFaceBox = null;
  let boxVisible = true;
  let zoomIndex = 0;

  function updateZoomControls() {
    zoomOut.disabled = zoomIndex === 0;
    zoomIn.disabled = zoomIndex === zoomLevels.length - 1;
    zoomLabel.textContent = `${Math.round(zoomLevels[zoomIndex] * 100)}%`;
  }

  function positionFaceBox() {
    marker.hidden = true;
    toggleBox.hidden = true;
    caption.textContent = "Immich preview — the original file is not downloaded.";
    const box = activeFaceBox;
    if (!box || !image.complete || !image.naturalWidth || !image.naturalHeight) return;
    const [x1, y1, x2, y2, sourceWidth, sourceHeight] = [box.x1, box.y1, box.x2, box.y2, box.width, box.height].map(Number);
    if (![x1, y1, x2, y2, sourceWidth, sourceHeight].every(Number.isFinite) ||
        sourceWidth <= 0 || sourceHeight <= 0 || x1 < 0 || y1 < 0 || x2 > sourceWidth || y2 > sourceHeight || x2 <= x1 || y2 <= y1 ||
        Math.abs((sourceWidth / sourceHeight) / (image.naturalWidth / image.naturalHeight) - 1) > 0.08) {
      caption.textContent = "Immich preview — face location unavailable for this image.";
      return;
    }
    const frameWidth = image.clientWidth, frameHeight = image.clientHeight;
    if (!frameWidth || !frameHeight) return;
    const scale = Math.min(frameWidth / image.naturalWidth, frameHeight / image.naturalHeight);
    const shownWidth = image.naturalWidth * scale, shownHeight = image.naturalHeight * scale;
    marker.style.left = `${(frameWidth - shownWidth) / 2 + x1 / sourceWidth * shownWidth}px`;
    marker.style.top = `${(frameHeight - shownHeight) / 2 + y1 / sourceHeight * shownHeight}px`;
    marker.style.width = `${(x2 - x1) / sourceWidth * shownWidth}px`;
    marker.style.height = `${(y2 - y1) / sourceHeight * shownHeight}px`;
    marker.hidden = !boxVisible;
    toggleBox.hidden = false;
    toggleLabel.textContent = boxVisible ? "Hide box" : "Show box";
    caption.textContent = boxVisible
      ? "Highlighted rectangle: the face assigned to this person. Immich preview; original file not downloaded."
      : "Face rectangle hidden. Immich preview; original file not downloaded.";
  }

  function setZoom(nextIndex) {
    const clamped = Math.max(0, Math.min(nextIndex, zoomLevels.length - 1));
    if (clamped === zoomIndex) return;
    const centerX = (viewport.scrollLeft + viewport.clientWidth / 2) / Math.max(viewport.scrollWidth, 1);
    const centerY = (viewport.scrollTop + viewport.clientHeight / 2) / Math.max(viewport.scrollHeight, 1);
    zoomIndex = clamped;
    const zoom = zoomLevels[zoomIndex];
    marker.hidden = true;
    stage.style.width = zoomIndex ? `${zoom * 100}%` : "";
    const fitHeight = Math.max(180, Math.min(window.innerHeight * 0.68, window.innerHeight - 210));
    image.style.maxHeight = zoomIndex ? `${fitHeight * zoom}px` : "";
    updateZoomControls();
    requestAnimationFrame(() => {
      viewport.scrollLeft = centerX * viewport.scrollWidth - viewport.clientWidth / 2;
      viewport.scrollTop = centerY * viewport.scrollHeight - viewport.clientHeight / 2;
      positionFaceBox();
    });
  }

  function openAssetPreview(assetId, fileName = "Photo preview", faceBox = null) {
    activeFaceBox = faceBox;
    boxVisible = true;
    zoomIndex = 0;
    stage.style.width = "";
    image.style.maxHeight = "";
    marker.hidden = true;
    toggleBox.hidden = true;
    caption.textContent = "Immich preview — the original file is not downloaded.";
    document.querySelector("#asset-preview-title").textContent = fileName || "Photo preview";
    image.src = assetThumb(assetId);
    image.alt = fileName || "Photo preview";
    dialog.showModal();
    viewport.scrollLeft = 0;
    viewport.scrollTop = 0;
    updateZoomControls();
    requestAnimationFrame(positionFaceBox);
  }

  toggleBox.addEventListener("click", () => { boxVisible = !boxVisible; positionFaceBox(); });
  zoomOut.addEventListener("click", () => setZoom(zoomIndex - 1));
  zoomIn.addEventListener("click", () => setZoom(zoomIndex + 1));
  dialog.addEventListener("keydown", event => {
    if (event.altKey || event.ctrlKey || event.metaKey) return;
    const key = event.key.toLowerCase();
    if (key === "h" && !toggleBox.hidden) { event.preventDefault(); boxVisible = !boxVisible; positionFaceBox(); }
    else if (key === "+" || key === "=") { event.preventDefault(); setZoom(zoomIndex + 1); }
    else if (key === "-" || key === "_") { event.preventDefault(); setZoom(zoomIndex - 1); }
    else if (key === "0") { event.preventDefault(); setZoom(0); }
  });
  image.addEventListener("load", positionFaceBox);
  window.addEventListener("resize", () => {
    if (zoomIndex) {
      const fitHeight = Math.max(180, Math.min(window.innerHeight * 0.68, window.innerHeight - 210));
      image.style.maxHeight = `${fitHeight * zoomLevels[zoomIndex]}px`;
    }
    positionFaceBox();
  });
  dialog.addEventListener("close", () => {
    activeFaceBox = null;
    marker.hidden = true;
    image.removeAttribute("src");
  });
  updateZoomControls();
  return openAssetPreview;
}
