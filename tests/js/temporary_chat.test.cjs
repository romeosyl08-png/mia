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

function harness({personas = [], temporaryChat = false, initializing = false, retryPersonas = []} = {}) {
  let click, renders = 0, loads = 0, sequence = 0;
  const state = {personas, temporaryChat, initializing, isAuthenticated: true,
    sending: false, savingTemporary: false, modelId: 'cloud', selectedWorkspaceId: null,
    conversations: [], activeConvoId: null, activePersonaId: personas[0]?.id};
  const globals = {state,
    uid: prefix => `${prefix}_${++sequence}`,
    renderAll() {renders++;},
    async loadPersonas() {loads++; state.personas = retryPersonas;},
    el: id => ({addEventListener(_name, handler) {if(id === 'temporaryChatBtn') click = handler;}})
  };
  vm.runInNewContext(
    section('  function conversationKey(', '  async function loadConversations(')
      + section('  function activePersona(){', '  function isLocalModelSelected(){')
      + section('  el("temporaryChatBtn").addEventListener(', '  el("temporaryMemoryPersona").addEventListener('),
    globals
  );
  return {state, click: () => click(), stats: () => ({renders, loads})};
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
