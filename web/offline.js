// Copyright (C) 2026 Serkan Bekdemir
// SPDX-License-Identifier: AGPL-3.0-only

export function formatBytes(value) {
  if (!Number.isFinite(value) || value < 0) return "unknown size";
  if (value < 1024) return `${value} B`;
  const unit = Math.min(3, Math.floor(Math.log(value) / Math.log(1024)));
  return `${(value / 1024 ** unit).toFixed(1)} ${["B", "KiB", "MiB", "GiB"][unit]}`;
}

export function createOfflineWorkspace({ api, state, document, window, showToast }) {
  const el = id => document.querySelector(`#${id}`);
  const dialog = el("offline-workspace-dialog"), entry = el("offline-workspace-button");
  let status = null, busy = false, timer = null, generation = 0, actionError = "", statusError = "";
  const activePhases = new Set(["planning", "downloading", "cancelling"]);

  function paint() {
    const job = status?.job || {}, phase = job.phase || "idle", offline = state.summary?.offline === true;
    const active = activePhases.has(phase), snapshot = status?.snapshot;
    const badge = el("offline-status");
    badge.hidden = !offline && !snapshot && !active;
    const stale = status?.stale === true || !!status?.staleReason;
    const date = snapshot?.createdAt ? new Date(snapshot.createdAt).toLocaleString() : "";
    badge.textContent = offline ? `Offline · ${snapshot?.assetCount ?? 0} saved photos · partial library${date ? ` · ${date}` : ""}`
      : active ? (phase === "planning" ? "Preparing offline photo list…" : `Offline download · ${job.downloadedFiles || 0}/${job.assetCount || 0}`)
        : stale ? "Offline snapshot needs refresh" : snapshot ? `Offline snapshot · ${snapshot.assetCount} photos${date ? ` · ${date}` : ""}` : "";
    badge.title = state.summary?.offlineCoverage?.scope || "Only photos containing unnamed detected clusters are included.";
    let copy = offline ? "You are working offline. Pending and drafts are saved on this computer. Sync requires reconnecting."
      : phase === "planning" ? "Finding unique photos containing unnamed detected clusters…"
        : phase === "planned" ? `${job.assetCount || 0} unique photos · estimated ${formatBytes(job.estimatedBytes)}${job.unknownSizes ? ` plus ${job.unknownSizes} photos of unknown size` : ""}. Confirm to download originals.`
          : ["downloading", "cancelling"].includes(phase) ? `${phase === "cancelling" ? "Stopping" : "Downloading originals"}: ${job.downloadedFiles || 0} / ${job.assetCount || 0} photos · ${formatBytes(job.downloadedBytes || 0)} saved.`
            : ["cancelled", "error"].includes(phase) ? `Download incomplete: ${job.downloadedFiles || 0} / ${job.assetCount || 0} photos saved. Resume while connected.`
              : snapshot ? `Saved snapshot: ${snapshot.assetCount} photos · ${formatBytes(snapshot.totalBytes)}${date ? ` · ${date}` : ""}.`
                : "Connect to Immich, then prepare a photo list to review its size before downloading.";
    if (stale && !offline && !active) copy += ` ${status.staleReason || "The saved snapshot needs refresh after Sync."} Stored originals are retained. Choose Prepare for offline use before opening or exporting it again.`;
    if (offline) copy += " Coverage is limited to the saved photo selection; Face review can be partial. Videos and photos outside the selection need Immich.";
    el("offline-workspace-description").textContent = copy;
    el("offline-plan").hidden = offline || active;
    el("offline-plan").disabled = busy || !state.summary?.connected;
    const resumable = ["planned", "cancelled", "error"].includes(phase) && !!job.planId;
    el("offline-download").hidden = offline || !resumable;
    el("offline-download").disabled = busy;
    el("offline-download").textContent = phase === "planned" ? "Download originals" : "Resume download";
    el("offline-cancel").hidden = offline || !active;
    el("offline-cancel").disabled = busy || phase === "cancelling";
    el("offline-open").hidden = offline || stale || status?.ready !== true;
    el("offline-open").disabled = busy || active;
    el("offline-reconnect").hidden = !offline;
    const progress = el("offline-workspace-progress");
    progress.hidden = !active;
    if (job.assetCount && phase !== "planning") { progress.max = job.assetCount; progress.value = job.downloadedFiles || 0; }
    else progress.removeAttribute("value");
    const error = el("offline-workspace-error");
    error.hidden = !(actionError || statusError || job.error); error.textContent = actionError || statusError || job.error || "";
  }

  async function refresh() {
    const request = ++generation;
    try {
      const next = await api("/api/offline/status");
      if (request !== generation) return;
      statusError = ""; status = next; paint();
      clearTimeout(timer);
      if (activePhases.has(status.job?.phase)) timer = setTimeout(refresh, 1000);
    } catch (error) {
      if (request !== generation) return;
      statusError = error.message; paint();
      // Retain controls for an explicit retry after a lost progress response.
      if (dialog.open) timer = setTimeout(refresh, 3000);
    }
  }

  async function act(path, body = {}) {
    if (busy) return;
    actionError = ""; busy = true; generation += 1; clearTimeout(timer); paint();
    try {
      await api(path, { method: "POST", body });
      if (path === "/api/offline/open") { window.location.assign("/naming/clusters"); return; }
      await refresh();
    } catch (error) { actionError = error.message; showToast(error.message); el("offline-workspace-error").textContent = error.message; el("offline-workspace-error").hidden = false; }
    finally { busy = false; paint(); }
  }

  entry.addEventListener("click", () => { if (!dialog.open) dialog.showModal(); el("offline-workspace-title").setAttribute("tabindex", "-1"); el("offline-workspace-title").focus(); return refresh(); });
  dialog.addEventListener("close", () => { if (!dialog.open) entry.focus(); });
  el("offline-plan").addEventListener("click", () => { if (!state.summary?.offline) return act("/api/offline/plan"); });
  el("offline-download").addEventListener("click", () => {
    if (state.summary?.offline || !status?.job?.planId || !["planned", "cancelled", "error"].includes(status.job.phase)) return;
    return act("/api/offline/download", { planId: status.job.planId, confirmation: "DOWNLOAD_ORIGINALS" });
  });
  el("offline-cancel").addEventListener("click", () => act("/api/offline/cancel"));
  el("offline-open").addEventListener("click", () => { if (status?.ready && !status?.stale && !status?.staleReason && !activePhases.has(status.job?.phase)) return act("/api/offline/open"); });
  return { refresh, updateSummary() { paint(); } };
}
