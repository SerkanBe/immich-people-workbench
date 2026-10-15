// Copyright (C) 2026 Serkan Bekdemir
// SPDX-License-Identifier: AGPL-3.0-only
import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import vm from 'node:vm';

const source = readFileSync(new URL('../web/app.js', import.meta.url), 'utf8');
const start = source.indexOf('async function api(path, options = {}) {');
const end = source.indexOf('\nfunction showToast', start);
const apiSource = source.slice(start, end);

function setup(status, data) {
  const calls = [], redirects = [];
  const context = vm.createContext({
    URLSearchParams,
    window: { location: { pathname: '/pending', assign: value => redirects.push(value) } },
    fetch: async (path, options) => { calls.push([path, options]); return { status, ok: status >= 200 && status < 300, statusText: 'Synthetic', json: async () => data }; },
  });
  vm.runInContext(`${apiSource}\nglobalThis.callApi = api;`, context);
  return { context, calls, redirects };
}

test('typed 401 carries only known reason and permissions, preserves route, and does not retry', async () => {
  const x = setup(401, { code: 'missing_permissions', missingPermissions: ['face.update', 'evil.scope', 'face.update'], error: 'synthetic-safe' });
  await assert.rejects(x.context.callApi('/api/state'), /Sign in required/);
  assert.equal(x.calls.length, 1);
  const url = new URL(x.redirects[0], 'http://workbench.test');
  assert.equal(url.pathname, '/login'); assert.equal(url.searchParams.get('next'), '/pending');
  assert.equal(url.searchParams.get('reason'), 'missing_permissions');
  assert.equal(url.searchParams.get('missing'), 'face.update');
  assert.equal([...url.searchParams.keys()].length, 3);
});

test('reconnect-required 403 redirects, while ordinary 403 remains inline and sends no retry', async () => {
  const reconnect = setup(403, { code: 'invalid_key', reconnectRequired: true, error: 'synthetic-safe' });
  await assert.rejects(reconnect.context.callApi('/api/sync', { method: 'POST', body: { confirmation: 'SYNC' } }));
  assert.equal(reconnect.calls.length, 1); assert.equal(reconnect.redirects.length, 1);
  assert.equal(new URL(reconnect.redirects[0], 'http://workbench.test').searchParams.get('reason'), 'invalid_key');

  const ordinary = setup(403, { code: 'invalid_origin', error: 'Origin refused' });
  await assert.rejects(ordinary.context.callApi('/api/sync', { method: 'POST', body: { confirmation: 'SYNC' } }), /Origin refused/);
  assert.equal(ordinary.calls.length, 1); assert.deepEqual(ordinary.redirects, []);
});

test('unknown reason and injected permission values are dropped from redirects', async () => {
  const x = setup(401, { code: '__proto__', missingPermissions: ['constructor', { toString: 'x' }], error: 'safe' });
  await assert.rejects(x.context.callApi('/api/state'));
  const url = new URL(x.redirects[0], 'http://workbench.test');
  assert.equal(url.searchParams.get('next'), '/pending');
  assert.equal(url.searchParams.has('reason'), false); assert.equal(url.searchParams.has('missing'), false);
});
