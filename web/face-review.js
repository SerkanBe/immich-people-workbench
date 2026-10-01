// Copyright (C) 2026 Serkan Bekdemir
// SPDX-License-Identifier: AGPL-3.0-only

export function createFaceReview({ api, assetThumb, drawFace, openAssetPreview, personThumb, refreshSummary, renderPagination, showToast, state, ui }) {
  const pageSize = 60;
  const gap = 12;
  let faces = [];
  let hidden = new Set();
  let mounted = new Map();
  let surface = null;
  let nextPage = 1;
  let hasMore = false;
  let loadError = false;
  let loading = false;
  let busy = false;
  let generation = 0;
  let anchorId = null;
  let areaMode = false;
  let areaDrag = null;
  let suppressClick = false;
  let previewBubble = null;
  let layout = { columns: 1, width: 180, height: 270, stride: 282 };

  const element = id => document.getElementById(id);
  const currentFaceReviewPerson = () => state.faceReviewCurrentPerson;
  const visibleFaces = () => faces.filter(face => !hidden.has(face.faceId));

  function showPhotoPeek(face, button) {
    if (!previewBubble) {
      previewBubble = document.createElement("div"); previewBubble.className = "face-review-peek";
      previewBubble.append(document.createElement("img")); document.body.append(previewBubble);
    }
    const image = previewBubble.querySelector("img"); image.src = assetThumb(face.assetId);
    const box = button.getBoundingClientRect();
    previewBubble.style.left = `${Math.max(8, Math.min(window.innerWidth - 228, box.right - 220))}px`;
    previewBubble.style.top = `${box.top > 210 ? box.top - 194 : box.bottom + 6}px`;
    previewBubble.classList.add("shown");
  }
  function hidePhotoPeek() { previewBubble?.classList.remove("shown"); }

  function renderFacePersonList() {
    const scrollTop = ui.facePersonList.scrollTop;
    ui.facePersonList.replaceChildren();
    for (const person of state.faceReviewPeople) {
      const button = document.createElement("button"); button.type = "button"; button.className = "face-person-button";
      button.classList.toggle("active", person.id === state.faceReviewPersonId);
      const image = document.createElement("img"); image.src = personThumb(person.id); image.alt = ""; image.loading = "lazy";
      const text = document.createElement("span");
      const name = document.createElement("strong"); name.textContent = person.name || "Unnamed person";
      const count = document.createElement("small");
      const total = person.reviewTotal ?? person.assetCount ?? 0;
      count.textContent = person.reviewed ? `✓ Reviewed · ${person.assetCount ?? "?"} photos` : `${person.assetCount ?? "?"} photos · ${person.reviewedCount || 0}/${total} reviewed`;
      button.classList.toggle("reviewed", Boolean(person.reviewed));
      text.append(name, count); button.append(image, text);
      button.addEventListener("click", () => selectFaceReviewPerson(person.id));
      ui.facePersonList.append(button);
    }
    if (!state.faceReviewPeople.length) {
      const empty = document.createElement("p"); empty.className = "muted"; empty.textContent = "No matching people."; ui.facePersonList.append(empty);
    }
    ui.facePersonList.scrollTop = scrollTop;
  }

  async function loadFaceReviewPeople() {
    const query = element("face-person-search").value.trim();
    const data = await api(`/api/people?kind=review&sort=${encodeURIComponent(state.sort)}&page=${state.pages.facePeople}&size=${state.pageSize}&q=${encodeURIComponent(query)}`);
    state.faceReviewPeople = data.people; state.pages.facePeople = data.page;
    renderFacePersonList();
    renderPagination("face-person-pagination", data, page => {
      state.pages.facePeople = page; return loadFaceReviewPeople();
    });
  }

  function updateFaceReviewProgress(result = state.faceReviewResult) {
    const person = currentFaceReviewPerson();
    if (!person || !result) return;
    person.reviewedCount = result.reviewedCount || 0;
    person.reviewTotal = result.totalAssets ?? result.reviewTotal ?? person.assetCount ?? 0;
    person.reviewed = Boolean(result.personReviewed);
    const pagePerson = state.faceReviewPeople.find(item => item.id === person.id);
    if (pagePerson && pagePerson !== person) Object.assign(pagePerson, { reviewedCount: person.reviewedCount, reviewTotal: person.reviewTotal, reviewed: person.reviewed });
    const reviewed = person.reviewed ? `✓ Reviewed · ${person.reviewedCount}/${person.reviewTotal}` : `${person.reviewedCount}/${person.reviewTotal} reviewed`;
    element("face-review-meta").textContent = `${faces.length} face crops loaded from ${result.totalAssets ?? "?"} photos · ${reviewed}`;
    element("face-review-content").classList.toggle("person-reviewed", person.reviewed);
    renderFacePersonList();
  }

  function updateControls() {
    const selected = state.faceReviewSelected.size;
    const hiddenSelected = [...state.faceReviewSelected].filter(id => hidden.has(id)).length;
    const visibleSelected = selected - hiddenSelected;
    const disabled = busy || loading;
    element("face-review-mark").disabled = !selected || disabled;
    element("face-review-hide").disabled = !visibleSelected || disabled;
    element("face-review-show").disabled = !hidden.size || disabled;
    element("face-review-show").textContent = hidden.size ? `Show hidden (${hidden.size})` : "Show hidden";
    element("face-review-clear").disabled = !selected || disabled;
    const queue = element("face-review-queue");
    queue.disabled = !selected || disabled;
    queue.textContent = selected ? `Queue ${selected} for Unnamed${hiddenSelected ? ` (${hiddenSelected} hidden)` : ""}` : "Queue selected for Unnamed";
    element("face-review-area").setAttribute("aria-pressed", String(areaMode));
    ui.faceReviewGrid.classList.toggle("area-mode", areaMode);
    const loadButton = element("face-review-load-more");
    loadButton.hidden = !hasMore && !loadError;
    loadButton.disabled = loading || busy;
    loadButton.textContent = loadError ? "Retry loading" : "Load more faces";
    element("face-review-load-status").textContent = loading ? "Loading more faces…" : loadError ? "Loading failed" : hasMore ? `${faces.length} face crops loaded · scroll or use Load more` : `${faces.length} face crops loaded`;
  }

  function updateMountedStates() {
    for (const [id, card] of mounted) {
      const face = faces.find(item => item.faceId === id);
      if (!face) continue;
      const selected = state.faceReviewSelected.has(id);
      card.classList.toggle("selected", selected);
      card.classList.toggle("reviewed", Boolean(face.reviewed));
      card.classList.toggle("queued", Boolean(face.queued));
      card.setAttribute("aria-selected", String(selected));
      const status = card.querySelector(".face-review-status");
      status.textContent = face.queued ? "Queued for Unnamed" : selected ? "Selected" : "Select face";
      const review = card.querySelector(".face-review-reviewed");
      review.textContent = face.reviewed ? "✓ Reviewed" : "○ Mark Reviewed";
      review.setAttribute("aria-pressed", String(Boolean(face.reviewed)));
      review.disabled = Boolean(face.queued) || busy;
    }
    updateControls();
  }

  function calculateLayout() {
    const width = Math.max(180, ui.faceReviewGrid.clientWidth - 16);
    const columns = Math.max(1, Math.floor((width + gap) / (180 + gap)));
    const cardWidth = (width - (columns - 1) * gap) / columns;
    const cardHeight = Math.ceil(cardWidth + 86);
    layout = { columns, width: cardWidth, height: cardHeight, stride: cardHeight + gap };
    state.faceReviewLayout = layout;
  }

  function createFaceReviewCard(face) {
    const card = document.createElement("article");
    card.className = "face-review-card"; card.dataset.faceId = face.faceId; card.tabIndex = face.queued ? -1 : 0;
    card.setAttribute("role", "option");
    card.setAttribute("aria-label", `Face from ${face.fileName}${face.reviewed ? ", Reviewed" : ""}${face.queued ? ", queued for Unnamed" : ""}`);
    const crop = document.createElement("canvas"); crop.width = 280; crop.height = 280;
    drawFace(crop, face).catch(() => {
      if (crop.isConnected) crop.replaceWith(Object.assign(document.createElement("img"), { src: assetThumb(face.assetId), alt: "" }));
    });
    const meta = document.createElement("div"); meta.className = "face-review-card-meta";
    const filename = document.createElement("code"); filename.textContent = face.fileName; filename.title = face.fileName;
    const status = document.createElement("span"); status.className = "face-review-status";
    const photo = document.createElement("button"); photo.type = "button"; photo.className = "quiet-button face-review-photo"; photo.textContent = "Photo";
    photo.addEventListener("click", event => { event.stopPropagation(); openAssetPreview(face.assetId, face.fileName); });
    photo.addEventListener("mouseenter", () => showPhotoPeek(face, photo));
    photo.addEventListener("mouseleave", hidePhotoPeek);
    photo.addEventListener("focus", () => showPhotoPeek(face, photo));
    photo.addEventListener("blur", hidePhotoPeek);
    const reviewed = document.createElement("button"); reviewed.type = "button"; reviewed.className = "face-review-reviewed";
    reviewed.setAttribute("aria-label", `Toggle Reviewed for face from ${face.fileName}`);
    reviewed.addEventListener("click", event => { event.stopPropagation(); toggleReviewed(face); });
    meta.append(filename, status, photo, reviewed); card.append(crop, meta);
    card.addEventListener("click", event => {
      if (suppressClick) { suppressClick = false; return; }
      if (!event.target.closest("button")) toggleSelection(face, event.shiftKey);
    });
    card.addEventListener("keydown", event => {
      if (event.target !== card || face.queued) return;
      if (event.key === " " || event.key === "Enter") { event.preventDefault(); toggleSelection(face, event.shiftKey); }
      else if (event.key.toLowerCase() === "r") { event.preventDefault(); event.stopPropagation(); toggleReviewed(face); }
      else if (event.key.toLowerCase() === "p") { event.preventDefault(); event.stopPropagation(); openAssetPreview(face.assetId, face.fileName); }
    });
    return card;
  }

  function renderVisible() {
    if (!surface) return;
    calculateLayout();
    const available = visibleFaces();
    const rows = Math.ceil(available.length / layout.columns);
    surface.style.height = `${Math.max(1, rows * layout.stride)}px`;
    const firstRow = Math.max(0, Math.floor(ui.faceReviewGrid.scrollTop / layout.stride) - 2);
    const lastRow = Math.min(rows, Math.ceil((ui.faceReviewGrid.scrollTop + ui.faceReviewGrid.clientHeight) / layout.stride) + 2);
    const needed = new Set();
    for (let index = firstRow * layout.columns; index < Math.min(available.length, lastRow * layout.columns); index += 1) {
      const face = available[index]; needed.add(face.faceId);
      let card = mounted.get(face.faceId);
      if (!card) {
        card = createFaceReviewCard(face); mounted.set(face.faceId, card);
        const following = [...surface.querySelectorAll(".face-review-card")].find(node => available.findIndex(item => item.faceId === node.dataset.faceId) > index);
        surface.insertBefore(card, following || null);
      }
      const column = index % layout.columns, row = Math.floor(index / layout.columns);
      card.style.width = `${layout.width}px`; card.style.height = `${layout.height}px`;
      card.style.left = `${column * (layout.width + gap)}px`; card.style.top = `${row * layout.stride}px`;
    }
    for (const [id, card] of mounted) {
      if (!needed.has(id)) { hidePhotoPeek(); card.remove(); mounted.delete(id); }
    }
    if (!available.length && !hasMore && !loadError && !loading) {
      const message = document.createElement("p"); message.className = "face-review-empty muted";
      message.textContent = hidden.size ? "All loaded faces are hidden. Use Show hidden to see them." : "Immich returned no assigned faces for this person.";
      surface.append(message);
    } else surface.querySelectorAll(".face-review-empty").forEach(node => node.remove());
    updateMountedStates();
  }

  function maybeLoadMore() {
    if (!surface || loading || busy || !hasMore || state.view !== "faces") return;
    if (ui.faceReviewGrid.scrollTop + ui.faceReviewGrid.clientHeight >= surface.offsetHeight - layout.stride * 2) loadNextPage();
  }

  async function loadNextPage() {
    const person = currentFaceReviewPerson();
    if (!person || loading || !hasMore) return;
    const requestedGeneration = generation;
    loading = true; updateControls();
    try {
      const result = await api(`/api/person-faces/${encodeURIComponent(person.id)}?page=${nextPage}&size=${pageSize}`);
      if (requestedGeneration !== generation) return;
      faces.push(...result.faces);
      nextPage = result.page + 1; hasMore = result.hasMore;
      state.faceReviewPage = result.page; state.faceReviewResult = result;
      updateFaceReviewProgress(result);
      renderVisible();
      loadError = false;
    } catch (error) {
      if (requestedGeneration !== generation) return;
      hasMore = false; loadError = true;
      showToast(`Could not load faces: ${error.message}`);
    } finally {
      if (requestedGeneration === generation) {
        loading = false; updateControls(); renderVisible();
        if (hasMore) requestAnimationFrame(maybeLoadMore);
      }
    }
  }

  function toggleSelection(face, range = false) {
    if (face.queued || busy) return;
    if (range && anchorId) {
      const available = visibleFaces();
      const start = available.findIndex(item => item.faceId === anchorId);
      const end = available.findIndex(item => item.faceId === face.faceId);
      if (start >= 0 && end >= 0) {
        for (let index = Math.min(start, end); index <= Math.max(start, end); index += 1) {
          const item = available[index];
          if (!item.queued) { state.faceReviewSelected.add(item.faceId); state.faceReviewSelectedData.set(item.faceId, item); }
        }
      }
    } else if (state.faceReviewSelected.has(face.faceId)) {
      state.faceReviewSelected.delete(face.faceId); state.faceReviewSelectedData.delete(face.faceId);
    } else {
      state.faceReviewSelected.add(face.faceId); state.faceReviewSelectedData.set(face.faceId, face);
    }
    anchorId = face.faceId;
    updateMountedStates();
  }

  function applyProgress(result) {
    state.faceReviewResult = { ...state.faceReviewResult, ...result };
    updateFaceReviewProgress(state.faceReviewResult);
    updateMountedStates();
  }

  async function toggleReviewed(face) {
    if (face.queued || busy) return;
    const personId = currentFaceReviewPerson().id, requestGeneration = generation;
    busy = true; updateControls();
    const next = !face.reviewed;
    try {
      const result = await api("/api/face-review/reviewed", { method: "POST", body: { personId, faceId: face.faceId, reviewed: next } });
      if (requestGeneration !== generation) return;
      face.reviewed = next; applyProgress(result);
      if (result.personReviewed) showToast(`${currentFaceReviewPerson().name || "This person"} is fully reviewed.`);
    } catch (error) { if (requestGeneration === generation) showToast(`Could not save review state: ${error.message}`); }
    finally { if (requestGeneration === generation) { busy = false; updateMountedStates(); } }
  }

  async function markSelectedReviewed() {
    if (!state.faceReviewSelected.size || busy) return;
    const selected = [...state.faceReviewSelectedData.values()].filter(face => !face.queued && !face.reviewed);
    if (!selected.length) { showToast("Selected faces are already Reviewed."); return; }
    const personId = currentFaceReviewPerson().id, requestGeneration = generation;
    busy = true; updateControls();
    let saved = 0;
    try {
      for (let offset = 0; offset < selected.length; offset += 500) {
        if (requestGeneration !== generation) break;
        const chunk = selected.slice(offset, offset + 500);
        const result = await api("/api/face-review/reviewed/bulk", { method: "POST", body: { personId, faceIds: chunk.map(face => face.faceId), reviewed: true } });
        if (requestGeneration !== generation) break;
        for (const face of chunk) face.reviewed = true;
        saved += chunk.length; applyProgress(result);
      }
      if (requestGeneration === generation) showToast(`Marked ${saved} ${saved === 1 ? "face" : "faces"} Reviewed.`);
    } catch (error) { if (requestGeneration === generation) showToast(`Marked ${saved}; could not finish: ${error.message}`); }
    finally { if (requestGeneration === generation) { busy = false; updateMountedStates(); } }
  }

  async function queueSelectedFacesForUnnamed() {
    const person = currentFaceReviewPerson();
    if (!person || !state.faceReviewSelected.size || busy || loading) return;
    const requestGeneration = generation;
    const selected = [...state.faceReviewSelectedData.values()];
    busy = true; updateControls();
    let queued = 0, newlyHandled = 0;
    try {
      for (const face of selected) {
        if (requestGeneration !== generation) break;
        await api("/api/face-detach/queue", { method: "POST", body: { personId: person.id, faceId: face.faceId, assetId: face.assetId, fileName: face.fileName } });
        queued += 1;
        if (requestGeneration !== generation) break;
        if (!face.reviewed) newlyHandled += 1;
        face.queued = true; face.reviewed = true;
        state.faceReviewSelected.delete(face.faceId); state.faceReviewSelectedData.delete(face.faceId);
      }
      if (requestGeneration === generation) showToast(`Queued ${queued} ${queued === 1 ? "face" : "faces"} for Unnamed in Pending.`);
    } catch (error) { if (requestGeneration === generation) showToast(`Queued ${queued}; stopped because: ${error.message}`); }
    finally {
      if (requestGeneration === generation) busy = false;
      if (queued) {
        if (requestGeneration === generation) {
          const result = state.faceReviewResult;
          const reviewedCount = Math.min(result.totalAssets || 0, (result.reviewedCount || 0) + newlyHandled);
          applyProgress({ reviewedCount, personReviewed: result.totalAssets > 0 && reviewedCount >= result.totalAssets });
        }
        await refreshSummary().catch(error => showToast(error.message));
      }
      if (requestGeneration === generation) updateMountedStates();
    }
  }

  function hideSelected() {
    for (const id of state.faceReviewSelected) hidden.add(id);
    renderVisible(); maybeLoadMore();
  }
  function showHidden() { hidden.clear(); renderVisible(); }
  function clearSelection() { state.faceReviewSelected.clear(); state.faceReviewSelectedData.clear(); anchorId = null; updateMountedStates(); }
  function setAreaMode(next) { areaMode = next; updateControls(); }
  function toggleShortcuts() {
    const panel = element("face-review-shortcuts"); panel.hidden = !panel.hidden;
    element("face-review-help").setAttribute("aria-expanded", String(!panel.hidden));
  }

  function startArea(event) {
    if (event.button !== 0 || !(event.shiftKey || areaMode) || event.target.closest("button") || busy) return;
    const rect = ui.faceReviewGrid.getBoundingClientRect();
    const point = { x: event.clientX - rect.left, y: event.clientY - rect.top + ui.faceReviewGrid.scrollTop };
    areaDrag = { start: point, end: point, moved: false, faceId: event.target.closest(".face-review-card")?.dataset.faceId || null };
    const box = document.createElement("div"); box.className = "face-review-selection-box"; areaDrag.box = box; surface.append(box);
    window.addEventListener("pointermove", moveArea);
    window.addEventListener("pointerup", endArea, { once: true });
  }
  function moveArea(event) {
    if (!areaDrag) return;
    const rect = ui.faceReviewGrid.getBoundingClientRect();
    const x = Math.max(0, Math.min(rect.width, event.clientX - rect.left));
    const y = Math.max(0, Math.min(rect.height, event.clientY - rect.top)) + ui.faceReviewGrid.scrollTop;
    areaDrag.end = { x, y };
    areaDrag.moved ||= Math.hypot(x - areaDrag.start.x, y - areaDrag.start.y) > 5;
    if (!areaDrag.moved) return;
    const left = Math.min(areaDrag.start.x, x), top = Math.min(areaDrag.start.y, y);
    Object.assign(areaDrag.box.style, { left: `${left}px`, top: `${top}px`, width: `${Math.abs(x - areaDrag.start.x)}px`, height: `${Math.abs(y - areaDrag.start.y)}px` });
  }
  function endArea(event) {
    window.removeEventListener("pointermove", moveArea);
    if (!areaDrag) return;
    const drag = areaDrag; areaDrag = null; drag.box.remove();
    suppressClick = true; setTimeout(() => { suppressClick = false; }, 0);
    if (!drag.moved) {
      const face = faces.find(item => item.faceId === drag.faceId);
      if (face) toggleSelection(face, event.shiftKey);
      return;
    }
    const left = Math.min(drag.start.x, drag.end.x), right = Math.max(drag.start.x, drag.end.x);
    const top = Math.min(drag.start.y, drag.end.y), bottom = Math.max(drag.start.y, drag.end.y);
    const available = visibleFaces();
    for (let index = 0; index < available.length; index += 1) {
      const face = available[index]; if (face.queued) continue;
      const x = index % layout.columns * (layout.width + gap);
      const y = Math.floor(index / layout.columns) * layout.stride;
      if (x < right && x + layout.width > left && y < bottom && y + layout.height > top) {
        state.faceReviewSelected.add(face.faceId); state.faceReviewSelectedData.set(face.faceId, face);
      }
    }
    updateMountedStates();
  }

  async function selectFaceReviewPerson(personId) {
    const person = state.faceReviewPeople.find(item => item.id === personId) || (state.faceReviewPersonId === personId ? state.faceReviewCurrentPerson : null);
    if (!person) return;
    generation += 1;
    state.faceReviewPersonId = personId; state.faceReviewCurrentPerson = person;
    state.faceReviewSelected.clear(); state.faceReviewSelectedData.clear();
    faces = []; hidden = new Set(); mounted = new Map(); nextPage = 1; hasMore = true; loadError = false; loading = false; busy = false; anchorId = null;
    areaMode = false;
    hidePhotoPeek();
    if (areaDrag) { areaDrag.box.remove(); areaDrag = null; window.removeEventListener("pointermove", moveArea); window.removeEventListener("pointerup", endArea); }
    renderFacePersonList();
    element("face-review-placeholder").hidden = true; element("face-review-content").hidden = false;
    element("face-review-name").textContent = person.name || "Unnamed person";
    element("face-review-meta").textContent = "Loading assigned faces…";
    ui.faceReviewGrid.replaceChildren(); ui.faceReviewGrid.scrollTop = 0;
    surface = document.createElement("div"); surface.className = "face-review-virtual"; ui.faceReviewGrid.append(surface);
    renderVisible(); await loadNextPage();
  }

  async function renderFaceReview() {
    await loadFaceReviewPeople();
    if (state.faceReviewPersonId && surface && currentFaceReviewPerson()?.id === state.faceReviewPersonId) {
      renderVisible(); maybeLoadMore();
    } else if (state.faceReviewPersonId) await selectFaceReviewPerson(state.faceReviewPersonId);
    else { element("face-review-placeholder").hidden = false; element("face-review-content").hidden = true; }
  }

  function bindFaceReviewControls() {
    let searchTimer = null;
    element("face-person-search").addEventListener("input", () => {
      clearTimeout(searchTimer); state.pages.facePeople = 1;
      searchTimer = setTimeout(() => loadFaceReviewPeople().catch(error => showToast(error.message)), 180);
    });
    element("face-review-queue").addEventListener("click", queueSelectedFacesForUnnamed);
    element("face-review-mark").addEventListener("click", markSelectedReviewed);
    element("face-review-hide").addEventListener("click", hideSelected);
    element("face-review-show").addEventListener("click", showHidden);
    element("face-review-clear").addEventListener("click", clearSelection);
    element("face-review-area").addEventListener("click", () => setAreaMode(!areaMode));
    element("face-review-help").addEventListener("click", toggleShortcuts);
    element("face-review-load-more").addEventListener("click", async event => {
      const keyboard = event.detail === 0, firstNewIndex = faces.length;
      hasMore = true; loadError = false;
      await loadNextPage();
      if (keyboard && faces.length > firstNewIndex) {
        const face = faces[firstNewIndex];
        const index = visibleFaces().findIndex(item => item.faceId === face.faceId);
        if (index >= 0) {
          ui.faceReviewGrid.scrollTop = Math.floor(index / layout.columns) * layout.stride;
          renderVisible(); mounted.get(face.faceId)?.focus();
        }
      }
    });
    ui.faceReviewGrid.addEventListener("scroll", () => { hidePhotoPeek(); renderVisible(); maybeLoadMore(); });
    ui.faceReviewGrid.addEventListener("pointerdown", startArea);
    ui.faceReviewGrid.addEventListener("click", event => { if (suppressClick) { event.stopPropagation(); suppressClick = false; } }, true);
    window.addEventListener("resize", () => {
      clearTimeout(state.faceReviewResizeTimer);
      state.faceReviewResizeTimer = setTimeout(() => { if (state.view === "faces" && surface) { renderVisible(); maybeLoadMore(); } }, 180);
    });
    window.addEventListener("keydown", event => {
      if (state.view !== "faces" || !currentFaceReviewPerson() || event.altKey || event.ctrlKey || event.metaKey || event.target.closest("input, textarea, select, dialog, [contenteditable]")) return;
      const key = event.key.toLowerCase();
      if (key === "a") { event.preventDefault(); setAreaMode(!areaMode); }
      else if (key === "m") { event.preventDefault(); markSelectedReviewed(); }
      else if (key === "h") { event.preventDefault(); event.shiftKey ? showHidden() : hideSelected(); }
      else if (key === "c") { event.preventDefault(); clearSelection(); }
      else if (key === "q") { event.preventDefault(); queueSelectedFacesForUnnamed(); }
      else if (key === "?") { event.preventDefault(); toggleShortcuts(); }
      else if (key === "escape" && areaMode) { event.preventDefault(); setAreaMode(false); }
    });
  }

  return { renderFaceReview, currentFaceReviewPerson, loadFaceReviewPeople, bindFaceReviewControls };
}
