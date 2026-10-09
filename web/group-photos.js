// Copyright (C) 2026 Serkan Bekdemir
// SPDX-License-Identifier: AGPL-3.0-only

export function createGroupPhotos({ api, assetThumb, cleanName, drawFace, loadNames, matchingNames, openDuplicate, refreshSummary, showToast, state }) {
  const $ = selector => document.querySelector(selector);
  let photo = null, active = 0, drafts = new Map(), matches = [], selectedIndex = -1;
  let pollTimer = null, saving = Promise.resolve(), busy = false;
  const unsavedDrafts = new Map();
  const input = $("#group-name"), list = $("#group-suggestions"), ghost = $("#group-ghost");
  const submitButton = $("#group-advance"), markerLayer = $("#group-markers");
  const markerVisibilityKey = "immichPeopleWorkbench.groupMarkersVisible.v1";
  const ignoreLabel = "Ignore this cluster";
  let markersVisible = true;
  try { markersVisible = localStorage.getItem(markerVisibilityKey) !== "false"; } catch { /* Keep the current session setting. */ }
  function activeFace() { return photo?.faces[active]; }
  function activeDraft() { return drafts.get(activeFace()?.personId) || { name: "", targetPersonId: null, operation: "rename" }; }
  function draftLabel(draft) { return draft.operation === "hide" ? ignoreLabel : draft.name; }
  function applyMarkerVisibility() {
    markerLayer.hidden = !markersVisible;
  }
  function toggleMarkers() {
    markersVisible = !markersVisible;
    applyMarkerVisibility();
    try { localStorage.setItem(markerVisibilityKey, String(markersVisible)); } catch { /* Keep the current session setting. */ }
    if (!markersVisible && document.activeElement?.classList.contains("group-marker")) submitButton.focus();
  }
  function setProgress(data) {
    const scan = data.scan;
    $("#group-progress").textContent = `${scan.indexed} eligible photos indexed${scan.provisional ? " so far" : ""} · ${scan.examined} photos checked from ${scan.discovered} found across ${scan.sources} unnamed clusters${scan.scanning ? " · scanning…" : ""} · ${data.deferred} deferred`;
    $("#group-scan-error").hidden = !scan.failures;
  }

  function checkpointText(data) {
    const scan = data.scan;
    if (data.deferred) return `${data.deferred} photos were deferred. Start a deliberate new pass when ready.`;
    if (scan.indexed) return "Every eligible photo has been shown or completed. Review Pending before Sync.";
    if (!scan.sources) return "There are no visible unnamed clusters to name. Unassigned faces cannot be named here, and names already in Pending are excluded until reviewed or returned.";
    if (!scan.discovered) return "Immich returned no photos linked to the available unnamed clusters. Check that Clusters shows faces, then retry the scan.";
    if (scan.filtered === scan.discovered) return `All ${scan.discovered} linked assets were excluded because they are videos, trashed, hidden, locked, or archived while Include archived photos is off.`;
    if (scan.belowMinimum) return `${scan.belowMinimum} photos had fewer than ${data.settings.minFaces} detected faces. Try lowering Minimum detected faces to 2 to see smaller groups.`;
    if (scan.withoutActionable) return `${scan.withoutActionable} photos had no currently nameable cluster. Names already in Pending are excluded.`;
    return "No photos met the current filters. Check Include archived photos or lower Minimum detected faces.";
  }

  async function persistDraft(key, snapshot) {
    await api("/api/group-photos/draft", { method: "POST", body: snapshot });
    if (unsavedDrafts.get(key) === snapshot) unsavedDrafts.delete(key);
  }

  function saveDraft(personId, draft) {
    if (!personId || !photo || busy) return;
    drafts.set(personId, draft);
    const snapshot = Object.freeze({ assetId: photo.id, personId, name: draft.name,
      targetPersonId: draft.targetPersonId, operation: draft.operation || "rename" });
    const key = `${photo.id}:${personId}`;
    unsavedDrafts.set(key, snapshot);
    saving = saving.catch(() => {}).then(() => persistDraft(key, snapshot));
    saving.catch(error => showToast(`Could not save the name draft: ${error.message} Submit Names will retry saving it.`));
  }

  async function retryDrafts(assetId) {
    await saving.catch(() => {});
    // Retry the exact latest unsaved local snapshots before any advance. Failed
    // attempts retain their draft; a lost response is safe to save again locally.
    for (const [key, snapshot] of [...unsavedDrafts]) {
      if (snapshot.assetId === assetId) await persistDraft(key, snapshot);
    }
  }

  function updateSuggestions() {
    list.replaceChildren(); ghost.replaceChildren();
    const face = activeFace();
    input.classList.toggle("ignore-selected", face?.status === "actionable" && activeDraft().operation === "hide");
    if (!face || face.status !== "actionable") return;
    const draft = activeDraft();
    $("#group-face-note").textContent = draft.operation === "hide" ? "Ignore this cluster will enter Pending. Immich changes only after Sync." : "This name applies to every face in the same cluster.";
    matches = [
      ...(draft.operation === "hide" || cleanName(input.value).toLocaleLowerCase() === "ignore" ? [{ kind: "ignore", name: ignoreLabel }] : []),
      ...matchingNames(draft.operation === "hide" ? draft.name : input.value, face.personId).map(person => ({ ...person, kind: "person" })),
    ];
    selectedIndex = draft.operation === "hide" ? matches.findIndex(item => item.kind === "ignore")
      : draft.targetPersonId ? matches.findIndex(item => item.kind === "person" && item.id === draft.targetPersonId) : -1;
    matches.forEach((item, index) => {
      const button = document.createElement("button"); button.type = "button"; button.className = `suggestion${index === selectedIndex ? " selected" : ""}`;
      button.tabIndex = -1; button.setAttribute("role", "option"); button.setAttribute("aria-selected", String(index === selectedIndex));
      const image = document.createElement(item.kind === "ignore" ? "span" : "img");
      if (item.kind === "ignore") { image.className = "suggestion-action-icon"; image.textContent = "⊘"; }
      else { image.src = `/media/person/${encodeURIComponent(item.id)}`; image.alt = ""; }
      const name = document.createElement("strong"); name.textContent = item.name;
      const count = document.createElement("small"); count.textContent = item.kind === "ignore" ? "Queue an ignore action in Pending" : item.isPending ? "pending in this run" : `${item.assetCount ?? 0} photos`;
      button.append(image, name, count);
      button.addEventListener("mousedown", event => event.preventDefault());
      button.addEventListener("click", () => chooseSuggestion(index));
      list.append(button);
    });
    list.classList.toggle("open", matches.length > 0 && document.activeElement === input);
    const candidate = matches.find(item => item.kind === "person" && item.name.toLocaleLowerCase().startsWith(input.value.toLocaleLowerCase()));
    if (candidate && input.value && candidate.name.length > input.value.length && !draft.targetPersonId && draft.operation !== "hide") {
      const typed = document.createElement("span"); typed.className = "typed"; typed.textContent = input.value;
      const suffix = document.createElement("span"); suffix.textContent = candidate.name.slice(input.value.length);
      ghost.append(typed, suffix);
    }
  }

  function chooseSuggestion(index) {
    if (busy) return;
    if (index < 0) {
      selectedIndex = -1;
      const draft = activeDraft();
      saveDraft(activeFace().personId, { name: draft.unfinished || draft.name, targetPersonId: null, operation: "rename" });
      input.value = activeDraft().name;
    } else {
      selectedIndex = index;
      const person = matches[index];
      const old = activeDraft();
      if (person.kind === "ignore") {
        saveDraft(activeFace().personId, { name: "Ignore", targetPersonId: null, operation: "hide", unfinished: old.unfinished || old.name });
        input.value = ignoreLabel;
      } else {
        saveDraft(activeFace().personId, { name: person.name, targetPersonId: person.id, operation: "rename", unfinished: old.unfinished || old.name });
        input.value = person.name;
      }
    }
    updateSuggestions(); input.focus();
  }

  function selectFace(index, focus = true) {
    if (!photo?.faces[index]) return;
    active = index; selectedIndex = -1; matches = [];
    const face = activeFace(), editable = face.status === "actionable";
    input.disabled = !editable;
    input.value = editable ? draftLabel(activeDraft()) : face.name;
    input.placeholder = editable ? "Type a name…" : face.status === "unassigned" ? "Unassigned face" : face.name;
    $("#group-face-position").textContent = `Face ${index + 1} of ${photo.faces.length}`;
    $("#group-face-note").textContent = editable ? "This name applies to every face in the same cluster." : face.status === "pending" ? "Already in Pending." : face.status === "unassigned" ? "Immich returned this face without a nameable person cluster. People Workbench does not detect faces; this one cannot be named here." : face.status === "ignored" ? "Ignored face." : "Already named in Immich.";
    $("#group-markers").querySelectorAll("button").forEach((button, number) => button.classList.toggle("active", number === index));
    const canvas = $("#group-face-crop"), crop = document.createElement("canvas");
    crop.width = canvas.width; crop.height = canvas.height;
    canvas.getContext("2d").clearRect(0, 0, canvas.width, canvas.height);
    const assetId = photo.id;
    drawFace(crop, { assetId, box: face.box }).then(() => {
      if (photo?.id === assetId && activeFace()?.id === face.id) canvas.getContext("2d").drawImage(crop, 0, 0);
    }).catch(() => {
      if (photo?.id === assetId && activeFace()?.id === face.id) canvas.getContext("2d").clearRect(0, 0, canvas.width, canvas.height);
    });
    updateSuggestions();
    if (focus) requestAnimationFrame(() => (editable ? input : $("#group-markers").children[index]).focus());
  }

  function moveFocus(direction, position = active) {
    if (!photo) return;
    const actionable = photo.faces.flatMap((face, index) => face.status === "actionable" ? [index] : []);
    const submit = photo.faces.length;
    const order = [...actionable, submit];
    let next;
    if (position < submit && photo.faces[position]?.status !== "actionable") {
      next = direction > 0 ? actionable.find(index => index > position) ?? submit
        : [...actionable].reverse().find(index => index < position) ?? submit;
    } else {
      const at = order.indexOf(position);
      next = order[(at + direction + order.length) % order.length];
    }
    if (next < photo.faces.length) selectFace(next);
    else submitButton.focus();
  }

  function showPhoto(next) {
    photo = next;
    drafts = new Map(next.drafts.map(row => [row.person_id, {
      name: row.name,
      targetPersonId: state.names.some(person => person.id === row.target_person_id && person.name === row.name) ? row.target_person_id : null,
      operation: row.operation === "hide" ? "hide" : "rename",
    }]));
    for (const snapshot of unsavedDrafts.values()) {
      if (snapshot.assetId === next.id) drafts.set(snapshot.personId, {
        name: snapshot.name, targetPersonId: snapshot.targetPersonId, operation: snapshot.operation,
      });
    }
    $("#group-workspace").hidden = false; $("#group-checkpoint").hidden = true;
    const image = $("#group-photo-image"); image.src = assetThumb(next.id); image.alt = next.fileName;
    $("#group-photo-caption").textContent = `${next.fileName} · ${next.faceCount} detected faces · ${next.unnamedCount} unnamed detections`;
    markerLayer.replaceChildren();
    next.faces.forEach((face, index) => {
      const box = face.box, width = Number(box.width), height = Number(box.height);
      const marker = document.createElement("button"); marker.type = "button"; marker.tabIndex = -1;
      marker.className = `group-marker ${face.status}`; marker.setAttribute("aria-label", `Face ${index + 1}: ${face.name || "unnamed"}`);
      if (width > 0 && height > 0) {
        marker.style.left = `${Math.max(0, Number(box.x1) / width * 100)}%`;
        marker.style.top = `${Math.max(0, Number(box.y1) / height * 100)}%`;
        marker.style.width = `${Math.max(2, (Number(box.x2) - Number(box.x1)) / width * 100)}%`;
        marker.style.height = `${Math.max(2, (Number(box.y2) - Number(box.y1)) / height * 100)}%`;
      }
      marker.textContent = String(index + 1);
      marker.addEventListener("click", () => selectFace(index)); markerLayer.append(marker);
    });
    applyMarkerVisibility();
    selectFace(Math.max(0, next.faces.findIndex(face => face.status === "actionable")), false);
  }

  function faceSignature(item) {
    return item.faces.map(face => `${face.id}:${face.personId || ""}:${face.status}:${face.name}`).join("|");
  }

  async function renderGroupPhotos() {
    if (state.view !== "group-photos") return;
    clearTimeout(pollTimer);
    const data = await api("/api/group-photos");
    $("#group-min-faces").value = data.settings.minFaces;
    $("#group-archived").checked = data.settings.includeArchived;
    $("#group-order").value = data.settings.order;
    setProgress(data);
    if (data.photo) {
      if (photo?.id !== data.photo.id || faceSignature(photo) !== faceSignature(data.photo)) showPhoto(data.photo);
      else photo = { ...photo, ...data.photo, drafts: photo.drafts };
    } else {
      photo = null; $("#group-workspace").hidden = true;
    }
    const checkpoint = data.checkpoint && !data.scan.failures;
    $("#group-checkpoint").hidden = !checkpoint;
    if (checkpoint) {
      $("#group-checkpoint-text").textContent = checkpointText(data);
      $("#group-review-deferred").hidden = !data.deferred;
    }
    if (data.scan.scanning) pollTimer = setTimeout(async () => {
      try {
        if (photo) {
          const scan = await api("/api/group-photos/progress");
          setProgress({ scan, deferred: data.deferred, photo });
          if (scan.scanning && state.view === "group-photos") pollTimer = setTimeout(() => renderGroupPhotos().catch(error => showToast(error.message)), 1500);
        } else await renderGroupPhotos();
      } catch (error) { showToast(error.message); }
    }, 900);
  }

  async function confirmDuplicates(decisions) {
    for (const decision of decisions) {
      if (decision.operation === "hide" || decision.targetPersonId) continue;
      const exact = state.names.find(person => person.id !== decision.personId && person.name.toLocaleLowerCase() === decision.name.toLocaleLowerCase());
      if (!exact) continue;
      const faceIndex = photo.faces.findIndex(face => face.personId === decision.personId);
      selectFace(faceIndex);
      const accepted = await new Promise(resolve => {
        state.duplicateCancel = () => resolve(false);
        openDuplicate({ input, person: { id: decision.personId } }, decision.name, exact, () => resolve(true));
      });
      if (!accepted) return false;
    }
    return true;
  }

  async function advance() {
    if (busy || !photo) return;
    busy = true;
    const assetId = photo.id;
    input.disabled = true; submitButton.disabled = true;
    try {
      await retryDrafts(assetId);
      if (photo?.id !== assetId) throw new Error("Photo changed; review it again before submitting names.");
      const actionable = new Set(photo.faces.filter(face => face.status === "actionable").map(face => face.personId));
      const decisions = [...actionable].map(personId => ({ personId, ...(drafts.get(personId) || {}) })).filter(row => row.operation === "hide" || cleanName(row.name || ""));
      decisions.forEach(row => {
        row.name = row.operation === "hide" ? "" : cleanName(row.name);
        if (row.operation === "hide") row.targetPersonId = null;
        delete row.unfinished;
      });
      if (!(await confirmDuplicates(decisions))) return;
      if (photo?.id !== assetId) throw new Error("Photo changed; review it again before submitting names.");
      const result = await api("/api/group-photos/advance", { method: "POST", body: { assetId, decisions } });
      showToast(result.queued ? `${result.queued} changes queued in Pending` : "Photo deferred");
      photo = null;
      await loadNames(); await refreshSummary(); await renderGroupPhotos();
      if (photo) requestAnimationFrame(() => (input.disabled ? $("#group-markers").children[active] : input).focus());
    } catch (error) {
      if (/changed|no longer eligible|stale/i.test(error.message)) {
        photo = null;
        await renderGroupPhotos();
      }
      throw error;
    } finally {
      busy = false; submitButton.disabled = false;
      if (photo) selectFace(active, false);
    }
  }

  function bindGroupPhotos() {
    for (const selector of ["#group-min-faces", "#group-archived", "#group-order"]) $(selector).addEventListener("change", async () => {
      try {
        await retryDrafts(photo?.id);
        await api("/api/group-photos/settings", { method: "POST", body: { minFaces: Number($("#group-min-faces").value), includeArchived: $("#group-archived").checked, order: $("#group-order").value } });
        photo = null; await renderGroupPhotos();
      } catch (error) { showToast(error.message); }
    });
    input.addEventListener("focus", updateSuggestions);
    input.addEventListener("blur", () => setTimeout(() => list.classList.remove("open"), 100));
    input.addEventListener("input", () => { if (busy) { input.value = draftLabel(activeDraft()); return; } selectedIndex = -1; saveDraft(activeFace()?.personId, { name: input.value, targetPersonId: null, operation: "rename" }); updateSuggestions(); });
    input.addEventListener("keydown", event => {
      if (event.key === "Tab") {
        const ignoreIndex = !event.shiftKey ? matches.findIndex(item => item.kind === "ignore") : -1;
        const candidate = !event.shiftKey && input.value.trim() && matches.find(item => item.kind === "person" && item.name.toLocaleLowerCase().startsWith(input.value.toLocaleLowerCase()) && item.name.length > input.value.length);
        event.preventDefault();
        if (ignoreIndex >= 0 && activeDraft().operation !== "hide") chooseSuggestion(ignoreIndex);
        else if (candidate && !activeDraft().targetPersonId && activeDraft().operation !== "hide") chooseSuggestion(matches.indexOf(candidate));
        else moveFocus(event.shiftKey ? -1 : 1);
      } else if (event.key === "ArrowDown" && matches.length) {
        event.preventDefault(); chooseSuggestion(Math.min(selectedIndex + 1, matches.length - 1));
      } else if (event.key === "ArrowUp" && selectedIndex >= 0) {
        event.preventDefault(); chooseSuggestion(selectedIndex - 1);
      } else if (event.key === "Enter" && !event.ctrlKey && !event.metaKey) {
        event.preventDefault(); if (cleanName(input.value)) moveFocus(1);
      }
    });
    document.addEventListener("keydown", event => {
      if (state.view !== "group-photos" || $("#duplicate-dialog").open || $("#group-help-dialog").open) return;
      if (event.key === "Tab" && !event.defaultPrevented) {
        const focused = document.activeElement;
        const position = focused?.classList.contains("group-marker") ? active
          : focused === submitButton ? photo?.faces.length : null;
        if (position !== null && position !== undefined) {
          event.preventDefault(); moveFocus(event.shiftKey ? -1 : 1, position); return;
        }
      }
      if ((event.ctrlKey || event.metaKey) && event.key === "Enter") { event.preventDefault(); advance().catch(error => showToast(error.message)); }
      if (photo && event.key.toLowerCase() === "h" && !event.ctrlKey && !event.metaKey && !event.altKey) {
        const focused = document.activeElement;
        if (focused !== input && !focused?.isContentEditable && !["INPUT", "TEXTAREA", "SELECT"].includes(focused?.tagName)) {
          event.preventDefault(); toggleMarkers(); return;
        }
      }
      if (event.key === "?" && !event.ctrlKey && !event.metaKey) { event.preventDefault(); $("#group-help-dialog").showModal(); }
    });
    submitButton.addEventListener("click", () => advance().catch(error => showToast(error.message)));
    $("#group-help").addEventListener("click", () => $("#group-help-dialog").showModal());
    $("#group-retry").addEventListener("click", async () => { try { await api("/api/group-photos/retry", { method: "POST", body: {} }); await renderGroupPhotos(); } catch (error) { showToast(error.message); } });
    $("#group-review-deferred").addEventListener("click", async () => { try { await api("/api/group-photos/deferred", { method: "POST", body: {} }); await renderGroupPhotos(); } catch (error) { showToast(error.message); } });
  }

  return { renderGroupPhotos, bindGroupPhotos };
}
