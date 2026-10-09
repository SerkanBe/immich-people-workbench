// Copyright (C) 2026 Serkan Bekdemir
// SPDX-License-Identifier: AGPL-3.0-only

export function createPending({ api, assetThumb, cleanName, drawFace, loadNames, matchingNames, openAssetPreview, personThumb, refreshSummary, renderPagination, showToast, state, ui, waitUntilReady }) {
  let syncing = false;
  let saves = 0;
  let selectionUnverified = false;
  let reviewToken = "";
  let syncConfirmation = null;
  let renderGeneration = 0;
  let detailGeneration = 0;
  let detailModel = null;
  let detailTarget = null;
  let detailLoading = false;

  function updateControls() {
    const busy = syncing || saves > 0 || syncConfirmation !== null;
    document.querySelector("#sync-button").disabled = busy || selectionUnverified || !reviewToken || state.faceReviewSaving || !(state.summary?.counts?.included > 0);
    document.querySelector("#reset-button").disabled = busy || !(state.summary?.counts?.pending > 0);
    const lockedMerge = detailModel?.mergeStep && detailModel.mergeStep !== "ready";
    document.querySelector("#detail-name").disabled = busy || detailLoading || !!lockedMerge;
    document.querySelector("#detail-save").disabled = busy || detailLoading || !detailModel || !!lockedMerge;
    document.querySelector("#return-button").disabled = busy || detailLoading || !detailModel;
    for (const checkbox of ui.pendingList.querySelectorAll("input")) checkbox.disabled = busy;
  }

  async function mutate(action) {
    if (syncing || saves || syncConfirmation) { showToast("Finish or cancel the current action, then retry."); return false; }
    saves += 1; updateControls();
    try { await action(); return true; }
    catch (error) { showToast(error.message); return false; }
    finally { saves -= 1; updateControls(); }
  }

  function closeDetails() {
    detailGeneration += 1; detailModel = null; detailTarget = null; detailLoading = false;
    state.detailItem = null; state.detailTarget = null; updateControls();
  }

  async function renderSampleStrip(container, personId, limit = 4) {
    container.replaceChildren();
    try {
      const samples = (await api(`/api/samples/${encodeURIComponent(personId)}`)).samples.slice(0, limit);
      if (!samples.length) {
        const image = document.createElement("img"); image.src = personThumb(personId); image.alt = ""; container.append(image); return;
      }
      for (const sample of samples) {
        const canvas = document.createElement("canvas"); canvas.width = 320; canvas.height = 320; container.append(canvas);
        drawFace(canvas, sample).catch(() => canvas.remove());
      }
    } catch { /* The main person thumbnail remains available. */ }
  }

  async function renderPending() {
    const generation = ++renderGeneration;
    const data = await api(`/api/pending?page=${state.pages.pending}&size=${state.pageSize}`);
    if (generation !== renderGeneration) return;
    reviewToken = typeof data.reviewToken === "string" ? data.reviewToken : "";
    selectionUnverified = !reviewToken;
    const pending = data.pending;
    state.pages.pending = data.page;
    ui.pendingList.replaceChildren();
    document.querySelector("#pending-empty").hidden = data.total > 0;
    updateControls();
    renderPagination("pending-pagination", data, page => { state.pages.pending = page; return renderPending(); });
    for (const item of pending) {
      const row = document.createElement("article"); row.className = "pending-item" + (item.included ? "" : " excluded");
      const checkbox = document.createElement("input"); checkbox.type = "checkbox"; checkbox.checked = item.included; checkbox.setAttribute("aria-label", "Include in next sync");
      checkbox.addEventListener("change", async () => {
        const desired = checkbox.checked;
        if (syncing || saves || syncConfirmation) { checkbox.checked = item.included; showToast("Finish or cancel the current action, then retry."); return; }
        await mutate(async () => {
          renderGeneration += 1; selectionUnverified = true;
          const path = item.kind === "face" ? "/api/face-detach/include" : "/api/pending/include";
          const body = item.kind === "face" ? { faceId: item.faceId, included: desired } : { personId: item.personId, included: desired };
          try { await api(path, { method: "POST", body }); }
          catch (error) {
            renderGeneration += 1;
            checkbox.checked = item.included;
            selectionUnverified = true;
            // A lost reply may follow a successful save. Read the authoritative
            // selection before enabling Sync; if that fails, keep Sync blocked.
            try { await refreshSummary(); await renderPending(); } catch { /* Retry by reloading Pending. */ }
            throw error;
          }
          renderGeneration += 1; selectionUnverified = true;
          await refreshSummary(); await renderPending();
        });
      });
      const image = document.createElement("img"); image.src = item.kind === "face" ? assetThumb(item.assetId) : item.featureAssetId ? assetThumb(item.featureAssetId) : personThumb(item.personId); image.alt = "";
      const title = document.createElement("div"); title.className = "pending-title";
      const strong = document.createElement("strong");
      strong.textContent = item.kind === "face" ? `${item.currentName || "Unnamed person"}: move face to a new unnamed cluster` : item.operation === "merge" ? `${item.currentName || "Unnamed"} → ${item.targetName || "unnamed merged person"}` : item.operation === "hide" ? "Ignore this person" : item.operation === "unhide" ? `Restore ${item.currentName || "person"}` : item.name;
      const operation = document.createElement("span"); operation.className = "operation"; operation.textContent = item.operation;
      title.append(strong, operation);
      if (item.kind === "face" && item.fileName) { const file = document.createElement("code"); file.textContent = item.fileName; file.title = item.fileName; title.append(file); }
      if (item.lastError) { const error = document.createElement("div"); error.className = "error-text"; error.textContent = item.lastError; title.append(error); }
      const samples = document.createElement("div"); samples.className = "pending-samples";
      const actions = document.createElement("div"); actions.className = "pending-actions";
      if (item.kind === "face") {
        const preview = document.createElement("button"); preview.className = "quiet-button"; preview.textContent = "Preview"; preview.addEventListener("click", () => openAssetPreview(item.assetId, item.fileName));
        const remove = document.createElement("button"); remove.className = "quiet-button"; remove.textContent = "Remove";
        remove.addEventListener("click", () => mutate(async () => { await api("/api/face-detach/return", { method: "POST", body: { faceId: item.faceId } }); state.faceReviewNeedsRefresh = true; await refreshSummary(); await renderPending(); }));
        actions.append(preview, remove);
      } else {
        const details = document.createElement("button"); details.className = "quiet-button"; details.textContent = "Inspect"; details.addEventListener("click", () => openDetails(item));
        actions.append(details); renderSampleStrip(samples, item.personId);
      }
      row.append(checkbox, image, title, samples, actions); ui.pendingList.append(row);
    }
    updateControls();
  }

  async function renderGallery(title, personId) {
    const section = document.createElement("section"); section.className = "detail-gallery";
    const heading = document.createElement("h3"); heading.textContent = title;
    const grid = document.createElement("div"); grid.className = "gallery-grid"; section.append(heading, grid);
    await renderSampleStrip(grid, personId, 12); return section;
  }

  async function openDetails(item) {
    const generation = ++detailGeneration;
    const model = Object.freeze({ ...item });
    detailModel = null; detailTarget = null; detailLoading = true;
    state.detailItem = null; state.detailTarget = null;
    document.querySelector("#detail-kind").textContent = "Loading this cluster…";
    const galleries = document.querySelector("#detail-galleries"); galleries.replaceChildren();
    const input = document.querySelector("#detail-name"); input.value = "";
    document.querySelector("#detail-suggestions").replaceChildren();
    updateControls();
    if (!ui.detail.open) ui.detail.showModal();
    const sections = await Promise.all([
      renderGallery("Current cluster", model.personId),
      model.targetPersonId ? renderGallery(model.targetName ? `Existing: ${model.targetName}` : "Surviving unnamed cluster", model.targetPersonId) : null,
    ]);
    if (generation !== detailGeneration || !ui.detail.open) return;
    detailModel = model; detailLoading = false;
    detailTarget = model.operation === "merge" ? { id: model.targetPersonId, name: model.targetName || "" } : null;
    state.detailItem = model; state.detailTarget = detailTarget;
    galleries.replaceChildren(...sections.filter(Boolean));
    document.querySelector("#detail-kind").textContent = model.mergeStep === "complete"
      ? "Merge completed. Retry Sync to finish only the representative photo update."
      : ["inflight", "uncertain"].includes(model.mergeStep)
        ? "Merge outcome is uncertain. Check both people in Immich before removing this request. Sync will not repeat it."
        : model.operation === "merge" ? "Compare source and destination before syncing." : "Inspect representative faces from this cluster.";
    input.value = model.targetName || model.name || model.currentName || "";
    updateControls(); input.focus();
  }

  function updateDetailSuggestions() {
    const input = document.querySelector("#detail-name"), list = document.querySelector("#detail-suggestions");
    if (!detailModel || detailLoading || (detailModel.mergeStep && detailModel.mergeStep !== "ready")) return;
    detailTarget = null; state.detailTarget = null; list.replaceChildren();
    const model = detailModel;
    const matches = matchingNames(input.value, model.personId).slice(0, 6);
    for (const person of matches) {
      const button = document.createElement("button"); button.type = "button"; button.className = "suggestion";
      button.tabIndex = -1;
      const image = document.createElement("img"); image.src = personThumb(person.id); image.alt = "";
      const name = document.createElement("strong"); name.textContent = person.name;
      const count = document.createElement("small");
      count.textContent = person.isPending ? "pending in this run" : person.assetCount == null ? "" : `${person.assetCount} photos`;
      button.append(image, name, count); button.addEventListener("click", () => { if (model !== detailModel || syncing || saves) return; detailTarget = { ...person }; state.detailTarget = detailTarget; input.value = person.name; list.classList.remove("open"); }); list.append(button);
    }
    list.classList.toggle("open", matches.length > 0);
  }

  function bindPendingControls() {
    const syncButton = document.querySelector("#sync-button");
    const syncDialog = document.querySelector("#sync-confirm-dialog");
    const syncApply = document.querySelector("#sync-confirm-apply");
    function cancelSyncConfirmation() {
      syncConfirmation = null;
      syncDialog.close();
      updateControls();
      if (!syncing) syncButton.focus();
    }
    document.querySelector("#sync-confirm-cancel").addEventListener("click", cancelSyncConfirmation);
    syncDialog.addEventListener("cancel", event => { event.preventDefault(); cancelSyncConfirmation(); });
    syncDialog.addEventListener("keydown", event => {
      if (event.key !== "Tab") return;
      event.preventDefault();
      event.stopPropagation();
      // Two controls: keep forward and reverse Tab inside the confirmation.
      (document.activeElement === syncApply ? document.querySelector("#sync-confirm-cancel") : syncApply).focus();
    });
    syncDialog.addEventListener("close", () => {
      if (!syncDialog.open) { syncConfirmation = null; updateControls(); }
    });
    document.addEventListener("face-review-save-state", updateControls);
    ui.detail.addEventListener("close", () => {
      // Native close events can arrive after a different inspection reopened
      // this dialog. An old close must not cancel the new dialog's model.
      if (!ui.detail.open) closeDetails();
    });
    ui.detail.addEventListener("cancel", closeDetails);
    syncButton.addEventListener("click", () => {
      if (syncing || syncConfirmation) return;
      if (saves || selectionUnverified || !reviewToken) { showToast("Wait for Pending selections to finish saving. Reload Pending if the selection could not be verified."); return; }
      if (state.faceReviewSaving) { showToast("Wait for Face Review decisions to finish saving before Sync."); return; }
      if (!(state.summary?.counts?.included > 0)) return;
      syncConfirmation = { reviewToken };
      updateControls();
      syncDialog.showModal();
      syncApply.focus();
    });
    syncApply.addEventListener("click", async () => {
      const confirmation = syncConfirmation;
      if (!confirmation || !syncDialog.open || syncing) return;
      if (saves || selectionUnverified || state.faceReviewSaving) {
        cancelSyncConfirmation();
        showToast("Wait for local saves to finish, then review Pending and Sync again.");
        return;
      }
      syncing = true;
      syncConfirmation = null;
      syncDialog.close();
      updateControls();
      syncButton.textContent = "Syncing to Immich…";
      syncButton.setAttribute("aria-busy", "true");
      try {
        const result = await api("/api/sync", { method: "POST", body: { confirmation: "SYNC", reviewToken: confirmation.reviewToken } });
        state.faceReviewNeedsRefresh = true;
        showToast(`Sync finished: ${result.results.length - result.failed} succeeded, ${result.failed} failed`);
        await loadNames(); await refreshSummary(); await renderPending();
      } catch (error) {
        showToast(error.message);
        if (/Pending changed/i.test(error.message)) {
          renderGeneration += 1; selectionUnverified = true;
          try { await refreshSummary(); await renderPending(); }
          catch { /* Keep Sync disabled until a fresh review can load. */ }
        }
      } finally {
        syncing = false;
        syncButton.textContent = "Sync checked changes to Immich";
        syncButton.removeAttribute("aria-busy");
        updateControls();
      }
    });
    document.querySelector("#reset-button").addEventListener("click", async () => {
      if (syncing || saves) { showToast("Wait for the current save or Sync to finish, then retry."); return; }
      const count = state.summary?.counts?.pending || 0;
      if (!count || !confirm(`Discard all ${count} unsynced local changes and reload the current names from Immich?\n\nAlready synced Immich changes are not affected.`)) return;
      await mutate(async () => {
        const result = await api("/api/reset", { method: "POST", body: { confirmation: "DISCARD" } });
        state.faceReviewNeedsRefresh = true; state.names = [];
        showToast(`Discarded ${result.discarded} local changes. Reloading from Immich…`);
        await waitUntilReady();
      });
    });
    document.querySelector("#detail-name").addEventListener("input", updateDetailSuggestions);
    document.querySelector("#detail-save").addEventListener("click", async () => {
      const item = detailModel, target = detailTarget;
      const name = cleanName(document.querySelector("#detail-name").value);
      if (!item || detailLoading || (item.mergeStep && item.mergeStep !== "ready")) return;
      const original = cleanName(item.targetName || item.name || item.currentName || "");
      if (name === original && (!target || target.id === item.targetPersonId)) {
        ui.detail.close(); return; // Inspecting unchanged must preserve hide/restore/merge.
      }
      if (!name) return;
      const generation = detailGeneration;
      const body = target ? { personId: item.personId, operation: "merge", name: target.name, targetPersonId: target.id, targetName: target.name, featureAssetId: item.featureAssetId } : { personId: item.personId, operation: "rename", name, featureAssetId: item.featureAssetId };
      await mutate(async () => {
        await api("/api/queue", { method: "POST", body });
        if (generation === detailGeneration) ui.detail.close();
        await refreshSummary(); await renderPending();
      });
    });
    document.querySelector("#return-button").addEventListener("click", async () => {
      const item = detailModel, generation = detailGeneration;
      if (!item || detailLoading) return;
      await mutate(async () => {
        await api("/api/pending/return", { method: "POST", body: { personId: item.personId } });
        if (generation === detailGeneration) ui.detail.close();
        await refreshSummary(); await renderPending();
      });
    });
  }

  return { renderPending, bindPendingControls };
}
