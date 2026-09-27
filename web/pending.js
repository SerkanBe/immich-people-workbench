// Copyright (C) 2026 Serkan Bekdemir
// SPDX-License-Identifier: AGPL-3.0-only

export function createPending({ api, assetThumb, drawFace, matchingNames, openAssetPreview, personThumb, refreshSummary, renderPagination, state, ui }) {
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
    const data = await api(`/api/pending?page=${state.pages.pending}&size=${state.pageSize}`);
    const pending = data.pending;
    state.pages.pending = data.page;
    ui.pendingList.replaceChildren();
    document.querySelector("#pending-empty").hidden = data.total > 0;
    document.querySelector("#sync-button").disabled = !(state.summary?.counts?.included > 0);
    renderPagination("pending-pagination", data, page => { state.pages.pending = page; return renderPending(); });
    for (const item of pending) {
      const row = document.createElement("article"); row.className = "pending-item" + (item.included ? "" : " excluded");
      const checkbox = document.createElement("input"); checkbox.type = "checkbox"; checkbox.checked = item.included; checkbox.setAttribute("aria-label", "Include in next sync");
      checkbox.addEventListener("change", async () => {
        const path = item.kind === "face" ? "/api/face-detach/include" : "/api/pending/include";
        const body = item.kind === "face" ? { faceId: item.faceId, included: checkbox.checked } : { personId: item.personId, included: checkbox.checked };
        await api(path, { method: "POST", body }); await refreshSummary(); await renderPending();
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
        remove.addEventListener("click", async () => { await api("/api/face-detach/return", { method: "POST", body: { faceId: item.faceId } }); await refreshSummary(); await renderPending(); });
        actions.append(preview, remove);
      } else {
        const details = document.createElement("button"); details.className = "quiet-button"; details.textContent = "Inspect"; details.addEventListener("click", () => openDetails(item));
        actions.append(details); renderSampleStrip(samples, item.personId);
      }
      row.append(checkbox, image, title, samples, actions); ui.pendingList.append(row);
    }
  }

  async function renderGallery(title, personId) {
    const section = document.createElement("section"); section.className = "detail-gallery";
    const heading = document.createElement("h3"); heading.textContent = title;
    const grid = document.createElement("div"); grid.className = "gallery-grid"; section.append(heading, grid);
    await renderSampleStrip(grid, personId, 12); return section;
  }

  async function openDetails(item) {
    state.detailItem = item; state.detailTarget = null;
    document.querySelector("#detail-kind").textContent = item.operation === "merge" ? (item.targetName ? "Compare source and destination before syncing." : "Compare both clusters before syncing. The surviving person remains unnamed.") : "Inspect representative faces from this cluster.";
    const galleries = document.querySelector("#detail-galleries"); galleries.replaceChildren();
    galleries.append(await renderGallery("Current cluster", item.personId));
    if (item.targetPersonId) galleries.append(await renderGallery(item.targetName ? `Existing: ${item.targetName}` : "Surviving unnamed cluster", item.targetPersonId));
    const input = document.querySelector("#detail-name"); input.value = item.targetName || item.name || item.currentName || "";
    document.querySelector("#detail-suggestions").replaceChildren();
    ui.detail.showModal(); input.focus();
  }

  function updateDetailSuggestions() {
    const input = document.querySelector("#detail-name"), list = document.querySelector("#detail-suggestions");
    state.detailTarget = null; list.replaceChildren();
    const matches = matchingNames(input.value, state.detailItem?.personId).slice(0, 6);
    for (const person of matches) {
      const button = document.createElement("button"); button.type = "button"; button.className = "suggestion";
      button.tabIndex = -1;
      const image = document.createElement("img"); image.src = personThumb(person.id); image.alt = "";
      const name = document.createElement("strong"); name.textContent = person.name;
      const count = document.createElement("small");
      count.textContent = person.isPending ? "pending in this run" : person.assetCount == null ? "" : `${person.assetCount} photos`;
      button.append(image, name, count); button.addEventListener("click", () => { state.detailTarget = person; input.value = person.name; list.classList.remove("open"); }); list.append(button);
    }
    list.classList.toggle("open", matches.length > 0);
  }

  return { renderPending, updateDetailSuggestions };
}
