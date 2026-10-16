const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');
const marked = require('../../static/app/vendor/marked.min.js');
const {MIN_RUST_LENGTH} = require('../../static/app/js/markdown.js');

const source = fs.readFileSync(path.join(__dirname, '../../static/app/js/markdown.js'), 'utf8');
const wasm = fs.readFileSync(path.join(__dirname, '../../static/app/wasm/markdown_core.wasm'));
const longText = 'Été 🐘 **important** [[sources:1,2]].\n\n'.repeat(300);

function harness(overrides = {}) {
  const requests = [], fallbacks = [];
  const context = {
    WebAssembly, TextEncoder, TextDecoder, Uint8Array,
    document: {currentScript: {dataset: {wasmUrl: '/static/app/wasm/markdown_core.wasm'}}},
    marked: {parse(text) { fallbacks.push(text); return marked.parse(text); }},
    fetch: async (url, options) => {
      requests.push({url, options});
      return {ok: true, arrayBuffer: async () => wasm};
    },
    ...overrides
  };
  vm.runInNewContext(source, context);
  return {renderer: context.AI2AIMarkdown, requests, fallbacks};
}

test('short messages never download Wasm, even after a successful explicit load', async () => {
  const {renderer, requests, fallbacks} = harness();
  assert.equal(renderer.parse('**Bonjour**'), '<p><strong>Bonjour</strong></p>\n');
  assert.equal(requests.length, 0);
  assert.equal(await renderer.load(), true);
  renderer.parse('Court');
  assert.equal(fallbacks.length, 2);
  assert.equal(requests.length, 1);
});

test('long messages use JavaScript while loading, then use the real Rust module', async () => {
  const {renderer, requests, fallbacks} = harness();
  assert.ok(longText.length >= MIN_RUST_LENGTH);
  renderer.parse(longText);
  renderer.parse(longText);
  assert.equal(await renderer.load(), true);
  assert.equal(requests.length, 1);
  assert.equal(renderer.backend, 'rust');
  const html = renderer.parse(longText);
  assert.equal(fallbacks.length, 2);
  assert.ok(html.includes('Été 🐘 <strong>important</strong> [[sources:1,2]]'));
});

test('Rust preserves tables, tasks, autolinks and interactive preview code fences', async () => {
  const {renderer, fallbacks} = harness();
  await renderer.load();
  const html = renderer.parse(longText + '| A | B |\n|---|---|\n| 1 | 2 |\n\n- [x] Fait\n\n'
    + '~~barré~~ https://example.com\n\n```2ai-preview\n{"kind":"comparison","title":"Été 🐘"}\n```');
  assert.equal(fallbacks.length, 0);
  for (const expected of ['<table>', 'type="checkbox"', '<del>barré</del>', 'href="https://example.com"', 'class="language-2ai-preview"']) {
    assert.ok(html.includes(expected), expected);
  }
});

test('fresh memory views handle growing and shrinking Unicode messages without stale content', async () => {
  const {renderer} = harness();
  await renderer.load();
  for (const count of [1, 8, 64, 2, 1]) {
    const html = renderer.parse(longText.repeat(count) + '\n\nFin unique à vérifier.');
    assert.equal((html.match(/Été 🐘/g) || []).length, 300 * count);
    assert.ok(html.endsWith('<p>Fin unique à vérifier.</p>\n'));
  }
});

test('offline, blocked, missing and malformed Wasm keep the existing parser usable', async () => {
  for (const overrides of [
    {WebAssembly: undefined},
    {fetch: async () => { throw Error('offline'); }},
    {fetch: async () => ({ok: false})},
    {fetch: async () => ({ok: true, arrayBuffer: async () => new Uint8Array([0, 1, 2])})}
  ]) {
    const {renderer} = harness(overrides);
    assert.equal(await renderer.load(), false);
    assert.equal(renderer.parse(longText), marked.parse(longText));
    assert.equal(renderer.backend, 'javascript');
  }
});

test('a runtime trap falls back for this message and every later message', async () => {
  const {renderer, fallbacks} = harness({WebAssembly: {
    instantiate: async () => ({instance: {exports: {
      memory: {buffer: new ArrayBuffer(32)},
      markdown_input() { throw Error('Wasm trap'); },
      markdown_render() {}, markdown_output_len() {}
    }}})
  }});
  assert.equal(await renderer.load(), true);
  assert.equal(renderer.parse(longText), marked.parse(longText));
  assert.equal(renderer.parse(longText), marked.parse(longText));
  assert.equal(fallbacks.length, 2);
  assert.equal(renderer.backend, 'javascript');
});
