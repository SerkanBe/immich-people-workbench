// Copyright (C) 2026 Serkan Bekdemir
// SPDX-License-Identifier: AGPL-3.0-only
import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import vm from 'node:vm';

const source = readFileSync(new URL('../web/login.js', import.meta.url), 'utf8');
function setup({ storageError = false, readError = false, next = '', reason = '', missing = '', initialUrl = 'http://synthetic.test', savedEntries, fetchResult } = {}) {
  const elements = {};
  for (const id of ['login-form', 'login-error', 'immich-url', 'immich-api-key', 'immich-email', 'immich-password', 'mode-key', 'mode-login', 'login-button', 'key-fields', 'login-fields', 'insecure-tls', 'key-permissions', 'connection-notice', 'connection-notice-title', 'connection-notice-copy']) {
    elements[id] = { value: '', checked: id === 'mode-key', hidden: false, disabled: false, textContent: '', listeners: {},
      classList: { toggle(name, active) { elements[id].classes ??= new Set(); active ? elements[id].classes.add(name) : elements[id].classes.delete(name); } },
      dataset: {}, attributes: {}, setAttribute(name, value) { this.attributes[name] = value; }, removeAttribute(name) { delete this.attributes[name]; },
      addEventListener(kind, fn) { this.listeners[kind] = fn; }, focus() { this.focused = true; } };
  }
  elements['connection-notice'].hidden = true;
  const permissions = ['user.read', 'person.read', 'person.statistics', 'person.update', 'person.create', 'person.merge', 'asset.read', 'asset.view', 'face.read', 'face.update'].map(value => ({
    dataset: { permission: value }, classes: new Set(), attributes: {}, classList: { toggle(name, active) { active ? this.owner.classes.add(name) : this.owner.classes.delete(name); } },
    setAttribute(name, value) { this.attributes[name] = value; }, removeAttribute(name) { delete this.attributes[name]; },
  }));
  for (const item of permissions) item.classList.owner = item;
  elements['immich-url'].value = initialUrl;
  elements['login-form'].reportValidity = () => !!elements['immich-url'].value &&
    (elements['mode-key'].checked ? !!elements['immich-api-key'].value : !!elements['immich-email'].value && !!elements['immich-password'].value);
  const saved = new Map(savedEntries ?? [['immichPeopleConsole.connection.v1', JSON.stringify({ url: 'http://old.test', apiKey: 'synthetic-old-secret' })]]);
  const calls = [], redirects = [], removals = [];
  const search = new URLSearchParams(); if (next) search.set('next', next); if (reason) search.set('reason', reason); if (missing) search.set('missing', missing);
  const context = vm.createContext({ document: { querySelector: selector => elements[selector.slice(1)], querySelectorAll: selector => selector === '[data-permission]' ? permissions : [] }, URLSearchParams, TypeError,
    window: { location: { search: '?' + search.toString(), replace: route => redirects.push(route) } },
    localStorage: {
      getItem(key) { if (storageError || readError) throw Error('Storage unavailable'); return saved.get(key); },
      removeItem(key) { removals.push(key); if (storageError) throw Error('Storage unavailable'); saved.delete(key); },
      setItem(key, value) { if (storageError) throw Error('Storage unavailable'); saved.set(key, value); },
    },
    fetch: async (url, options) => { calls.push([url, JSON.parse(options.body)]); return fetchResult ? fetchResult(url, options) : { ok: true, json: async () => ({ ok: true }) }; },
  });
  vm.runInContext(source, context);
  return { elements, permissions, calls, redirects, saved, removals,
    submit: () => elements['login-form'].listeners.submit({ preventDefault() {} }),
    mode(isKey) { elements['mode-key'].checked = isKey; elements['mode-login'].checked = !isKey; elements[isKey ? 'mode-key' : 'mode-login'].listeners.change(); } };
}

test('API key default disables hidden required admin inputs; native change switches keyboard focus and clears secrets', () => {
  const x = setup(); const e = x.elements;
  assert.equal(e['immich-email'].disabled, true); assert.equal(e['immich-password'].disabled, true);
  e['immich-api-key'].value = 'synthetic-key'; e['immich-password'].value = 'synthetic-password';
  x.mode(false);
  assert.equal(e['immich-api-key'].value, ''); assert.equal(e['immich-password'].value, '');
  assert.equal(e['key-fields'].hidden, true); assert.equal(e['immich-api-key'].disabled, true);
  assert.equal(e['immich-email'].focused, true); assert.equal(e['immich-email'].disabled, false);
  x.mode(true); assert.equal(e['immich-api-key'].focused, true);
});

