// Copyright (C) 2026 Serkan Bekdemir
// SPDX-License-Identifier: AGPL-3.0-only

const FOCUSABLE = 'a[href], button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])';

const ROUTED_VIEWS = ["unnamed", "group-photos", "investigate", "faces", "pending", "named", "ignored"];

export function routeForView(view) {
  return view === "unnamed" ? "/naming/clusters" : view === "group-photos" ? "/naming/group-photos" : `/${view}`;
}

export function viewFromPath(path, mergeEnabled = false) {
  const view = path.replace(/^\/|\/$/g, "");
  if (view === "naming/clusters") return "unnamed";
  if (view === "naming/group-photos") return "group-photos";
  return [...ROUTED_VIEWS, ...(mergeEnabled ? ["merge"] : [])].includes(view) ? view : "unnamed";
}

export function updateNavigationState(document, view) {
  document.querySelectorAll(".nav-button").forEach(button => {
    const active = button.dataset.view === view;
    button.classList.toggle("active", active);
    if (active) button.setAttribute("aria-current", "page");
    else button.removeAttribute("aria-current");
  });
}

export function createNavigationDrawer({ document, window, onNavigate, breakpoint = 900 }) {
  const sidebar = document.querySelector("#app-sidebar");
  const menu = document.querySelector("#nav-menu");
  const closeButton = document.querySelector("#nav-close");
  const backdrop = document.querySelector("#nav-backdrop");
  const mainColumn = document.querySelector("#app-main-column");
  const skipLink = document.querySelector(".skip-link");
  let open = false;
  let previousOverflow = "";
  let previousInert = null;
  let focusRevision = 0;

  const isMobile = () => window.matchMedia
    ? window.matchMedia(`(max-width: ${breakpoint}px)`).matches
    : window.innerWidth <= breakpoint;

  function focusableElements() {
    return [...sidebar.querySelectorAll(FOCUSABLE)].filter(element => {
      if (element.hidden || element.getAttribute("aria-hidden") === "true") return false;
      if (element.closest?.('[hidden], [inert], [aria-hidden="true"]')) return false;
      const style = window.getComputedStyle?.(element);
      if (style?.display === "none" || style?.visibility === "hidden") return false;
      return !element.getClientRects || element.getClientRects().length > 0;
    });
  }

  function close({ restoreFocus = false } = {}) {
    const wasOpen = open;
    open = false;
    sidebar.classList.remove("drawer-open");
    sidebar.inert = isMobile();
    menu.setAttribute("aria-expanded", "false");
    backdrop.hidden = true;
    if (wasOpen && previousInert) {
      for (const [element, inert] of previousInert) element.inert = inert;
      previousInert = null;
    }
    document.body.classList.remove("nav-drawer-open");
    if (wasOpen) document.body.style.overflow = previousOverflow;
    if (restoreFocus && isMobile()) menu.focus();
  }

  function show() {
    if (!isMobile() || open || document.querySelector("dialog[open]")) return;
    open = true;
    previousOverflow = document.body.style.overflow;
    sidebar.inert = false;
    sidebar.classList.add("drawer-open");
    menu.setAttribute("aria-expanded", "true");
    backdrop.hidden = false;
    const background = [mainColumn, skipLink, ...document.querySelectorAll("dialog")].filter(Boolean);
    previousInert = new Map(background.map(element => [element, element.inert]));
    for (const element of background) element.inert = true;
    document.body.classList.add("nav-drawer-open");
    closeButton.focus();
  }

  menu.addEventListener("click", () => {
    focusRevision++;
    open ? close({ restoreFocus: true }) : show();
  });
  closeButton.addEventListener("click", () => { focusRevision++; close({ restoreFocus: true }); });
  backdrop.addEventListener("click", () => { focusRevision++; close({ restoreFocus: true }); });
  sidebar.addEventListener("click", event => {
    const link = event.target.closest?.("a[data-view]");
    if (!link || event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
    event.preventDefault();
    const wasOpen = open;
    focusRevision++;
    if (wasOpen) close();
    const destination = link.dataset.view;
    const navigation = Promise.resolve(onNavigate(destination, wasOpen));
    if (wasOpen) {
      const heading = document.querySelector(".view.active h2");
      if (heading) {
        heading.setAttribute("tabindex", "-1");
        heading.focus();
      }
    }
    const focusIntentRevision = focusRevision;
    navigation.then(() => {
      if (!wasOpen) return;
      if (focusIntentRevision !== focusRevision) return;
      const current = document.querySelector('.nav-button[aria-current="page"]')?.dataset.view;
      if (current && current !== destination) return;
      const heading = document.querySelector(".view.active h2");
      if (heading) {
        heading.setAttribute("tabindex", "-1");
        heading.focus();
      }
    });
  });
  document.addEventListener("keydown", event => {
    if (!open) return;
    if (event.key === "Escape") {
      event.preventDefault();
      focusRevision++;
      close({ restoreFocus: true });
      return;
    }
    if (event.key !== "Tab") return;
    const items = focusableElements();
    if (!items.length) {
      event.preventDefault();
      closeButton.focus();
      return;
    }
    const first = items[0], last = items.at(-1);
    if (event.shiftKey && (document.activeElement === first || !sidebar.contains(document.activeElement))) {
      event.preventDefault(); last.focus();
    } else if (!event.shiftKey && (document.activeElement === last || !sidebar.contains(document.activeElement))) {
      event.preventDefault(); first.focus();
    }
  });
  document.addEventListener("focusin", () => { focusRevision++; });
  document.addEventListener("toggle", event => {
    if (event.target?.matches?.("dialog[open]") || event.target?.open) {
      focusRevision++;
      close();
    }
  }, true);

  const syncViewport = () => {
    if (!isMobile()) {
      if (open) close();
      sidebar.inert = false;
      if (document.activeElement === closeButton) sidebar.querySelector('a[href]:not([hidden])')?.focus();
      return;
    }
    if (open) return;
    sidebar.inert = true;
    if (sidebar.contains(document.activeElement)) menu.focus();
  };
  const media = window.matchMedia?.(`(max-width: ${breakpoint}px)`);
  if (media?.addEventListener) media.addEventListener("change", syncViewport);
  else window.addEventListener("resize", syncViewport);
  syncViewport();

  return { close: () => close({ restoreFocus: true }), isOpen: () => open };
}
