// Copyright (C) 2026 Serkan Bekdemir
// SPDX-License-Identifier: AGPL-3.0-only

export function createFaceReview({ api, assetThumb, drawFace, openAssetPreview, personThumb, refreshSummary, renderPagination, showToast, state, ui }) {
  const pageSize = 60;
  let gap = 8;
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
  let focusedFaceId = null;
  let previewBubble = null;
  let layout = { columns: 1, width: 104, height: 104, stride: 112 };
  const modeKey = "peopleWorkbench.faceReviewMode";
  let mode = "compact";
  try { if (localStorage.getItem(modeKey) === "detail") mode = "detail"; } catch { /* Private browsing may block storage. */ }

  const element = id => document.getElementById(id);
  const currentFaceReviewPerson = () => state.faceReviewCurrentPerson;
  const statusOf = face => face.draftVerdict === "wrong" ? "wrong" : face.draftVerdict === "unreviewed" ? "untouched" : face.draftVerdict === "correct" || face.reviewed ? "correct" : "untouched";
  const visibleFaces = () => faces.filter(face => !face.queued && !hidden.has(face.faceId));

  function showPhotoPeek(face, card) {
    if (!previewBubble) {
      previewBubble = document.createElement("div"); previewBubble.className = "face-review-peek";
      previewBubble.append(document.createElement("img")); document.body.append(previewBubble);
    }
    const image = previewBubble.querySelector("img"); image.src = assetThumb(face.assetId);
    const box = card.getBoundingClientRect();
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
      count.textContent = person.reviewed ? `✓ Checked · ${person.assetCount ?? "?"} photos` : `${person.reviewedCount || 0}/${total} checked${person.draftCount ? ` · ${person.draftCount} to submit` : ""}`;
      button.classList.toggle("reviewed", Boolean(person.reviewed));
      text.append(name, count);
      if (person.pendingOperation) {
        const badge = document.createElement("em"); badge.className = "face-person-pending";
        badge.textContent = `Pending ${person.pendingOperation === "merge-target" ? "merge target" : person.pendingOperation}`;
        text.append(badge);
      }
      button.append(image, text);
      button.addEventListener("click", () => selectFaceReviewPerson(person.id, true));
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
    person.draftCount = result.draftCount || 0;
    const pagePerson = state.faceReviewPeople.find(item => item.id === person.id);
    if (pagePerson && pagePerson !== person) Object.assign(pagePerson, { reviewedCount: person.reviewedCount, reviewTotal: person.reviewTotal, reviewed: person.reviewed, draftCount: person.draftCount });
    const pending = person.pendingOperation ? ` · Pending ${person.pendingOperation}${person.pendingName ? ` → ${person.pendingName}` : ""}` : "";
    element("face-review-meta").textContent = `${result.correctCount || 0} correct · ${result.wrongDraftCount || 0} wrong to submit · ${result.queuedFaceCount || 0} awaiting Sync · ${result.untouchedCount ?? "?"} untouched${pending}`;
    element("face-review-content").classList.toggle("person-reviewed", person.reviewed);
    renderFacePersonList();
  }

  function updateControls() {
    const selected = state.faceReviewSelected.size;
    const disabled = busy || loading;
    const focused = visibleFaces().some(face => face.faceId === focusedFaceId);
    const actionable = selected > 0 || focused;
    element("face-review-mark").disabled = !actionable || disabled;
    element("face-review-reset").disabled = !actionable || disabled;
    element("face-review-hide").disabled = disabled || !visibleFaces().some(face => statusOf(face) !== "untouched");
    element("face-review-show").disabled = !hidden.size || disabled;
    element("face-review-show-label").textContent = hidden.size ? `Show hidden (${hidden.size})` : "Show hidden";
    element("face-review-clear").disabled = !selected || disabled;
    element("face-review-queue").disabled = !actionable || disabled;
    element("face-review-submit").disabled = disabled || !(state.faceReviewResult?.draftCount > 0);
    element("face-review-submit").textContent = state.faceReviewResult?.draftCount ? `Review & submit (${state.faceReviewResult.draftCount})` : "Review & submit";
    element("face-review-compact").setAttribute("aria-pressed", String(mode === "compact"));
    element("face-review-detail").setAttribute("aria-pressed", String(mode === "detail"));
    ui.faceReviewGrid.classList.toggle("compact", mode === "compact");
    ui.faceReviewGrid.classList.toggle("detail", mode === "detail");
    const loadButton = element("face-review-load-more");
    loadButton.hidden = !hasMore && !loadError;
    loadButton.disabled = loading || busy;
    loadButton.textContent = loadError ? "Retry loading" : "Load more faces";
    element("face-review-load-status").textContent = loading ? "Loading more faces…" : loadError ? "Loading failed" : hasMore ? `${visibleFaces().length} of ${faces.length} loaded faces shown · scroll or use Load more` : `${visibleFaces().length} of ${faces.length} loaded faces shown`;
  }

  function updateMountedStates() {
    for (const [id, card] of mounted) {
      const face = faces.find(item => item.faceId === id);
      if (!face) continue;
      const selected = state.faceReviewSelected.has(id);
      card.classList.toggle("selected", selected);
      const status = statusOf(face);
      card.classList.toggle("correct", status === "correct");
      card.classList.toggle("wrong", status === "wrong");
      card.setAttribute("aria-selected", String(selected));
      card.setAttribute("aria-label", `Face from ${face.fileName}, ${status} person${face.draftVerdict ? ", awaiting review submission" : ""}`);
      card.querySelector(".face-review-badge").textContent = status === "correct" ? "✓" : status === "wrong" ? "×" : "";
    }
    updateControls();
  }

  function calculateLayout() {
    const minimum = mode === "compact" ? 104 : 180;
    gap = mode === "compact" ? 8 : 12;
    const width = Math.max(minimum, ui.faceReviewGrid.clientWidth - 16);
    const columns = Math.max(1, Math.floor((width + gap) / (minimum + gap)));
    const cardWidth = (width - (columns - 1) * gap) / columns;
    const cardHeight = Math.ceil(cardWidth + (mode === "compact" ? 0 : 65));
    layout = { columns, width: cardWidth, height: cardHeight, stride: cardHeight + gap };
    state.faceReviewLayout = layout;
  }

  function createFaceReviewCard(face) {
    const card = document.createElement("article");
    card.className = "face-review-card"; card.dataset.faceId = face.faceId; card.tabIndex = 0;
    card.setAttribute("role", "option");
    card.title = face.fileName;
    const crop = document.createElement("canvas"); crop.width = 280; crop.height = 280;
    drawFace(crop, face).catch(() => {
      if (crop.isConnected) crop.replaceWith(Object.assign(document.createElement("img"), { src: assetThumb(face.assetId), alt: "" }));
    });
    const meta = document.createElement("div"); meta.className = "face-review-card-meta";
    const filename = document.createElement("code"); filename.textContent = face.fileName; filename.title = face.fileName;
    const photo = document.createElement("button"); photo.type = "button"; photo.className = "quiet-button face-review-photo"; photo.innerHTML = "Photo <kbd>P</kbd>";
    photo.addEventListener("click", event => { event.stopPropagation(); openAssetPreview(face.assetId, face.fileName); });
    photo.addEventListener("mouseenter", () => showPhotoPeek(face, card));
    photo.addEventListener("mouseleave", hidePhotoPeek);
    photo.addEventListener("focus", () => showPhotoPeek(face, card));
    photo.addEventListener("blur", hidePhotoPeek);
    const badge = document.createElement("span"); badge.className = "face-review-badge"; badge.setAttribute("aria-hidden", "true");
    meta.append(filename, photo); card.append(crop, badge, meta);
    card.addEventListener("mouseenter", () => { if (mode === "compact") showPhotoPeek(face, card); });
    card.addEventListener("mouseleave", hidePhotoPeek);
    card.addEventListener("focus", () => { focusedFaceId = face.faceId; updateControls(); if (mode === "compact") showPhotoPeek(face, card); });
    card.addEventListener("blur", hidePhotoPeek);
    card.addEventListener("click", event => {
      if (!event.target.closest("button")) toggleSelection(face, event.shiftKey);
    });
    card.addEventListener("keydown", event => {
      if (event.target !== card) return;
      if (event.key === " " || event.key === "Enter") { event.preventDefault(); toggleSelection(face, event.shiftKey); }
      else if (["ArrowLeft", "ArrowRight", "ArrowUp", "ArrowDown"].includes(event.key)) {
        event.preventDefault(); navigateFaces(face.faceId, event.key).catch(error => showToast(error.message));
      } else if (event.key.toLowerCase() === "p") { event.preventDefault(); event.stopPropagation(); openAssetPreview(face.assetId, face.fileName); }
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
      message.textContent = hidden.size ? "Decided faces are hidden. Use Show hidden to see them." : "No faces remain to review here. Wrong faces submitted to Pending are hidden from this grid.";
      surface.append(message);
    } else surface.querySelectorAll(".face-review-empty").forEach(node => node.remove());
    updateMountedStates();
  }

  function focusVisibleFace(index) {
    const available = visibleFaces();
    const nextIndex = Math.max(0, Math.min(index, available.length - 1));
    const face = available[nextIndex];
    if (!face) { ui.faceReviewGrid.focus(); return; }
    calculateLayout();
    const top = Math.floor(nextIndex / layout.columns) * layout.stride;
    if (top < ui.faceReviewGrid.scrollTop) ui.faceReviewGrid.scrollTop = top;
    else if (top + layout.height > ui.faceReviewGrid.scrollTop + ui.faceReviewGrid.clientHeight)
      ui.faceReviewGrid.scrollTop = top + layout.height - ui.faceReviewGrid.clientHeight;
    renderVisible();
    mounted.get(face.faceId)?.focus({ preventScroll: true });
  }

  async function navigateFaces(faceId, key) {
    const available = visibleFaces();
    const index = available.findIndex(face => face.faceId === faceId);
    if (index < 0) return;
    calculateLayout();
    const delta = key === "ArrowLeft" ? -1 : key === "ArrowRight" ? 1 : key === "ArrowUp" ? -layout.columns : layout.columns;
    const target = index + delta;
    if (target >= available.length && hasMore) await loadNextPage();
    focusVisibleFace(target);
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

  async function stageFaces(decisions) {
    const person = currentFaceReviewPerson();
    if (!person || !decisions.length || busy) return;
    const requestGeneration = generation;
    busy = true; updateControls();
    let saved = 0;
    try {
      for (let offset = 0; offset < decisions.length; offset += 500) {
        const chunk = decisions.slice(offset, offset + 500);
        const result = await api("/api/face-review/draft", {
          method: "POST", body: { personId: person.id, items: chunk.map(({ face, verdict }) => ({
            faceId: face.faceId, assetId: face.assetId, fileName: face.fileName, verdict,
          })) },
        });
        saved += chunk.length;
        if (requestGeneration !== generation) continue;
        for (const { face, verdict } of chunk) face.draftVerdict = verdict === "clear" ? null : verdict;
        applyProgress(result);
      }
      if (requestGeneration === generation) {
        clearSelection(); renderVisible(); maybeLoadMore();
        showToast(`${saved} ${saved === 1 ? "decision" : "decisions"} saved locally. Submit review when ready.`);
      }
    } catch (error) {
      if (requestGeneration === generation) showToast(`${saved} decisions saved; could not finish: ${error.message}`);
    } finally {
      if (requestGeneration === generation) { busy = false; updateMountedStates(); }
    }
  }

  function stageSelected(verdict) {
    const selected = state.faceReviewSelectedData.size
      ? [...state.faceReviewSelectedData.values()].filter(face => !face.queued)
      : visibleFaces().filter(face => face.faceId === focusedFaceId);
    if (!selected.length || busy) return;
    if (selected.some(face => hidden.has(face.faceId))) {
      showToast("Show hidden selected faces before marking them.");
      return;
    }
    stageFaces(selected.map(face => ({ face, verdict: verdict === "reset" ? face.reviewed ? "unreviewed" : "clear" : verdict })));
  }

  async function submitReview() {
    const person = currentFaceReviewPerson();
    if (!person || busy || !(state.faceReviewResult?.draftCount > 0)) return;
    const requestGeneration = generation;
    busy = true; updateControls();
    try {
      const result = await api("/api/face-review/submit", { method: "POST", body: { personId: person.id } });
      if (requestGeneration !== generation) return;
      const submitted = new Map(result.submittedFaces.map(item => [item.faceId, item.verdict]));
      for (const face of faces) {
        const verdict = submitted.get(face.faceId);
        if (verdict === "correct") face.reviewed = true;
        else if (verdict === "wrong") { face.reviewed = false; face.queued = true; }
        else if (verdict === "unreviewed") face.reviewed = false;
        if (verdict) face.draftVerdict = null;
      }
      hidePhotoPeek();
      clearSelection();
      applyProgress(result);
      renderVisible(); maybeLoadMore();
      await refreshSummary().catch(error => showToast(error.message));
      showToast(`Review submitted locally: ${result.submitted.correct} correct, ${result.submitted.wrong} wrong faces added to Pending. Immich is unchanged.`);
    } catch (error) {
      if (requestGeneration === generation) showToast(`Review was not submitted: ${error.message}`);
    } finally {
      if (requestGeneration === generation) { busy = false; updateMountedStates(); }
    }
  }

  function hideDecided() {
    const available = visibleFaces();
    const focusIndex = available.findIndex(face => face.faceId === focusedFaceId);
    for (const face of available) if (statusOf(face) !== "untouched") hidden.add(face.faceId);
    clearSelection(); hidePhotoPeek(); renderVisible(); maybeLoadMore();
    if (focusIndex >= 0 && hidden.has(focusedFaceId)) focusVisibleFace(focusIndex);
  }
  function showHidden() { hidden.clear(); renderVisible(); maybeLoadMore(); }
  function clearSelection() { state.faceReviewSelected.clear(); state.faceReviewSelectedData.clear(); anchorId = null; updateMountedStates(); }
  function setMode(next) {
    mode = next;
    try { localStorage.setItem(modeKey, mode); } catch { /* Keep the current session mode. */ }
    hidePhotoPeek(); renderVisible(); maybeLoadMore();
  }
  function toggleShortcuts() {
    const panel = element("face-review-shortcuts"); panel.hidden = !panel.hidden;
    element("face-review-help").setAttribute("aria-expanded", String(!panel.hidden));
  }

  async function selectFaceReviewPerson(personId, focusGrid = false) {
    const person = state.faceReviewPeople.find(item => item.id === personId) || (state.faceReviewPersonId === personId ? state.faceReviewCurrentPerson : null);
    if (!person) return;
    generation += 1;
    state.faceReviewPersonId = personId; state.faceReviewCurrentPerson = person;
    state.faceReviewResult = null;
    state.faceReviewSelected.clear(); state.faceReviewSelectedData.clear();
    faces = []; hidden = new Set(); mounted = new Map(); nextPage = 1; hasMore = true; loadError = false; loading = false; busy = false; anchorId = null; focusedFaceId = null;
    hidePhotoPeek();
    renderFacePersonList();
    element("face-review-placeholder").hidden = true; element("face-review-content").hidden = false;
    element("face-review-name").textContent = person.name || "Unnamed person";
    element("face-review-meta").textContent = "Loading assigned faces…";
    ui.faceReviewGrid.replaceChildren(); ui.faceReviewGrid.scrollTop = 0;
    surface = document.createElement("div"); surface.className = "face-review-virtual"; ui.faceReviewGrid.append(surface);
    renderVisible(); await loadNextPage();
    if (focusGrid && state.faceReviewPersonId === personId) focusVisibleFace(0);
  }

  async function renderFaceReview() {
    await loadFaceReviewPeople();
    if (state.faceReviewNeedsRefresh) {
      state.faceReviewNeedsRefresh = false;
      if (state.faceReviewPersonId) await selectFaceReviewPerson(state.faceReviewPersonId);
    } else if (state.faceReviewPersonId && surface && currentFaceReviewPerson()?.id === state.faceReviewPersonId) {
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
    element("face-review-queue").addEventListener("click", () => stageSelected("wrong"));
    element("face-review-mark").addEventListener("click", () => stageSelected("correct"));
    element("face-review-reset").addEventListener("click", () => stageSelected("reset"));
    element("face-review-hide").addEventListener("click", hideDecided);
    element("face-review-show").addEventListener("click", showHidden);
    element("face-review-clear").addEventListener("click", clearSelection);
    element("face-review-compact").addEventListener("click", () => setMode("compact"));
    element("face-review-detail").addEventListener("click", () => setMode("detail"));
    element("face-review-submit").addEventListener("click", () => {
      const result = state.faceReviewResult;
      element("face-review-submit-summary").textContent = `${result?.draftCount || 0} decisions: ${result?.wrongDraftCount || 0} wrong faces will leave this review grid and enter Pending; correct faces will be saved locally. Immich changes only after you review Pending and explicitly Sync.`;
      element("face-review-submit-dialog").showModal();
    });
    element("face-review-cancel-submit").addEventListener("click", () => element("face-review-submit-dialog").close());
    element("face-review-confirm-submit").addEventListener("click", async () => { element("face-review-submit-dialog").close(); await submitReview(); });
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
    ui.faceReviewGrid.addEventListener("keydown", event => {
      if (event.target !== ui.faceReviewGrid || !["ArrowLeft", "ArrowRight", "ArrowUp", "ArrowDown"].includes(event.key)) return;
      event.preventDefault(); focusVisibleFace(0);
    });
    window.addEventListener("resize", () => {
      clearTimeout(state.faceReviewResizeTimer);
      state.faceReviewResizeTimer = setTimeout(() => { if (state.view === "faces" && surface) { renderVisible(); maybeLoadMore(); } }, 180);
    });
    window.addEventListener("keydown", event => {
      if (state.view !== "faces" || !currentFaceReviewPerson() || event.altKey || event.ctrlKey || event.metaKey || event.target.closest("input, textarea, select, dialog, button, [contenteditable]")) return;
      const key = event.key.toLowerCase();
      if (key === "a" && !event.shiftKey) { event.preventDefault(); stageSelected("correct"); }
      else if (key === "s" && !event.shiftKey) { event.preventDefault(); stageSelected("wrong"); }
      else if (key === "d" && !event.shiftKey) { event.preventDefault(); stageSelected("reset"); }
      else if (key === "f") { event.preventDefault(); event.shiftKey ? showHidden() : hideDecided(); }
      else if (key === "?") { event.preventDefault(); toggleShortcuts(); }
    });
  }

  return { renderFaceReview, currentFaceReviewPerson, loadFaceReviewPeople, bindFaceReviewControls };
}
