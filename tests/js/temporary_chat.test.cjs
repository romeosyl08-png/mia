const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');

// Run the actual conversation factory and click handler with controlled loading.
const source = fs.readFileSync(path.join(__dirname, '../../static/app/js/script.js'), 'utf8');
function section(start, end) {
  const from = source.indexOf(start), to = source.indexOf(end, from);
  assert.ok(from >= 0 && to > from);
  return source.slice(from, to);
}

function harness({personas = [], temporaryChat = false, initializing = false, retryPersonas = [], mode = 'image'} = {}) {
  let click, renders = 0, loads = 0, sequence = 0;
  const requests = [], notices = [];
  const state = {personas, temporaryChat, initializing, isAuthenticated: true,
    sending: false, savingTemporary: false, modelId: 'cloud', selectedWorkspaceId: null,
    credits: 12345, hasActiveModels: true, usageLog: [],
    conversations: [], activeConvoId: null, activePersonaId: personas[0]?.id};
  const globals = {state,
    uid: prefix => `${prefix}_${++sequence}`,
    renderAll() {renders++;},
    async loadPersonas() {loads++; state.personas = retryPersonas;},
    el: id => ({
      value: '0.7', selectedOptions: [], classList: {contains: () => false},
      addEventListener(_name, handler) {if(id === 'temporaryChatBtn') click = handler;}
    }),
    document: {querySelector: selector => selector.includes('data-mode')
      ? {dataset: {mode}} : {value: 'csrf-token'}},
    async fetch(url, options) {
      requests.push({url, body: JSON.parse(options.body)});
      return {ok: true, headers: {get: () => 'application/json'},
        async json() {return {reply: 'Image générée', media_url: '/image.png', media_kind: 'image', remaining_balance: 11715};}};
    },
    isLocalModelSelected: () => false,
    ensureCreditAccess: () => true,
    collectMediaOptions: () => ({image_size: '1024x1024'}),
    createStreamingRenderer: () => () => {},
    renderCredits() {}, renderHeader() {},
    estimateTokens: text => Math.max(1, Math.round(text.length / 4)),
    showChatNotice: message => notices.push(message)
  };
  vm.runInNewContext(
    section('  function conversationKey(', '  async function loadConversations(')
      + section('  function activePersona(){', '  function isLocalModelSelected(){')
      + section('  el("temporaryChatBtn").addEventListener(', '  el("temporaryMemoryPersona").addEventListener(')
      + section('  async function callBackend(', '  function normalizeLocalBaseUrl(')
      + section('  async function sendMessage(', '  async function regenerateResponse('),
    globals
  );
  return {state, requests, notices, click: () => click(),
    send: text => globals.sendMessage(text),
    newConversation: personaId => globals.newConversation(personaId),
    stats: () => ({renders, loads})};
}

test('the ninja opens a temporary session even when persona loading has failed', async () => {
  const app = harness();
  await app.click();
  assert.equal(app.state.temporaryChat, true);
  assert.equal(app.state.conversations.length, 1);
  const conversation = app.state.conversations[0];
  assert.equal(conversation.temporary, true);
  assert.equal(conversation.personaId, null);
  assert.equal(conversation.id, null);
  assert.equal(conversation.localId, null);
  assert.equal(app.state.activeConvoId, conversation.draftId);
  assert.equal(app.stats().loads, 1);
  assert.ok(app.stats().renders > 0);
});

test('the click retries persona loading and attaches a recovered account persona', async () => {
  const app = harness({retryPersonas: [{id: 7, name: 'Rédacteur'}]});
  await app.click();
  assert.equal(app.state.conversations[0].temporary, true);
  assert.equal(app.state.conversations[0].personaId, 7);
  assert.equal(app.stats().loads, 1);
});

test('the ninja toggles the displayed conversation even if the previous mode flag is stale', async () => {
  const app = harness({personas: [{id: 7}], temporaryChat: true});
  const saved = {id: 12, personaId: 7, messages: []};
  app.state.conversations.push(saved);
  app.state.activeConvoId = saved.id;
  await app.click();
  assert.equal(app.state.conversations[0].temporary, true);
  await app.click();
  assert.equal(app.state.conversations[0].temporary, false);
  assert.ok(app.state.conversations.includes(saved));
  assert.ok(app.state.conversations.every(conversation => !conversation.temporary));
  assert.equal(app.stats().loads, 0);
});

test('startup and in-progress turns cannot be replaced by a temporary session', async () => {
  for (const busy of ['initializing', 'sending', 'savingTemporary']) {
    const app = harness({personas: [{id: 7}]});
    app.state[busy] = true;
    await app.click();
    assert.equal(app.state.conversations.length, 0);
    assert.equal(app.stats().renders, 0);
  }
});

for (const mode of ['image', 'chat']) {
  test(`a temporary ${mode} request uses the fallback when persona loading fails`, async () => {
    const app = harness({mode});
    await app.click();
    await app.send('Crée une image');

    assert.equal(app.requests.length, 1);
    const request = app.requests[0];
    assert.equal(request.url, mode === 'image' ? '/chat/api/media/generate/' : '/chat/api/send/');
    assert.equal(request.body.content, 'Crée une image');
    assert.equal(request.body.system_prompt, 'Tu es un assistant poli, clair et utile.');
    assert.equal(request.body.persona_id, null);
    assert.equal(request.body.temporary, true);
    assert.equal(request.body.kind, mode === 'image' ? 'image' : null);
    assert.equal(app.state.conversations[0].messages.at(-1).content, 'Image générée');
    assert.equal(app.state.conversations[0].messages.at(-1).mediaUrl, '/image.png');
    assert.equal(app.state.usageLog.length, 2);
    assert.equal(app.state.credits, 11715);
    assert.equal(app.state.sending, false);
  });
}

test('an image request keeps the persona selected for its conversation', async () => {
  const app = harness({personas: [{id: 7, name: 'Général', prompt: 'Général'},
    {id: 9, name: 'Artiste', prompt: 'Instructions artiste'}]});
  await app.newConversation(9);
  await app.send('Crée une image');

  assert.equal(app.requests.length, 1);
  assert.equal(app.requests[0].body.persona_id, 9);
  assert.equal(app.requests[0].body.system_prompt, 'Instructions artiste');
  assert.equal(app.requests[0].body.temporary, false);
  assert.equal(app.state.conversations[0].messages.at(-1).content, 'Image générée');
});

test('an existing saved conversation reports missing personas before sending', async () => {
  const app = harness();
  app.state.conversations.push({id: 22, personaId: 7, messages: []});
  app.state.activeConvoId = 22;
  await app.send('Crée une image');

  assert.equal(app.requests.length, 0);
  assert.equal(app.state.conversations[0].messages.length, 0);
  assert.equal(app.notices.length, 1);
  assert.match(app.notices[0], /persona/);
  assert.equal(app.state.sending, false);
});
