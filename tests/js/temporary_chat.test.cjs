const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');

// Run the actual conversation factory and click handler with controlled loading.
const source = fs.readFileSync(path.join(__dirname, '../../static/app/js/script.js'), 'utf8');
const capabilities = {window: {}};
vm.runInNewContext(fs.readFileSync(path.join(__dirname, '../../static/app/js/capabilities.js'), 'utf8'), capabilities);
const chatConfig = {
  capabilityRules: JSON.parse(fs.readFileSync(path.join(__dirname, '../../chat/capability_rules.json'), 'utf8')),
  modelCapabilities: {cloud: {tools: true, images: false}}, webSearchAvailable: false, codeExecutionAvailable: false
};
function section(start, end) {
  const from = source.indexOf(start), to = source.indexOf(end, from);
  assert.ok(from >= 0 && to > from);
  return source.slice(from, to);
}

function harness({personas = [], temporaryChat = false, initializing = false, retryPersonas = [], mode = 'chat', serverError = null} = {}) {
  let click, renders = 0, loads = 0, sequence = 0;
  const requests = [], notices = [];
  const composerInput = {value: ''};
  const state = {personas, temporaryChat, initializing, isAuthenticated: true,
    sending: false, savingTemporary: false, modelId: 'cloud', selectedWorkspaceId: null,
    credits: 12345, hasActiveModels: true, usageLog: [], workspaces: [],
    conversations: [], activeConvoId: null, activePersonaId: personas[0]?.id};
  const globals = {state, chatConfig, window: capabilities.window,
    uid: prefix => `${prefix}_${++sequence}`,
    renderAll() {renders++;},
    async loadPersonas() {loads++; state.personas = retryPersonas;},
    el: id => id === 'composerInput' ? composerInput : ({
      value: '0.7', selectedOptions: [], classList: {contains: () => false},
      addEventListener(_name, handler) {if(id === 'temporaryChatBtn') click = handler;}
    }),
    document: {querySelector: selector => selector.includes('data-mode')
      ? {dataset: {mode}} : {value: 'csrf-token'}},
    async fetch(url, options) {
      requests.push({url, body: JSON.parse(options.body)});
      if(serverError) return {ok: false, json: async () => ({error: {type: 'capability_unavailable', message: serverError}})};
      return {ok: true, headers: {get: () => 'application/json'},
        async json() {return {reply: 'Image générée', media_url: '/image.png', media_kind: 'image', remaining_balance: 11715};}};
    },
    isLocalModelSelected: () => false,
    ensureCreditAccess: () => true,
    collectMediaOptions: () => ({image_size: '1024x1024'}),
    createStreamingRenderer: () => () => {},
    renderCredits() {}, renderHeader() {},
    estimateTokens: text => Math.max(1, Math.round(text.length / 4)),
    showChatNotice: message => notices.push(message),
    showCapabilityIssue: problem => notices.push(problem.message)
  };
  vm.runInNewContext(
    section('  function conversationKey(', '  async function loadConversations(')
      + section('  function activePersona(){', '  function isLocalModelSelected(){')
      + section('  el("temporaryChatBtn").addEventListener(', '  el("temporaryMemoryPersona").addEventListener(')
      + section('  async function callBackend(', '  function normalizeLocalBaseUrl(')
      + section('  function messageCapabilityIssue(', '  // ---------- Event wiring ----------'),
    globals
  );
  return {state, requests, notices, composerInput, click: () => click(),
    send: text => globals.sendMessage(text),
    regenerate: index => globals.regenerateResponse(index),
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

for (const mode of ['chat']) {
  test(`a temporary ${mode} request uses the fallback when persona loading fails`, async () => {
    const app = harness({mode});
    await app.click();
    const text = mode === 'image' ? 'Crée une image' : 'Bonjour';
    await app.send(text);

    assert.equal(app.requests.length, 1);
    const request = app.requests[0];
    assert.equal(request.url, '/chat/api/send/');
    assert.equal(request.body.content, text);
    assert.equal(request.body.system_prompt, 'Tu es un assistant poli, clair et utile.');
    assert.equal(request.body.persona_id, null);
    assert.equal(request.body.temporary, true);
    assert.equal(request.body.kind, undefined);
    assert.equal(app.state.conversations[0].messages.at(-1).content, 'Image générée');
    assert.equal(app.state.conversations[0].messages.at(-1).mediaUrl, '/image.png');
    assert.equal(app.state.usageLog.length, 2);
    assert.equal(app.state.credits, 11715);
    assert.equal(app.state.sending, false);
  });
}

test('a chat request keeps the persona selected for its conversation', async () => {
  const app = harness({personas: [{id: 7, name: 'Général', prompt: 'Général'},
    {id: 9, name: 'Artiste', prompt: 'Instructions artiste'}]});
  await app.newConversation(9);
  await app.send('Décris un paysage');

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
  await app.send('Bonjour');

  assert.equal(app.requests.length, 0);
  assert.equal(app.state.conversations[0].messages.length, 0);
  assert.equal(app.notices.length, 1);
  assert.match(app.notices[0], /persona/);
  assert.equal(app.state.sending, false);
});

test('an incompatible request is blocked before creating a conversation or sending anything', async () => {
  const app = harness({mode: 'chat', personas: [{id: 7}]});
  await app.send('Peux-tu générer une image de chat ?');
  assert.equal(app.requests.length, 0);
  assert.equal(app.state.conversations.length, 0);
  assert.equal(app.state.usageLog.length, 0);
  assert.equal(app.state.credits, 12345);
  assert.equal(app.state.sending, false);
  assert.match(app.notices[0], /Générer une image/);
});

test('a blocked regeneration preserves the existing reply and attachments', async () => {
  const app = harness({mode: 'chat', personas: [{id: 7}]});
  const conversation = {id: 42, personaId: 7, pendingImageIds: [5], messages: [
    {role: 'user', content: 'Crée une image'}, {role: 'bot', content: 'Réponse précédente'}
  ]};
  app.state.conversations.push(conversation);
  app.state.activeConvoId = 42;
  await app.regenerate(1);
  assert.equal(app.requests.length, 0);
  assert.equal(conversation.messages[1].content, 'Réponse précédente');
  assert.deepEqual(conversation.pendingImageIds, [5]);
});

test('a server capability rejection restores the draft without adding a fake reply or usage', async () => {
  const app = harness({mode: 'chat', personas: [{id: 7}], serverError: 'Outil désactivé. Aucun jeton débité.'});
  await app.click();
  await app.send('Bonjour');
  assert.equal(app.requests.length, 1);
  assert.equal(app.state.conversations[0].messages.length, 0);
  assert.equal(app.composerInput.value, 'Bonjour');
  assert.equal(app.state.credits, 12345);
  assert.equal(app.state.usageLog.length, 0);
  assert.equal(app.state.sending, false);
  assert.match(app.notices[0], /Aucun jeton débité/);
});
