// Copyright (C) 2026 Serkan Bekdemir
// SPDX-License-Identifier: AGPL-3.0-only

export function createInvestigate({ api, assetThumb, attachNameBehavior, moveSlide, openAssetPreview, personThumb, refreshSummary, renderPagination, showSlide, showToast, state, ui }) {
  async function renderInvestigateMedia(container, personId) {
    try {
      const files = (await api(`/api/files/${encodeURIComponent(personId)}`)).files;
      container.replaceChildren();
      if (!files.length) {
        const empty = document.createElement("p"); empty.className = "muted"; empty.textContent = "No filenames returned by Immich."; container.append(empty); return;
      }
      let index = 0;
      const stage = document.createElement("div"); stage.className = "investigate-photo-stage"; stage.tabIndex = 0;
      const image = document.createElement("img"); image.className = "investigate-photo";
      const previous = document.createElement("button"); previous.type = "button"; previous.className = "carousel-button previous"; previous.tabIndex = -1; previous.textContent = "‹"; previous.setAttribute("aria-label", "Previous photo");
      const next = document.createElement("button"); next.type = "button"; next.className = "carousel-button next"; next.tabIndex = -1; next.textContent = "›"; next.setAttribute("aria-label", "Next photo");
      const counter = document.createElement("span"); counter.className = "carousel-count"; stage.append(image, previous, next, counter);
      const caption = document.createElement("div"); caption.className = "investigate-photo-caption";
      const filename = document.createElement("code");
      const copy = document.createElement("button"); copy.type = "button"; copy.className = "quiet-button"; copy.textContent = "Copy filename"; copy.tabIndex = -1;
      caption.append(filename, copy);
      const strip = document.createElement("div"); strip.className = "investigate-photo-strip";
      const thumbs = files.map((file, fileIndex) => {
        const button = document.createElement("button"); button.type = "button"; button.className = "investigate-photo-thumb"; button.tabIndex = -1; button.title = file.fileName;
        const thumb = document.createElement("img"); thumb.src = assetThumb(file.assetId); thumb.alt = ""; thumb.loading = "lazy";
        button.append(thumb); button.addEventListener("click", () => show(fileIndex)); strip.append(button); return button;
      });
      const show = nextIndex => {
        index = (nextIndex + files.length) % files.length;
        const file = files[index];
        image.src = assetThumb(file.assetId); image.alt = file.fileName; image.title = "Click for a larger preview";
        filename.textContent = file.fileName; filename.title = file.originalPath || file.fileName;
        counter.textContent = `${index + 1} / ${files.length}`;
        thumbs.forEach((button, thumbIndex) => button.classList.toggle("active", thumbIndex === index));
        thumbs[index]?.scrollIntoView({ block: "nearest", inline: "nearest" });
      };
      previous.addEventListener("click", () => show(index - 1)); next.addEventListener("click", () => show(index + 1));
      image.addEventListener("click", () => openAssetPreview(files[index].assetId, files[index].fileName));
      copy.addEventListener("click", async () => { try { await navigator.clipboard.writeText(files[index].fileName); showToast(`Copied ${files[index].fileName}`); } catch { showToast("Could not copy filename"); } });
      stage.addEventListener("keydown", event => {
        if (event.key === "ArrowLeft" || event.key === "ArrowRight") { event.preventDefault(); show(index + (event.key === "ArrowRight" ? 1 : -1)); }
      });
      container.append(stage, caption, strip); show(0);
    } catch (error) {
      container.textContent = `Could not load filenames: ${error.message}`;
    }
  }

  async function renderInvestigate(focusFirst = false, focusIndex = 0) {
    const data = await api(`/api/people?kind=investigate&sort=${encodeURIComponent(state.sort)}&page=${state.pages.investigate}&size=${state.pageSize}`);
    const people = data.people;
    state.pages.investigate = data.page;
    ui.investigateGrid.replaceChildren();
    document.querySelector("#investigate-empty").hidden = data.total > 0;
    renderPagination("investigate-pagination", data, page => { state.pages.investigate = page; return renderInvestigate(true); });
    for (const [index, person] of people.entries()) {
      const card = document.createElement("article"); card.className = "investigate-card";
      const head = document.createElement("div"); head.className = "investigate-head";
      const portrait = document.createElement("div"); portrait.className = "portrait-wrap investigate-person-portrait";
      const image = document.createElement("img"); image.className = "portrait person-thumb"; image.src = personThumb(person.id); image.alt = person.name || "Unnamed person";
      const canvas = document.createElement("canvas"); canvas.className = "portrait sample-thumb"; canvas.width = 480; canvas.height = 480; canvas.hidden = true;
      const previous = document.createElement("button"); previous.type = "button"; previous.className = "carousel-button previous"; previous.tabIndex = -1; previous.textContent = "‹"; previous.setAttribute("aria-label", "Previous face");
      const next = document.createElement("button"); next.type = "button"; next.className = "carousel-button next"; next.tabIndex = -1; next.textContent = "›"; next.setAttribute("aria-label", "Next face");
      const counter = document.createElement("span"); counter.className = "carousel-count"; portrait.append(image, canvas, previous, next, counter);
      const meta = document.createElement("div");
      const title = document.createElement("h3"); title.textContent = person.name || "Unnamed cluster";
      const count = document.createElement("p"); count.textContent = person.assetCount == null ? "Photo count unavailable" : `${person.assetCount} photos`;
      const nameWrap = document.createElement("div"); nameWrap.className = "autocomplete-wrap name-wrap investigate-name";
      const ghost = document.createElement("div"); ghost.className = "ghost"; ghost.setAttribute("aria-hidden", "true");
      const input = document.createElement("input"); input.className = "name-input"; input.autocomplete = "off"; input.spellcheck = false; input.setAttribute("aria-label", "Person name"); input.placeholder = "Type a name…"; input.value = person.name || "";
      const suggestions = document.createElement("div"); suggestions.className = "suggestions"; suggestions.setAttribute("role", "listbox");
      nameWrap.append(ghost, input, suggestions);
      const restore = document.createElement("button"); restore.type = "button"; restore.className = "quiet-button"; restore.textContent = "Return to naming queue"; restore.tabIndex = -1;
      restore.addEventListener("click", async () => { await api("/api/investigate/remove", { method: "POST", body: { personId: person.id } }); await renderInvestigate(); await refreshSummary(); });
      meta.append(title, count, nameWrap, restore); head.append(portrait, meta);
      const files = document.createElement("div"); files.className = "investigate-media"; files.textContent = "Loading photo previews…";
      card.append(head, files); ui.investigateGrid.append(card);
      const model = {
        person, mode: "investigate", cardIndex: index, card, image, canvas, counter, input, list: suggestions, ghost,
        matches: [], ghostCandidate: null, selectedIndex: -1, selectedTarget: null,
        draft: input.value, slides: [{ type: "person", assetId: null }], slideIndex: 0, samplesLoaded: false,
      };
      previous.addEventListener("click", () => moveSlide(model, -1)); next.addEventListener("click", () => moveSlide(model, 1));
      attachNameBehavior(model); showSlide(model); renderInvestigateMedia(files, person.id);
    }
    if (focusFirst && people.length) requestAnimationFrame(() => ui.investigateGrid.querySelectorAll(".name-input")[Math.min(focusIndex, people.length - 1)]?.focus());
  }

  function bindInvestigateControls() {
    document.querySelector("#investigate-return-all").addEventListener("click", async () => {
      const count = state.summary?.counts?.investigate || 0;
      if (!count) return;
      if (!confirm(`Return all ${count} Investigate clusters to Unnamed?\n\nThis only changes the local queue; Immich is not modified.`)) return;
      const button = document.querySelector("#investigate-return-all"); button.disabled = true;
      try {
        const result = await api("/api/investigate/clear", { method: "POST", body: { confirmation: "RETURN_ALL" } });
        showToast(`Returned ${result.returned} clusters to Unnamed`);
        await renderInvestigate();
        await refreshSummary();
      } catch (error) {
        showToast(error.message);
      } finally {
        button.disabled = !(state.summary?.counts?.investigate > 0);
      }
    });
  }

  return { renderInvestigate, bindInvestigateControls };
}
