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

export function createOfflineWorkspace({ api, state, document, window, showToast, onImported = async () => {} }) {
  const el = id => document.querySelector(`#${id}`);
  const dialog = el("offline-workspace-dialog"), entry = el("offline-workspace-button");
  const activePhases = new Set(["planning", "downloading", "cancelling"]);
  let status = null, statusValid = false, busy = false, timer = null, generation = 0;
  let actionError = "", statusError = "", packagesError = "", packages = [], snapshots = [], packagesGeneration = 0, packagesTimer = null;
  let packageUncertain = false, packageId = null, packageStarting = false, preview = null, previewGeneration = 0, expiryTimer = null;
  let selectedFile = null, encodedFile = "", previewBusy = false, pageBusy = false, applying = false;
  let importMessage = "", importError = "", session = 0, navigating = false, lastContext, lastMode, revision = 0;

  const portable = () => state.summary?.portable === true || status?.portable === true;
  const sourceMode = () => !portable() && state.summary?.offline !== true && state.summary?.connected === true;
  const saveBusy = () => !!(state.localRequests || state.namingSaving || state.groupSaving || state.faceReviewSaving || state.pendingBusy || state.pendingUnverified || ["loading", "statistics"].includes(state.summary?.phase));
  const captureBusy = () => busy || applying || previewBusy || pageBusy || saveBusy();
  const mode = () => `${portable()}:${!!state.summary?.offline}:${!!state.summary?.connected}`;
  const context = () => `${mode()}:${state.localRevision || 0}`;
  const groupsLabel = size => `${size} ${size === 1 ? "group" : "groups"}`;
  const count = value => Number.isSafeInteger(value) && value >= 0 && value <= GROUP_LIMIT;
  const text = (tag, value, className) => { const node = document.createElement(tag); node.textContent = value; if (className) node.className = className; return node; };
  const errorText = error => error?.message || "Workbench could not complete this request. Retry explicitly.";
  function link(label, path) { const node = text("a", label); node.href = path; return node; }

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
    el("offline-import-file").disabled = applying;
    el("offline-import-retry").hidden = !selectedFile || !!preview;
    el("offline-import-retry").disabled = applying || previewBusy || pageBusy || saveBusy();
    el("offline-import-counts").textContent = preview ? `${preview.counts.ready} ready · ${preview.counts.applied} already imported · ${preview.counts.conflict} conflicting` : "";
    el("offline-import-selection").textContent = preview ? `${groupsLabel(preview.selected.size)} selected across all pages` : "";
    el("offline-import-apply").textContent = applying ? "Importing locally…" : "Import selected changes";
    el("offline-import-apply").disabled = !valid || !sourceMode() || captureBusy() || !preview.selected.size;
    for (const id of ["offline-import-all", "offline-import-none"]) el(id).disabled = !valid || captureBusy();
    el("offline-import-previous").disabled = !valid || pageBusy || applying || !preview?.pageIndex;
    el("offline-import-next").disabled = !valid || pageBusy || applying || !preview?.nextCursor;
    el("offline-import-page").textContent = preview ? `Page ${preview.pageIndex + 1}${pageBusy ? " · loading…" : ""}` : "";
    for (const checkbox of el("offline-import-rows").querySelectorAll("input")) checkbox.disabled = !valid || captureBusy();
  }
  function paint() {
    const job = status?.job || {}, phase = job.phase || "idle", offline = state.summary?.offline === true;
    const active = activePhases.has(phase), snapshot = status?.snapshot, isPortable = portable();
    const stale = status?.stale === true || !!status?.staleReason;
    const date = snapshot?.createdAt ? new Date(snapshot.createdAt).toLocaleString() : "";
    const badge = el("offline-status"); badge.hidden = !offline && !snapshot && !active && !isPortable;
    badge.textContent = offline || isPortable ? `Offline · ${snapshot?.assetCount ?? 0} saved photos · partial library${date ? ` · ${date}` : ""}`
      : active ? phase === "planning" ? "Checking photos and size…" : `Offline download · ${job.downloadedFiles || 0}/${job.assetCount || 0}`
        : stale ? "Offline snapshot needs refresh" : snapshot ? `Offline snapshot · ${snapshot.assetCount} photos${date ? ` · ${date}` : ""}` : "";
    badge.title = state.summary?.offlineCoverage?.scope || "Only photos containing unnamed detected clusters are included.";
    let copy = isPortable ? "Working offline. Download your changes and return them to the Workbench that created this copy."
      : offline ? "Working offline. Changes stay in Pending until you reconnect and Sync."
        : phase === "planning" ? "Checking photos and size. No photos are being downloaded…"
          : phase === "planned" ? `${job.assetCount || 0} photos · estimated ${formatBytes(job.estimatedBytes)}${job.unknownSizes ? ` plus ${job.unknownSizes} photos of unknown size` : ""}.`
            : ["downloading", "cancelling"].includes(phase) ? `${phase === "cancelling" ? "Stopping" : "Downloading originals"}: ${job.downloadedFiles || 0} / ${job.assetCount || 0} photos · ${formatBytes(job.downloadedBytes || 0)} saved.`
              : ["cancelled", "error"].includes(phase) && job.planId ? `Download incomplete: ${job.downloadedFiles || 0} / ${job.assetCount || 0} photos saved. Resume while connected.`
                : phase === "error" ? "The photo check could not finish. Check photos & size to try again."
                  : snapshot ? `Saved snapshot: ${snapshot.assetCount} photos · ${formatBytes(snapshot.totalBytes)}${date ? ` · ${date}` : ""}.`
                    : "Check which photos are needed and their estimated size. No photos are downloaded yet.";
    if (stale && !offline && !isPortable && ["idle", "ready"].includes(phase)) copy = "This workspace needs refreshing after Sync. Check photos & size to prepare it again. Stored files are kept.";
    el("offline-workspace-description").textContent = copy;
    el("offline-workspace-intro").hidden = isPortable || offline;
    el("offline-storage").hidden = isPortable || offline;
    el("offline-storage-path").textContent = status?.storagePath || "Workbench’s state directory";
    el("offline-plan").textContent = "Check photos & size";
    el("offline-plan").hidden = offline || isPortable || active || phase === "planned";
    el("offline-plan").disabled = busy || !state.summary?.connected;
    const resumable = ["planned", "cancelled", "error"].includes(phase) && !!job.planId;
    el("offline-download").hidden = offline || isPortable || !resumable;
    el("offline-download").disabled = busy;
    el("offline-download").textContent = phase === "planned" ? "Download & prepare" : "Resume download";
    el("offline-cancel").hidden = offline || isPortable || !active;
    el("offline-cancel").disabled = busy || phase === "cancelling";
    el("offline-open").hidden = offline || isPortable || stale || !statusValid || status?.ready !== true;
    el("offline-open").disabled = busy || active;
    el("offline-reconnect").hidden = !offline || isPortable;
    el("offline-source-sections").hidden = !sourceMode();
    el("offline-package-create").hidden = !statusValid || status?.ready !== true || stale || active;
    const artifact = packages.find(item => item.id === packageId);
    const building = packageStarting || !!(packageId && !artifact) || packages.some(item => item.status === "preparing");
    const downloadReady = artifact?.status === "ready" && artifact.downloadReady === true;
    el("offline-package-build").disabled = captureBusy() || packageUncertain || building;
    el("offline-package-build").className = downloadReady ? "quiet-button" : "primary-button";
    el("offline-package-build").textContent = artifact?.status === "error" ? "Retry ZIP creation" : downloadReady ? "Create another portable ZIP" : "Create portable ZIP";
    el("offline-package-message").textContent = building ? "Preparing portable ZIP…" : downloadReady ? "Portable ZIP is ready." : artifact?.status === "error" ? artifact.error || "ZIP preparation failed." : "";
    el("offline-package-progress").hidden = !building;
    const download = el("offline-package-download");
    download.hidden = !downloadReady;
    if (downloadReady) download.href = `/api/offline/packages/${encodeURIComponent(artifact.id)}/download`;
    else download.removeAttribute("href");
    el("offline-package-name").disabled = busy || applying;
    el("offline-results").hidden = !isPortable;
    el("offline-results-download").setAttribute("aria-disabled", String(captureBusy()));
    if (isPortable && !captureBusy()) el("offline-results-download").href = "/api/offline/results";
    else el("offline-results-download").removeAttribute("href");
    const savedWarning = state.pendingUnverified ? "Open Pending again to verify its saved selection." : saveBusy() ? "Wait for local saves to finish." : state.groupUnsaved ? "Some drafts could not be saved. Only saved changes are included." : "Only saved changes are included.";
    el("offline-save-notice").textContent = savedWarning;
    el("offline-snapshot-create").disabled = captureBusy();
    const progress = el("offline-workspace-progress"); progress.hidden = !active;
    if (job.assetCount && phase !== "planning") { progress.max = job.assetCount; progress.value = job.downloadedFiles || 0; }
    else progress.removeAttribute("value");
    const error = el("offline-workspace-error");
    error.hidden = !(actionError || statusError || job.error); error.textContent = actionError || statusError || job.error || "";
    el("offline-packages-error").textContent = packagesError; el("offline-packages-error").hidden = !packagesError;
    paintImport();
  }

  async function refresh() {
    const request = ++generation, current = mode();
    try {
      const next = await api("/api/offline/status");
      if (request !== generation || current !== mode()) return;
      statusError = ""; status = next; statusValid = true; updateContext(); paint();
      clearTimeout(timer);
      if (activePhases.has(status.job?.phase)) timer = setTimeout(refresh, 1000);
    } catch (error) {
      if (request !== generation || current !== mode()) return;
      statusValid = false; statusError = errorText(error); paint();
      clearTimeout(timer); if (dialog.open) timer = setTimeout(refresh, 3000);
    }
  }
  function renderPackages() {
    const list = el("offline-package-list"); list.replaceChildren();
    if (!packages.length) list.append(text("p", "No portable exports yet.", "muted"));
    for (const item of packages) {
      const row = text("article", "", "offline-history-row");
      row.append(text("strong", item.name || "Portable workspace"));
      row.append(text("span", `${item.createdAt ? new Date(item.createdAt).toLocaleString() : ""} · ${item.assetCount ?? 0} photos · media ${formatBytes(item.mediaBytes)} · ZIP ${formatBytes(item.archiveBytes)}`, "muted"));
      row.append(text("span", ({preparing:"Preparing",ready:"Ready",error:"Failed"})[item.status] || "Unavailable"));
      if (item.importSummary?.importedGroups) row.append(text("span", `${groupsLabel(item.importSummary.importedGroups)} imported${item.importSummary.lastImportedAt ? ` · ${new Date(item.importSummary.lastImportedAt).toLocaleString()}` : ""}`, "muted"));
      if (item.error) row.append(text("p", item.error, "error-text"));
      if (item.status === "ready" && item.downloadReady === true) row.append(link("Download portable ZIP", `/api/offline/packages/${encodeURIComponent(item.id)}/download`));
      if (item.status === "preparing") {
        const cancel = text("button", "Cancel ZIP preparation", "quiet-button"); cancel.type = "button";
        cancel.addEventListener("click", () => sourceAction(`/api/offline/packages/${encodeURIComponent(item.id)}/cancel`, {})); row.append(cancel);
      }
      list.append(row);
    }
    const saved = el("offline-snapshot-list"); saved.replaceChildren();
    if (!snapshots.length) saved.append(text("p", "No saved state snapshots yet.", "muted"));
    for (const item of snapshots) {
      const row = text("div", "", "offline-history-row");
      row.append(text("span", `${item.name || "State snapshot"} · ${item.createdAt ? new Date(item.createdAt).toLocaleString() : ""} · ${item.reason || ""}`));
      row.append(link("Download state snapshot", `/api/offline/state-snapshots/${encodeURIComponent(item.id)}/download`)); saved.append(row);
    }
  }
  async function refreshPackages(explicit = false) {
    if (!sourceMode()) return;
    const request = ++packagesGeneration, current = mode();
    try {
      const next = await api("/api/offline/packages");
      if (request !== packagesGeneration || current !== mode() || !sourceMode()) return;
      if (!Array.isArray(next.workspaces) || !Array.isArray(next.snapshots) || next.portable === true) throw Error("Export history could not be read. Refresh to retry.");
      packages = next.workspaces; snapshots = next.snapshots; packagesError = "";
      if (!packageId) packageId = packages.find(item => item.status === "preparing")?.id ?? null;
      if (explicit) packageUncertain = false;
      renderPackages(); paint(); clearTimeout(packagesTimer);
      if (dialog.open && (packages.some(item => item.status === "preparing") || packageId && !packages.some(item => item.id === packageId))) packagesTimer = setTimeout(refreshPackages, 1000);
    } catch (error) {
      if (request !== packagesGeneration || current !== mode()) return;
      packagesError = errorText(error); paint();
      // Keep prior ready downloads visible. Never replay a create/cancel request.
    }
  }
  async function act(path, body = {}) {
    if (busy || applying || !sourceMode()) return;
    actionError = ""; busy = true; generation += 1; clearTimeout(timer); paint();
    try {
      await api(path, { method: "POST", body });
      if (path === "/api/offline/open") { window.location.assign("/naming/clusters"); return; }
      await refresh();
    } catch (error) { actionError = errorText(error); showToast(actionError); }
    finally { busy = false; paint(); }
  }
  async function sourceAction(path, body) {
    const creating = path === "/api/offline/packages", cancel = path.endsWith("/cancel");
    if (!sourceMode() || (cancel ? busy || applying : captureBusy())) return;
    busy = true; packageStarting = creating; packagesError = ""; paint();
    try {
      const result = await api(path, {method:"POST",body});
      if (creating) {
        if (typeof result.id !== "string" || !result.id) throw Error("ZIP preparation could not be confirmed.");
        packageId = result.id;
      }
      await refreshPackages();
    } catch (error) {
      packagesError = errorText(error);
      if (creating && !error?.status) { packageUncertain = true; packagesError += " Refresh export history before creating another ZIP."; }
    } finally { busy = false; packageStarting = false; paint(); }
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
    clearPreview(); importError = ""; const request = previewGeneration, current = context();
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
      const conflictHelp = counts.conflict ? " Conflicts stay unchanged; reconcile them at the source or prepare a fresh export." : "";
      importMessage = `${next.workspaceName || "Returned workspace"} · review the groups below.${conflictHelp}`;
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
    const applySession = session; applying = true; publishImporting(true); importError = ""; paint();
    let result;
    try {
      result = await api("/api/offline/import/apply", {method:"POST",body});
      if (!count(result.imported) || result.imported === 0 || !count(result.skipped) || !result.snapshot || typeof result.snapshot.id !== "string") throw Error("The import response could not be verified.");
      clearPreview(`Imported ${groupsLabel(result.imported)} locally · ${result.skipped} skipped. State snapshot saved. Review Pending before Sync.`);
      publishImporting(false);
      const navigate = sourceMode() && dialog.open && session === applySession;
      if (navigate) { navigating = true; dialog.close(); }
      try { await onImported(result, {navigate}); }
      catch { showToast("Import was saved. Open Pending again to refresh its review before Sync."); }
      if (!navigate) showToast(`Imported ${groupsLabel(result.imported)} locally. Open Pending to review them.`);
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
    if (next !== lastContext) clearPreview(selectedFile ? "Local state or mode changed. Review the selected file again." : "");
    if (nextMode !== lastMode) { packagesGeneration += 1; clearTimeout(packagesTimer); }
    lastContext = next; lastMode = nextMode;
  }
  lastContext = context(); lastMode = mode(); revision = state.localRevision || 0;
  entry.addEventListener("click", async () => {
    const opened = ++session; navigating = false;
    if (!dialog.open) dialog.showModal();
    el("offline-workspace-title").setAttribute("tabindex", "-1"); el("offline-workspace-title").focus();
    await refresh(); if (dialog.open && session === opened) await refreshPackages();
  });
  dialog.addEventListener("close", () => {
    if (dialog.open) return;
    session += 1; packagesGeneration += 1; clearTimeout(packagesTimer);
    if (!applying) clearPreview(selectedFile ? "Review the selected file again before importing." : "");
    if (!navigating) entry.focus(); paint();
  });
  el("offline-refresh").addEventListener("click", async () => { await refresh(); await refreshPackages(true); });
  el("offline-plan").addEventListener("click", () => { if (!state.summary?.offline) return act("/api/offline/plan"); });
  el("offline-download").addEventListener("click", () => {
    if (state.summary?.offline || !status?.job?.planId || !["planned","cancelled","error"].includes(status.job.phase)) return;
    return act("/api/offline/download", {planId:status.job.planId,confirmation:"DOWNLOAD_ORIGINALS"});
  });
  el("offline-cancel").addEventListener("click", () => act("/api/offline/cancel"));
  el("offline-open").addEventListener("click", () => { if (statusValid && status?.ready && !status?.stale && !status?.staleReason && !activePhases.has(status.job?.phase)) return act("/api/offline/open"); });
  el("offline-package-build").addEventListener("click", () => {
    if (!statusValid || status?.ready !== true || status?.stale || status?.staleReason || activePhases.has(status.job?.phase) || packageUncertain || packageId && !packages.some(item => item.id === packageId) || packages.some(item => item.status === "preparing")) return;
    const name = el("offline-package-name").value.trim();
    if (!name) { packagesError = "Enter a name for this portable ZIP."; paint(); el("offline-package-name").focus(); return; }
    return sourceAction("/api/offline/packages", {name});
  });
  el("offline-snapshot-create").addEventListener("click", () => sourceAction("/api/offline/state-snapshots", {name:el("offline-snapshot-name").value.trim() || "State snapshot"}));
  el("offline-results-download").addEventListener("click", event => { if (!portable() || captureBusy()) { event.preventDefault(); showToast("Wait for local saves to finish before downloading changes."); } });
  el("offline-import-file").addEventListener("change", () => {
    if (applying) return;
    selectedFile = el("offline-import-file").files?.[0] || null; encodedFile = ""; clearPreview(); importError = ""; paint();
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
    paint();
  });
  return {refresh, updateSummary() { updateContext(); paint(); }};
}
