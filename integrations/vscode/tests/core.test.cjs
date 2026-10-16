'use strict';

const test = require('node:test');
const assert = require('node:assert/strict');
const core = require('../core');
const manifest = require('../package.json');

test('le manifeste déclare le même participant de chat que l’extension et garde l’inline opt-in', () => {
  const participant = manifest.contributes.chatParticipants[0];
  assert.equal(participant.id, 'ai2ai.barrassons.chat');
  assert.ok(manifest.activationEvents.includes(`onChatParticipant:${participant.id}`));
  assert.equal(manifest.contributes.configuration.properties['barrassons.inlineCompletions.enabled'].default, false);
});

test('les chemins sensibles usuels sont exclus du contexte transmis', () => {
  for (const file of [
    '.env', '.env.production', '.netrc', '.npmrc', 'id_ed25519',
    'keys/private.pem', 'config/private-key.json', 'tokens/access.txt', '.git/config'
  ]) assert.equal(core.isSensitiveFile(file), true, file);
  for (const file of ['src/tokenizer.py', 'src/account_form.js', 'src/key_manager.py', 'src/main.py', 'README.md']) {
    assert.equal(core.isSensitiveFile(file), false, file);
  }
});

test('normalizeApiBase ajoute /v1 à l’URL de serveur et retire les slashs finaux', () => {
  assert.equal(core.normalizeApiBase('https://ai.example.test'), 'https://ai.example.test/v1');
  assert.equal(core.normalizeApiBase('https://ai.example.test/'), 'https://ai.example.test/v1');
  assert.equal(core.normalizeApiBase('https://ai.example.test/v1///'), 'https://ai.example.test/v1');
  assert.equal(core.normalizeApiBase('https://ai.example.test/custom/v2/'), 'https://ai.example.test/custom/v2');
  assert.equal(core.normalizeApiBase('http://127.0.0.1:8000/v1'), 'http://127.0.0.1:8000/v1');
});

test('normalizeApiBase refuse les URL non HTTP(S), les identifiants, paramètres et clés en chemin', () => {
  for (const value of [
    '', 'not a url', 'ftp://ai.example.test', 'https://user:pass@ai.example.test',
    'https://ai.example.test?key=secret', 'https://ai.example.test/#token',
    'http://ai.example.test/v1', 'https://ai.example.test/sk-ai2ai-secret'
  ]) {
    assert.throws(() => core.normalizeApiBase(value), /Adresse API invalide/);
  }
});

test('les messages de complétion conservent le curseur et bornent le contexte', () => {
  const messages = core.buildInlineMessages({
    language: 'javascript', fileName: 'src/example.js',
    before: 'a'.repeat(1_000), after: 'b'.repeat(1_000), maxContextChars: 800
  });
  assert.equal(messages[0].role, 'system');
  assert.match(messages[0].content, /uniquement avec le texte à insérer/);
  assert.match(messages[1].content, /Langage : javascript/);
  assert.match(messages[1].content, /Fichier : src\/example\.js/);
  const before = messages[1].content.match(/<before>\n([\s\S]*?)\n<\/before>/)[1];
  const after = messages[1].content.match(/<after>\n([\s\S]*?)\n<\/after>/)[1];
  assert.equal(before.length + after.length, 800);
  assert.ok(before.endsWith('a'.repeat(20)));
  assert.ok(after.startsWith('b'.repeat(20)));
});

test('extractInlineCompletion retire une clôture de code, préserve le texte et borne la longueur', () => {
  assert.equal(core.extractInlineCompletion({choices: [{message: {content: '```js\nreturn 42;\n```'}}]}), 'return 42;');
  assert.equal(core.extractInlineCompletion({choices: [{message: {content: '  next();  '}}]}), '  next();  ');
  assert.equal(core.extractInlineCompletion({choices: [{message: {content: 'x'.repeat(2_000)}}]}).length, core.MAX_INLINE_COMPLETION_CHARS);
  assert.equal(core.extractInlineCompletion({choices: [{message: {content: null}}]}), '');
});

