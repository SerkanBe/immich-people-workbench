// Copyright (C) 2026 Serkan Bekdemir
// SPDX-License-Identifier: AGPL-3.0-only

export function createFaceReview({ api, assetThumb, drawFace, openAssetPreview, personThumb, refreshSummary, renderPagination, showToast, state, ui }) {
  function currentFaceReviewPerson() {
    return state.faceReviewCurrentPerson;
  }

  function calculateFaceReviewCapacity() {
    const fallback = { columns: 5, rows: 4, size: 20, height: 0 };
    const width = ui.faceReviewGrid.clientWidth;
    const top = ui.faceReviewGrid.getBoundingClientRect().top;
    if (!width || !Number.isFinite(top)) return fallback;
    const styles = getComputedStyle(ui.faceReviewGrid);
    const gap = Number.parseFloat(styles.columnGap) || 12;
    const columns = Math.max(1, Math.floor((width + gap) / (180 + gap)));
    const cardWidth = (width - gap * (columns - 1)) / columns;
    const cardHeight = cardWidth + 76;
    const available = Math.max(cardHeight, window.innerHeight - top - 76);
    const rows = Math.max(1, Math.min(4, Math.floor((available + gap) / (cardHeight + gap))));
    return { columns, rows, size: columns * rows, height: Math.ceil(rows * cardHeight + (rows - 1) * gap) };
  }

  function applyFaceReviewCapacity() {
    const layout = calculateFaceReviewCapacity();
    ui.faceReviewGrid.style.gridTemplateColumns = `repeat(${layout.columns}, minmax(0, 1fr))`;
    ui.faceReviewGrid.style.minHeight = layout.height ? `${layout.height}px` : "";
    const changed = layout.size !== state.faceReviewPageSize;
    state.faceReviewLayout = layout; state.faceReviewPageSize = layout.size;
    return changed;
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
    document.querySelector("#face-review-meta").textContent = `${person.assetCount ?? result.totalAssets ?? "?"} photos · showing ${result.from}–${result.to} · ${reviewed}`;
    document.querySelector("#face-review-content").classList.toggle("person-reviewed", person.reviewed);
  }

  function renderFacePersonList() {
    ui.facePersonList.replaceChildren();
    for (const person of state.faceReviewPeople) {
      const button = document.createElement("button"); button.type = "button"; button.className = "face-person-button";
      button.classList.toggle("active", person.id === state.faceReviewPersonId);
      const image = document.createElement("img"); image.src = personThumb(person.id); image.alt = ""; image.loading = "lazy";
      const text = document.createElement("span");
      const name = document.createElement("strong"); name.textContent = person.name || "Unnamed person";
      const count = document.createElement("small");
      const reviewTotal = person.reviewTotal ?? person.assetCount ?? 0;
      count.textContent = person.reviewed ? `✓ Reviewed · ${person.assetCount ?? "?"} photos` : `${person.assetCount ?? "?"} photos · ${person.reviewedCount || 0}/${reviewTotal} reviewed`;
      button.classList.toggle("reviewed", Boolean(person.reviewed));
      text.append(name, count); button.append(image, text);
      button.addEventListener("click", () => selectFaceReviewPerson(person.id));
      ui.facePersonList.append(button);
    }
    if (!state.faceReviewPeople.length) { const empty = document.createElement("p"); empty.className = "muted"; empty.textContent = "No matching people."; ui.facePersonList.append(empty); }
  }

  async function loadFaceReviewPeople() {
    const query = document.querySelector("#face-person-search").value.trim();
    const data = await api(`/api/people?kind=review&sort=${encodeURIComponent(state.sort)}&page=${state.pages.facePeople}&size=${state.pageSize}&q=${encodeURIComponent(query)}`);
    state.faceReviewPeople = data.people; state.pages.facePeople = data.page;
    renderFacePersonList();
    renderPagination("face-person-pagination", data, page => {
      state.pages.facePeople = page; return loadFaceReviewPeople();
    });
  }

  function updateFaceReviewSelection() {
    ui.faceReviewGrid.querySelectorAll(".face-review-card").forEach(card => {
      const selected = state.faceReviewSelected.has(card.dataset.faceId); card.classList.toggle("selected", selected);
      const status = card.querySelector(".face-review-status");
      if (status && !card.classList.contains("queued")) status.textContent = selected ? "Selected for Unnamed" : "Select for Unnamed";
    });
    const button = document.querySelector("#face-review-queue"), count = state.faceReviewSelected.size;
    button.disabled = count === 0 || state.faceReviewLoading;
    button.textContent = count ? `Queue ${count} for Unnamed` : "Queue selected for Unnamed";
  }

  function createFaceReviewCard(face) {
    const card = document.createElement("article"); card.className = "face-review-card"; card.dataset.faceId = face.faceId; card.dataset.assetId = face.assetId; card.dataset.fileName = face.fileName; card.tabIndex = face.queued ? -1 : 0;
    card.classList.toggle("queued", Boolean(face.queued));
    card.classList.toggle("selected", state.faceReviewSelected.has(face.faceId));
    card.classList.toggle("reviewed", Boolean(face.reviewed));
    const crop = document.createElement("canvas"); crop.width = 280; crop.height = 280; crop.setAttribute("aria-label", `Face from ${face.fileName}`);
    drawFace(crop, face).catch(() => { crop.replaceWith(Object.assign(document.createElement("img"), { src: assetThumb(face.assetId), alt: face.fileName })); });
    const meta = document.createElement("div"); meta.className = "face-review-card-meta";
    const filename = document.createElement("code"); filename.textContent = face.fileName; filename.title = face.fileName;
    const status = document.createElement("span"); status.className = "face-review-status"; status.textContent = face.queued ? "Queued for Unnamed" : "Select for Unnamed";
    const preview = document.createElement("button"); preview.type = "button"; preview.className = "quiet-button"; preview.textContent = "Photo"; preview.tabIndex = -1;
    preview.addEventListener("click", event => { event.stopPropagation(); openAssetPreview(face.assetId, face.fileName); });
    const reviewedLabel = document.createElement("label"); reviewedLabel.className = "face-reviewed-check";
    const reviewed = document.createElement("input"); reviewed.type = "checkbox"; reviewed.checked = Boolean(face.reviewed); reviewed.disabled = Boolean(face.queued); reviewed.tabIndex = -1;
    reviewed.setAttribute("aria-label", `Reviewed face from ${face.fileName}`);
    reviewedLabel.append(reviewed, document.createTextNode("Reviewed"));
    reviewedLabel.addEventListener("click", event => event.stopPropagation());
    reviewed.addEventListener("change", async event => {
      event.stopPropagation(); const next = reviewed.checked; reviewed.disabled = true;
      try {
        const result = await api("/api/face-review/reviewed", { method: "POST", body: { personId: currentFaceReviewPerson().id, faceId: face.faceId, reviewed: next } });
        face.reviewed = next; card.classList.toggle("reviewed", next); Object.assign(state.faceReviewResult, result); updateFaceReviewProgress();
        const scrollTop = ui.facePersonList.scrollTop; renderFacePersonList(); ui.facePersonList.scrollTop = scrollTop;
        if (result.personReviewed) showToast(`${currentFaceReviewPerson().name || "This person"} is fully reviewed.`);
      } catch (error) { reviewed.checked = !next; showToast(`Could not save review state: ${error.message}`); }
      finally { reviewed.disabled = Boolean(face.queued); }
    });
    meta.append(filename, status, preview, reviewedLabel); card.append(crop, meta);
    const toggle = () => {
      if (face.queued) return;
      if (state.faceReviewSelected.has(face.faceId)) {
        state.faceReviewSelected.delete(face.faceId); state.faceReviewSelectedData.delete(face.faceId);
      } else {
        state.faceReviewSelected.add(face.faceId); state.faceReviewSelectedData.set(face.faceId, face);
      }
      updateFaceReviewSelection();
    };
    card.addEventListener("click", event => { if (!event.target.closest("button, label, input")) toggle(); });
    card.addEventListener("keydown", event => {
      if (event.key === " " || event.key === "Enter") { event.preventDefault(); toggle(); }
      else if (event.key.toLocaleLowerCase() === "r" && !face.queued) { event.preventDefault(); reviewed.click(); }
    });
    return card;
  }

  async function loadFaceReviewPage(page = state.faceReviewPage) {
    const person = currentFaceReviewPerson();
    if (!person || state.faceReviewLoading) return;
    state.faceReviewLoading = true; updateFaceReviewSelection();
    try {
      const result = await api(`/api/person-faces/${encodeURIComponent(person.id)}?page=${page}&size=${state.faceReviewPageSize}`);
      ui.faceReviewGrid.replaceChildren(...result.faces.map(createFaceReviewCard));
      state.faceReviewPage = result.page; state.faceReviewResult = result;
      updateFaceReviewProgress(result);
      renderPagination("face-review-pagination", result, nextPage => loadFaceReviewPage(nextPage));
      if (!ui.faceReviewGrid.children.length) {
        const empty = document.createElement("p"); empty.className = "muted"; empty.textContent = "Immich returned no assigned faces for this person."; ui.faceReviewGrid.append(empty);
      }
    } catch (error) {
      showToast(`Could not load faces: ${error.message}`);
    } finally {
      state.faceReviewLoading = false; updateFaceReviewSelection();
    }
  }

  async function selectFaceReviewPerson(personId) {
    const person = state.faceReviewPeople.find(item => item.id === personId) || (state.faceReviewPersonId === personId ? state.faceReviewCurrentPerson : null);
    if (!person) return;
    state.faceReviewPersonId = personId; state.faceReviewCurrentPerson = person; state.faceReviewPage = 1;
    state.faceReviewSelected.clear(); state.faceReviewSelectedData.clear();
    renderFacePersonList();
    document.querySelector("#face-review-placeholder").hidden = true;
    document.querySelector("#face-review-content").hidden = false;
    document.querySelector("#face-review-name").textContent = person?.name || "Unnamed person";
    document.querySelector("#face-review-meta").textContent = "Loading assigned faces…";
    ui.faceReviewGrid.replaceChildren(); applyFaceReviewCapacity(); await loadFaceReviewPage(1);
  }

  async function renderFaceReview() {
    await loadFaceReviewPeople();
    if (state.faceReviewPersonId) await selectFaceReviewPerson(state.faceReviewPersonId);
    else {
      document.querySelector("#face-review-placeholder").hidden = false;
      document.querySelector("#face-review-content").hidden = true;
    }
  }

  async function queueSelectedFacesForUnnamed() {
    const person = currentFaceReviewPerson();
    if (!person || !state.faceReviewSelected.size || state.faceReviewLoading) return;
    const selectedData = [...state.faceReviewSelectedData.values()]
      .map(face => ({ faceId: face.faceId, assetId: face.assetId, fileName: face.fileName }));
    state.faceReviewLoading = true; updateFaceReviewSelection();
    let queued = 0;
    try {
      for (const face of selectedData) {
        await api("/api/face-detach/queue", { method: "POST", body: { personId: person.id, ...face } });
        queued += 1;
      }
      state.faceReviewSelected.clear(); state.faceReviewSelectedData.clear();
      state.faceReviewLoading = false;
      showToast(`Queued ${queued} ${queued === 1 ? "face" : "faces"} to become unnamed on sync.`);
      await loadFaceReviewPage(state.faceReviewPage); await refreshSummary();
    } catch (error) {
      state.faceReviewLoading = false;
      showToast(`Queued ${queued}; stopped because: ${error.message}`);
      await loadFaceReviewPage(state.faceReviewPage); await refreshSummary();
    } finally {
      state.faceReviewLoading = false; updateFaceReviewSelection();
    }
  }

  return { renderFaceReview, currentFaceReviewPerson, applyFaceReviewCapacity, loadFaceReviewPage, loadFaceReviewPeople, queueSelectedFacesForUnnamed };
}
