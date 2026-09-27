// Copyright (C) 2026 Serkan Bekdemir
// SPDX-License-Identifier: AGPL-3.0-only

export function createMergeWorkbench({ api, cleanName, moveSlide, personThumb, refreshSummary, saveMergeCanvasState, showSlide, showToast, state, ui, MERGE_CARD_WIDTH, MERGE_CARD_HEIGHT, MERGE_MIN_WORLD_WIDTH, MERGE_MIN_WORLD_HEIGHT, MERGE_MIN_ZOOM, MERGE_MAX_ZOOM }) {
  function selectedMergePeople() {
    const byId = new Map(state.mergePeople.map(person => [person.id, person]));
    return [...state.mergeSelected].map(id => byId.get(id)).filter(Boolean);
  }

  function clearMergeSelectionContext() {
    state.mergeSelectedBucketId = null;
    state.mergeSelectedGroupId = null;
  }

  function mergeSurvivor(people) {
    return people.reduce((best, person) => !best || (person.assetCount || 0) > (best.assetCount || 0) ? person : best, null);
  }

  function mergeGroupColor(groupId) {
    let hash = 0;
    for (const character of groupId) hash = ((hash << 5) - hash + character.charCodeAt(0)) | 0;
    return `hsl(${Math.abs(hash) % 360} 72% 55%)`;
  }

  function mergeBucketLabel(bucketId) {
    const match = /^slot-([1-9])$/.exec(bucketId || "");
    return match ? match[1] : "•";
  }

  function nextMergeGroupId() {
    return globalThis.crypto?.randomUUID?.() || `group-${Date.now()}-${Math.random().toString(16).slice(2)}`;
  }

  function isMergeCardProtected(personId) {
    return Boolean(state.mergeCanvas.groups[personId] || state.mergeCanvas.buckets[personId]);
  }

  function applyMergeCardGroup(card, personId) {
    const groupId = state.mergeCanvas.groups[personId] || null;
    const bucketId = state.mergeCanvas.buckets[personId] || null;
    card.classList.toggle("canvas-grouped", Boolean(groupId));
    card.classList.toggle("canvas-bucketed", Boolean(bucketId));
    card.dataset.canvasGroup = groupId || "";
    card.dataset.canvasBucket = bucketId || "";
    const groupMark = card.querySelector(".merge-group-mark");
    const bucketMark = card.querySelector(".merge-bucket-mark");
    groupMark.hidden = !groupId;
    if (groupId) {
      const color = mergeGroupColor(groupId);
      card.style.setProperty("--canvas-group-color", color);
      groupMark.style.background = color;
      groupMark.textContent = "G";
      groupMark.title = "Persistent person group";
    } else {
      card.style.removeProperty("--canvas-group-color");
      groupMark.style.removeProperty("background");
    }
    bucketMark.hidden = !bucketId;
    if (bucketId) {
      const color = mergeGroupColor(bucketId);
      card.style.setProperty("--canvas-bucket-color", color);
      bucketMark.style.background = color;
      bucketMark.textContent = mergeBucketLabel(bucketId);
      bucketMark.title = `Mental sorting bucket ${mergeBucketLabel(bucketId)}`;
    } else {
      card.style.removeProperty("--canvas-bucket-color");
      bucketMark.style.removeProperty("background");
    }
  }

  function makeMergeCanvasGroup() {
    const selected = [...state.mergeSelected];
    if (selected.length < 2) { showToast("Select at least two faces for a person group."); return; }
    const existing = new Set(selected.map(id => state.mergeCanvas.groups[id]).filter(Boolean));
    if (existing.size === 1 && selected.every(id => state.mergeCanvas.groups[id] === [...existing][0])) {
      state.mergeSelectedBucketId = null;
      state.mergeSelectedGroupId = [...existing][0];
      state.mergeTrayTab = "groups";
      renderMergeTray();
      showToast("Selection is already one person group."); return;
    }
    const groupId = nextMergeGroupId();
    for (const id of selected) state.mergeCanvas.groups[id] = groupId;
    state.mergeSelectedBucketId = null;
    state.mergeSelectedGroupId = groupId;
    state.mergeTrayTab = "groups";
    ui.mergeGrid.querySelectorAll(".merge-cluster-card").forEach(card => applyMergeCardGroup(card, card.dataset.personId));
    saveMergeCanvasState();
    renderMergeTray();
    showToast(`Created a person group with ${selected.length} faces.`);
  }

  function assignMergeNumberGroup(number) {
    const personId = state.mergeHoveredId || state.mergeFocusedId;
    if (!personId || !state.mergePeople.some(person => person.id === personId)) { showToast("Hover or focus a face first."); return; }
    const previousBucket = state.mergeCanvas.buckets[personId] || null;
    if (number === 0) {
      if (!state.mergeCanvas.buckets[personId]) { showToast("That face is not in a canvas bucket."); return; }
      delete state.mergeCanvas.buckets[personId];
      showToast("Removed the face from its canvas bucket.");
    } else {
      const targetBucket = `slot-${number}`;
      if (previousBucket === targetBucket) {
        delete state.mergeCanvas.buckets[personId];
        showToast(`Removed the face from canvas bucket ${number}.`);
      } else {
        state.mergeCanvas.buckets[personId] = targetBucket;
        showToast(`Assigned the face to canvas bucket ${number}.`);
      }
    }
    const card = mergeCardForId(personId); if (card) applyMergeCardGroup(card, personId);
    if (state.mergeSelectedBucketId && previousBucket === state.mergeSelectedBucketId && state.mergeCanvas.buckets[personId] !== previousBucket) {
      state.mergeSelected.delete(personId);
      if (!state.mergeSelected.size) state.mergeSelectedBucketId = null;
      syncMergeCardSelection();
    }
    renderMergeTray(); saveMergeCanvasState();
  }

  function ungroupMergeSelection() {
    let removed = 0;
    for (const id of state.mergeSelected) {
      if (state.mergeCanvas.groups[id]) { delete state.mergeCanvas.groups[id]; removed += 1; }
    }
    if (state.mergeSelectedGroupId) {
      state.mergeSelectedGroupId = null;
    }
    ui.mergeGrid.querySelectorAll(".merge-cluster-card").forEach(card => applyMergeCardGroup(card, card.dataset.personId));
    saveMergeCanvasState();
    renderMergeTray();
    if (removed) showToast(`Removed ${removed} faces from their person groups.`);
  }

  function syncMergeCardSelection() {
    const order = new Map([...state.mergeSelected].map((id, index) => [id, index + 1]));
    ui.mergeGrid.querySelectorAll(".merge-cluster-card").forEach(card => {
      const selected = order.has(card.dataset.personId);
      card.classList.toggle("selected", selected);
      card.setAttribute("aria-selected", String(selected));
      card.querySelector(".merge-selection-mark").textContent = selected ? String(order.get(card.dataset.personId)) : "+";
    });
  }

  function toggleMergePerson(person) {
    clearMergeSelectionContext();
    if (state.mergeSelected.has(person.id)) state.mergeSelected.delete(person.id);
    else state.mergeSelected.add(person.id);
    syncMergeCardSelection();
    renderMergeTray();
  }

  function collectMergeCollections(property) {
    const collections = new Map();
    for (const person of state.mergePeople) {
      const collectionId = state.mergeCanvas[property][person.id];
      if (!collectionId) continue;
      if (!collections.has(collectionId)) collections.set(collectionId, []);
      collections.get(collectionId).push(person);
    }
    return collections;
  }

  function setMergeTrayTab(tab) {
    state.mergeTrayTab = tab === "buckets" ? "buckets" : "groups";
    const groupsActive = state.mergeTrayTab === "groups";
    const groupsTab = document.querySelector("#merge-groups-tab");
    const bucketsTab = document.querySelector("#merge-buckets-tab");
    groupsTab.classList.toggle("active", groupsActive);
    bucketsTab.classList.toggle("active", !groupsActive);
    groupsTab.setAttribute("aria-selected", String(groupsActive));
    bucketsTab.setAttribute("aria-selected", String(!groupsActive));
    document.querySelector("#merge-groups-panel").hidden = !groupsActive;
    document.querySelector("#merge-buckets-panel").hidden = groupsActive;
  }

  function selectMergeCollection(kind, collectionId, people) {
    state.mergeSelected = new Set(people.map(person => person.id));
    state.mergeSelectedGroupId = kind === "group" ? collectionId : null;
    state.mergeSelectedBucketId = kind === "bucket" ? collectionId : null;
    state.mergeTrayTab = kind === "group" ? "groups" : "buckets";
    syncMergeCardSelection(); renderMergeTray();
    requestAnimationFrame(() => focusMergeCollection(people));
  }

  function renderMergeOrganizerLists() {
    const bucketContainer = document.querySelector("#merge-canvas-buckets");
    const groupContainer = document.querySelector("#merge-person-groups");
    const bucketEntries = [...collectMergeCollections("buckets").entries()].sort(([first], [second]) => {
      const firstNumber = Number(mergeBucketLabel(first)), secondNumber = Number(mergeBucketLabel(second));
      if (Number.isFinite(firstNumber) !== Number.isFinite(secondNumber)) return Number.isFinite(firstNumber) ? -1 : 1;
      return Number.isFinite(firstNumber) ? firstNumber - secondNumber : first.localeCompare(second);
    });
    const groupEntries = [...collectMergeCollections("groups").entries()].sort(([first], [second]) => first.localeCompare(second));
    document.querySelector("#merge-bucket-count").textContent = bucketEntries.length;
    document.querySelector("#merge-person-group-count").textContent = groupEntries.length;
    document.querySelector("#merge-review-groups").disabled = groupEntries.length === 0;
    bucketContainer.replaceChildren(); groupContainer.replaceChildren();

    if (!bucketEntries.length) {
      const empty = document.createElement("p"); empty.className = "muted"; empty.textContent = "Hover a face and press 1–9 to create a mental sorting bucket."; bucketContainer.append(empty);
    }
    for (const [groupId, people] of bucketEntries) {
      const row = document.createElement("article"); row.className = "canvas-group-row";
      row.classList.toggle("active", state.mergeSelectedBucketId === groupId);
      row.style.setProperty("--canvas-group-color", mergeGroupColor(groupId));
      const select = document.createElement("button"); select.type = "button"; select.className = "canvas-group-select";
      select.title = `Select all ${people.length} faces in canvas bucket ${mergeBucketLabel(groupId)}`;
      select.setAttribute("aria-pressed", String(state.mergeSelectedBucketId === groupId));
      const key = document.createElement("span"); key.className = "canvas-group-key"; key.textContent = mergeBucketLabel(groupId);
      const thumbs = document.createElement("span"); thumbs.className = "canvas-group-thumbs";
      for (const person of people.slice(0, 4)) {
        const image = document.createElement("img"); image.src = personThumb(person.id); image.alt = ""; thumbs.append(image);
      }
      const count = document.createElement("span"); count.className = "canvas-group-count"; count.textContent = String(people.length);
      select.append(key, thumbs, count);
      select.addEventListener("click", () => selectMergeCollection("bucket", groupId, people));
      const name = document.createElement("input"); name.className = "canvas-group-name"; name.type = "text";
      name.value = state.mergeCanvas.bucketNames[groupId] || ""; name.placeholder = `Name bucket ${mergeBucketLabel(groupId)}`;
      name.setAttribute("aria-label", `Name canvas bucket ${mergeBucketLabel(groupId)}`);
      name.addEventListener("input", () => {
        const value = name.value.trim();
        if (value) state.mergeCanvas.bucketNames[groupId] = value; else delete state.mergeCanvas.bucketNames[groupId];
        saveMergeCanvasState();
      });
      row.append(select, name); bucketContainer.append(row);
    }

    if (!groupEntries.length) {
      const empty = document.createElement("p"); empty.className = "muted"; empty.textContent = "Select faces and press G to create a person group."; groupContainer.append(empty);
    }
    groupEntries.forEach(([groupId, people], index) => {
      const row = document.createElement("article"); row.className = "canvas-group-row";
      row.classList.toggle("active", state.mergeSelectedGroupId === groupId);
      row.style.setProperty("--canvas-group-color", mergeGroupColor(groupId));
      const select = document.createElement("button"); select.type = "button"; select.className = "canvas-group-select";
      select.title = `Select all ${people.length} faces in person group ${index + 1}`;
      select.setAttribute("aria-pressed", String(state.mergeSelectedGroupId === groupId));
      const key = document.createElement("span"); key.className = "canvas-group-key"; key.textContent = `G${index + 1}`;
      const thumbs = document.createElement("span"); thumbs.className = "canvas-group-thumbs";
      for (const person of people.slice(0, 4)) {
        const image = document.createElement("img"); image.src = personThumb(person.id); image.alt = ""; thumbs.append(image);
      }
      const count = document.createElement("span"); count.className = "canvas-group-count"; count.textContent = String(people.length);
      select.append(key, thumbs, count);
      select.addEventListener("click", () => selectMergeCollection("group", groupId, people));
      row.append(select); groupContainer.append(row);
    });

    setMergeTrayTab(state.mergeTrayTab);
  }

  function removeMergeTrayPerson(person) {
    const bucketId = state.mergeSelectedBucketId;
    const groupId = state.mergeSelectedGroupId;
    if (bucketId && state.mergeCanvas.buckets[person.id] === bucketId) {
      delete state.mergeCanvas.buckets[person.id];
      state.mergeSelected.delete(person.id);
      const card = mergeCardForId(person.id); if (card) applyMergeCardGroup(card, person.id);
      if (!state.mergeSelected.size) state.mergeSelectedBucketId = null;
      saveMergeCanvasState();
    } else if (groupId && state.mergeCanvas.groups[person.id] === groupId) {
      delete state.mergeCanvas.groups[person.id];
      state.mergeSelected.delete(person.id);
      const card = mergeCardForId(person.id); if (card) applyMergeCardGroup(card, person.id);
      if (!state.mergeSelected.size) {
        state.mergeSelectedGroupId = null;
      }
      saveMergeCanvasState();
    } else {
      state.mergeSelected.delete(person.id);
    }
    syncMergeCardSelection(); renderMergeTray();
  }

  function renderMergeTray() {
    const people = selectedMergePeople();
    const bucketId = state.mergeSelectedBucketId;
    const bucketMode = Boolean(bucketId);
    const groupId = state.mergeSelectedGroupId;
    const groupMode = Boolean(groupId);
    const survivor = mergeSurvivor(people);
    const bucketName = bucketId ? cleanName(state.mergeCanvas.bucketNames[bucketId] || "") : "";
    const groupIds = [...collectMergeCollections("groups").keys()].sort((first, second) => first.localeCompare(second));
    const groupNumber = groupMode ? Math.max(1, groupIds.indexOf(groupId) + 1) : null;
    document.querySelector("#merge-selection-kicker").textContent = bucketMode
      ? `BUCKET ${mergeBucketLabel(bucketId)}${bucketName ? ` · ${bucketName}` : ""}`
      : groupMode ? `PERSON GROUP G${groupNumber}` : "LOOSE SELECTION";
    document.querySelector("#merge-operation-panel").hidden = !groupMode;
    document.querySelector("#merge-selected-count").textContent = people.length;
    ui.mergeSelected.replaceChildren();
    if (!people.length) {
      const empty = document.createElement("p"); empty.className = "muted"; empty.textContent = bucketMode
        ? "This mental sorting bucket is empty."
        : groupMode ? "This person group is empty." : "Mark faces, then press G to create a person group.";
      ui.mergeSelected.append(empty);
    } else {
      for (const person of people) {
        const row = document.createElement("div"); row.className = "merge-selected-person";
        if (person.id === survivor?.id && people.length >= 2) row.classList.add("survivor");
        const image = document.createElement("img"); image.src = personThumb(person.id); image.alt = "";
        const text = document.createElement("div");
        const strong = document.createElement("strong");
        strong.textContent = bucketMode
          ? `Bucket ${mergeBucketLabel(bucketId)} member`
          : groupMode ? (person.id === survivor?.id && people.length >= 2 ? "Surviving cluster" : "Person-group member") : "Marked face";
        const small = document.createElement("small"); small.textContent = person.assetCount == null ? "Photo count unavailable" : `${person.assetCount} photos`;
        const remove = document.createElement("button"); remove.type = "button"; remove.textContent = "×";
        remove.title = bucketMode ? "Remove from this canvas bucket" : groupMode ? "Remove from this person group" : "Remove from selection";
        remove.addEventListener("click", () => removeMergeTrayPerson(person));
        text.append(strong, small); row.append(image, text, remove); ui.mergeSelected.append(row);
      }
    }

    const targetBox = document.querySelector("#merge-target");
    targetBox.className = "merge-target muted";
    targetBox.textContent = people.length >= 2
      ? `The largest cluster (${survivor?.assetCount ?? "?"} photos) survives unnamed; ${people.length - 1} ${people.length === 2 ? "cluster is" : "clusters are"} merged into it.`
      : "A person group needs at least two clusters.";
    const canQueue = groupMode && people.length >= 2;
    const queue = document.querySelector("#merge-queue"); queue.disabled = !canQueue;
    queue.textContent = `Queue merge · ${Math.max(0, people.length - 1)} ${people.length === 2 ? "merge" : "merges"}`;
    renderMergeOrganizerLists();
  }

  function moveMergeFocus(card, key) {
    const cards = [...ui.mergeGrid.querySelectorAll(".merge-cluster-card")];
    const current = card.getBoundingClientRect();
    const candidates = cards.filter(candidate => candidate !== card).map(candidate => ({ card: candidate, rect: candidate.getBoundingClientRect() }));
    const directional = candidates.filter(({ rect }) => key === "ArrowLeft" ? rect.right <= current.left + 2 : key === "ArrowRight" ? rect.left >= current.right - 2 : key === "ArrowUp" ? rect.bottom <= current.top + 2 : rect.top >= current.bottom - 2);
    directional.sort((a, b) => {
      const primary = key === "ArrowLeft" ? current.left - a.rect.right : key === "ArrowRight" ? a.rect.left - current.right : key === "ArrowUp" ? current.top - a.rect.bottom : a.rect.top - current.bottom;
      const primaryB = key === "ArrowLeft" ? current.left - b.rect.right : key === "ArrowRight" ? b.rect.left - current.right : key === "ArrowUp" ? current.top - b.rect.bottom : b.rect.top - current.bottom;
      const cross = ["ArrowLeft", "ArrowRight"].includes(key) ? Math.abs(a.rect.top - current.top) : Math.abs(a.rect.left - current.left);
      const crossB = ["ArrowLeft", "ArrowRight"].includes(key) ? Math.abs(b.rect.top - current.top) : Math.abs(b.rect.left - current.left);
      return primary - primaryB || cross - crossB;
    });
    directional[0]?.card.focus();
  }

  function mergeCardForId(personId) {
    return [...ui.mergeGrid.querySelectorAll(".merge-cluster-card")].find(card => card.dataset.personId === personId) || null;
  }

  function ensureMergeCanvasPositions(people) {
    const occupied = Object.values(state.mergeCanvas.positions);
    let slot = 0;
    for (const person of people) {
      if (state.mergeCanvas.positions[person.id]) continue;
      let point;
      do {
        point = { x: 140 + (slot % 32) * 255, y: 140 + Math.floor(slot / 32) * 295 };
        slot += 1;
      } while (occupied.some(other => Math.abs(other.x - point.x) < 225 && Math.abs(other.y - point.y) < 265));
      state.mergeCanvas.positions[person.id] = point; occupied.push(point);
    }
    ensureMergeWorldContains(...occupied); saveMergeCanvasState();
  }

  function positionMergeCard(card, personId) {
    const point = state.mergeCanvas.positions[personId];
    if (point) card.style.transform = `translate(${point.x}px, ${point.y}px)`;
  }

  function applyMergeCanvasScale() {
    const zoom = state.mergeCanvas.zoom;
    ui.mergeGrid.style.transform = `scale(${zoom})`;
    ui.mergeGrid.style.width = `${state.mergeCanvas.worldWidth}px`;
    ui.mergeGrid.style.height = `${state.mergeCanvas.worldHeight}px`;
    ui.mergeSurface.style.width = `${state.mergeCanvas.worldWidth * zoom}px`;
    ui.mergeSurface.style.height = `${state.mergeCanvas.worldHeight * zoom}px`;
  }

  function ensureMergeWorldContains(...points) {
    const valid = points.filter(point => Number.isFinite(point?.x) && Number.isFinite(point?.y));
    if (!valid.length) return;
    const roundUp = value => Math.ceil(value / 2000) * 2000;
    const neededWidth = Math.max(...valid.map(point => point.x + MERGE_CARD_WIDTH + 1500));
    const neededHeight = Math.max(...valid.map(point => point.y + MERGE_CARD_HEIGHT + 1500));
    const width = Math.max(state.mergeCanvas.worldWidth, MERGE_MIN_WORLD_WIDTH, roundUp(neededWidth));
    const height = Math.max(state.mergeCanvas.worldHeight, MERGE_MIN_WORLD_HEIGHT, roundUp(neededHeight));
    if (width === state.mergeCanvas.worldWidth && height === state.mergeCanvas.worldHeight) return;
    state.mergeCanvas.worldWidth = width; state.mergeCanvas.worldHeight = height; applyMergeCanvasScale();
  }

  function setMergeCanvasZoom(nextZoom, clientX = null, clientY = null) {
    const viewport = ui.mergeViewport, oldZoom = state.mergeCanvas.zoom;
    const next = Math.max(MERGE_MIN_ZOOM, Math.min(MERGE_MAX_ZOOM, nextZoom));
    if (Math.abs(next - oldZoom) < .001) return;
    const rect = viewport.getBoundingClientRect();
    const focalX = clientX == null ? viewport.clientWidth / 2 : clientX - rect.left;
    const focalY = clientY == null ? viewport.clientHeight / 2 : clientY - rect.top;
    const worldX = (viewport.scrollLeft + focalX) / oldZoom, worldY = (viewport.scrollTop + focalY) / oldZoom;
    state.mergeCanvas.zoom = next; applyMergeCanvasScale();
    viewport.scrollLeft = worldX * next - focalX; viewport.scrollTop = worldY * next - focalY;
    state.mergeCanvas.scrollLeft = viewport.scrollLeft; state.mergeCanvas.scrollTop = viewport.scrollTop; saveMergeCanvasState();
  }

  function fitMergeCanvas() {
    const points = state.mergePeople.map(person => state.mergeCanvas.positions[person.id]).filter(Boolean);
    if (!points.length) return;
    const minX = Math.min(...points.map(point => point.x)), minY = Math.min(...points.map(point => point.y));
    const maxX = Math.max(...points.map(point => point.x + MERGE_CARD_WIDTH)), maxY = Math.max(...points.map(point => point.y + MERGE_CARD_HEIGHT));
    const padding = 100, width = maxX - minX + padding * 2, height = maxY - minY + padding * 2;
    const zoom = Math.max(MERGE_MIN_ZOOM, Math.min(1.2, Math.min(ui.mergeViewport.clientWidth / width, ui.mergeViewport.clientHeight / height)));
    state.mergeCanvas.zoom = zoom; applyMergeCanvasScale();
    ui.mergeViewport.scrollLeft = Math.max(0, (minX - padding) * zoom);
    ui.mergeViewport.scrollTop = Math.max(0, (minY - padding) * zoom);
    state.mergeCanvas.scrollLeft = ui.mergeViewport.scrollLeft; state.mergeCanvas.scrollTop = ui.mergeViewport.scrollTop; saveMergeCanvasState();
  }

  function focusMergeCollection(people) {
    const points = people.map(person => ({ id: person.id, point: state.mergeCanvas.positions[person.id] })).filter(item => item.point);
    if (!points.length) return;
    const minX = Math.min(...points.map(item => item.point.x));
    const minY = Math.min(...points.map(item => item.point.y));
    const maxX = Math.max(...points.map(item => item.point.x + MERGE_CARD_WIDTH));
    const maxY = Math.max(...points.map(item => item.point.y + MERGE_CARD_HEIGHT));
    const padding = 90;
    const width = Math.max(MERGE_CARD_WIDTH, maxX - minX) + padding * 2;
    const height = Math.max(MERGE_CARD_HEIGHT, maxY - minY) + padding * 2;
    const zoom = Math.max(MERGE_MIN_ZOOM, Math.min(1.15, Math.min(ui.mergeViewport.clientWidth / width, ui.mergeViewport.clientHeight / height)));
    state.mergeCanvas.zoom = zoom; applyMergeCanvasScale();
    const left = Math.max(0, (minX - padding) * zoom);
    const top = Math.max(0, (minY - padding) * zoom);
    state.mergeCanvas.scrollLeft = left; state.mergeCanvas.scrollTop = top; saveMergeCanvasState();
    ui.mergeViewport.scrollTo({ left, top, behavior: "smooth" });

    clearTimeout(state.mergeHighlightTimer);
    ui.mergeGrid.querySelectorAll(".merge-cluster-card.collection-highlight").forEach(card => card.classList.remove("collection-highlight"));
    for (const { id } of points) mergeCardForId(id)?.classList.add("collection-highlight");
    state.mergeHighlightTimer = setTimeout(() => {
      ui.mergeGrid.querySelectorAll(".merge-cluster-card.collection-highlight").forEach(card => card.classList.remove("collection-highlight"));
    }, 2900);
  }

  function focusMergeCard(personId, card = mergeCardForId(personId)) {
    state.mergeFocusedId = personId;
    ui.mergeGrid.querySelectorAll(".merge-cluster-card").forEach(item => item.classList.toggle("focused", item.dataset.personId === personId));
    document.querySelector("#merge-arrange-similar").disabled = !personId;
    card?.focus({ preventScroll: true });
  }

  async function arrangeSimilarNearby() {
    const anchorId = state.mergeFocusedId || (state.mergeSelected.size === 1 ? [...state.mergeSelected][0] : null);
    if (!anchorId || !state.mergeCanvas.positions[anchorId]) { showToast("Focus one cluster first."); return; }
    const button = document.querySelector("#merge-arrange-similar"), limit = Number(document.querySelector("#merge-similar-count").value) || 12;
    button.disabled = true;
    try {
      const ranked = (await api(`/api/similar/${encodeURIComponent(anchorId)}?limit=${Math.min(100, limit * 5)}`)).people;
      const protectedCount = ranked.filter(person => isMergeCardProtected(person.id)).length;
      const people = ranked.filter(person => !isMergeCardProtected(person.id)).slice(0, limit);
      const anchor = state.mergeCanvas.positions[anchorId];
      people.forEach((person, index) => {
        const ring = Math.floor(index / 8), indexInRing = index % 8, ringCount = Math.min(8, people.length - ring * 8);
        const angle = -Math.PI / 2 + indexInRing * Math.PI * 2 / ringCount, radius = 310 + ring * 260;
        state.mergeCanvas.positions[person.id] = {
          x: Math.max(20, anchor.x + Math.cos(angle) * radius),
          y: Math.max(20, anchor.y + Math.sin(angle) * radius),
        };
        const card = mergeCardForId(person.id); if (card) positionMergeCard(card, person.id);
      });
      ensureMergeWorldContains(...people.map(person => state.mergeCanvas.positions[person.id])); saveMergeCanvasState();
      showToast(`Arranged ${people.length} Immich-ranked similar clusters${protectedCount ? `; kept ${protectedCount} bucketed clusters in place` : ""}.`);
    } catch (error) { showToast(`Could not arrange similar clusters: ${error.message}`); }
    finally { button.disabled = !state.mergeFocusedId; }
  }

  function arrangeUngroupedGrid() {
    const grouped = state.mergePeople.filter(person => isMergeCardProtected(person.id));
    const loose = state.mergePeople.filter(person => !isMergeCardProtected(person.id));
    const occupied = grouped.map(person => state.mergeCanvas.positions[person.id]).filter(Boolean);
    let slot = 0;
    for (const person of loose) {
      let point;
      do {
        point = { x: 140 + (slot % 32) * 255, y: 140 + Math.floor(slot / 32) * 295 };
        slot += 1;
      } while (occupied.some(other => Math.abs(other.x - point.x) < 235 && Math.abs(other.y - point.y) < 275));
      state.mergeCanvas.positions[person.id] = point; occupied.push(point);
      const card = mergeCardForId(person.id); if (card) positionMergeCard(card, person.id);
    }
    ensureMergeWorldContains(...occupied); saveMergeCanvasState();
    showToast(`Returned ${loose.length} unbucketed clusters to the grid; ${grouped.length} bucketed clusters stayed in place.`);
  }

  function packMergeSelection() {
    const ids = [...state.mergeSelected].filter(id => state.mergeCanvas.positions[id]);
    if (ids.length < 2) { showToast("Select at least two faces to pack them."); return; }
    const centers = ids.map(id => state.mergeCanvas.positions[id]);
    const centerX = centers.reduce((sum, point) => sum + point.x + MERGE_CARD_WIDTH / 2, 0) / ids.length;
    const centerY = centers.reduce((sum, point) => sum + point.y + MERGE_CARD_HEIGHT / 2, 0) / ids.length;
    const columns = Math.ceil(Math.sqrt(ids.length)), rows = Math.ceil(ids.length / columns);
    const stepX = MERGE_CARD_WIDTH + 24, stepY = MERGE_CARD_HEIGHT + 24;
    const startX = Math.max(20, centerX - (columns * stepX - 24) / 2);
    const startY = Math.max(20, centerY - (rows * stepY - 24) / 2);
    ids.forEach((id, index) => {
      state.mergeCanvas.positions[id] = { x: startX + (index % columns) * stepX, y: startY + Math.floor(index / columns) * stepY };
      const card = mergeCardForId(id); if (card) positionMergeCard(card, id);
    });
    ensureMergeWorldContains(...ids.map(id => state.mergeCanvas.positions[id])); saveMergeCanvasState();
    showToast(`Packed ${ids.length} selected faces into a compact grid.`);
  }

  function animationFrame() {
    return new Promise(resolve => requestAnimationFrame(resolve));
  }

  function resolveMergeCardOverlaps(movingIds, desiredById, graphEdges) {
    const gap = 24, stepX = MERGE_CARD_WIDTH + gap, stepY = MERGE_CARD_HEIGHT + gap;
    const buckets = new Map(), rectangles = [];
    const bucketKeys = rectangle => {
      const left = Math.floor((rectangle.x - gap) / stepX), right = Math.floor((rectangle.x + MERGE_CARD_WIDTH + gap) / stepX);
      const top = Math.floor((rectangle.y - gap) / stepY), bottom = Math.floor((rectangle.y + MERGE_CARD_HEIGHT + gap) / stepY);
      const keys = [];
      for (let column = left; column <= right; column += 1) for (let row = top; row <= bottom; row += 1) keys.push(`${column}:${row}`);
      return keys;
    };
    const add = rectangle => {
      const index = rectangles.push(rectangle) - 1;
      for (const key of bucketKeys(rectangle)) {
        if (!buckets.has(key)) buckets.set(key, []);
        buckets.get(key).push(index);
      }
    };
    const collides = candidate => {
      const candidates = new Set(bucketKeys(candidate).flatMap(key => buckets.get(key) || []));
      for (const index of candidates) {
        const other = rectangles[index];
        if (candidate.x < other.x + MERGE_CARD_WIDTH + gap && candidate.x + MERGE_CARD_WIDTH + gap > other.x &&
            candidate.y < other.y + MERGE_CARD_HEIGHT + gap && candidate.y + MERGE_CARD_HEIGHT + gap > other.y) return true;
      }
      return false;
    };
    for (const person of state.mergePeople) {
      if (!isMergeCardProtected(person.id)) continue;
      const point = state.mergeCanvas.positions[person.id];
      if (point) add({ id: person.id, x: point.x, y: point.y });
    }
    const degree = new Map(movingIds.map(id => [id, 0]));
    for (const edge of graphEdges) {
      const weight = Number(edge.weight) || 0;
      if (degree.has(edge.source)) degree.set(edge.source, degree.get(edge.source) + weight);
      if (degree.has(edge.target)) degree.set(edge.target, degree.get(edge.target) + weight);
    }
    const ordered = [...movingIds].sort((first, second) => degree.get(second) - degree.get(first) || first.localeCompare(second));
    const resolved = new Map();
    for (const id of ordered) {
      const desired = desiredById.get(id);
      let chosen = { id, x: Math.max(20, desired.x), y: Math.max(20, desired.y) };
      if (collides(chosen)) {
        let found = null;
        for (let ring = 1; ring <= 80 && !found; ring += 1) {
          const candidates = [];
          for (let offset = -ring; offset <= ring; offset += 1) {
            candidates.push({ id, x: desired.x + offset * stepX, y: desired.y - ring * stepY });
            candidates.push({ id, x: desired.x + offset * stepX, y: desired.y + ring * stepY });
          }
          for (let offset = -ring + 1; offset < ring; offset += 1) {
            candidates.push({ id, x: desired.x - ring * stepX, y: desired.y + offset * stepY });
            candidates.push({ id, x: desired.x + ring * stepX, y: desired.y + offset * stepY });
          }
          candidates.sort((first, second) => (first.x - desired.x) ** 2 + (first.y - desired.y) ** 2 - ((second.x - desired.x) ** 2 + (second.y - desired.y) ** 2));
          found = candidates.find(candidate => candidate.x >= 20 && candidate.y >= 20 && !collides(candidate)) || null;
        }
        if (found) chosen = found;
        else chosen = { id, x: 20, y: Math.max(20, ...rectangles.map(rectangle => rectangle.y + MERGE_CARD_HEIGHT + gap)) };
      }
      add(chosen); resolved.set(id, { x: chosen.x, y: chosen.y });
    }
    return resolved;
  }

  async function arrangeGlobalSimilarity() {
    const button = document.querySelector("#merge-similarity-map");
    button.disabled = true; button.setAttribute("aria-busy", "true"); button.textContent = "Reading Immich ranks…";
    try {
      const graph = await api("/api/similarity-graph?neighbors=8");
      const available = new Set(state.mergePeople.map(person => person.id));
      const movingIds = graph.nodes.filter(id => available.has(id) && !isMergeCardProtected(id));
      if (movingIds.length < 2) { showToast("At least two unbucketed clusters are needed for a similarity map."); return; }

      const movingIndex = new Map(movingIds.map((id, index) => [id, index]));
      const fixed = new Map(state.mergePeople
        .filter(person => isMergeCardProtected(person.id) && state.mergeCanvas.positions[person.id])
        .map(person => {
          const point = state.mergeCanvas.positions[person.id];
          return [person.id, { x: point.x + MERGE_CARD_WIDTH / 2, y: point.y + MERGE_CARD_HEIGHT / 2 }];
        }));
      const count = movingIds.length, side = Math.max(6000, Math.sqrt(count) * 460);
      const centerX = 300 + side / 2, centerY = 300 + side / 2;
      const x = new Float64Array(count), y = new Float64Array(count);
      const goldenAngle = Math.PI * (3 - Math.sqrt(5));
      movingIds.forEach((id, index) => {
        let hash = 0; for (const character of id) hash = ((hash << 5) - hash + character.charCodeAt(0)) | 0;
        const radius = side * .44 * Math.sqrt((index + .5) / count), angle = index * goldenAngle + (Math.abs(hash) % 1000) / 1000;
        x[index] = centerX + Math.cos(angle) * radius; y[index] = centerY + Math.sin(angle) * radius;
      });
      const edges = graph.edges.map(edge => ({
        a: movingIndex.has(edge.source) ? movingIndex.get(edge.source) : null,
        b: movingIndex.has(edge.target) ? movingIndex.get(edge.target) : null,
        af: fixed.get(edge.source) || null, bf: fixed.get(edge.target) || null,
        strength: (.28 + Math.min(2, Number(edge.weight) || 0) * .42) * (edge.mutual ? 1.25 : 1),
      })).filter(edge => (edge.a != null || edge.af) && (edge.b != null || edge.bf) && !(edge.a == null && edge.b == null));
      const fixedPoints = [...fixed.values()], k = Math.max(330, Math.min(520, side / Math.sqrt(count)));
      const dx = new Float64Array(count), dy = new Float64Array(count), iterations = count > 1000 ? 100 : 150;
      for (let iteration = 0; iteration < iterations; iteration += 1) {
        dx.fill(0); dy.fill(0);
        for (let first = 0; first < count; first += 1) {
          for (let second = first + 1; second < count; second += 1) {
            let deltaX = x[second] - x[first], deltaY = y[second] - y[first];
            let distanceSquared = deltaX * deltaX + deltaY * deltaY;
            if (distanceSquared < 1) { deltaX = 1; deltaY = .5; distanceSquared = 1.25; }
            const force = k * k / distanceSquared;
            dx[first] -= deltaX * force; dy[first] -= deltaY * force;
            dx[second] += deltaX * force; dy[second] += deltaY * force;
          }
          for (const point of fixedPoints) {
            let deltaX = point.x - x[first], deltaY = point.y - y[first];
            let distanceSquared = Math.max(1, deltaX * deltaX + deltaY * deltaY);
            const force = k * k * 1.4 / distanceSquared;
            dx[first] -= deltaX * force; dy[first] -= deltaY * force;
          }
        }
        for (const edge of edges) {
          const ax = edge.a == null ? edge.af.x : x[edge.a], ay = edge.a == null ? edge.af.y : y[edge.a];
          const bx = edge.b == null ? edge.bf.x : x[edge.b], by = edge.b == null ? edge.bf.y : y[edge.b];
          const deltaX = bx - ax, deltaY = by - ay, distance = Math.max(1, Math.hypot(deltaX, deltaY));
          const force = distance * distance / k * edge.strength;
          const pullX = deltaX / distance * force, pullY = deltaY / distance * force;
          if (edge.a != null) { dx[edge.a] += pullX; dy[edge.a] += pullY; }
          if (edge.b != null) { dx[edge.b] -= pullX; dy[edge.b] -= pullY; }
        }
        const temperature = Math.max(4, side * .07 * (1 - iteration / iterations));
        for (let index = 0; index < count; index += 1) {
          dx[index] += (centerX - x[index]) * .018; dy[index] += (centerY - y[index]) * .018;
          const distance = Math.max(1, Math.hypot(dx[index], dy[index])), step = Math.min(distance, temperature);
          x[index] = Math.max(MERGE_CARD_WIDTH / 2 + 20, x[index] + dx[index] / distance * step);
          y[index] = Math.max(MERGE_CARD_HEIGHT / 2 + 20, y[index] + dy[index] / distance * step);
        }
        if (iteration % 10 === 9) {
          button.textContent = `Laying out faces… ${Math.round((iteration + 1) / iterations * 100)}%`;
          await animationFrame();
        }
      }
      button.textContent = "Removing overlaps…"; await animationFrame();
      const desiredById = new Map(movingIds.map((id, index) => [id, {
        x: x[index] - MERGE_CARD_WIDTH / 2, y: y[index] - MERGE_CARD_HEIGHT / 2,
      }]));
      const resolved = resolveMergeCardOverlaps(movingIds, desiredById, graph.edges);
      movingIds.forEach(id => {
        state.mergeCanvas.positions[id] = resolved.get(id);
        const card = mergeCardForId(id); if (card) positionMergeCard(card, id);
      });
      ensureMergeWorldContains(...movingIds.map(id => state.mergeCanvas.positions[id])); saveMergeCanvasState();
      showToast(`Mapped ${movingIds.length} unbucketed clusters without overlaps; canvas buckets and the current camera stayed fixed${graph.failed ? ` (${graph.failed} lookups failed)` : ""}. Press F to fit everything.`);
    } catch (error) { showToast(`Could not build similarity map: ${error.message}`); }
    finally { button.disabled = false; button.removeAttribute("aria-busy"); button.textContent = "Similarity map [M]"; }
  }

  function createMergeClusterCard(person) {
    const card = document.createElement("article"); card.className = "merge-cluster-card"; card.dataset.personId = person.id; card.tabIndex = 0; card.setAttribute("role", "option");
    const portrait = document.createElement("div"); portrait.className = "portrait-wrap";
    const image = document.createElement("img"); image.className = "portrait person-thumb"; image.src = personThumb(person.id); image.alt = "Unnamed cluster"; image.draggable = false;
    const canvas = document.createElement("canvas"); canvas.className = "portrait sample-thumb"; canvas.width = 480; canvas.height = 480; canvas.hidden = true;
    const previous = document.createElement("button"); previous.className = "carousel-button previous"; previous.type = "button"; previous.tabIndex = -1; previous.textContent = "‹"; previous.setAttribute("aria-label", "Previous face");
    const next = document.createElement("button"); next.className = "carousel-button next"; next.type = "button"; next.tabIndex = -1; next.textContent = "›"; next.setAttribute("aria-label", "Next face");
    const counter = document.createElement("span"); counter.className = "carousel-count";
    portrait.append(image, canvas, previous, next, counter);
    const meta = document.createElement("div"); meta.className = "merge-cluster-meta";
    const count = document.createElement("span"); count.textContent = person.assetCount == null ? "? photos" : `${person.assetCount} photos`;
    const groupMark = document.createElement("span"); groupMark.className = "merge-group-mark"; groupMark.textContent = "G"; groupMark.hidden = true;
    const bucketMark = document.createElement("span"); bucketMark.className = "merge-bucket-mark"; bucketMark.hidden = true;
    const mark = document.createElement("span"); mark.className = "merge-selection-mark";
    meta.append(count, groupMark, bucketMark, mark); card.append(portrait, meta); applyMergeCardGroup(card, person.id);
    const model = { person, image, canvas, counter, slides: [{ type: "person", assetId: null }], slideIndex: 0, samplesLoaded: false };
    previous.addEventListener("pointerdown", event => event.stopPropagation()); previous.addEventListener("click", event => { event.stopPropagation(); moveSlide(model, -1); });
    next.addEventListener("pointerdown", event => event.stopPropagation()); next.addEventListener("click", event => { event.stopPropagation(); moveSlide(model, 1); });
    card.addEventListener("pointerenter", () => { state.mergeHoveredId = person.id; });
    card.addEventListener("pointerleave", () => { if (state.mergeHoveredId === person.id) state.mergeHoveredId = null; });
    card.addEventListener("focus", () => focusMergeCard(person.id, null));
    card.addEventListener("pointerdown", event => {
      if (event.button !== 0 || event.target.closest("button")) return;
      event.stopPropagation(); focusMergeCard(person.id, null); card.setPointerCapture(event.pointerId);
      const originX = event.clientX, originY = event.clientY;
      const movingIds = state.mergeSelected.has(person.id) && state.mergeSelected.size > 1 ? [...state.mergeSelected] : [person.id];
      const starts = new Map(movingIds.map(id => [id, { ...state.mergeCanvas.positions[id] }]));
      let dragged = false;
      const move = moveEvent => {
        const dx = (moveEvent.clientX - originX) / state.mergeCanvas.zoom, dy = (moveEvent.clientY - originY) / state.mergeCanvas.zoom;
        if (!dragged && Math.hypot(dx, dy) < 4) return;
        dragged = true; card.classList.add("dragging");
        for (const id of movingIds) {
          const start = starts.get(id);
          state.mergeCanvas.positions[id] = {
            x: Math.max(0, start.x + dx),
            y: Math.max(0, start.y + dy),
          };
          const movingCard = mergeCardForId(id); if (movingCard) positionMergeCard(movingCard, id);
        }
      };
      const finish = () => {
        card.removeEventListener("pointermove", move); card.removeEventListener("pointerup", finish); card.removeEventListener("pointercancel", finish); card.classList.remove("dragging");
        if (dragged) { ensureMergeWorldContains(...movingIds.map(id => state.mergeCanvas.positions[id])); saveMergeCanvasState(); }
        else toggleMergePerson(person);
      };
      card.addEventListener("pointermove", move); card.addEventListener("pointerup", finish); card.addEventListener("pointercancel", finish);
    });
    card.addEventListener("keydown", async event => {
      if (["PageUp", "PageDown"].includes(event.key)) { event.preventDefault(); await moveSlide(model, event.key === "PageDown" ? 1 : -1); return; }
      if (event.key === " " || event.key === "Enter") { event.preventDefault(); toggleMergePerson(person); return; }
      if (["ArrowLeft", "ArrowRight", "ArrowUp", "ArrowDown"].includes(event.key)) { event.preventDefault(); moveMergeFocus(card, event.key); }
    });
    positionMergeCard(card, person.id); showSlide(model); return card;
  }

  async function renderMergeWorkbench(focusFirst = false) {
    const people = (await api(`/api/people?kind=unnamed&sort=${encodeURIComponent(state.sort)}`)).people;
    state.mergePeople = people;
    const available = new Set(people.map(person => person.id));
    for (const id of state.mergeSelected) if (!available.has(id)) state.mergeSelected.delete(id);
    if (state.mergeSelectedGroupId && ![...state.mergeSelected].some(id => state.mergeCanvas.groups[id] === state.mergeSelectedGroupId)) {
      state.mergeSelectedGroupId = null;
    }
    if (state.mergeSelectedBucketId && ![...state.mergeSelected].some(id => state.mergeCanvas.buckets[id] === state.mergeSelectedBucketId)) state.mergeSelectedBucketId = null;
    if (state.mergeFocusedId && !available.has(state.mergeFocusedId)) state.mergeFocusedId = null;
    if (state.mergeHoveredId && !available.has(state.mergeHoveredId)) state.mergeHoveredId = null;
    ensureMergeCanvasPositions(people); applyMergeCanvasScale();
    ui.mergeGrid.replaceChildren(...people.map(createMergeClusterCard));
    document.querySelector("#merge-empty").hidden = people.length > 0;
    syncMergeCardSelection(); renderMergeTray();
    requestAnimationFrame(() => {
      if (!state.mergeCanvasRestored) {
        ui.mergeViewport.scrollLeft = state.mergeCanvas.scrollLeft; ui.mergeViewport.scrollTop = state.mergeCanvas.scrollTop; state.mergeCanvasRestored = true;
      }
      if (state.mergeFocusedId) focusMergeCard(state.mergeFocusedId);
      else if (focusFirst && people.length) focusMergeCard(people[0].id);
    });
  }

  function clearMergeGroup() {
    state.mergeSelected.clear();
    clearMergeSelectionContext();
    syncMergeCardSelection(); renderMergeTray();
  }

  async function queueMergeGroup() {
    if (!state.mergeSelectedGroupId) { showToast("Select a person group before merging."); return; }
    const people = selectedMergePeople();
    if (people.length < 2) return;
    const survivor = mergeSurvivor(people);
    const button = document.querySelector("#merge-queue"); button.disabled = true;
    try {
      const result = await api("/api/queue/group", { method: "POST", body: { personIds: people.map(person => person.id), survivorPersonId: survivor.id } });
      for (const person of people) delete state.mergeCanvas.groups[person.id];
      saveMergeCanvasState();
      showToast(`Queued ${result.merged} ${result.merged === 1 ? "merge" : "merges"}; the surviving person remains unnamed.`);
      clearMergeGroup(); await renderMergeWorkbench(); await refreshSummary();
    } catch (error) {
      showToast(error.message); renderMergeTray();
    }
  }

  function currentMergeReviewGroup() {
    return state.mergeReviewGroups[state.mergeReviewIndex] || null;
  }

  function setMergeReviewStatus(message = "") {
    const status = document.querySelector("#group-review-status");
    status.textContent = message; status.hidden = !message;
  }

  function renderMergeGroupReview() {
    const entry = currentMergeReviewGroup();
    if (!entry) { finishMergeGroupReview(); return; }
    const survivor = mergeSurvivor(entry.people);
    document.querySelector("#group-review-progress").textContent = `Group ${state.mergeReviewIndex + 1} of ${state.mergeReviewGroups.length} · ${entry.people.length} clusters`;
    const gallery = document.querySelector("#group-review-faces"); gallery.replaceChildren();
    for (const person of entry.people) {
      const item = document.createElement("div"); item.className = "group-review-face";
      if (person.id === survivor?.id) item.classList.add("survivor");
      const image = document.createElement("img"); image.src = personThumb(person.id); image.alt = "Face cluster";
      const label = document.createElement("span");
      label.textContent = person.id === survivor?.id
        ? `Survives · ${person.assetCount ?? "?"} photos`
        : `${person.assetCount ?? "?"} photos`;
      item.append(image, label); gallery.append(item);
    }
    document.querySelector("#group-review-confirm").disabled = !state.mergeReviewSupported || state.mergeReviewBusy || entry.people.length < 2;
    document.querySelector("#group-review-skip").disabled = state.mergeReviewBusy;
    requestAnimationFrame(() => document.querySelector("#group-review-confirm").focus());
  }

  function openMergeGroupReview() {
    const groups = [...collectMergeCollections("groups").entries()]
      .sort(([first], [second]) => first.localeCompare(second))
      .map(([id, people]) => ({ id, people: [...people] }));
    if (!groups.length) { showToast("There are no person groups to review."); return; }
    state.mergeReviewGroups = groups; state.mergeReviewIndex = 0; state.mergeReviewBusy = false;
    state.mergeReviewQueued = 0; state.mergeReviewSkipped = 0;
    state.mergeReviewSupported = state.summary?.capabilities?.unnamedGroupMerge === true;
    document.querySelector("#group-review-dialog").showModal(); renderMergeGroupReview();
    setMergeReviewStatus(state.mergeReviewSupported ? "" : "The running People Workbench server is still using the old merge API. Restart people_workbench.py, then reload this page before confirming groups.");
  }

  async function finishMergeGroupReview(cancelled = false) {
    const dialog = document.querySelector("#group-review-dialog");
    if (dialog.open) dialog.close();
    const queued = state.mergeReviewQueued, skipped = state.mergeReviewSkipped;
    state.mergeReviewGroups = []; state.mergeReviewIndex = 0; state.mergeReviewBusy = false;
    setMergeReviewStatus();
    if (queued) { await renderMergeWorkbench(); await refreshSummary(); }
    showToast(cancelled ? `Review closed · ${queued} queued, ${skipped} skipped.` : `Group review complete · ${queued} queued, ${skipped} skipped.`);
  }

  function skipMergeReviewGroup() {
    if (state.mergeReviewBusy || !currentMergeReviewGroup()) return;
    state.mergeReviewSkipped += 1; state.mergeReviewIndex += 1; renderMergeGroupReview();
  }

  async function confirmMergeReviewGroup() {
    const entry = currentMergeReviewGroup();
    if (!entry || entry.people.length < 2 || state.mergeReviewBusy || !state.mergeReviewSupported) return;
    state.mergeReviewBusy = true; renderMergeGroupReview();
    setMergeReviewStatus();
    const survivor = mergeSurvivor(entry.people);
    try {
      await api("/api/queue/group", { method: "POST", body: { personIds: entry.people.map(person => person.id), survivorPersonId: survivor.id } });
      for (const person of entry.people) delete state.mergeCanvas.groups[person.id];
      saveMergeCanvasState();
      state.mergeReviewQueued += 1; state.mergeReviewIndex += 1;
    } catch (error) {
      const message = /name is required/i.test(error.message)
        ? "The running server still expects a name. Restart people_workbench.py, reload the page, and try again."
        : `Could not queue this group: ${error.message}`;
      setMergeReviewStatus(message); showToast(message);
    } finally {
      state.mergeReviewBusy = false; renderMergeGroupReview();
    }
  }

  function mergeMarqueeHits(rect) {
    return [...ui.mergeGrid.querySelectorAll(".merge-cluster-card")].filter(card => {
      const cardRect = card.getBoundingClientRect();
      const centerX = cardRect.left + cardRect.width / 2, centerY = cardRect.top + cardRect.height / 2;
      return centerX >= rect.left && centerX <= rect.right && centerY >= rect.top && centerY <= rect.bottom;
    });
  }

  function startMergeMarquee(event) {
    const viewport = ui.mergeViewport, bounds = viewport.getBoundingClientRect();
    const clampX = value => Math.max(bounds.left, Math.min(bounds.right, value));
    const clampY = value => Math.max(bounds.top, Math.min(bounds.bottom, value));
    const startX = clampX(event.clientX), startY = clampY(event.clientY);
    const additive = event.shiftKey || event.ctrlKey || event.metaKey;
    const box = document.createElement("div"); box.className = "merge-selection-box"; box.hidden = true; document.body.append(box);
    let currentRect = { left: startX, right: startX, top: startY, bottom: startY }, moved = false;
    viewport.setPointerCapture(event.pointerId); viewport.classList.add("area-selecting");
    const move = moveEvent => {
      const endX = clampX(moveEvent.clientX), endY = clampY(moveEvent.clientY);
      moved = moved || Math.hypot(endX - startX, endY - startY) >= 4;
      currentRect = { left: Math.min(startX, endX), right: Math.max(startX, endX), top: Math.min(startY, endY), bottom: Math.max(startY, endY) };
      box.hidden = !moved; box.style.left = `${currentRect.left}px`; box.style.top = `${currentRect.top}px`;
      box.style.width = `${currentRect.right - currentRect.left}px`; box.style.height = `${currentRect.bottom - currentRect.top}px`;
      const hits = new Set(mergeMarqueeHits(currentRect));
      ui.mergeGrid.querySelectorAll(".merge-cluster-card").forEach(card => card.classList.toggle("marquee-hit", hits.has(card)));
    };
    const cleanup = () => {
      viewport.removeEventListener("pointermove", move); viewport.removeEventListener("pointerup", finish); viewport.removeEventListener("pointercancel", cancel);
      viewport.classList.remove("area-selecting"); box.remove();
      ui.mergeGrid.querySelectorAll(".merge-cluster-card.marquee-hit").forEach(card => card.classList.remove("marquee-hit"));
    };
    const finish = () => {
      const hits = moved ? mergeMarqueeHits(currentRect) : [];
      cleanup();
      clearMergeSelectionContext();
      if (!additive) state.mergeSelected.clear();
      for (const card of hits) state.mergeSelected.add(card.dataset.personId);
      syncMergeCardSelection(); renderMergeTray();
    };
    const cancel = () => cleanup();
    viewport.addEventListener("pointermove", move); viewport.addEventListener("pointerup", finish); viewport.addEventListener("pointercancel", cancel);
  }

  function initializeMergeCanvas() {
    const viewport = ui.mergeViewport;
    viewport.addEventListener("pointerdown", event => {
      if (event.button !== 0 || event.target.closest(".merge-cluster-card")) return;
      if (state.mergeAreaSelect || event.shiftKey) { event.preventDefault(); startMergeMarquee(event); return; }
      viewport.setPointerCapture(event.pointerId); viewport.classList.add("panning");
      const startX = event.clientX, startY = event.clientY, startLeft = viewport.scrollLeft, startTop = viewport.scrollTop;
      let moved = false;
      const move = moveEvent => {
        const dx = moveEvent.clientX - startX, dy = moveEvent.clientY - startY;
        moved = moved || Math.hypot(dx, dy) >= 4;
        if (!moved) return;
        viewport.scrollLeft = startLeft - dx; viewport.scrollTop = startTop - dy;
      };
      const cleanup = () => {
        viewport.removeEventListener("pointermove", move); viewport.removeEventListener("pointerup", finish); viewport.removeEventListener("pointercancel", cancel); viewport.classList.remove("panning");
      };
      const finish = () => {
        cleanup();
        if (moved) {
          state.mergeCanvas.scrollLeft = viewport.scrollLeft; state.mergeCanvas.scrollTop = viewport.scrollTop; saveMergeCanvasState();
        } else if (state.mergeSelected.size) clearMergeGroup();
      };
      const cancel = () => cleanup();
      viewport.addEventListener("pointermove", move); viewport.addEventListener("pointerup", finish); viewport.addEventListener("pointercancel", cancel);
    });
    viewport.addEventListener("wheel", event => {
      if (!event.ctrlKey) return;
      event.preventDefault(); setMergeCanvasZoom(state.mergeCanvas.zoom * (event.deltaY > 0 ? .9 : 1.1), event.clientX, event.clientY);
    }, { passive: false });
    viewport.addEventListener("scroll", () => {
      state.mergeCanvas.scrollLeft = viewport.scrollLeft; state.mergeCanvas.scrollTop = viewport.scrollTop; saveMergeCanvasState();
    });
    document.querySelector("#merge-zoom-out").addEventListener("click", () => setMergeCanvasZoom(state.mergeCanvas.zoom / 1.15));
    document.querySelector("#merge-zoom-in").addEventListener("click", () => setMergeCanvasZoom(state.mergeCanvas.zoom * 1.15));
    document.querySelector("#merge-zoom-reset").addEventListener("click", fitMergeCanvas);
    document.querySelector("#merge-arrange-similar").addEventListener("click", () => arrangeSimilarNearby());
    document.querySelector("#merge-similarity-map").addEventListener("click", () => arrangeGlobalSimilarity());
    document.querySelector("#merge-toggle-tray").addEventListener("click", () => toggleMergeTray());
    document.querySelector("#merge-zen").addEventListener("click", () => toggleMergeZen());
    document.querySelector("#merge-help").addEventListener("click", () => document.querySelector("#merge-help-dialog").showModal());
    document.querySelector("#merge-groups-tab").addEventListener("click", () => setMergeTrayTab("groups"));
    document.querySelector("#merge-buckets-tab").addEventListener("click", () => setMergeTrayTab("buckets"));
    document.addEventListener("keydown", event => {
      const editing = event.target instanceof Element && Boolean(event.target.closest("input, textarea, select, [contenteditable='true']"));
      if (event.key === "Escape" && !editing && !document.querySelector("dialog[open]")) {
        if (state.view === "merge" && state.mergeSelected.size) { event.preventDefault(); clearMergeGroup(); return; }
        if (state.mergeZen) { toggleMergeZen(false); return; }
      }
      if (state.view !== "merge" || document.querySelector("dialog[open]") || event.ctrlKey || event.metaKey || event.altKey) return;
      if (editing) return;
      const key = event.key.toLocaleLowerCase();
      if (/^[0-9]$/.test(key)) {
        if (event.repeat) return;
        event.preventDefault(); assignMergeNumberGroup(Number(key)); return;
      }
      if (key === "?" && !event.repeat) { event.preventDefault(); document.querySelector("#merge-help-dialog").showModal(); return; }
      const directAction = { a: toggleMergeAreaSelect, g: makeMergeCanvasGroup, u: ungroupMergeSelection, p: packMergeSelection, l: arrangeUngroupedGrid, v: openMergeGroupReview }[key];
      if (directAction) {
        if (event.repeat) return;
        event.preventDefault(); directAction(); return;
      }
      const selector = {
        r: "#merge-arrange-similar", m: "#merge-similarity-map",
        f: "#merge-zoom-reset", h: "#merge-toggle-tray",
        z: "#merge-zen", c: "#merge-clear", "-": "#merge-zoom-out", "+": "#merge-zoom-in", "=": "#merge-zoom-in",
      }[key];
      if (!selector || (event.repeat && !["-", "+", "="].includes(key))) return;
      const button = document.querySelector(selector);
      if (!button || button.disabled) return;
      event.preventDefault(); button.click();
    });
  }

  function toggleMergeAreaSelect(force = null) {
    const explicit = typeof force === "boolean" ? force : null;
    state.mergeAreaSelect = explicit == null ? !state.mergeAreaSelect : explicit;
    ui.mergeViewport.classList.toggle("area-select-mode", state.mergeAreaSelect);
    showToast(state.mergeAreaSelect ? "Area-select mode on." : "Area-select mode off.");
  }

  function toggleMergeTray(force = null) {
    const explicit = typeof force === "boolean" ? force : null;
    state.mergeTrayHidden = explicit == null ? !state.mergeTrayHidden : explicit;
    document.body.classList.toggle("merge-tray-hidden", state.mergeTrayHidden);
    const button = document.querySelector("#merge-toggle-tray");
    button.textContent = state.mergeTrayHidden ? "Show tray [H]" : "Hide tray [H]";
    button.setAttribute("aria-pressed", String(state.mergeTrayHidden));
    requestAnimationFrame(applyMergeCanvasScale);
  }

  function toggleMergeZen(force = null) {
    const explicit = typeof force === "boolean" ? force : null;
    state.mergeZen = explicit == null ? !state.mergeZen : explicit;
    document.body.classList.toggle("merge-zen", state.mergeZen);
    const button = document.querySelector("#merge-zen");
    button.textContent = state.mergeZen ? "Exit Zen [Z]" : "Zen [Z]";
    button.setAttribute("aria-pressed", String(state.mergeZen));
    requestAnimationFrame(applyMergeCanvasScale);
  }

  return { renderMergeWorkbench, clearMergeGroup, queueMergeGroup, openMergeGroupReview, confirmMergeReviewGroup, skipMergeReviewGroup, finishMergeGroupReview, initializeMergeCanvas };
}
