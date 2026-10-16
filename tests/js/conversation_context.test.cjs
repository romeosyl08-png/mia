const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');

const window = {};
vm.runInNewContext(fs.readFileSync(path.join(__dirname, '../../static/app/js/conversation_context.js'), 'utf8'), {window});
const build = window.AI2AIConversationContext.build;

test('local continuity retains an old decision and the current request without uploading messages', () => {
  const messages = [{role: 'user', content: 'Budget décidé : 572 euros.'}];
  for(let i = 0; i < 120; i++) messages.push({role: i % 2 ? 'assistant' : 'user', content: 'Discussion du projet.'});
  messages.push({role: 'user', content: 'Quel budget avions-nous décidé ?'});
  const before = JSON.stringify(messages);
  const result = build(messages);
  assert.ok(result.some(row => row.content.includes('572 euros')));
  assert.equal(result.at(-1).content, messages.at(-1).content);
  assert.ok(result.length <= 49);
  assert.ok(result.slice(0, -1).reduce((sum, row) => sum + row.content.length, 0) <= 48000);
  assert.equal(JSON.stringify(messages), before);
});

test('local recall finds a detail deep inside an old long message', () => {
  const messages = [{role: 'user', content: 'Introduction. '.repeat(100) + '\nNom interne : kumquat.\n' + 'Fin. '.repeat(150)}];
  for(let i = 0; i < 80; i++) messages.push({role: 'user', content: `Budget décidé : ${i + 1000} euros.`});
  messages.push({role: 'user', content: 'Quel était le nom kumquat ?'});
  const result = build(messages, {maxChars: 8000, maxMessages: 6});
  assert.ok(result.some(row => row.content.includes('Nom interne : kumquat.')));
  assert.ok(result.slice(0, -1).reduce((sum, row) => sum + row.content.length, 0) <= 8000);
});

test('long assistant responses keep a user boundary and stay within the history budget', () => {
  const messages = [{role: 'user', content: 'Budget initial : 310 euros.'},
    {role: 'assistant', content: 'Détail. '.repeat(5000)}, {role: 'user', content: 'Continue.'}];
  const result = build(messages, {maxChars: 8000, maxMessages: 6});
  assert.equal(result[0].role, 'user');
  assert.ok(result.some(row => row.role === 'assistant'));
  assert.ok(result.some(row => row.content.includes('310 euros')));
  assert.ok(result.slice(0, -1).reduce((sum, row) => sum + row.content.length, 0) <= 8000);
});

test('an edited local branch has no memory of messages removed from the branch', () => {
  const messages = [{role: 'user', content: 'Départ.'}];
  for(let i = 0; i < 20; i++) messages.push({role: 'user', content: 'Suite.'});
  messages.push({role: 'assistant', content: 'Décision future : origan.'}, {role: 'user', content: 'Continue.'});
  assert.ok(build(messages, {maxMessages: 6}).some(row => row.content.includes('origan')));
  const prefix = messages.slice(0, 10).concat({role: 'user', content: 'Nouvelle branche.'});
  assert.ok(build(prefix, {maxMessages: 6}).every(row => !row.content.includes('origan')));
});

test('the actual local request uses compacted history and sends message content only to the local model', async () => {
  const source = fs.readFileSync(path.join(__dirname, '../../static/app/js/script.js'), 'utf8');
  const start = source.indexOf('  function normalizeLocalBaseUrl(');
  const end = source.indexOf('  function messageCapabilityIssue(', start);
  assert.ok(start >= 0 && end > start);
  const requests = [];
  const globals = {
    URL, window: {...window, AI2AIPersonaContext: {load: async () => 'Persona'}},
    state: {temperatureOverride: 0.7}, el: () => ({value: '500'}),
    fetch: async (url, options) => {
      requests.push({url, body: JSON.parse(options.body)});
      return {ok: true, headers: {get: () => 'application/json'},
        json: async () => ({choices: [{message: {content: 'Réponse'}}]})};
    }
  };
  vm.runInNewContext(source.slice(start, end), globals);
  const messages = [{role: 'user', content: 'Budget décidé : 572 euros.'}];
  for(let i = 0; i < 120; i++) messages.push({role: 'bot', content: 'Discussion.'});
  messages.push({role: 'user', content: 'Quel budget ?'});
  await globals.callLocalModel({}, {localModel: {modelId: 'local', baseUrl: 'http://localhost:11434'}, messages}, () => {});
  assert.equal(requests.length, 1);
  assert.equal(requests[0].url, 'http://localhost:11434/v1/chat/completions');
  assert.ok(requests[0].body.messages.some(row => row.content.includes('572 euros')));
});
