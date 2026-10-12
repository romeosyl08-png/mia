const assert = require('node:assert/strict');
const test = require('node:test');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

function run(file, globals) {
  vm.runInNewContext(fs.readFileSync(path.join(__dirname, '../../static/app/js', file), 'utf8'), {console, ...globals}, {filename: file});
}
const flush = () => new Promise(resolve => setImmediate(resolve));

test('local model context is fetched again for each request without caching old sharing permissions', async () => {
  const calls = [];
  const window = {};
  let context = 'Contexte partagé autorisé';
  run('persona_context.js', {window, fetch: async (url, options) => {
    calls.push({url, options});
    return {ok: true, json: async () => ({system_prompt: context})};
  }});
  const persona = {id: 4, prompt: 'Ancien prompt'};
  assert.equal(await window.AI2AIPersonaContext.load(persona), context);
  context = 'Contexte après révocation';
  assert.equal(await window.AI2AIPersonaContext.load(persona), context);
  assert.equal(calls.length, 2);
  assert.equal(calls[0].url, '/chat/api/personas/4/context/');
  assert.ok(calls.every(call => call.options.cache === 'no-store'));
});

test('local context failures stop the request rather than reusing an old shared prompt', async () => {
  const window = {};
  run('persona_context.js', {window, fetch: async () => ({ok: false})});
  await assert.rejects(window.AI2AIPersonaContext.load({id: 4, prompt: 'Ancien contexte partagé'}), /contexte autorisé/);
});

test('temporary local requests explicitly select a memory and can turn it off again', async () => {
  const calls = [], window = {};
  run('persona_context.js', {window, fetch: async (url, options) => {
    calls.push({url, options});
    return {ok: true, json: async () => ({system_prompt: 'Contexte sélectionné'})};
  }});
  const conversation = {temporary: true, temporaryMemoryPersonaId: null};
  await window.AI2AIPersonaContext.load({id: 4}, conversation);
  conversation.temporaryMemoryPersonaId = 7;
  await window.AI2AIPersonaContext.load({id: 4}, conversation);
  conversation.temporaryMemoryPersonaId = null;
  await window.AI2AIPersonaContext.load({id: 4}, conversation);
  assert.deepEqual(calls.map(call => call.url), [
    '/chat/api/personas/4/context/?temporary=1&memory_persona_id=',
    '/chat/api/personas/4/context/?temporary=1&memory_persona_id=7',
    '/chat/api/personas/4/context/?temporary=1&memory_persona_id='
  ]);
  assert.ok(calls.every(call => call.options.cache === 'no-store'));
});

test('invalid context responses are rejected and demo personas need no account lookup', async () => {
  const window = {};
  let requests = 0;
  run('persona_context.js', {window, fetch: async () => {
    requests++;
    return {ok: true, json: async () => ({system_prompt: null})};
  }});
  assert.equal(await window.AI2AIPersonaContext.load({id: 'demo', prompt: 'Démo'}), 'Démo');
  assert.equal(requests, 0);
  await assert.rejects(window.AI2AIPersonaContext.load({id: 4}), /invalide/);
});

function node() {
  return {handlers: {}, children: [],
    addEventListener(name, callback) { this.handlers[name] = callback; },
    replaceChildren() { this.children = []; },
    append(child) { this.children.push(child); }
  };
}
function sharingHarness(selected = null) {
  const requests = [], events = {};
  const ids = ['personaShareContext', 'personaSharingControls', 'personaShareStatus', 'bigPersonaStatus', 'bigPersonaSources', 'bigPersonaSnapshot', 'bigPersonaChatLink'];
  const nodes = Object.fromEntries(ids.map(id => [id, node()]));
  run('persona_sharing.js', {
    window: {AI2AIPersonaSettings: {getSelected: () => selected}},
    document: {getElementById: id => nodes[id], addEventListener: (name, callback) => { events[name] = callback; }, querySelector: () => ({value: 'csrf-fixture'}), createElement: node},
    fetch(url, options) { return new Promise(resolve => requests.push({url, options, resolve})); }
  });
  async function resolve(index, data, ok = true) {
    requests[index].resolve({ok, json: async () => data});
    await flush();
  }
  const initial = {persona: {id: 4}, personas: [], user: {email: 'owner@example.test'}, site: {name: '2AI'}};
  return {requests, events, nodes, resolve, initial};
}

test('sharing controls default to the selected persona permission and exclude Big Persona as a source', async () => {
  const app = sharingHarness();
  await app.resolve(0, app.initial);
  app.events['ai2ai:persona-selected']({detail: {id: 1, is_big_persona: false, share_with_personas: false}});
  assert.equal(app.nodes.personaShareContext.checked, false);
  assert.equal(app.nodes.personaShareContext.disabled, false);
  assert.equal(app.nodes.bigPersonaChatLink.href, '/?persona=4');
  app.events['ai2ai:persona-selected']({detail: {id: 4, is_big_persona: true}});
  assert.equal(app.nodes.personaSharingControls.hidden, true);
  assert.equal(app.nodes.personaShareContext.disabled, true);
});

test('sharing controls initialize when persona selection happened before the sharing script loaded', async () => {
  const app = sharingHarness({id: 1, share_with_personas: true});
  await app.resolve(0, app.initial);
  assert.equal(app.nodes.personaSharingControls.hidden, false);
  assert.equal(app.nodes.personaShareContext.checked, true);
  assert.equal(app.nodes.personaShareContext.disabled, false);
});

test('failed consent updates restore the previous checkbox state', async () => {
  const app = sharingHarness();
  await app.resolve(0, app.initial);
  app.events['ai2ai:persona-selected']({detail: {id: 1, share_with_personas: false}});
  app.nodes.personaShareContext.checked = true;
  const pending = app.nodes.personaShareContext.handlers.change();
  assert.equal(app.requests[1].url, '/chat/api/personas/1/');
  assert.deepEqual(JSON.parse(app.requests[1].options.body), {share_with_personas: true});
  assert.equal(app.nodes.personaShareContext.disabled, true);
  await app.resolve(1, {error: {message: 'Autorisation refusée'}}, false);
  await pending;
  assert.equal(app.nodes.personaShareContext.checked, false);
  assert.equal(app.nodes.personaShareStatus.textContent, 'Autorisation refusée');
});

test('consent responses cannot overwrite the checkbox of a newly selected persona', async () => {
  const app = sharingHarness();
  await app.resolve(0, app.initial);
  app.events['ai2ai:persona-selected']({detail: {id: 1, share_with_personas: false}});
  app.nodes.personaShareContext.checked = true;
  const pending = app.nodes.personaShareContext.handlers.change();
  app.events['ai2ai:persona-selected']({detail: {id: 2, share_with_personas: false}});
  await app.resolve(1, {id: 1, share_with_personas: true});
  await app.resolve(2, {...app.initial, personas: [{id: 1, name: 'Source autorisée'}]});
  await pending;
  assert.equal(app.nodes.personaShareContext.checked, false);
  assert.equal(app.nodes.personaShareContext.disabled, false);
  assert.equal(app.nodes.bigPersonaSources.children[0].textContent, 'Source autorisée');
});
