// Copyright (C) 2026 Serkan Bekdemir
// SPDX-License-Identifier: AGPL-3.0-only
"use strict";

const URL_KEY = "immichPeopleWorkbench.url.v1";
const OLD_KEYS = ["immichPeopleWorkbench.connection.v1", "immichPeopleConsole.connection.v1"];
const form = document.querySelector("#login-form");
const error = document.querySelector("#login-error");
const urlInput = document.querySelector("#immich-url");
const keyInput = document.querySelector("#immich-api-key");
const emailInput = document.querySelector("#immich-email");
const passwordInput = document.querySelector("#immich-password");
const keyMode = document.querySelector("#mode-key");
const loginMode = document.querySelector("#mode-login");
const button = document.querySelector("#login-button");
let submitting = false;

// URL storage is optional. Attempt legacy deletion separately even if a read fails.
let savedUrl;
try { savedUrl = localStorage.getItem(URL_KEY); } catch { /* Optional storage. */ }
for (const key of OLD_KEYS) {
  try {
    const old = localStorage.getItem(key);
    if (!savedUrl && old) {
      try { savedUrl = JSON.parse(old)?.url; } catch { /* Invalid old settings. */ }
    }
  } catch { /* Optional storage. */ }
  try { localStorage.removeItem(key); } catch { /* Optional storage. */ }
}
if (typeof savedUrl === "string" && savedUrl) urlInput.value = savedUrl;

function clearSecrets() { keyInput.value = ""; passwordInput.value = ""; }
function updateMode(focus = false) {
  clearSecrets();
  const isKey = keyMode.checked;
  document.querySelector("#key-fields").hidden = !isKey;
  document.querySelector("#login-fields").hidden = isKey;
  keyInput.disabled = !isKey;
  emailInput.disabled = isKey;
  passwordInput.disabled = isKey;
  button.textContent = isKey ? "Connect" : "Sign in";
  error.hidden = true;
  if (focus) (isKey ? keyInput : emailInput).focus();
}
for (const input of [keyMode, loginMode]) input.addEventListener("change", () => updateMode(true));
updateMode();

form.addEventListener("submit", async event => {
  event.preventDefault();
  if (submitting || !form.reportValidity()) return;
  submitting = true;
  error.hidden = true;
  button.disabled = true;
  keyMode.disabled = true;
  loginMode.disabled = true;
  const isKey = keyMode.checked;
  const url = urlInput.value.trim();
  try {
    const payload = { url, insecureTls: document.querySelector("#insecure-tls").checked };
    if (isKey) payload.apiKey = keyInput.value;
    else { payload.email = emailInput.value.trim(); payload.password = passwordInput.value; }
    const response = await fetch(isKey ? "/api/connect-key" : "/api/login", {
      method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload),
    });
    const result = await response.json().catch(() => ({}));
    if (!response.ok) {
      if (isKey && ["invalid_key", "missing_permissions", "unsupported_introspection"].includes(result.code)) clearSecrets();
      // Only the server's sanitized user-facing message is displayed.
      throw new Error(result.error || "Connection failed. Re-enter your credentials and retry.");
    }
    try { localStorage.setItem(URL_KEY, url); } catch { /* Optional convenience. */ }
    clearSecrets();
    const next = new URLSearchParams(window.location.search).get("next");
    const routes = new Set(["/", "/unnamed", "/naming/clusters", "/naming/group-photos", "/investigate", "/faces", "/pending", "/named", "/ignored"]);
    window.location.replace(routes.has(next) ? next : "/");
  } catch (cause) {
    error.textContent = cause instanceof TypeError ? "Cannot reach Workbench. Check the connection and retry." : cause.message;
    error.hidden = false;
  } finally {
    submitting = false;
    button.disabled = false;
    keyMode.disabled = false;
    loginMode.disabled = false;
  }
});
