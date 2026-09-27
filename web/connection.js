// Copyright (C) 2026 Serkan Bekdemir
// SPDX-License-Identifier: AGPL-3.0-only
import { readBrowserSettings, saveBrowserSettings, forgetBrowserSettings } from "./browser-storage.js";

export function createConnection({ api, showToast, ui, waitUntilReady }) {
  const savedConnection = readBrowserSettings();

  function fillConnectionForm(settings) {
    if (!settings) return;
    document.querySelector("#immich-url").value = settings.url;
    document.querySelector("#immich-key").value = settings.apiKey;
    document.querySelector("#insecure-tls").checked = settings.insecureTls;
    document.querySelector("#remember-connection").checked = true;
    document.querySelector("#forget-settings").disabled = false;
  }

  function connectionFormSettings() {
    return {
      url: document.querySelector("#immich-url").value.trim(),
      apiKey: document.querySelector("#immich-key").value,
      insecureTls: document.querySelector("#insecure-tls").checked,
    };
  }

  async function connectWithSettings(settings) {
    return api("/api/connect", { method: "POST", body: settings });
  }

  function bindConnectionControls() {
    document.querySelector("#connect-button").addEventListener("click", () => ui.connect.showModal());
    document.querySelector("#connect-form").addEventListener("submit", async event => {
      event.preventDefault();
      const settings = connectionFormSettings();
      const remember = document.querySelector("#remember-connection").checked;
      try {
        await connectWithSettings(settings);
        let stored = true;
        if (remember) stored = saveBrowserSettings(settings);
        else forgetBrowserSettings();
        document.querySelector("#forget-settings").disabled = !remember || !stored;
        if (!remember) document.querySelector("#immich-key").value = "";
        ui.connect.close(); await waitUntilReady();
        if (remember && !stored) showToast("Connected, but this browser did not allow the settings to be saved.");
      } catch (error) { showToast(error.message); }
    });
    document.querySelector("#forget-settings").addEventListener("click", () => {
      forgetBrowserSettings();
      document.querySelector("#forget-settings").disabled = true;
      document.querySelector("#remember-connection").checked = false;
      document.querySelector("#immich-key").value = "";
      showToast("Saved browser settings removed. The current server session remains connected until restart.");
    });
    fillConnectionForm(savedConnection);
    document.querySelector("#forget-settings").disabled = !savedConnection;
  }

  async function restoreConnection() {
    if (savedConnection?.apiKey) {
      try { await connectWithSettings(savedConnection); await waitUntilReady(); }
      catch (error) { showToast(`Automatic connection failed: ${error.message}`); ui.connect.showModal(); }
    } else ui.connect.showModal();
  }

  return { bindConnectionControls, restoreConnection };
}
