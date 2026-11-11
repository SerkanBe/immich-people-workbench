// Copyright (C) 2026 Serkan Bekdemir
// SPDX-License-Identifier: AGPL-3.0-only

const RESULT_LIMIT = 16 * 1024 * 1024;
const APPLY_LIMIT = 4 * 1024 * 1024;
const GROUP_LIMIT = 50000;

export function formatBytes(value) {
  if (!Number.isFinite(value) || value < 0) return "unknown size";
  if (value < 1024) return `${value} B`;
  const unit = Math.min(4, Math.floor(Math.log(value) / Math.log(1024)));
  return `${(value / 1024 ** unit).toFixed(1)} ${["B", "KiB", "MiB", "GiB", "TiB"][unit]}`;
}

export function createOfflineWorkspace({ api, state, document, window, showToast, onImported = async () => {}, onNavigate = () => {} }) {
  const el = id => document.querySelector(`#${id}`);
  const activePhases = new Set(["planning", "downloading", "cancelling"]);
  const TRANSFER_KEY = "immichPeopleWorkbench.offlineTransfer.v1";
  let status = null, statusValid = false, busy = false, timer = null, generation = 0;
  let actionError = "", statusError = "", packagesError = "", snapshotError = "", packages = [], snapshots = [];
  let packageUncertain = false, packageId = null, packageStarting = false, transfer = null, transferEpoch = 0;
  let preview = null, previewGeneration = 0, expiryTimer = null, selectedFile = null, encodedFile = "";
  let previewBusy = false, pageBusy = false, applying = false, importDone = false, importMessage = "", importError = "";
  let currentView = state.view || "offline-create", previousView = "offline-create", helpTrigger = null;
  let lastContext, lastMode, revision = 0;
  const portable = () => state.summary?.portable === true || status?.portable === true;
  const sourceMode = () => !portable() && state.summary?.offline !== true && state.summary?.connected === true;
  const saveBusy = () => !!(state.localRequests || state.namingSaving || state.groupSaving || state.faceReviewSaving || state.pendingBusy || state.pendingUnverified || ["loading", "statistics"].includes(state.summary?.phase));
  const captureBusy = () => busy || applying || previewBusy || pageBusy || saveBusy();
  const mode = () => `${portable()}:${!!state.summary?.offline}:${!!state.summary?.connected}`;
  const context = () => `${mode()}:${state.localRevision || 0}`;
  const groupsLabel = value => `${value} ${value === 1 ? "group" : "groups"}`;
  const count = value => Number.isSafeInteger(value) && value >= 0 && value <= GROUP_LIMIT;
  const text = (tag, value, className) => { const node = document.createElement(tag); node.textContent = value; if (className) node.className = className; return node; };
  const errorText = error => error?.message || "Workbench could not complete this request.";
  function link(label, path) { const node = text("a", label); node.href = path; return node; }
  let savedPlanId = "";
  try {
    const saved = JSON.parse(window.sessionStorage?.getItem(TRANSFER_KEY) || "null");
    if (typeof saved?.planId === "string" && saved.planId.length <= 256) savedPlanId = saved.planId;
    if (typeof saved?.packageId === "string" && saved.packageId.length <= 256) packageId = saved.packageId;
  } catch { /* Optional IDs for GET-only reconciliation; never restore consent. */ }
  function remember() {
    try { window.sessionStorage?.setItem(TRANSFER_KEY, JSON.stringify({planId:savedPlanId,packageId})); } catch { /* Optional storage. */ }
  }
  function clearPreview(message = "") {
    previewGeneration += 1; clearTimeout(expiryTimer); expiryTimer = null;
    preview = null; previewBusy = false; pageBusy = false;
    el("offline-import-rows").replaceChildren(); importMessage = message;
  }
  function hasPreview() { return preview && preview.expires > Date.now(); }
  function paintImport() {
    const valid = hasPreview();
    el("offline-import-message").textContent = importMessage;
    el("offline-import-error").textContent = importError; el("offline-import-error").hidden = !importError;
    el("offline-import-review").hidden = !preview;
    el("offline-import-file").disabled = applying || !sourceMode() || !statusValid || saveBusy();
    el("offline-import-pending").hidden = !importDone || !(state.summary?.counts?.pending > 0);
    el("offline-import-retry").hidden = !selectedFile || !!preview;
    el("offline-import-retry").disabled = applying || previewBusy || pageBusy || saveBusy();
    el("offline-import-counts").textContent = preview ? `${preview.counts.ready} ready · ${preview.counts.applied} already imported · ${preview.counts.conflict} conflicting` : "";
    el("offline-import-selection").textContent = preview ? `${preview.selected.size} selected` : "";
    el("offline-import-apply").textContent = applying ? "Importing…" : "Import selected changes";
    el("offline-import-apply").disabled = !valid || !sourceMode() || captureBusy() || !preview.selected.size;
    for (const id of ["offline-import-all", "offline-import-none"]) el(id).disabled = !valid || captureBusy();
    el("offline-import-previous").disabled = !valid || pageBusy || applying || !preview?.pageIndex;
    el("offline-import-next").disabled = !valid || pageBusy || applying || !preview?.nextCursor;
    el("offline-import-pagination").hidden = !preview || (!preview.nextCursor && preview.pageIndex === 0);
    el("offline-import-page").textContent = preview ? `Page ${preview.pageIndex + 1}${pageBusy ? " · loading…" : ""}` : "";
    for (const checkbox of el("offline-import-rows").querySelectorAll("input")) checkbox.disabled = !valid || captureBusy();
  }
  function mediaReady() {
    const job = status?.job || {}, snapshot = status?.snapshot;
    if (!statusValid || status?.ready !== true || status.stale || status.staleReason || !snapshot?.id) return false;
    // ready can describe the previous snapshot while a new plan is pending.
    return job.planId ? job.phase === "ready" && snapshot.id === job.planId : job.phase === "idle";
  }
  function artifact() { return packages.find(item => item.id === packageId); }
  function packageActive() { return packageStarting || !!(packageId && !artifact()) || packages.some(item => item.status === "preparing"); }
  function readyArtifact() { const item = artifact(); return item?.status === "ready" && item.downloadReady === true ? item : null; }
  function paint() {
    const job = status?.job || {}, phase = job.phase || "idle", active = activePhases.has(phase);
    const isSource = sourceMode(), isPortable = portable(), offline = state.summary?.offline === true || isPortable;
    el("offline-navigation").hidden = !isSource;
    el("offline-exports-link").hidden = !isSource || !packages.length;
    el("offline-snapshots-link").hidden = !isSource;
    for (const [name,id] of [["create","create"],["import","import"],["exports","exports"],["snapshots","snapshots"]]) {
      el(`offline-${id}-content`).hidden = !isSource;
      el(`offline-${name}-unavailable`).hidden = isSource;
    }
    document.querySelectorAll?.("[data-offline-source-help]").forEach(node => { node.hidden = !isSource; });
    el("offline-status").hidden = !offline && !active && !packageActive();
    el("offline-status").textContent = offline ? "Working offline" : phase === "planning" ? "Finding photos…" : active ? `Saving photos · ${job.downloadedFiles || 0}/${job.assetCount || 0}` : packageActive() ? "Creating offline copy…" : "";
    el("offline-results-download").hidden = !isPortable;
    el("offline-results-download").setAttribute("aria-disabled", String(captureBusy()));
    if (isPortable && !captureBusy()) el("offline-results-download").href = "/api/offline/results";
    else el("offline-results-download").removeAttribute("href");
    el("offline-portable-help").hidden = !offline;
    el("offline-reconnect").hidden = !offline || isPortable;
    const warning = state.pendingUnverified ? "Open Pending to verify its saved selection." : saveBusy() ? "Saving changes…" : state.groupUnsaved ? "Some drafts could not be saved." : "";
    el("offline-save-notice").hidden = !warning; el("offline-save-notice").textContent = warning;
    const estimate = Number.isFinite(job.estimatedBytes) && job.estimatedBytes > 0 ? `${formatBytes(job.estimatedBytes)} estimated${job.unknownSizes ? ` · ${job.unknownSizes} sizes unknown` : ""}` : "Size unknown";
    const stepLabel = ({original:"Saving originals",preview:"Saving previews",person_thumbnail:"Saving portraits",activation:"Finishing"})[job.step] || "Saving photos";
    const saved = `${job.downloadedFiles || 0} / ${job.assetCount || 0} photos · ${formatBytes(job.downloadedBytes || 0)} saved`;
    const info = phase === "planning" ? "Finding photos…" : ["downloading","cancelling"].includes(phase) ? `${phase === "cancelling" ? "Stopping" : stepLabel} · ${saved}`
      : ["cancelled","error"].includes(phase) && job.planId ? saved
        : phase === "planned" || phase === "ready" ? `${job.assetCount ?? status?.snapshot?.assetCount ?? 0} photos · ${estimate}` : "";
    el("offline-workspace-description").textContent = info;
    el("offline-storage-path").textContent = status?.storagePath || "Workbench’s state directory";
    const ready = readyArtifact(), building = packageActive();
    const resumable = !!job.planId && ["planned","cancelled","error"].includes(phase);
    const matchingMedia = mediaReady();
    el("offline-plan").hidden = active || building || !!transfer;
    el("offline-scope-help").hidden = el("offline-plan").hidden;
    el("offline-plan").disabled = busy || !isSource;
    el("offline-plan").textContent = ready || matchingMedia || resumable ? "Find photos again" : "Find photos";
    el("offline-plan").className = matchingMedia || resumable || ready ? "quiet-button" : "primary-button";
    el("offline-copy-name").hidden = (!resumable && !matchingMedia) || !!ready || building || active || !!transfer;
    el("offline-package-name").disabled = busy || applying || !!transfer;
    el("offline-download").hidden = !isSource || !!ready || active || building || !!transfer || (!resumable && !matchingMedia);
    el("offline-download").disabled = !statusValid || captureBusy() || packageUncertain;
    el("offline-download").textContent = ["cancelled","error"].includes(phase) && job.planId ? "Resume" : matchingMedia || savedPlanId ? "Continue" : "Create offline copy";
    el("offline-cancel").hidden = !isSource || (!active && !building && !transfer);
    el("offline-cancel").disabled = busy || applying || phase === "cancelling" || building && !artifact();
    el("offline-package-message").textContent = building ? "Creating offline copy…" : ready ? "Ready" : transfer && saveBusy() ? "Waiting for saves…" : artifact()?.status === "error" ? artifact().error || "Copy creation failed." : "";
    el("offline-package-progress").hidden = !building;
    el("offline-package-download").hidden = !ready;
    if (ready) el("offline-package-download").href = `/api/offline/packages/${encodeURIComponent(ready.id)}/download`;
    else el("offline-package-download").removeAttribute("href");
    const progress = el("offline-workspace-progress"); progress.hidden = !active;
    if (phase !== "planning" && Number.isFinite(job.stepTotal) && job.stepTotal > 0 && Number.isFinite(job.stepDone) && job.stepDone >= 0) {
      progress.max = job.stepTotal; progress.value = Math.min(job.stepDone, job.stepTotal);
    } else if (phase !== "planning" && (!job.step || job.step === "original") && job.assetCount) {
      progress.max = job.assetCount; progress.value = job.downloadedFiles || 0;
    } else progress.removeAttribute("value");
    const error = actionError || statusError || job.error || packagesError;
    el("offline-workspace-error").hidden = !error; el("offline-workspace-error").textContent = error;
    el("offline-packages-error").hidden = !packagesError; el("offline-packages-error").textContent = packagesError;
    el("offline-refresh").hidden = !statusError && !actionError && !packagesError && !packageUncertain;
    el("offline-snapshot-create").disabled = !isSource || captureBusy();
    el("offline-snapshot-error").hidden = !snapshotError; el("offline-snapshot-error").textContent = snapshotError;
    paintImport();
  }
  function renderPackages() {
    const list = el("offline-package-list"); list.replaceChildren();
    if (!packages.length) list.append(text("p", "No exports", "muted"));
    for (const item of packages) {
      const row = text("article", "", "offline-history-row");
      row.append(text("strong", item.name || "Offline copy"), text("span", `${item.createdAt ? new Date(item.createdAt).toLocaleString() : ""} · ${item.assetCount ?? 0} photos · ${formatBytes(item.archiveBytes)}`, "muted"));
      row.append(text("span", ({preparing:"Creating",ready:"Ready",error:"Failed"})[item.status] || "Unavailable"));
      if (item.importSummary?.importedGroups) row.append(text("span", `${item.importSummary.importedGroups} changes imported`, "muted"));
      if (item.error) row.append(text("p", item.error, "error-text"));
      if (item.status === "ready" && item.downloadReady === true) row.append(link("Download offline copy", `/api/offline/packages/${encodeURIComponent(item.id)}/download`));
      if (item.status === "preparing") {
        const cancel = text("button", "Cancel", "quiet-button"); cancel.type = "button";
        cancel.addEventListener("click", () => cancelPackage(item.id)); row.append(cancel);
      }
      list.append(row);
    }
    const saved = el("offline-snapshot-list"); saved.replaceChildren();
    if (!snapshots.length) saved.append(text("p", "No snapshots", "muted"));
    for (const item of snapshots) {
      const row = text("div", "", "offline-history-row");
      row.append(text("span", `${item.name || "State snapshot"} · ${item.createdAt ? new Date(item.createdAt).toLocaleString() : ""}`), link("Download", `/api/offline/state-snapshots/${encodeURIComponent(item.id)}/download`)); saved.append(row);
    }
  }
  function schedule() {
    clearTimeout(timer);
    if (activePhases.has(status?.job?.phase) || transfer || packageActive()) timer = setTimeout(refresh, statusError || packagesError ? 3000 : 1000);
  }
  async function refresh(explicit = false) {
    const request = ++generation, current = mode(), source = sourceMode();
    const results = await Promise.allSettled([api("/api/offline/status"), ...(source ? [api("/api/offline/packages")] : [])]);
    if (request !== generation || current !== mode()) return;
    if (results[0].status === "fulfilled") { status = results[0].value; statusValid = true; statusError = ""; }
    else { statusValid = false; statusError = errorText(results[0].reason); }
    if (source && sourceMode()) {
      const result = results[1];
      if (result.status === "fulfilled" && Array.isArray(result.value.workspaces) && Array.isArray(result.value.snapshots) && result.value.portable !== true) {
        packages = result.value.workspaces; snapshots = result.value.snapshots; packagesError = "";
        if (packageId && !packages.some(item => item.id === packageId)) { packageId = null; actionError = "Previous copy is unavailable. Find photos again or continue."; }
        if (!packageId) packageId = packages.find(item => item.status === "preparing")?.id ?? null;
        if (explicit) packageUncertain = false;
        remember(); renderPackages();
      } else packagesError = result.status === "rejected" ? errorText(result.reason) : "Export status unavailable. Refresh status to retry.";
    }
    updateContext(); paint(); schedule(); await advanceTransfer();
  }
  function stopContinuation() { transfer = null; transferEpoch += 1; }
  async function mediaAction(path, body = {}) {
    if (busy || applying || !sourceMode()) return;
    busy = true; actionError = ""; generation += 1; clearTimeout(timer); paint();
    try { await api(path, {method:"POST",body}); }
    catch (error) { stopContinuation(); actionError = `${errorText(error)} Refresh status before continuing.`; }
    finally { busy = false; await refresh(); }
  }
  async function findPhotos() {
    if (captureBusy() || !sourceMode() || packageActive()) return;
    stopContinuation(); savedPlanId = ""; packageId = null; packageUncertain = false; remember();
    await mediaAction("/api/offline/plan");
  }
  async function createCopy() {
    if (!sourceMode() || !statusValid || captureBusy() || packageUncertain || packageActive()) return;
    const job = status?.job || {}, ready = mediaReady();
    if (!ready && (!job.planId || !["planned","cancelled","error"].includes(job.phase))) return;
    savedPlanId = job.planId || ""; remember();
    transfer = {planId:savedPlanId,snapshotId:ready ? status.snapshot.id : savedPlanId,name:el("offline-package-name").value.trim() || "Offline copy",mode:mode(),epoch:++transferEpoch};
    if (ready) await advanceTransfer();
    else await mediaAction("/api/offline/download", {planId:job.planId,confirmation:"DOWNLOAD_ORIGINALS"});
  }
  async function advanceTransfer() {
    if (!transfer || !sourceMode() || busy || captureBusy() || !statusValid) return;
    const job = status?.job || {};
    if (transfer.mode !== mode() || job.planId !== transfer.planId) { stopContinuation(); actionError = "Photo selection changed. Refresh status before continuing."; paint(); return; }
    if (["cancelled","error"].includes(job.phase)) { stopContinuation(); paint(); return; }
    if (!mediaReady() || status.snapshot.id !== transfer.snapshotId) return;
    const consent = transfer; transfer = null; busy = true; packageStarting = true; actionError = ""; paint();
    try {
      const result = await api("/api/offline/packages", {method:"POST",body:{name:consent.name}});
      if (consent.epoch !== transferEpoch || consent.mode !== mode()) return;
      if (typeof result.id !== "string" || !result.id) throw Error("Copy creation could not be confirmed.");
      packageId = result.id; remember();
    } catch (error) {
      if (consent.mode !== mode()) return;
      packageUncertain = true; actionError = `${errorText(error)} Refresh status before continuing.`;
    } finally { busy = false; packageStarting = false; await refresh(); }
  }
  async function cancelPackage(id) {
    if (!sourceMode() || busy || applying) return;
    stopContinuation(); busy = true; actionError = ""; paint();
    try { await api(`/api/offline/packages/${encodeURIComponent(id)}/cancel`, {method:"POST",body:{}}); }
    catch (error) { actionError = errorText(error); }
    finally { busy = false; await refresh(); }
  }
  async function cancel() {
    if (busy || applying || !sourceMode()) return;
    stopContinuation();
    if (artifact()?.status === "preparing") return cancelPackage(packageId);
    return mediaAction("/api/offline/cancel");
  }
  async function saveSnapshot() {
    if (!sourceMode() || captureBusy()) return;
    busy = true; snapshotError = ""; el("offline-snapshot-message").textContent = "Saving…"; paint();
    try {
      await api("/api/offline/state-snapshots", {method:"POST",body:{name:el("offline-snapshot-name").value.trim() || "State snapshot"}});
      el("offline-snapshot-message").textContent = "Snapshot saved"; await refresh();
    } catch (error) { snapshotError = errorText(error); el("offline-snapshot-message").textContent = ""; }
    finally { busy = false; paint(); }
  }
  function validatePage(data, readyIds) {
    if (!Array.isArray(data.changes) || data.changes.length > 200 || data.nextCursor != null && (typeof data.nextCursor !== "string" || data.nextCursor.length > 4096)) throw Error("Invalid preview page. Review the file again.");
    const seen = new Set();
    for (const group of data.changes) {
      if (!group || typeof group.id !== "string" || !group.id || group.id.length > 256 || seen.has(group.id) || !["ready","conflict","applied"].includes(group.status) || typeof group.label !== "string" || (group.status === "ready") !== readyIds.has(group.id)) throw Error("Invalid preview groups. Review the file again.");
      seen.add(group.id);
    }
  }
  function renderPreviewRows() {
    const rows = el("offline-import-rows"); rows.replaceChildren(); if (!preview) return;
    for (const group of preview.changes) {
      const row = text("div", "", "offline-import-row");
      if (group.status === "ready") {
        const label = text("label", "", "offline-import-choice"), checkbox = document.createElement("input");
        checkbox.type = "checkbox"; checkbox.checked = preview.selected.has(group.id);
        checkbox.addEventListener("change", () => {
          if (!hasPreview() || captureBusy()) { checkbox.checked = !!preview?.selected.has(group.id); return; }
          if (checkbox.checked) preview.selected.add(group.id); else preview.selected.delete(group.id); paintImport();
        });
        label.append(checkbox, text("span", group.label)); row.append(label);
      } else row.append(text("strong", group.label));
      row.append(text("span", ({ready:"Ready",conflict:"Conflict",applied:"Already imported"})[group.status], "muted"));
      if (group.reason) row.append(text("p", group.reason, group.status === "conflict" ? "error-text" : "muted"));
      rows.append(row);
    }
  }
  async function reviewFile() {
    if (!selectedFile || !sourceMode() || busy || applying || saveBusy()) return;
    clearPreview(); importDone = false; importError = ""; const request = previewGeneration, current = context();
    previewBusy = true; importMessage = encodedFile ? "Checking changes…" : "Reading result file…"; paint();
    try {
      if (!Number.isSafeInteger(selectedFile.size) || selectedFile.size <= 0 || selectedFile.size > RESULT_LIMIT) throw Error("Choose a result ZIP no larger than 16 MiB.");
      if (!encodedFile) {
        const buffer = await selectedFile.arrayBuffer();
        if (request !== previewGeneration || current !== context()) return;
        if (buffer.byteLength !== selectedFile.size || buffer.byteLength > RESULT_LIMIT) throw Error("The selected file changed. Choose it again.");
        const bytes = new Uint8Array(buffer), chunks = [];
        for (let i = 0; i < bytes.length; i += 0x6000) chunks.push(String.fromCharCode(...bytes.subarray(i, i + 0x6000)));
        encodedFile = btoa(chunks.join(""));
      }
      importMessage = "Checking changes…"; paintImport();
      const next = await api("/api/offline/import/preview", {method:"POST",body:{zipBase64:encodedFile}});
      if (request !== previewGeneration || current !== context()) return;
      const counts = next.counts, expires = Date.parse(next.expiresAt);
      if (typeof next.token !== "string" || !next.token || next.token.length > 4096 || !counts || ![counts.ready,counts.conflict,counts.applied].every(count) || counts.ready + counts.conflict + counts.applied > GROUP_LIMIT || !Array.isArray(next.readyIds) || next.readyIds.length !== counts.ready || !next.readyIds.every(id => typeof id === "string" && id.length > 0 && id.length <= 256) || !Number.isFinite(expires) || expires <= Date.now()) throw Error("Invalid or expired preview. Review the file again.");
      const readyIds = new Set(next.readyIds); if (readyIds.size !== next.readyIds.length) throw Error("Invalid preview IDs. Review the file again.");
      validatePage(next, readyIds);
      preview = {...next,counts,readyIds,selected:new Set(readyIds),expires,pageIndex:0,cursors:[null],firstPage:{changes:next.changes,nextCursor:next.nextCursor}};
      importMessage = next.workspaceName || "Returned workspace";
      if (next.lastImport && count(next.lastImport.imported) && next.lastImport.imported > 0) importMessage = `Previous import confirmed: ${groupsLabel(next.lastImport.imported)} imported${next.lastImport.snapshot ? "; state snapshot saved" : ""}. ${importMessage}`;
      renderPreviewRows();
      expiryTimer = setTimeout(() => { if (preview?.token === next.token) { clearPreview("Preview expired. Review the selected file again."); paint(); } }, Math.min(expires - Date.now(), 600000));
    } catch (error) {
      if (request !== previewGeneration || current !== context()) return;
      clearPreview(); importError = errorText(error);
    } finally { if (request === previewGeneration || !previewBusy) { previewBusy = false; paint(); } }
  }
  async function changePage(direction) {
    if (!hasPreview() || pageBusy || applying || !sourceMode()) return;
    const model = preview, request = previewGeneration, index = model.pageIndex + direction;
    if (index < 0 || direction > 0 && !model.nextCursor) return;
    const cursor = direction > 0 ? model.nextCursor : model.cursors[index];
    pageBusy = true; paintImport();
    try {
      const next = index === 0 ? model.firstPage : await api("/api/offline/import/preview/page", {method:"POST",body:{token:model.token,cursor}});
      if (request !== previewGeneration || model !== preview) return;
      validatePage(next, model.readyIds);
      if (direction > 0) { model.cursors.splice(index); model.cursors[index] = cursor; }
      model.pageIndex = index; model.changes = next.changes; model.nextCursor = next.nextCursor;
      renderPreviewRows();
    } catch (error) {
      if (request !== previewGeneration) return;
      clearPreview(); importError = `${errorText(error)} Review the file again before importing.`; paint();
    } finally { if (request === previewGeneration) { pageBusy = false; paint(); } }
  }
  function publishImporting(value) {
    state.offlineImporting = value;
    if (typeof document.dispatchEvent === "function" && typeof Event === "function") document.dispatchEvent(new Event("offline-import-state"));
  }
  async function applySelection() {
    if (!sourceMode() || !hasPreview() || captureBusy() || !preview.selected.size) return;
    const body = {token:preview.token,ids:[...preview.selected],confirmation:"IMPORT_LOCAL"};
    if (new TextEncoder().encode(JSON.stringify(body)).length > APPLY_LIMIT) { importError = "Too many selected IDs. Exclude some groups before importing."; paint(); return; }
    applying = true; publishImporting(true); importError = ""; paint();
    let result;
    try {
      result = await api("/api/offline/import/apply", {method:"POST",body});
      if (!count(result.imported) || result.imported === 0 || !count(result.skipped) || !result.snapshot || typeof result.snapshot.id !== "string") throw Error("The import response could not be verified.");
      clearPreview(`Imported ${result.imported} changes`);
      importDone = true;
      publishImporting(false);
      try { await onImported(result, {navigate:false}); }
      catch { showToast("Imported changes. Pending could not refresh."); }
    } catch (error) {
      clearPreview();
      const stale = ["preview_stale", "preview_expired"].includes(error?.code);
      importError = stale ? "The preview is no longer current. Review the selected file again before importing."
        : `${errorText(error)} Import was not confirmed. Review this file again to check what was saved before another import.`;
      // A lost response can follow a commit. Only an explicit fresh preview reconciles it.
    } finally { applying = false; if (state.offlineImporting) publishImporting(false); paint(); }
  }
  function updateContext() {
    const next = context(), nextMode = mode();
    if (next !== lastContext) { clearPreview(selectedFile ? "Review this file again: local state changed." : ""); importDone = false; }
    if (nextMode !== lastMode) { stopContinuation(); generation += 1; clearTimeout(timer); }
    lastContext = next; lastMode = nextMode;
  }
  const fieldHelp = {
    scope:["Which photos?", "Accessible still photos containing unnamed people, with the other faces for context. Videos are excluded; named people may have partial coverage."],
    name:["Copy name", "Optional. Leave blank to use Offline copy. The name identifies this export on the Exports page."],
  };
  document.querySelectorAll?.("[data-offline-help]").forEach(button => button.addEventListener("click", () => {
    const copy = fieldHelp[button.dataset.offlineHelp]; if (!copy) return;
    helpTrigger = button; el("offline-field-help-title").textContent = copy[0]; el("offline-field-help-copy").textContent = copy[1];
    el("offline-field-help").showModal(); el("offline-field-help-title").setAttribute("tabindex","-1"); el("offline-field-help-title").focus();
  }));
  el("offline-field-help").addEventListener("close", () => { if (!el("offline-field-help").open) helpTrigger?.focus(); });
  document.querySelectorAll?.("[data-offline-help-back]").forEach(button => button.addEventListener("click", async () => {
    const destination = previousView;
    await onNavigate(destination);
    if (currentView === destination && document.activeElement === button) {
      const heading = document.querySelector(".view.active h2"); heading?.setAttribute("tabindex", "-1"); heading?.focus();
    }
  }));
  el("offline-plan").addEventListener("click", findPhotos);
  el("offline-download").addEventListener("click", createCopy);
  el("offline-cancel").addEventListener("click", cancel);
  for (const id of ["offline-refresh","offline-exports-refresh"]) el(id).addEventListener("click", () => refresh(true));
  el("offline-snapshot-create").addEventListener("click", saveSnapshot);
  el("offline-results-download").addEventListener("click", event => { if (!portable() || captureBusy()) { event.preventDefault(); showToast("Wait for local saves to finish."); } });
  el("offline-import-file").addEventListener("change", () => {
    if (applying) return;
    selectedFile = el("offline-import-file").files?.[0] || null; encodedFile = ""; clearPreview(); importError = ""; importDone = false; paint();
    if (selectedFile) return reviewFile();
  });
  el("offline-import-retry").addEventListener("click", reviewFile);
  el("offline-import-all").addEventListener("click", () => { if (hasPreview() && !captureBusy()) { preview.selected = new Set(preview.readyIds); for (const checkbox of el("offline-import-rows").querySelectorAll("input")) checkbox.checked = true; paintImport(); } });
  el("offline-import-none").addEventListener("click", () => { if (hasPreview() && !captureBusy()) { preview.selected.clear(); for (const checkbox of el("offline-import-rows").querySelectorAll("input")) checkbox.checked = false; paintImport(); } });
  el("offline-import-previous").addEventListener("click", () => changePage(-1));
  el("offline-import-next").addEventListener("click", () => changePage(1));
  el("offline-import-apply").addEventListener("click", applySelection);
  document.addEventListener?.("workbench-save-state", () => {
    if (revision !== (state.localRevision || 0)) { revision = state.localRevision || 0; updateContext(); }
    paint(); advanceTransfer().catch(error => { stopContinuation(); actionError = errorText(error); paint(); });
  });
  lastContext = context(); lastMode = mode(); revision = state.localRevision || 0;
  paint();
  return {refresh, setView(view) {
    if (["offline-help","snapshots-help"].includes(view) && !["offline-help","snapshots-help"].includes(currentView)) previousView = currentView;
    currentView = view;
  }, async showView(view) {
    this.setView(view); paint();
    if (!statusValid || (["offline-exports", "snapshots"].includes(view) && !previewBusy && !applying)) await refresh();
  }, updateSummary() { updateContext(); paint(); }};
}
