// Copyright (C) 2026 Serkan Bekdemir
// SPDX-License-Identifier: AGPL-3.0-only

export function showFaceInReview(face, showChecked, hidden, draft) {
  return Boolean(!face.queued && (!hidden || showChecked && face.reviewed)
    && (showChecked || !face.reviewed || draft && draft !== "clear"));
}

export function createFaceReview({ api, assetThumb, changePeopleSort, drawFace, openAssetPreview, personThumb, refreshSummary, renderPagination, showToast, state, ui }) {
  const pageSize = 60;
  let gap = 8;
  let faces = [];
  let visibility = { hidden: new Set(), hideDecided: false, decisionsAfterHide: new Set() };
  let visibilityByPerson = new Map();
  let showCheckedByPerson = new Map();
  let showChecked = false;
  let hidden = visibility.hidden;
  let mounted = new Map();
  let surface = null;
  let nextPage = 1;
  let hasMore = false;
  let loadError = false;
  let loading = false;
  let pageLoad = null;
  let busy = false;
  let draftSaving = false;
  let draftRevision = 0;
  const stagedVerdicts = new Map();
  let activeDecisionGeneration = null;
  let actionQueue = [];
  let generation = 0;
  let anchorId = null;
  let focusedFaceId = null;
  let focusIntentRevision = 0;
  let lightboxFaceId = null;
  let lightboxGeneration = null;
  let lightboxMoving = false;
  let lightboxMessage = "";
  let previewBubble = null;
  let hideCompletedPeople = false;
  let nameFilter = "all";
  let openPeopleControl = null;
  let peopleLoadRevision = 0;
  let scanRunning = false;
  let scanRestartRequested = false;
  let scanRetryRequested = false;
  let layout = { columns: 1, width: 104, height: 104, stride: 112 };
  const modeKey = "peopleWorkbench.faceReviewMode";
  let mode = "compact";
  try { if (localStorage.getItem(modeKey) === "detail") mode = "detail"; } catch { /* Private browsing may block storage. */ }

  const element = id => document.getElementById(id);
  const currentFaceReviewPerson = () => state.faceReviewCurrentPerson;
  const sortLabels = { most: "Most photos first", fewest: "Fewest photos first", "updated-newest": "Recently updated", "updated-oldest": "Least recently updated", random: "Random" };

  function updatePeopleControlState() {
    const query = element("face-person-search").value.trim();
    const search = element("face-person-search-toggle");
    search.classList.toggle("active", Boolean(query) || openPeopleControl === "search");
    search.title = query ? "Search people (query active)" : "Search people";
    search.setAttribute("aria-label", search.title);
    element("face-person-search-clear").disabled = !query;
    const filter = element("face-person-filter-toggle");
    filter.classList.toggle("active", nameFilter !== "all" || openPeopleControl === "filter");
    filter.title = `Filter people by name: ${nameFilter === "all" ? "All people" : nameFilter === "named" ? "Named people" : "Unnamed people"}`;
    filter.setAttribute("aria-label", filter.title);
    for (const value of ["all", "named", "unnamed"]) element(`face-person-name-${value}`).checked = value === nameFilter;
    const sort = element("face-person-sort-toggle");
    sort.classList.toggle("active", state.sort !== "most" || openPeopleControl === "sort");
    sort.title = `Order people: ${sortLabels[state.sort] || sortLabels.most}`;
    sort.setAttribute("aria-label", sort.title);
    for (const value of Object.keys(sortLabels)) element(`face-person-sort-${value}`).checked = value === state.sort;
    const completed = element("face-person-toggle-completed");
    completed.classList.toggle("active", hideCompletedPeople);
    completed.setAttribute("aria-pressed", String(hideCompletedPeople));
    completed.title = hideCompletedPeople ? "Show completed people" : "Hide completed people";
    completed.setAttribute("aria-label", "Hide completed people");
  }

  function setOpenPeopleControl(name, focus = true) {
    openPeopleControl = name;
    const searchActive = Boolean(element("face-person-search").value.trim());
    for (const control of ["search", "filter", "sort"]) {
      const open = control === name || (control === "search" && searchActive);
      const drawer = element(`face-person-${control}-drawer`);
      drawer.classList.toggle("open", open);
      drawer.inert = !open;
      drawer.setAttribute("aria-hidden", String(!open));
      element(`face-person-${control}-toggle`).setAttribute("aria-expanded", String(open));
    }
    updatePeopleControlState();
    if (focus && name === "search") element("face-person-search").focus();
    else if (focus && name === "filter") element(`face-person-name-${nameFilter}`).focus();
    else if (focus && name === "sort") element(`face-person-sort-${sortLabels[state.sort] ? state.sort : "most"}`).focus();
  }
  const statusOf = face => {
    const staged = stagedVerdicts.get(face.faceId);
    const draft = staged?.generation === generation ? staged.verdict === "clear" ? null : staged.verdict : face.draftVerdict;
    return draft === "wrong" ? "wrong" : draft === "unreviewed" ? "untouched" : draft === "correct" || face.reviewed ? "correct" : "untouched";
  };
  const visibleFaces = () => faces.filter(face => {
    const staged = stagedVerdicts.get(face.faceId);
    const draft = staged?.generation === generation ? staged.verdict : face.draftVerdict;
    return showFaceInReview(face, showChecked, hidden.has(face.faceId), draft);
  });

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

  function renderFacePersonList(resetScroll = false) {
    const scrollTop = resetScroll ? 0 : ui.facePersonList.scrollTop;
    ui.facePersonList.replaceChildren();
    for (const person of state.faceReviewPeople) {
      const button = document.createElement("button"); button.type = "button"; button.className = "face-person-button";
      button.classList.toggle("active", person.id === state.faceReviewPersonId);
      const image = document.createElement("img"); image.src = personThumb(person.id); image.alt = ""; image.loading = "lazy";
      const text = document.createElement("span");
      const name = document.createElement("strong"); name.textContent = person.name || "Unnamed person";
      const count = document.createElement("small");
      count.textContent = person.assetCount == null ? "Photo count unavailable" : `${person.assetCount} ${person.assetCount === 1 ? "photo" : "photos"}`;
      const progress = person.reviewed ? `✓ Review complete · ${person.reviewTotal} faces`
        : person.inventoryComplete ? `${person.reviewedCount || 0}/${person.reviewTotal} faces checked`
        : person.reviewedCount ? `${person.reviewedCount} faces checked so far` : "";
      const status = [progress, person.draftCount ? `${person.draftCount} to submit` : person.reviewReopened ? "reopened" : ""].filter(Boolean).join(" · ");
      button.classList.toggle("reviewed", Boolean(person.reviewed));
      text.append(name, count);
      if (status) {
        const detail = document.createElement("small"); detail.textContent = status; text.append(detail);
      }
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
      const empty = document.createElement("p"); empty.className = "muted";
      empty.textContent = hideCompletedPeople ? "No people need review with these filters. Show completed people or change the search." : "No people match these filters.";
      ui.facePersonList.append(empty);
    }
    ui.facePersonList.scrollTop = scrollTop;
  }

  async function loadFaceReviewPeople(resetScroll = false) {
    const revision = ++peopleLoadRevision;
    const query = element("face-person-search").value.trim();
    const data = await api(`/api/people?kind=review&sort=${encodeURIComponent(state.sort)}&page=${state.pages.facePeople}&size=${state.pageSize}&q=${encodeURIComponent(query)}&hideCompleted=${hideCompletedPeople ? "1" : "0"}&nameFilter=${nameFilter}`);
    if (revision !== peopleLoadRevision) return;
    state.faceReviewPeople = data.people; state.pages.facePeople = data.page;
    renderFacePersonList(resetScroll);
    renderPagination("face-person-pagination", data, page => {
      state.pages.facePeople = page; return loadFaceReviewPeople(true);
    });
    element("face-person-pagination").querySelectorAll("button:not(:disabled):not(.current)").forEach(button => {
      button.tabIndex = 0;
      if (button.title) button.setAttribute("aria-label", button.title);
    });
  }

  async function scanFaceCounts(retryFailed = false) {
    if (state.view !== "faces") return;
    if (scanRunning) {
      scanRestartRequested = true;
      scanRetryRequested ||= retryFailed;
      return;
    }
    scanRunning = true;
    const status = element("face-person-scan-status");
    const retry = element("face-person-scan-retry");
    retry.hidden = true;
    status.textContent = "Counting faces in background…";
    let previousComplete = -1;
    try {
      while (state.view === "faces") {
        const result = await api("/api/face-review/scan-next", {
          method: "POST", body: { activePersonId: state.faceReviewPersonId || "", retryFailed },
        });
        if (state.view !== "faces") break;
        if (result.busy) {
          await new Promise(resolve => setTimeout(resolve, 500));
          continue;
        }
        retryFailed = false;
        const peopleLabel = result.total === 1 ? "person" : "people";
        const message = result.done
          ? result.failed ? `Counted ${result.complete}/${result.total} ${peopleLabel} · ${result.failed} failed` : `Face counts ready · ${result.total} ${peopleLabel}`
          : `Counting faces · ${result.complete}/${result.total} ${peopleLabel}${result.failed ? ` · ${result.failed} failed` : ""}`;
        if (status.textContent !== message) status.textContent = message;
        retry.hidden = !(result.done && result.failed);
        if (result.personId === state.faceReviewPersonId && result.progress && !draftSaving && !actionQueue.length) {
          applyProgress(result.progress);
        }
        if (result.complete !== previousComplete) {
          previousComplete = result.complete;
          await loadFaceReviewPeople().catch(error => showToast(`Could not refresh face counts: ${error.message}`));
        }
        if (result.done) break;
        await new Promise(resolve => setTimeout(resolve, 150));
      }
    } catch (error) {
      status.textContent = `Could not count faces: ${error.message}`;
      retry.hidden = false;
    } finally {
      scanRunning = false;
      const restart = scanRestartRequested && state.view === "faces";
      const retry = scanRetryRequested;
      scanRestartRequested = false; scanRetryRequested = false;
      if (restart) {
        scanFaceCounts(retry).catch(error => showToast(`Could not count faces: ${error.message}`));
      }
    }
  }

  function updateFaceReviewProgress(result = state.faceReviewResult) {
    const person = currentFaceReviewPerson();
    if (!person || !result) return;
    person.reviewedCount = result.reviewedCount || 0;
    person.reviewTotal = result.reviewTotal;
    person.inventoryComplete = Boolean(result.inventoryComplete);
    person.reviewed = Boolean(result.personReviewed);
    person.reviewReopened = Boolean(result.reviewReopened);
    person.draftCount = result.draftCount || 0;
    const pagePerson = state.faceReviewPeople.find(item => item.id === person.id);
    if (pagePerson && pagePerson !== person) Object.assign(pagePerson, { reviewedCount: person.reviewedCount, reviewTotal: person.reviewTotal, inventoryComplete: person.inventoryComplete, reviewed: person.reviewed, reviewReopened: person.reviewReopened, draftCount: person.draftCount });
    const pending = person.pendingOperation ? ` · Pending ${person.pendingOperation}${person.pendingName ? ` → ${person.pendingName}` : ""}` : "";
    const reopened = person.reviewReopened ? " · Review reopened" : "";
    const remaining = person.inventoryComplete ? `${result.untouchedCount} untouched` : `${result.untouchedCount} untouched in loaded photos · load all photos for total`;
    element("face-review-meta").textContent = `${result.correctCount || 0} accepted · ${result.wrongDraftCount || 0} dismissed to submit · ${result.queuedFaceCount || 0} awaiting Sync · ${remaining}${reopened}${pending}`;
    element("face-review-content").classList.toggle("person-reviewed", person.reviewed);
    renderFacePersonList();
  }

  function updateControls() {
    const selected = state.faceReviewSelected.size;
    const disabled = busy;
    const focused = visibleFaces().some(face => face.faceId === focusedFaceId);
    const actionable = selected > 0 || focused;
    element("face-review-mark").disabled = !actionable || disabled;
    element("face-review-hide").disabled = disabled || (!draftSaving && !hasMore && !visibleFaces().some(face => statusOf(face) !== "untouched"));
    element("face-review-show").disabled = disabled || (!visibility.hideDecided && !hidden.size && !draftSaving);
    const manuallyHidden = faces.filter(face => hidden.has(face.faceId) && !(showChecked && face.reviewed)).length;
    element("face-review-show-label").textContent = manuallyHidden ? `Show hidden (${manuallyHidden})` : "Show hidden";
    element("face-review-show-checked").textContent = showChecked ? "Hide checked" : "Show checked";
    element("face-review-show-checked").setAttribute("aria-pressed", String(showChecked));
    element("face-review-clear").disabled = !selected || disabled;
    element("face-review-queue").disabled = !actionable || disabled;
    const review = state.faceReviewResult;
    const canFinish = review?.inventoryComplete && review?.reviewReopened && !review?.untouchedCount;
    const submitButton = element("face-review-submit");
    submitButton.disabled = disabled || draftSaving || actionQueue.length > 0 || !(review?.draftCount || review?.personReviewed || canFinish);
    submitButton.textContent = draftSaving ? "Saving decisions…" : review?.draftCount ? `Submit review (${review.draftCount})` : review?.personReviewed ? "Reopen review" : canFinish ? "Finish review" : "Submit review";
    submitButton.title = review?.draftCount ? "Submit review: Ctrl+Enter or Command+Enter" : "";
    if (review?.draftCount) submitButton.setAttribute("aria-keyshortcuts", "Control+Enter Meta+Enter");
    else submitButton.removeAttribute("aria-keyshortcuts");
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
      const label = status === "correct" ? "accepted" : status === "wrong" ? "dismissed" : "undecided";
      card.setAttribute("aria-label", `Face from ${face.fileName}, ${label}${face.draftVerdict ? ", awaiting review submission" : ""}`);
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
    const cardHeight = Math.ceil(cardWidth + (mode === "compact" ? 0 : 38));
    layout = { columns, width: cardWidth, height: cardHeight, stride: cardHeight + gap };
    state.faceReviewLayout = layout;
  }

  function createFaceReviewCard(face) {
    const card = document.createElement("article");
    card.className = "face-review-card"; card.dataset.faceId = face.faceId; card.tabIndex = -1;
    card.setAttribute("role", "option");
    card.title = face.fileName;
    const crop = document.createElement("canvas"); crop.className = "face-review-crop"; crop.width = 280; crop.height = 280;
    drawFace(crop, face).catch(() => {
      if (crop.isConnected) crop.replaceWith(Object.assign(document.createElement("img"), { className: "face-review-crop", src: assetThumb(face.assetId), alt: "" }));
    });
    const meta = document.createElement("div"); meta.className = "face-review-card-meta";
    const filename = document.createElement("code"); filename.textContent = face.fileName; filename.title = face.fileName;
    const badge = document.createElement("span"); badge.className = "face-review-badge"; badge.setAttribute("aria-hidden", "true");
    meta.append(filename); card.append(crop, badge, meta);
    card.addEventListener("mouseenter", () => { if (mode === "compact") showPhotoPeek(face, card); });
    card.addEventListener("mouseleave", hidePhotoPeek);
    card.addEventListener("focus", () => { focusedFaceId = face.faceId; updateControls(); });
    card.addEventListener("blur", hidePhotoPeek);
    card.addEventListener("click", event => {
      if (mode === "detail" && !event.shiftKey && event.target.classList.contains("face-review-crop")) {
        card.focus({ preventScroll: true }); openReviewPhoto(face); return;
      }
      card.focus({ preventScroll: true }); toggleSelection(face, event.shiftKey);
    });
    card.addEventListener("keydown", event => {
      if (event.target !== card) return;
      if (event.key === "Enter") { event.preventDefault(); toggleSelection(face, event.shiftKey); }
      else if (event.key === " ") { event.preventDefault(); openReviewPhoto(face); }
      else if (["ArrowLeft", "ArrowRight", "ArrowUp", "ArrowDown"].includes(event.key)) {
        event.preventDefault(); navigateFaces(face.faceId, event.key, event.shiftKey).catch(error => showToast(error.message));
      }
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
      message.textContent = hidden.size && !showChecked ? "Decided faces are hidden. Use Show hidden to see them."
        : !showChecked && faces.some(face => face.reviewed && !face.queued) ? "Checked faces are hidden. Use Show checked to see them."
          : "No faces remain to review here. Dismissed faces submitted to Pending are hidden from this grid.";
      surface.append(message);
    } else surface.querySelectorAll(".face-review-empty").forEach(node => node.remove());
    updateMountedStates();
  }

  function revealVisibleFace(index) {
    const available = visibleFaces();
    const nextIndex = Math.max(0, Math.min(index, available.length - 1));
    const face = available[nextIndex];
    if (!face) return null;
    calculateLayout();
    const top = Math.floor(nextIndex / layout.columns) * layout.stride;
    if (top < ui.faceReviewGrid.scrollTop) ui.faceReviewGrid.scrollTop = top;
    else if (top + layout.height > ui.faceReviewGrid.scrollTop + ui.faceReviewGrid.clientHeight)
      ui.faceReviewGrid.scrollTop = top + layout.height - ui.faceReviewGrid.clientHeight;
    renderVisible();
    return face;
  }

  function focusVisibleFace(index) {
    const face = revealVisibleFace(index);
    if (!face) { ui.faceReviewGrid.focus(); return; }
    mounted.get(face.faceId)?.focus({ preventScroll: true });
  }

  function nextReviewCandidate(oldOrder, oldAnchorIndex) {
    const available = visibleFaces();
    const eligible = face => face && !face.queued && !face.reviewed && !face.draftVerdict;
    const byId = new Map(available.map(face => [face.faceId, face]));
    const oldIds = new Set(oldOrder.map(face => face.faceId));
    for (const face of oldOrder.slice(Math.max(0, oldAnchorIndex + 1))) {
      if (eligible(byId.get(face.faceId))) return byId.get(face.faceId);
    }
    for (const face of available) {
      if (!oldIds.has(face.faceId) && eligible(face)) return face;
    }
    for (const face of oldOrder.slice(0, Math.max(0, oldAnchorIndex)).reverse()) {
      if (eligible(byId.get(face.faceId))) return byId.get(face.faceId);
    }
    const anchor = byId.get(oldOrder[oldAnchorIndex]?.faceId);
    if (eligible(anchor)) return anchor;
    return null;
  }

  async function continueReviewFocus(personId, requestGeneration, intentRevision, oldOrder, anchorIndex) {
    const stillInFlow = () => requestGeneration === generation
      && currentFaceReviewPerson()?.id === personId
      && state.view === "faces" && focusIntentRevision === intentRevision;
    while (stillInFlow()) {
      const candidate = nextReviewCandidate(oldOrder, anchorIndex);
      if (candidate) {
        focusVisibleFace(visibleFaces().findIndex(face => face.faceId === candidate.faceId));
        return;
      }
      if (!hasMore || loadError) break;
      const before = faces.length;
      await loadNextPage();
      if (!stillInFlow() || loadError || faces.length === before && hasMore) break;
    }
    if (!stillInFlow()) return;
    const retry = element("face-review-load-more");
    if (loadError && !retry.hidden && !retry.disabled) retry.focus();
    else {
      const submit = element("face-review-submit");
      if (!submit.hidden && !submit.disabled) submit.focus();
      else ui.faceReviewGrid.focus();
    }
  }

  async function navigateFaces(faceId, key, extendSelection = false) {
    const available = visibleFaces();
    const index = available.findIndex(face => face.faceId === faceId);
    if (index < 0) return;
    const requestGeneration = generation;
    const selectionAnchor = extendSelection && available.some(face => face.faceId === anchorId) ? anchorId : faceId;
    if (!extendSelection && state.faceReviewSelected.size) clearSelection();
    calculateLayout();
    const delta = key === "ArrowLeft" ? -1 : key === "ArrowRight" ? 1 : key === "ArrowUp" ? -layout.columns : layout.columns;
    const target = index + delta;
    if (target >= available.length && hasMore) await loadNextPage();
    if (requestGeneration !== generation) return;
    if (extendSelection) {
      const shown = visibleFaces();
      const start = shown.findIndex(face => face.faceId === selectionAnchor);
      const end = Math.max(0, Math.min(target, shown.length - 1));
      state.faceReviewSelected.clear(); state.faceReviewSelectedData.clear();
      if (start >= 0) {
        for (let position = Math.min(start, end); position <= Math.max(start, end); position += 1) {
          const face = shown[position];
          if (!face.queued) { state.faceReviewSelected.add(face.faceId); state.faceReviewSelectedData.set(face.faceId, face); }
        }
      }
      anchorId = selectionAnchor;
    }
    focusVisibleFace(target);
  }

  function maybeLoadMore() {
    if (!surface || loading || busy || !hasMore || state.view !== "faces") return;
    if (ui.faceReviewGrid.scrollTop + ui.faceReviewGrid.clientHeight >= surface.offsetHeight - layout.stride * 2) loadNextPage();
  }

  async function loadNextPage() {
    const person = currentFaceReviewPerson();
    if (!person) return;
    if (loading) return pageLoad?.generation === generation ? pageLoad.promise : undefined;
    if (!hasMore) return;
    const requestedGeneration = generation;
    const requestedDraftRevision = draftRevision;
    const pendingDraft = activeDecisionGeneration === requestedGeneration
      || actionQueue.some(action => action.type === "decision" && action.requestGeneration === requestedGeneration);
    loading = true; updateControls();
    const promise = (async () => {
      try {
        const result = await api(`/api/person-faces/${encodeURIComponent(person.id)}?page=${nextPage}&size=${pageSize}`);
        if (requestedGeneration !== generation) return;
        faces.push(...result.faces);
        if (visibility.hideDecided) {
          for (const face of result.faces) {
            if (statusOf(face) !== "untouched" && !visibility.decisionsAfterHide.has(face.faceId)) hidden.add(face.faceId);
          }
        }
        nextPage = result.page + 1; hasMore = result.hasMore;
        state.faceReviewPage = result.page;
        if (!pendingDraft && requestedDraftRevision === draftRevision) {
          state.faceReviewResult = result;
          updateFaceReviewProgress(result);
          if (hideCompletedPeople && result.personReviewed) loadFaceReviewPeople().catch(error => showToast(`Could not refresh people: ${error.message}`));
        }
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
    })();
    pageLoad = { generation: requestedGeneration, promise };
    try { await promise; }
    finally { if (pageLoad?.promise === promise) pageLoad = null; }
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

  async function saveDecisions({ decisions, personId, requestGeneration, revision }) {
    let saved = 0;
    try {
      for (let offset = 0; offset < decisions.length; offset += 500) {
        const chunk = decisions.slice(offset, offset + 500);
        const result = await api("/api/face-review/draft", {
          method: "POST", body: { personId, items: chunk.map(({ face, verdict }) => ({
            faceId: face.faceId, assetId: face.assetId, fileName: face.fileName, verdict,
          })) },
        });
        saved += chunk.length;
        for (const { face } of chunk) {
          if (stagedVerdicts.get(face.faceId)?.revision === revision) stagedVerdicts.delete(face.faceId);
        }
        const savedVisibility = visibilityByPerson.get(personId);
        if (savedVisibility?.hideDecided) {
          for (const { face, verdict } of chunk) {
            if ((verdict === "correct" || verdict === "wrong") && !savedVisibility.hidden.has(face.faceId)) {
              savedVisibility.decisionsAfterHide.add(face.faceId);
            }
          }
        }
        if (requestGeneration === generation) {
          for (const { face, verdict } of chunk) face.draftVerdict = verdict === "clear" ? null : verdict;
          applyProgress(result);
        }
      }
      if (requestGeneration === generation) { renderVisible(); maybeLoadMore(); }
    } catch (error) {
      for (const { face } of decisions.slice(saved)) {
        if (stagedVerdicts.get(face.faceId)?.revision === revision) stagedVerdicts.delete(face.faceId);
      }
      if (requestGeneration === generation) renderVisible();
      showToast(`${saved} decisions saved; could not finish: ${error.message}`);
    }
  }

  function enqueueAction(action) {
    if (action.type === "decision") {
      draftRevision += 1;
      action.revision = draftRevision;
      for (const { face, verdict } of action.decisions) {
        stagedVerdicts.set(face.faceId, { generation, revision: draftRevision, verdict });
      }
    }
    actionQueue.push({ ...action, requestGeneration: generation });
    state.faceReviewSaving = true;
    drainActions().catch(error => showToast(error.message));
    if (action.type === "decision") updateMountedStates();
  }

  async function drainActions() {
    if (draftSaving || busy) return;
    draftSaving = true; updateControls();
    try {
      while (actionQueue.length) {
        const action = actionQueue.shift();
        if (action.type === "decision") {
          activeDecisionGeneration = action.requestGeneration;
          try { await saveDecisions(action); } finally { activeDecisionGeneration = null; }
        } else if (action.requestGeneration !== generation) continue;
        else if (action.type === "hide") hideDecidedNow();
        else if (action.type === "show") showHiddenNow();
      }
    } finally {
      draftSaving = false; state.faceReviewSaving = false;
      document.dispatchEvent(new Event("face-review-save-state"));
      updateControls();
    }
  }

  function stageFaces(selected, verdict, clearSelected = false) {
    const person = currentFaceReviewPerson();
    if (!person || !selected.length || busy) return false;
    if (selected.some(face => hidden.has(face.faceId) && !(showChecked && face.reviewed))) {
      showToast("Show hidden selected faces before marking them.");
      return false;
    }
    const reset = selected.every(face => statusOf(face) === verdict);
    const decisions = selected.map(face => ({
      face,
      verdict: reset ? face.reviewed ? "unreviewed" : "clear" : verdict,
    }));
    if (clearSelected && state.faceReviewSelected.size) clearSelection();
    enqueueAction({ type: "decision", personId: person.id, decisions });
    return true;
  }

  function stageSelected(verdict) {
    focusIntentRevision += 1;
    const selected = state.faceReviewSelectedData.size
      ? [...state.faceReviewSelectedData.values()].filter(face => !face.queued)
      : visibleFaces().filter(face => face.faceId === focusedFaceId);
    stageFaces(selected, verdict, true);
  }

  function updateLightboxReview() {
    if (element("asset-preview-review").hidden || lightboxGeneration !== generation) return;
    const available = visibleFaces();
    const index = available.findIndex(face => face.faceId === lightboxFaceId);
    const face = available[index];
    if (!face) return;
    const status = statusOf(face);
    element("asset-preview-position").textContent = lightboxMessage || `Face ${index + 1} of ${available.length}${hasMore ? " loaded" : ""}`;
    const statusLabel = element("asset-preview-review-status");
    statusLabel.textContent = status === "correct" ? "Accepted" : status === "wrong" ? "Dismissed" : "Undecided";
    statusLabel.dataset.status = status;
    element("asset-preview-accept").setAttribute("aria-pressed", String(status === "correct"));
    element("asset-preview-dismiss").setAttribute("aria-pressed", String(status === "wrong"));
    element("asset-preview-accept").disabled = busy || lightboxMoving;
    element("asset-preview-dismiss").disabled = busy || lightboxMoving;
    element("asset-preview-previous").disabled = lightboxMoving || index === 0;
    const canAdvance = index < available.length - 1 || hasMore || loadError;
    element("asset-preview-next").disabled = lightboxMoving || !canAdvance;
    element("asset-preview-skip").disabled = lightboxMoving || !canAdvance;
  }

  function openReviewPhoto(face) {
    if (lightboxFaceId === null) lightboxGeneration = generation;
    lightboxFaceId = face.faceId;
    focusedFaceId = face.faceId;
    lightboxMessage = "";
    openAssetPreview(face.assetId, face.fileName, face.box, { review: true });
    updateLightboxReview();
  }

  function decideLightbox(verdict) {
    if (lightboxGeneration !== generation || lightboxMoving) return;
    const face = visibleFaces().find(item => item.faceId === lightboxFaceId);
    if (!face) return;
    if (stageFaces([face], verdict)) moveLightbox(1);
  }

  async function moveLightbox(direction) {
    if (lightboxGeneration !== generation || lightboxMoving) return;
    const available = visibleFaces();
    const index = available.findIndex(face => face.faceId === lightboxFaceId);
    if (index < 0) return;
    const target = index + direction;
    if (target < 0) return;
    const requestGeneration = generation;
    lightboxMoving = true;
    if (target >= available.length) lightboxMessage = "Loading next faces…";
    updateLightboxReview();
    try {
      while (direction > 0 && target >= visibleFaces().length && (hasMore || loadError)) {
        if (loadError) { hasMore = true; loadError = false; }
        await loadNextPage();
        if (requestGeneration !== generation || !element("asset-preview-dialog").open || loadError) break;
      }
      if (requestGeneration !== generation || !element("asset-preview-dialog").open) return;
      const next = visibleFaces()[target];
      if (next) {
        revealVisibleFace(target);
        openReviewPhoto(next);
      } else lightboxMessage = loadError ? "Load failed · Right to retry" : "No further visible faces";
    } finally {
      lightboxMoving = false;
      updateLightboxReview();
    }
  }

  function closeReviewPhoto() {
    const faceId = lightboxFaceId;
    const reviewGeneration = lightboxGeneration;
    lightboxFaceId = null;
    lightboxGeneration = null;
    lightboxMoving = false;
    lightboxMessage = "";
    if (faceId && reviewGeneration === generation && state.view === "faces") {
      const index = visibleFaces().findIndex(face => face.faceId === faceId);
      if (index >= 0) focusVisibleFace(index);
    }
  }

  async function submitReview() {
    const person = currentFaceReviewPerson();
    if (!person || busy || draftSaving || actionQueue.length || !(state.faceReviewResult?.draftCount > 0)) return;
    const requestGeneration = generation;
    const oldOrder = visibleFaces().slice();
    let anchorIndex = oldOrder.findIndex(face => face.faceId === focusedFaceId);
    if (anchorIndex < 0) anchorIndex = oldOrder.findIndex(face => state.faceReviewSelected.has(face.faceId));
    if (anchorIndex < 0) anchorIndex = 0;
    const intentRevision = focusIntentRevision;
    const stillInFlow = () => requestGeneration === generation
      && currentFaceReviewPerson()?.id === person.id
      && state.view === "faces" && focusIntentRevision === intentRevision;
    let submitted = false;
    busy = true; updateControls();
    try {
      const result = await api("/api/face-review/submit", { method: "POST", body: { personId: person.id } });
      if (requestGeneration !== generation) return;
      submitted = true;
      const submittedByFace = new Map(result.submittedFaces.map(item => [item.faceId, item.verdict]));
      for (const face of faces) {
        const verdict = submittedByFace.get(face.faceId);
        if (verdict === "correct") face.reviewed = true;
        else if (verdict === "wrong") { face.reviewed = false; face.queued = true; }
        else if (verdict === "unreviewed") face.reviewed = false;
        if (verdict) face.draftVerdict = null;
      }
      hidePhotoPeek();
      clearSelection();
      applyProgress(result);
      renderVisible();
      showToast(`Review submitted locally: ${result.submitted.correct} accepted, ${result.submitted.wrong} dismissed faces added to Pending. Immich is unchanged.`);
      await refreshSummary().catch(error => showToast(`Could not refresh summary: ${error.message}`));
      if (hideCompletedPeople) await loadFaceReviewPeople().catch(error => showToast(`Could not refresh people: ${error.message}`));
    } catch (error) {
      if (requestGeneration === generation) showToast(`Review was not submitted: ${error.message}`);
    } finally {
      if (requestGeneration === generation) {
        busy = false; updateMountedStates();
        if (stillInFlow()) {
          if (!submitted) {
            const available = visibleFaces();
            const originalIndex = available.findIndex(face => face.faceId === focusedFaceId);
            const submit = element("face-review-submit");
            if (originalIndex >= 0 && !available[originalIndex].queued) focusVisibleFace(originalIndex);
            else if (!submit.hidden && !submit.disabled) submit.focus();
            else ui.faceReviewGrid.focus();
          }
          else {
            const submit = element("face-review-submit");
            if (state.faceReviewResult?.draftCount === 0 && state.faceReviewResult?.personReviewed
                && !submit.hidden && !submit.disabled) submit.focus();
            else await continueReviewFocus(person.id, requestGeneration, intentRevision, oldOrder, anchorIndex)
              .catch(() => { if (stillInFlow()) ui.faceReviewGrid.focus(); });
          }
        }
      }
    }
  }

  async function toggleReviewReopened() {
    const person = currentFaceReviewPerson();
    const review = state.faceReviewResult;
    if (!person || !review || busy || draftSaving || actionQueue.length || review.draftCount || !(review.personReviewed || review.inventoryComplete && review.reviewReopened && !review.untouchedCount)) return;
    const requestGeneration = generation;
    busy = true; updateControls();
    try {
      const result = await api("/api/face-review/reopen", { method: "POST", body: { personId: person.id, reopened: Boolean(review.personReviewed) } });
      if (requestGeneration !== generation) return;
      applyProgress(result);
      if (hideCompletedPeople) await loadFaceReviewPeople().catch(error => showToast(`Could not refresh people: ${error.message}`));
    } catch (error) {
      if (requestGeneration === generation) showToast(`Could not change review state: ${error.message}`);
    } finally {
      if (requestGeneration === generation) { busy = false; updateControls(); }
    }
  }

  function hideDecidedNow() {
    const available = visibleFaces();
    const focusIndex = available.findIndex(face => face.faceId === focusedFaceId);
    visibility.hideDecided = true;
    visibility.decisionsAfterHide.clear();
    for (const face of available) if (statusOf(face) !== "untouched") hidden.add(face.faceId);
    for (const id of hidden) { state.faceReviewSelected.delete(id); state.faceReviewSelectedData.delete(id); }
    if (hidden.has(anchorId)) anchorId = null;
    hidePhotoPeek(); renderVisible(); maybeLoadMore();
    if (focusIndex >= 0 && hidden.has(focusedFaceId)) focusVisibleFace(focusIndex);
  }
  function showHiddenNow() {
    visibility.hideDecided = false;
    visibility.decisionsAfterHide.clear();
    hidden.clear(); renderVisible(); maybeLoadMore();
  }
  function hideDecided() { if (!busy) enqueueAction({ type: "hide" }); }
  function showHidden() { if (!busy) enqueueAction({ type: "show" }); }
  function toggleChecked() {
    showChecked = !showChecked;
    if (state.faceReviewPersonId) showCheckedByPerson.set(state.faceReviewPersonId, showChecked);
    if (!showChecked) {
      for (const face of faces) {
        if (face.reviewed && statusOf(face) !== "untouched") {
          state.faceReviewSelected.delete(face.faceId);
          state.faceReviewSelectedData.delete(face.faceId);
        }
      }
    }
    hidePhotoPeek(); renderVisible(); maybeLoadMore(); updateControls();
  }
  function clearSelection() { state.faceReviewSelected.clear(); state.faceReviewSelectedData.clear(); anchorId = null; updateMountedStates(); }
  function setMode(next) {
    mode = next;
    try { localStorage.setItem(modeKey, mode); } catch { /* Keep the current session mode. */ }
    hidePhotoPeek(); renderVisible(); maybeLoadMore();
  }
  function toggleShortcuts() {
    const dialog = element("face-review-help-dialog");
    if (dialog.open) dialog.close(); else dialog.showModal();
  }

  async function selectFaceReviewPerson(personId, focusGrid = false) {
    const person = state.faceReviewPeople.find(item => item.id === personId) || (state.faceReviewPersonId === personId ? state.faceReviewCurrentPerson : null);
    if (!person) return;
    generation += 1;
    state.faceReviewPersonId = personId; state.faceReviewCurrentPerson = person;
    state.faceReviewResult = null;
    state.faceReviewSelected.clear(); state.faceReviewSelectedData.clear();
    faces = [];
    visibility = visibilityByPerson.get(personId) || { hidden: new Set(), hideDecided: false, decisionsAfterHide: new Set() };
    visibilityByPerson.set(personId, visibility);
    showChecked = showCheckedByPerson.get(personId) || false;
    hidden = visibility.hidden;
    mounted = new Map(); nextPage = 1; hasMore = true; loadError = false; loading = false; busy = false; anchorId = null; focusedFaceId = null;
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
    updatePeopleControlState();
    await loadFaceReviewPeople();
    if (state.faceReviewNeedsRefresh) {
      state.faceReviewNeedsRefresh = false;
      visibilityByPerson = new Map();
      if (state.faceReviewPersonId) await selectFaceReviewPerson(state.faceReviewPersonId);
    } else if (state.faceReviewPersonId && surface && currentFaceReviewPerson()?.id === state.faceReviewPersonId) {
      renderVisible(); maybeLoadMore();
    } else if (state.faceReviewPersonId) await selectFaceReviewPerson(state.faceReviewPersonId);
    else { element("face-review-placeholder").hidden = false; element("face-review-content").hidden = true; }
    scanFaceCounts().catch(error => showToast(`Could not count faces: ${error.message}`));
  }

  function bindFaceReviewControls() {
    document.addEventListener("focusin", () => { focusIntentRevision += 1; }, true);
    let searchTimer = null;
    window.addEventListener("beforeunload", event => {
      if (!state.faceReviewSaving) return;
      event.preventDefault(); event.returnValue = "";
    });
    for (const control of ["search", "filter", "sort"]) {
      element(`face-person-${control}-toggle`).addEventListener("click", () => {
        if (control === "search" && element("face-person-search").value.trim()) {
          setOpenPeopleControl("search"); return;
        }
        setOpenPeopleControl(openPeopleControl === control ? null : control);
      });
    }
    document.addEventListener("pointerdown", event => {
      if (openPeopleControl && !element("face-person-controls").contains(event.target)) setOpenPeopleControl(null);
    });
    window.addEventListener("keydown", event => {
      if (state.view !== "faces" || event.key !== "Escape" || !openPeopleControl || event.target.closest("dialog")) return;
      const control = openPeopleControl;
      event.preventDefault(); element(`face-person-${control}-toggle`).focus();
      setOpenPeopleControl(null);
    });
    const search = element("face-person-search");
    const scheduleSearch = () => {
      setOpenPeopleControl("search", false);
      clearTimeout(searchTimer); state.pages.facePeople = 1;
      searchTimer = setTimeout(() => loadFaceReviewPeople(true).catch(error => showToast(error.message)), 180);
    };
    search.addEventListener("input", scheduleSearch);
    element("face-person-search-clear").addEventListener("click", () => {
      if (!search.value) return;
      search.value = ""; scheduleSearch(); search.focus();
    });
    element("face-person-name-filter").addEventListener("change", async event => {
      if (!["all", "named", "unnamed"].includes(event.target.value)) return;
      const previous = nameFilter;
      nameFilter = event.target.value;
      updatePeopleControlState();
      element("face-person-filter-toggle").focus();
      setOpenPeopleControl(null);
      state.pages.facePeople = 1;
      const revision = peopleLoadRevision + 1;
      const fieldset = element("face-person-name-filter");
      fieldset.disabled = true;
      try { await loadFaceReviewPeople(true); }
      catch (error) {
        if (revision === peopleLoadRevision) {
          nameFilter = previous;
          updatePeopleControlState();
          await loadFaceReviewPeople(true).catch(() => {});
        }
        showToast(`Could not change people filter: ${error.message}`);
      } finally { fieldset.disabled = false; }
    });
    element("face-person-sort-options").addEventListener("change", async event => {
      if (!Object.hasOwn(sortLabels, event.target.value)) return;
      element("face-person-sort-toggle").focus();
      setOpenPeopleControl(null);
      try { await changePeopleSort(event.target.value); }
      catch (error) { showToast(`Could not change people order: ${error.message}`); }
      updatePeopleControlState();
    });
    element("face-person-toggle-completed").addEventListener("click", async () => {
      const previous = hideCompletedPeople;
      hideCompletedPeople = !hideCompletedPeople;
      const button = element("face-person-toggle-completed");
      setOpenPeopleControl(null);
      updatePeopleControlState();
      state.pages.facePeople = 1;
      const revision = peopleLoadRevision + 1;
      button.disabled = true;
      try { await loadFaceReviewPeople(true); }
      catch (error) {
        if (revision === peopleLoadRevision) {
          hideCompletedPeople = previous;
          updatePeopleControlState();
          await loadFaceReviewPeople(true).catch(() => {});
        }
        showToast(`Could not change people filter: ${error.message}`);
      } finally { button.disabled = false; }
    });
    setOpenPeopleControl(null);
    element("face-person-scan-retry").addEventListener("click", () => scanFaceCounts(true));
    element("face-review-queue").addEventListener("click", () => stageSelected("wrong"));
    element("face-review-mark").addEventListener("click", () => stageSelected("correct"));
    element("face-review-show-checked").addEventListener("click", toggleChecked);
    element("face-review-hide").addEventListener("click", hideDecided);
    element("face-review-show").addEventListener("click", showHidden);
    element("face-review-clear").addEventListener("click", clearSelection);
    element("face-review-compact").addEventListener("click", () => setMode("compact"));
    element("face-review-detail").addEventListener("click", () => setMode("detail"));
    element("face-review-submit").addEventListener("click", () => {
      if (state.faceReviewResult?.draftCount) submitReview();
      else toggleReviewReopened();
    });
    element("face-review-help").addEventListener("click", toggleShortcuts);
    element("asset-preview-accept").addEventListener("click", () => decideLightbox("correct"));
    element("asset-preview-dismiss").addEventListener("click", () => decideLightbox("wrong"));
    element("asset-preview-previous").addEventListener("click", () => moveLightbox(-1));
    element("asset-preview-next").addEventListener("click", () => moveLightbox(1));
    element("asset-preview-skip").addEventListener("click", () => moveLightbox(1));
    element("asset-preview-dialog").addEventListener("close", closeReviewPhoto);
    element("asset-preview-dialog").addEventListener("keydown", event => {
      if (element("asset-preview-review").hidden || lightboxGeneration !== generation || event.altKey || event.ctrlKey || event.metaKey || event.shiftKey) return;
      const key = event.key.toLowerCase();
      if (key === " " && !event.target.closest("button, input, textarea, select, [contenteditable]")) {
        event.preventDefault(); element("asset-preview-dialog").close();
      }
      else if (key === "a") { event.preventDefault(); decideLightbox("correct"); }
      else if (key === "d") { event.preventDefault(); decideLightbox("wrong"); }
      else if (key === "s" || key === "arrowright") { event.preventDefault(); moveLightbox(1); }
      else if (key === "arrowleft") { event.preventDefault(); moveLightbox(-1); }
    });
    document.addEventListener("face-review-save-state", updateLightboxReview);
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
      if (state.view !== "faces" || !currentFaceReviewPerson() || event.altKey) return;
      if (event.key === "?" && element("face-review-help-dialog").open && !event.ctrlKey && !event.metaKey) {
        event.preventDefault(); toggleShortcuts(); return;
      }
      if (event.key === "Enter" && (event.ctrlKey || event.metaKey) && !event.shiftKey
          && !event.target.closest("input, textarea, select, dialog, [contenteditable]")
          && state.faceReviewResult?.draftCount) {
        event.preventDefault(); submitReview(); return;
      }
      if (event.ctrlKey || event.metaKey || event.target.closest("input, textarea, select, dialog, button, [contenteditable]")) return;
      const key = event.key.toLowerCase();
      if (key === " " && event.target === ui.faceReviewGrid && !event.shiftKey) {
        focusIntentRevision += 1;
        const face = visibleFaces().find(item => item.faceId === focusedFaceId) || visibleFaces()[0];
        if (face) { event.preventDefault(); openReviewPhoto(face); }
      }
      else if (key === "a" && !event.shiftKey) { event.preventDefault(); stageSelected("correct"); }
      else if (key === "d" && !event.shiftKey) { event.preventDefault(); stageSelected("wrong"); }
      else if (key === "f") { focusIntentRevision += 1; event.preventDefault(); event.shiftKey ? showHidden() : hideDecided(); }
      else if (key === "?") { focusIntentRevision += 1; event.preventDefault(); toggleShortcuts(); }
    });
  }

  return { renderFaceReview, currentFaceReviewPerson, loadFaceReviewPeople, bindFaceReviewControls };
}
