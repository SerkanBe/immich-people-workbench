// Copyright (C) 2026 Serkan Bekdemir
// SPDX-License-Identifier: AGPL-3.0-only
import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import vm from 'node:vm';

const source = readFileSync(new URL('../web/login.js', import.meta.url), 'utf8');
function setup({ storageError = false, readError = false, next = '', fetchResult } = {}) {
  const elements = {};
  for (const id of ['login-form', 'login-error', 'immich-url', 'immich-api-key', 'immich-email', 'immich-password', 'mode-key', 'mode-login', 'login-button', 'key-fields', 'login-fields', 'insecure-tls']) {
    elements[id] = { value: '', checked: id === 'mode-key', hidden: false, disabled: false, textContent: '', listeners: {},
      addEventListener(kind, fn) { this.listeners[kind] = fn; }, focus() { this.focused = true; } };
  }
  elements['immich-url'].value = 'http://synthetic.test';
  elements['login-form'].reportValidity = () => !!elements['immich-url'].value &&
    (elements['mode-key'].checked ? !!elements['immich-api-key'].value : !!elements['immich-email'].value && !!elements['immich-password'].value);
  const saved = new Map([['immichPeopleConsole.connection.v1', JSON.stringify({ url: 'http://old.test', apiKey: 'synthetic-old-secret' })]]);
  const calls = [], redirects = [], removals = [];
  const context = vm.createContext({ document: { querySelector: selector => elements[selector.slice(1)] }, URLSearchParams, TypeError,
    window: { location: { search: '?next=' + encodeURIComponent(next), replace: route => redirects.push(route) } },
    localStorage: {
      getItem(key) { if (storageError || readError) throw Error('Storage unavailable'); return saved.get(key); },
      removeItem(key) { removals.push(key); if (storageError) throw Error('Storage unavailable'); saved.delete(key); },
      setItem(key, value) { if (storageError) throw Error('Storage unavailable'); saved.set(key, value); },
    },
    fetch: async (url, options) => { calls.push([url, JSON.parse(options.body)]); return fetchResult ? fetchResult(url, options) : { ok: true, json: async () => ({ ok: true }) }; },
  });
  vm.runInContext(source, context);
  return { elements, calls, redirects, saved, removals,
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