test('key mode sends explicit key payload; successful connection stores only URL and clears secrets', async () => {
  const x = setup({ next: '/pending' }); x.elements['immich-api-key'].value = 'synthetic-key';
  await x.submit();
  assert.deepEqual(x.calls, [['/api/connect-key', { url: 'http://old.test', insecureTls: false, apiKey: 'synthetic-key' }]]);
  assert.deepEqual(x.redirects, ['/pending']); assert.equal(x.elements['immich-api-key'].value, '');
  assert.equal([...x.saved.values()].some(value => value.includes('synthetic-key')), false);
});

test('admin mode submits compatible login payload and clears password', async () => {
  const x = setup(); x.mode(false); x.elements['immich-email'].value = 'admin@example.test'; x.elements['immich-password'].value = 'synthetic-password';
  await x.submit();
  assert.deepEqual(x.calls, [['/api/login', { url: 'http://old.test', insecureTls: false, email: 'admin@example.test', password: 'synthetic-password' }]]);
  assert.equal(x.elements['immich-password'].value, '');
});

test('invalid visible fields prevent connection and hidden fields do not block key mode', async () => {
  const x = setup(); await x.submit(); assert.equal(x.calls.length, 0);
  x.elements['immich-api-key'].value = 'synthetic-key'; await x.submit(); assert.equal(x.calls.length, 1);
});

test('legacy credential storage deletion survives current URL read failure', () => {
  const x = setup({ readError: true });
  assert.deepEqual(x.removals, ['immichPeopleWorkbench.connection.v1', 'immichPeopleConsole.connection.v1']);
  assert.equal(x.saved.has('immichPeopleConsole.connection.v1'), false);
});

test('optional storage failure permits connection', async () => {
  const x = setup({ storageError: true }); x.elements['immich-api-key'].value = 'synthetic-key'; await x.submit();
  assert.deepEqual(x.redirects, ['/']);
});

test('all permitted next routes and untrusted redirects', async () => {
  for (const next of ['/', '/unnamed', '/naming/clusters', '/naming/group-photos', '/investigate', '/faces', '/pending', '/named', '/ignored', 'https://evil.test', '//evil.test', '/merge', '/unknown']) {
    const x = setup({ next }); x.elements['immich-api-key'].value = 'synthetic-key'; await x.submit();
    assert.equal(x.redirects[0], ['https://evil.test', '//evil.test', '/merge', '/unknown'].includes(next) ? '/' : next);
  }
});

test('rejected and missing-scope keys clear and errors allow explicit retry', async () => {
  for (const code of ['invalid_key', 'missing_permissions', 'unsupported_introspection']) {
    const x = setup({ fetchResult: async () => ({ ok: false, json: async () => ({ code, error: 'Add face.update and re-enter the key.' }) }) });
    x.elements['immich-api-key'].value = 'synthetic-key'; await x.submit();
    assert.equal(x.elements['immich-api-key'].value, ''); assert.equal(x.elements['login-error'].hidden, false);
    assert.match(x.elements['login-error'].textContent, /face.update/); assert.equal(x.elements['login-button'].disabled, false);
  }
});

test('persistent missing-permission notice opens details, filters unknown values, and survives method changes', () => {
  const x = setup({ reason: 'missing_permissions', missing: 'face.update,evil.injected,person.merge' });
  assert.equal(x.elements['connection-notice'].hidden, false);
  assert.match(x.elements['connection-notice-title'].textContent, /permissions are missing/);
  assert.equal(x.elements['key-permissions'].open, true);
  assert.deepEqual(x.permissions.filter(item => item.classes.has('permission-missing')).map(item => item.dataset.permission), ['person.merge', 'face.update']);
  x.mode(false); assert.equal(x.elements['connection-notice'].hidden, false);
  assert.equal(x.elements['connection-notice-title'].textContent, 'Some required permissions are missing');
});