test('les messages de chat incluent l’historique, les commandes et le contexte explicite', () => {
  const messages = core.buildChatMessages({
    history: [
      {role: 'user', content: 'Pourquoi ce test échoue ?'},
      {role: 'assistant', content: 'La valeur attendue ne correspond pas.'},
      {role: 'system', content: 'Ne pas transmettre ce rôle.'}
    ],
    prompt: 'Corrige ce cas.',
    command: 'fix',
    context: [{label: 'src/test.js · sélection', text: 'assert.equal(actual, expected);'}]
  });
  assert.equal(messages[0].role, 'system');
  assert.match(messages[0].content, /propose une correction/);
  assert.deepEqual(messages.slice(1).map(message => message.role), ['user', 'assistant', 'user']);
  assert.match(messages.at(-1).content, /Corrige ce cas/);
  assert.match(messages.at(-1).content, /src\/test\.js · sélection/);
  assert.match(messages.at(-1).content, /assert\.equal/);
});

test('createTextBatcher regroupe les petits tokens et vide son tampon sans laisser de timer', () => {
  const output = [];
  const batcher = core.createTextBatcher(text => output.push(text), {maxChars: 5, intervalMs: 10_000});
  batcher.push('Bon');
  batcher.push('jour');
  assert.deepEqual(output, ['Bonjour']);
  batcher.push(' fin');
  batcher.dispose();
  batcher.push(' ignoré');
  assert.deepEqual(output, ['Bonjour', ' fin']);
});

test('createTextBatcher abandonne les données encore tamponnées après annulation', () => {
  const output = [];
  let cancelled = false;
  const batcher = core.createTextBatcher(text => output.push(text), {
    intervalMs: 10_000, isCancelled: () => cancelled
  });
  batcher.push('réponse partielle');
  cancelled = true;
  batcher.dispose();
  assert.deepEqual(output, []);
});

test('requestCompletion utilise l’API Bearer sans exposer la clé dans l’URL', async () => {
  const apiKey = 'sk-ai2ai-test-secret';
  let request;
  const result = await core.requestCompletion({
    apiBase: 'https://ai.example.test', apiKey, model: 'auto',
    messages: [{role: 'user', content: 'Complète ce code.'}], maxTokens: 64,
    fetchImpl: async (url, options) => {
      request = {url: String(url), options};
      return new Response(JSON.stringify({choices: [{message: {content: 'console.log(1);'}}]}), {
        status: 200, headers: {'content-type': 'application/json'}
      });
    }
  });
  assert.equal(request.url, 'https://ai.example.test/v1/chat/completions');
  assert.equal(request.options.headers.Authorization, `Bearer ${apiKey}`);
  assert.equal(request.options.redirect, 'error');
  assert.equal(JSON.parse(request.options.body).max_tokens, 64);
  assert.ok(!request.url.includes(apiKey));
  assert.equal(result.choices[0].message.content, 'console.log(1);');
});

test('les erreurs HTTP sont lisibles et ne reprennent pas la clé', async () => {
  await assert.rejects(core.getModels({
    apiBase: 'https://ai.example.test/v1', apiKey: 'sk-ai2ai-private',
    fetchImpl: async () => new Response(JSON.stringify({error: {message: 'invalid'}}), {status: 401})
  }), error => {
    assert.match(error.message, /Clé API refusée/);
    assert.ok(!error.message.includes('sk-ai2ai-private'));
    return true;
  });
});

test('consumeCompletionStream reconstitue les événements SSE fractionnés et ignore les événements sans texte', async () => {
  const encoder = new TextEncoder();
  const chunks = [
    'data: {"choices":[{"delta":{"content":"Bonjour"}}]}\r\n\r\n',
    'data: {"choices":[]}\n\n',
    'data: {"choices":[{"delta":{"content":" le monde"}}]}\n\n',
    'data: [DONE]\n\n'
  ];
  const stream = new ReadableStream({
    start(controller) {
      const first = encoder.encode(chunks[0]);
      controller.enqueue(first.slice(0, 17));
      controller.enqueue(first.slice(17));
      for (const chunk of chunks.slice(1)) controller.enqueue(encoder.encode(chunk));
      controller.close();
    }
  });
  const output = [];
  await core.consumeCompletionStream({body: stream}, text => output.push(text));
  assert.equal(output.join(''), 'Bonjour le monde');
});

test('consumeCompletionStream transmet les erreurs émises au milieu du flux', async () => {
  const stream = new ReadableStream({
    start(controller) {
      controller.enqueue(new TextEncoder().encode('data: {"error":{"message":"provider stopped"}}\n\n'));
      controller.close();
    }
  });
  await assert.rejects(core.consumeCompletionStream({body: stream}, () => {}), /provider stopped/);
});
