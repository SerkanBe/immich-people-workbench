// Copyright (C) 2026 Serkan Bekdemir
// SPDX-License-Identifier: AGPL-3.0-only
"use strict";

const URL_KEY = "immichPeopleWorkbench.url.v1";
const OLD_KEYS = ["immichPeopleWorkbench.connection.v1", "immichPeopleConsole.connection.v1"];
const form = document.querySelector("#login-form");
const error = document.querySelector("#login-error");
const urlInput = document.querySelector("#immich-url");

try {
  let savedUrl = localStorage.getItem(URL_KEY);
  for (const key of OLD_KEYS) {
    const old = localStorage.getItem(key);
    if (!savedUrl && old) {
      try { savedUrl = JSON.parse(old)?.url; } catch { /* Ignore invalid old settings. */ }
    }
    localStorage.removeItem(key);
  }
  if (typeof savedUrl === "string" && savedUrl) urlInput.value = savedUrl;
} catch { /* Browser storage may be unavailable. */ }

form.addEventListener("submit", async event => {
  event.preventDefault();
  error.hidden = true;
  const button = document.querySelector("#login-button");
  button.disabled = true;
  const url = urlInput.value.trim();
  try {
    const response = await fetch("/api/login", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        url,
        email: document.querySelector("#immich-email").value.trim(),
        password: document.querySelector("#immich-password").value,
        insecureTls: document.querySelector("#insecure-tls").checked,
      }),
    });
    const result = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(result.error || "Sign-in failed");
    try { localStorage.setItem(URL_KEY, url); } catch { /* Optional convenience only. */ }
    document.querySelector("#immich-password").value = "";
    const next = new URLSearchParams(window.location.search).get("next");
    const routes = new Set(["/", "/unnamed", "/merge", "/investigate", "/faces", "/pending", "/named", "/ignored"]);
    window.location.replace(routes.has(next) ? next : "/");
  } catch (cause) {
    error.textContent = cause.message;
    error.hidden = false;
    button.disabled = false;
  }
});