test('supported notices use static copy without requests; unknown and prototype-like reasons are ignored', () => {
  const reasons = ['missing_permissions', 'invalid_key', 'session_expired', 'unavailable', 'unsupported_introspection', 'invalid_login', 'owner_mismatch', 'connection_ended'];
  for (const reason of reasons) {
    const x = setup({ reason });
    assert.equal(x.elements['connection-notice'].hidden, false, reason);
    assert.equal(x.calls.length, 0, reason);
    assert.equal(x.elements['mode-login'].checked, reason === 'invalid_login', reason);
    if (reason === 'invalid_login') {
      assert.equal(x.elements['key-fields'].hidden, true);
      assert.equal(x.elements['login-fields'].hidden, false);
      assert.equal(x.elements['immich-api-key'].disabled, true);
    }
  }
  for (const reason of ['unknown_reason', '__proto__', 'constructor', 'toString']) {
    const x = setup({ reason });
    assert.equal(x.elements['connection-notice'].hidden, true, reason);
    assert.equal(x.calls.length, 0, reason);
  }
});

test('initial missing-scope attempt highlights only known scopes and next attempt clears stale highlights', async () => {
  let attempt = 0;
  const x = setup({ fetchResult: async () => { attempt++; return { ok: false, json: async () => attempt === 1
    ? { code: 'missing_permissions', missingPermissions: ['face.update', 'unknown.injected'], error: 'Add face.update and retry.' }
    : { code: 'invalid_key', error: 'Rejected.' } }; } });
  x.elements['immich-api-key'].value = 'synthetic-key'; await x.submit();
  assert.deepEqual(x.permissions.filter(item => item.classes.has('permission-missing')).map(item => item.dataset.permission), ['face.update']);
  x.elements['immich-api-key'].value = 'synthetic-key'; await x.submit();
  assert.equal(x.permissions.some(item => item.classes.has('permission-missing')), false);
});

test('offline failure displays safe message and restores connect button', async () => {
  const x = setup({ fetchResult: async () => { throw new TypeError('Failed to fetch'); } });
  x.elements['immich-api-key'].value = 'synthetic-key'; await x.submit();
  assert.match(x.elements['login-error'].textContent, /Cannot reach Workbench/); assert.equal(x.elements['login-button'].disabled, false);
});

test('repeated submit starts only one connection while awaiting response', async () => {
  let resolve;
  const waiting = new Promise(r => { resolve = r; });
  const x = setup({ fetchResult: async () => waiting }); x.elements['immich-api-key'].value = 'synthetic-key';
  const first = x.submit(); await x.submit(); assert.equal(x.calls.length, 1);
  assert.equal(x.elements['mode-key'].disabled, true);
  resolve({ ok: true, json: async () => ({ ok: true }) }); await first;
  assert.equal(x.elements['mode-key'].disabled, false);
});


test('fresh setup defaults remain editable and never initiate a connection', async () => {
  for (const initialUrl of ['http://localhost:2283', '', 'http://immich-server:2283']) {
    const x = setup({ initialUrl, savedEntries: [] });
    assert.equal(x.elements['immich-url'].value, initialUrl);
    assert.equal(x.calls.length, 0);
    assert.equal(x.saved.size, 0);
    x.elements['immich-url'].value = 'https://photos.example.test';
    x.elements['immich-api-key'].value = 'synthetic-key';
    await x.submit();
    assert.equal(x.calls.length, 1);
    assert.equal(x.calls[0][1].url, 'https://photos.example.test');
  }
});

test('blank standalone default requires an address before connecting', async () => {
  const x = setup({ initialUrl: '', savedEntries: [] });
  x.elements['immich-api-key'].value = 'synthetic-key';
  await x.submit();
  assert.equal(x.calls.length, 0);
});

test('saved and legacy URLs take precedence over deployment defaults without changing other settings', () => {
  for (const key of ['immichPeopleWorkbench.url.v1', 'immichPeopleWorkbench.connection.v1', 'immichPeopleConsole.connection.v1']) {
    const savedUrl = 'https://saved.example.test';
    const savedValue = key.endsWith('url.v1') ? savedUrl : JSON.stringify({ url: savedUrl, apiKey: 'synthetic-old-secret' });
    const canvasKey = 'immichPeopleConsole.mergeCanvas.v1';
    const x = setup({ initialUrl: 'http://immich-server:2283', savedEntries: [[key, savedValue], [canvasKey, 'synthetic-canvas']] });
    assert.equal(x.elements['immich-url'].value, savedUrl);
    assert.equal(x.saved.get(canvasKey), 'synthetic-canvas');
    assert.equal(x.elements['immich-api-key'].value, '');
    assert.equal(x.calls.length, 0);
  }
});

test('a deployment default works when optional browser storage is unavailable', () => {
  const x = setup({ initialUrl: 'http://immich-server:2283', storageError: true });
  assert.equal(x.elements['immich-url'].value, 'http://immich-server:2283');
  assert.equal(x.calls.length, 0);
});
