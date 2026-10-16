const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');

function response(version) {
  return {version, ok: true, type: 'basic', headers: {get: () => ''}, clone() {return this;}};
}

function harness(assetPath, cached = response('old')) {
  const handlers = {}, saved = [], background = [];
  let resolveFetch, rejectFetch, result;
  const network = new Promise((resolve, reject) => {resolveFetch = resolve; rejectFetch = reject;});
  vm.runInNewContext(fs.readFileSync(path.join(__dirname, '../../static/app/pwa/service-worker.js'), 'utf8'), {
    console, URL, TypeError, Response,
    self: {location: {origin: 'https://example.test'}, addEventListener(name, handler) {handlers[name] = handler;}},
    caches: {open: async () => ({match: async () => cached, put: async (_request, value) => saved.push(value)})},
    fetch: () => network
  });
  handlers.fetch({request: {url: 'https://example.test' + assetPath, method: 'GET', mode: 'cors'},
    respondWith(promise) {result = promise;}, waitUntil(promise) {background.push(promise);}});
  return {result, resolveFetch, rejectFetch, saved, background};
}

test('interactive scripts use the current network version even when an older script is cached', async () => {
  const app = harness('/static/app/js/script.js?v=temporary-persona-2');
  let delivered = false;
  app.result.then(() => {delivered = true;});
  await new Promise(resolve => setImmediate(resolve));
  assert.equal(delivered, false);
  const current = response('new');
  app.resolveFetch(current);
  assert.equal(await app.result, current);
  assert.deepEqual(app.saved, [current]);
});

test('scripts fall back to the cached version when the network is unavailable', async () => {
  const cached = response('offline');
  const app = harness('/static/app/js/script.js', cached);
  app.rejectFetch(new TypeError('Offline'));
  assert.equal(await app.result, cached);
});

test('Wasm modules wait for the current network version and use cached bytes offline', async () => {
  const app = harness('/static/app/wasm/markdown_core.wasm');
  let delivered = false;
  app.result.then(() => { delivered = true; });
  await new Promise(resolve => setImmediate(resolve));
  assert.equal(delivered, false);
  const current = response('new-wasm');
  app.resolveFetch(current);
  assert.equal(await app.result, current);
  const offline = response('cached-wasm');
  const fallback = harness('/static/app/wasm/markdown_core.wasm', offline);
  fallback.rejectFetch(new TypeError('Offline'));
  assert.equal(await fallback.result, offline);
});

test('public images remain immediately available from cache while being refreshed', async () => {
  const cached = response('cached-icon');
  const app = harness('/static/app/pwa/icon-192.png', cached);
  assert.equal(await app.result, cached);
  assert.equal(app.background.length, 1);
  const current = response('new-icon');
  app.resolveFetch(current);
  await Promise.all(app.background);
  assert.deepEqual(app.saved, [current]);
});
