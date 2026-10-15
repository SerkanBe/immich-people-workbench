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
const permissionDetails = document.querySelector("#key-permissions");
const permissionItems = [...document.querySelectorAll("[data-permission]")];
const notice = document.querySelector("#connection-notice");
const noticeTitle = document.querySelector("#connection-notice-title");
const noticeCopy = document.querySelector("#connection-notice-copy");
const KNOWN_PERMISSIONS = new Set(permissionItems.map(item => item.dataset.permission));
const NOTICE_COPY = {
  missing_permissions: ["Some required permissions are missing", "Edit this API key in Immich to add the highlighted permissions, then enter it below to reconnect."],
  invalid_key: ["Immich rejected this API key", "The key may have been revoked. Enter a valid key below to reconnect."],
  session_expired: ["The Workbench connection expired", "Reconnect below to continue reviewing your library."],
  unavailable: ["Immich could not be reached", "Check the Immich URL and service, then reconnect below."],
  unsupported_introspection: ["Immich permissions could not be verified", "Use a compatible Immich server or connect with Admin login below."],
  invalid_login: ["Admin access needs to be renewed", "Sign in again below with an Immich administrator account."],
  owner_mismatch: ["Reconnect to the original Immich account", "This state directory is bound to one account and server. Use a separate state directory for another."],
  connection_ended: ["The Workbench connection ended", "Reconnect below to continue reviewing your library."],
};
let submitting = false;

function highlightMissing(values) {
  const missing = new Set(Array.isArray(values) ? values.filter(value => KNOWN_PERMISSIONS.has(value)).slice(0, 10) : []);
  for (const item of permissionItems) {
    const active = missing.has(item.dataset.permission);
    item.classList.toggle("permission-missing", active);
    if (active) item.setAttribute("aria-label", `${item.dataset.permission}, missing`);
    else item.removeAttribute("aria-label");
  }
  if (missing.size) permissionDetails.open = true;
}

function showConnectionNotice() {
  const params = new URLSearchParams(window.location.search);
  const reason = params.get("reason");
  if (!Object.hasOwn(NOTICE_COPY, reason)) return;
  const copy = NOTICE_COPY[reason];
  noticeTitle.textContent = copy[0];
  noticeCopy.textContent = copy[1];
  notice.hidden = false;
  if (params.get("reason") === "missing_permissions") {
    keyMode.checked = true;
    loginMode.checked = false;
    highlightMissing((params.get("missing") || "").slice(0, 512).split(","));
  } else if (reason === "invalid_login") {
    loginMode.checked = true;
    keyMode.checked = false;
  }
  updateMode();
}

function clearMissing() { highlightMissing([]); }

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
showConnectionNotice();

form.addEventListener("submit", async event => {
  event.preventDefault();
  if (submitting || !form.reportValidity()) return;
  submitting = true;
  error.hidden = true;
  clearMissing();
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
      if (result.code === "missing_permissions") highlightMissing(result.missingPermissions);
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
