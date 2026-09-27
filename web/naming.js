// Copyright (C) 2026 Serkan Bekdemir
// SPDX-License-Identifier: AGPL-3.0-only

export function createNaming({ api, assetThumb, cleanName, loadNames, personThumb, refreshSummary, renderInvestigate, renderPagination, showToast, state, ui }) {
  function matchingNames(fragment, excludeId = "") {
    const needle = cleanName(fragment).toLocaleLowerCase();
    if (!needle) return [];
    return state.names
      .filter(person => person.id !== excludeId && person.name.toLocaleLowerCase().includes(needle))
      .sort((a, b) => {
        const ap = a.name.toLocaleLowerCase().startsWith(needle) ? 0 : 1;
        const bp = b.name.toLocaleLowerCase().startsWith(needle) ? 0 : 1;
        return ap - bp || a.name.localeCompare(b.name);
      }).slice(0, 10);
  }

  function exactMatches(name, excludeId = "") {
    const needle = cleanName(name).toLocaleLowerCase();
    return state.names.filter(person => person.id !== excludeId && person.name.toLocaleLowerCase() === needle);
  }

  function setGhost(ghost, typed, candidate) {
    ghost.replaceChildren();
    if (!typed || !candidate || !candidate.name.toLocaleLowerCase().startsWith(typed.toLocaleLowerCase()) || candidate.name.length === typed.length) return;
    const hidden = document.createElement("span"); hidden.className = "typed"; hidden.textContent = typed;
    const suffix = document.createElement("span"); suffix.textContent = candidate.name.slice(typed.length);
    ghost.append(hidden, suffix);
  }

  function showSuggestions(model) {
    const { input, list, ghost } = model;
    list.replaceChildren();
    model.matches.forEach((person, index) => {
      const button = document.createElement("button");
      button.type = "button"; button.className = "suggestion" + (index === model.selectedIndex ? " selected" : "");
      button.tabIndex = -1;
      button.setAttribute("role", "option"); button.setAttribute("aria-selected", String(index === model.selectedIndex));
      const image = document.createElement("img"); image.src = personThumb(person.id); image.alt = "";
      const name = document.createElement("strong"); name.textContent = person.name;
      const count = document.createElement("small");
      count.textContent = person.isPending ? "pending in this run" : person.assetCount == null ? "" : `${person.assetCount} photos`;
      button.append(image, name, count);
      button.addEventListener("mousedown", event => event.preventDefault());
      button.addEventListener("click", () => { selectSuggestion(model, index); input.focus(); });
      list.append(button);
    });
    list.classList.toggle("open", model.matches.length > 0 && document.activeElement === input);
    setGhost(ghost, model.draft, model.ghostCandidate);
  }

  function updateSuggestions(model, keepDraft = false) {
    const typed = cleanName(model.input.value);
    if (!keepDraft) model.draft = typed;
    model.matches = matchingNames(typed, model.person.id);
    model.ghostCandidate = model.matches.find(person => person.name.toLocaleLowerCase().startsWith(typed.toLocaleLowerCase())) || null;
    if (!keepDraft) { model.selectedIndex = -1; model.selectedTarget = null; }
    showSuggestions(model);
  }

  function selectSuggestion(model, index) {
    if (index < 0) {
      model.selectedIndex = -1; model.selectedTarget = null; model.input.value = model.draft;
    } else {
      model.selectedIndex = Math.min(index, model.matches.length - 1);
      model.selectedTarget = model.matches[model.selectedIndex];
      model.input.value = model.selectedTarget.name;
    }
    model.ghostCandidate = null;
    showSuggestions(model);
    model.onSelectionChanged?.();
  }

  async function drawFace(canvas, sample) {
    const image = new Image();
    image.src = assetThumb(sample.assetId);
    await image.decode();
    const box = sample.box || {};
    const sx = Number(box.x1), sy = Number(box.y1), ex = Number(box.x2), ey = Number(box.y2);
    const originalWidth = Number(box.width), originalHeight = Number(box.height);
    const scaleX = image.naturalWidth / (originalWidth || image.naturalWidth);
    const scaleY = image.naturalHeight / (originalHeight || image.naturalHeight);
    const faceWidth = Math.max(1, (ex - sx) * scaleX), faceHeight = Math.max(1, (ey - sy) * scaleY);
    const size = Math.min(Math.max(faceWidth, faceHeight) * 2.05, image.naturalWidth, image.naturalHeight);
    const cx = ((sx + ex) / 2) * scaleX, cy = ((sy + ey) / 2) * scaleY;
    const sourceX = Math.max(0, Math.min(image.naturalWidth - size, cx - size / 2));
    const sourceY = Math.max(0, Math.min(image.naturalHeight - size, cy - size / 2));
    const context = canvas.getContext("2d");
    context.clearRect(0, 0, canvas.width, canvas.height);
    context.drawImage(image, sourceX, sourceY, size, size, 0, 0, canvas.width, canvas.height);
  }

  async function ensureSamples(model) {
    if (model.samplesLoaded) return;
    model.samplesLoaded = true;
    try {
      const data = await api(`/api/samples/${encodeURIComponent(model.person.id)}`);
      model.slides.push(...data.samples.map(sample => ({ type: "sample", assetId: sample.assetId, sample })));
    } catch (error) {
      showToast(`Could not load additional faces: ${error.message}`);
    }
  }

  async function showSlide(model) {
    const slide = model.slides[model.slideIndex];
    model.counter.textContent = `${model.slideIndex + 1} / ${model.slides.length}${model.samplesLoaded ? "" : "+"}`;
    if (slide.type === "person") {
      model.image.hidden = false; model.canvas.hidden = true;
    } else {
      model.image.hidden = true; model.canvas.hidden = false;
      try { await drawFace(model.canvas, slide.sample); } catch { model.canvas.hidden = true; model.image.hidden = false; }
    }
  }

  async function moveSlide(model, direction) {
    if (!model.samplesLoaded) await ensureSamples(model);
    model.slideIndex = (model.slideIndex + direction + model.slides.length) % model.slides.length;
    await showSlide(model);
  }

  function currentFeature(model) {
    return model.slides[model.slideIndex]?.assetId || null;
  }

  async function openDuplicate(model, name, target, onConfirm) {
    state.duplicateAction = onConfirm;
    state.duplicateInput = model.input;
    document.querySelector("#duplicate-source").src = personThumb(model.person.id);
    document.querySelector("#duplicate-target").src = personThumb(target.id);
    document.querySelector("#duplicate-name").textContent = target.name;
    ui.duplicate.showModal();
    document.querySelector("#duplicate-confirm").focus();
  }

  function updateOptimisticNames(model, operation, extra) {
    state.names = state.names.filter(person => person.id !== model.person.id);
    if (operation === "rename") {
      state.names.push({ ...model.person, name: extra.name, isPending: true });
      state.names.sort((a, b) => a.name.localeCompare(b.name));
    }
  }

  function currentCardIndex(model) {
    const container = model.mode === "unnamed" ? ui.grid : model.mode === "investigate" ? ui.investigateGrid : ui.namedGrid;
    const index = [...container.querySelectorAll(".name-input")].indexOf(model.input);
    return Math.max(0, index);
  }

  function removeCardAndFocusNext(model) {
    const container = model.mode === "unnamed" ? ui.grid : model.mode === "investigate" ? ui.investigateGrid : ui.namedGrid;
    const inputs = [...container.querySelectorAll(".name-input")];
    const index = inputs.indexOf(model.input);
    const next = inputs[index + 1] || inputs.find(input => input !== model.input) || null;
    model.card.remove();
    if (model.mode === "unnamed" && !container.querySelector(".person-card")) document.querySelector("#unnamed-empty").hidden = false;
    if (model.mode === "investigate" && !container.querySelector(".investigate-card")) document.querySelector("#investigate-empty").hidden = false;
    requestAnimationFrame(() => next?.focus());
  }

  function queueCard(model, operation, extra = {}) {
    const focusIndex = currentCardIndex(model);
    const payload = { personId: model.person.id, operation, featureAssetId: currentFeature(model), ...extra };
    updateOptimisticNames(model, operation, extra);
    removeCardAndFocusNext(model);
    showToast(operation === "merge" ? `Merge into ${extra.targetName} queued` : operation === "hide" ? "Person queued as ignored" : `${extra.name} queued`);
    api("/api/queue", { method: "POST", body: payload })
      .then(() => refreshSummary())
      .catch(async error => {
        showToast(`Could not save the pending change: ${error.message}`);
        await loadNames();
        if (model.mode === "investigate") await renderInvestigate(true, focusIndex);
        else await renderPeopleGrid(model.mode, model.mode === "unnamed" ? ui.grid : ui.namedGrid, true, focusIndex);
        await refreshSummary();
      });
  }

  async function submitCard(model) {
    const name = cleanName(model.input.value);
    if (!name) return;
    model.list.classList.remove("open");
    if (model.selectedTarget && model.selectedTarget.name === name) {
      await queueCard(model, "merge", { name, targetPersonId: model.selectedTarget.id, targetName: model.selectedTarget.name });
      return;
    }
    if (["named", "investigate"].includes(model.mode) && name === model.person.name) {
      showToast("Name is unchanged"); return;
    }
    const exact = exactMatches(name, model.person.id);
    const saveSeparate = () => queueCard(model, "rename", { name });
    if (exact.length) { await openDuplicate(model, name, exact[0], saveSeparate); return; }
    await saveSeparate();
  }

  async function skipCard(model) {
    const focusIndex = currentCardIndex(model);
    await api("/api/skip", { method: "POST", body: { personId: model.person.id } });
    await renderPeopleGrid("unnamed", ui.grid, true, focusIndex);
  }

  async function investigateCard(model) {
    await api("/api/investigate/add", { method: "POST", body: { personId: model.person.id } });
    removeCardAndFocusNext(model);
    showToast("Moved to Investigate");
    await refreshSummary();
  }

  function attachNameBehavior(model) {
    const { input } = model;
    input.addEventListener("focus", () => updateSuggestions(model));
    input.addEventListener("input", () => updateSuggestions(model));
    input.addEventListener("blur", () => setTimeout(() => model.list.classList.remove("open"), 120));
    input.addEventListener("keydown", async event => {
      try {
        if (model.mode === "unnamed" && event.altKey && event.key.toLocaleLowerCase() === "i") {
          event.preventDefault(); await queueCard(model, "hide"); return;
        }
        if (model.mode === "unnamed" && event.altKey && event.key.toLocaleLowerCase() === "s") {
          event.preventDefault(); await skipCard(model); return;
        }
        if (model.mode === "unnamed" && event.altKey && event.key.toLocaleLowerCase() === "d") {
          event.preventDefault(); await investigateCard(model); return;
        }
        if (event.key === "PageDown" || event.key === "PageUp") {
          event.preventDefault(); await moveSlide(model, event.key === "PageDown" ? 1 : -1); return;
        }
        if (event.key === "ArrowDown" && model.matches.length) {
          event.preventDefault();
          if (model.selectedIndex < 0) model.draft = cleanName(input.value);
          selectSuggestion(model, Math.min(model.selectedIndex + 1, model.matches.length - 1)); return;
        }
        if (event.key === "ArrowUp" && model.selectedIndex >= 0) {
          event.preventDefault(); selectSuggestion(model, model.selectedIndex - 1); return;
        }
        if (event.key === "Tab" && !event.shiftKey && model.ghostCandidate) {
          event.preventDefault();
          const index = model.matches.findIndex(person => person.id === model.ghostCandidate.id);
          selectSuggestion(model, index); return;
        }
        if (event.key === "Enter") { event.preventDefault(); await submitCard(model); }
      } catch (error) { showToast(error.message); }
    });
  }

  function createPersonCard(person, mode, index) {
    const card = document.querySelector("#person-card-template").content.firstElementChild.cloneNode(true);
    const image = card.querySelector(".person-thumb"), canvas = card.querySelector(".sample-thumb"), input = card.querySelector(".name-input");
    image.src = personThumb(person.id); image.alt = person.name ? person.name : "Unnamed person";
    input.placeholder = mode === "unnamed" ? "Type a name…" : person.name;
    input.value = mode === "named" ? person.name : "";
    card.querySelector(".photo-count").textContent = person.assetCount == null ? "Photo count unavailable" : `${person.assetCount} photos`;
    card.querySelector(".round-count").textContent = person.skipCount ? `Round ${person.skipCount + 1}` : "";
    const model = {
      person, mode, cardIndex: index, card, image, canvas, input, list: card.querySelector(".suggestions"), ghost: card.querySelector(".ghost"),
      counter: card.querySelector(".carousel-count"), matches: [], ghostCandidate: null, selectedIndex: -1, selectedTarget: null,
      draft: input.value, slides: [{ type: "person", assetId: null }], slideIndex: 0, samplesLoaded: false,
    };
    const previous = card.querySelector(".previous"), next = card.querySelector(".next");
    previous.tabIndex = -1; next.tabIndex = -1;
    previous.addEventListener("click", () => moveSlide(model, -1));
    next.addEventListener("click", () => moveSlide(model, 1));
    if (mode === "unnamed") card.querySelector(".investigate-button").addEventListener("click", () => investigateCard(model).catch(error => showToast(error.message)));
    else card.querySelector(".card-actions").remove();
    attachNameBehavior(model); showSlide(model);
    return card;
  }

  async function renderPeopleGrid(kind, target, focusFirst = false, focusIndex = 0) {
    const query = kind === "named" ? document.querySelector("#named-search").value.trim() : "";
    const data = await api(`/api/people?kind=${encodeURIComponent(kind)}&sort=${encodeURIComponent(state.sort)}&page=${state.pages[kind] || 1}&size=${state.pageSize}&q=${encodeURIComponent(query)}`);
    const people = data.people;
    state.pages[kind] = data.page;
    target.replaceChildren(...people.map((person, index) => createPersonCard(person, kind, index)));
    renderPagination(`${kind}-pagination`, data, page => {
      state.pages[kind] = page; return renderPeopleGrid(kind, target, true);
    });
    if (kind === "unnamed") {
      document.querySelector("#unnamed-empty").hidden = data.total > 0;
      const banner = document.querySelector("#round-banner");
      const round = people.length ? (people[0].skipCount || 0) + 1 : 1;
      banner.hidden = round === 1;
      banner.textContent = `Round ${round} — only previously skipped clusters remain.`;
    }
    if (focusFirst && people.length) requestAnimationFrame(() => target.querySelectorAll(".name-input")[Math.min(focusIndex, people.length - 1)]?.focus());
  }

  function bindNamingControls() {
    let namedSearchTimer = null;
    document.querySelector("#named-search").addEventListener("input", () => {
      clearTimeout(namedSearchTimer); state.pages.named = 1;
      namedSearchTimer = setTimeout(() => renderPeopleGrid("named", ui.namedGrid).catch(error => showToast(error.message)), 180);
    });
    document.querySelector("#duplicate-confirm").addEventListener("click", async () => { ui.duplicate.close(); try { await state.duplicateAction?.(); } catch (error) { showToast(error.message); } finally { state.duplicateAction = null; } });
    document.querySelector("#duplicate-cancel").addEventListener("click", () => { ui.duplicate.close(); requestAnimationFrame(() => state.duplicateInput?.focus()); });
    ui.duplicate.addEventListener("cancel", event => { event.preventDefault(); ui.duplicate.close(); requestAnimationFrame(() => state.duplicateInput?.focus()); });
    ui.duplicate.addEventListener("keydown", event => { if (event.key === "Enter") { event.preventDefault(); document.querySelector("#duplicate-confirm").click(); } });
  }

  return { matchingNames, drawFace, moveSlide, showSlide, attachNameBehavior, renderPeopleGrid, bindNamingControls };
}
