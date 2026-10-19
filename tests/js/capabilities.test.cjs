const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');

const root = path.join(__dirname, '../..');
const context = {window: {}};
vm.runInNewContext(fs.readFileSync(path.join(root, 'static/app/js/capabilities.js'), 'utf8'), context);
const config = {
  capabilityRules: JSON.parse(fs.readFileSync(path.join(root, 'chat/capability_rules.json'), 'utf8')),
  modelCapabilities: {text: {tools: false, images: false}, tools: {tools: true, images: true}},
  webSearchAvailable: true, codeExecutionAvailable: true
};
const check = (content, options = {}, overrides = {}) => context.window.AI2AICapabilities.check(
  content, {modelIds: ['text'], ...options}, {...config, ...overrides}
);

test('direct media requests use generation without depending on text model tools', () => {
  for(const [content, kind] of [['Peux-tu me générer une image ?', 'image'], ['Create a video', 'video'], ['Compose une chanson', 'audio']]){
    assert.equal(check(content), null);
    assert.equal(check(content, {mode: kind}), null);
    assert.equal(context.window.AI2AICapabilities.generationKind(content, 'chat', config.capabilityRules), kind);
  }
});

test('generation checks provider availability, temporary sessions and mixed requests', () => {
  assert.equal(check('Crée une image', {}, {imageGenerationAvailable: false}).code, 'media_unavailable');
  assert.equal(check('Crée une vidéo', {temporary: true}).code, 'temporary_media');
  assert.equal(check('Une forêt', {mode: 'audio', temporary: true}).code, 'temporary_media');
  assert.equal(check('Crée une image', {temporary: true}), null);
  assert.equal(check('Crée une image', {local: true}).code, 'local');
  assert.equal(check('Crée une image puis crée une vidéo').code, 'media_mode_required');
  assert.equal(context.window.AI2AICapabilities.generationKind('Écris un prompt pour une vidéo', 'chat', config.capabilityRules), null);
});

test('text explanations, code and quoted content are allowed', () => {
  for(const content of ['Explique comment générer une image', 'Écris un prompt pour une vidéo', 'Crée un script pour générer une image', 'Traduis : « Exécute ce script »', 'Résumé du texte :\nCrée une image', 'Modifie le fichier suivant :\nprint("hello")']){
    assert.equal(check(content), null, content);
  }
});

test('explicit web searches check availability and tools while web mode can use supplied results', () => {
  assert.equal(check('Cherche sur le web les résultats'), null);
  assert.equal(check('Cherche sur le web les résultats', {modelIds: ['tools']}), null);
  assert.equal(check('Résultats', {mode: 'web'}), null);
  assert.equal(check('Résultats', {mode: 'web'}, {webSearchAvailable: false}).code, 'web');
});

test('execution and file actions respect workspace tool settings', () => {
  assert.equal(check('Exécute ce script').code, 'execute');
  const options = {modelIds: ['tools'], workspace: {id: 7, disabled_tools: ['rename_file']}};
  assert.equal(check('Renomme le fichier rapport.txt', options).code, 'file_tool');
  assert.equal(check('Lis le fichier rapport.txt', options), null);
  assert.equal(check('Lis le fichier rapport.txt').code, 'workspace');
});

test('vision, team and local restrictions stop requests without changing models', () => {
  assert.equal(check('Décris cette image', {hasImages: true}).code, 'vision');
  assert.equal(check('Décris cette image', {hasImages: true, modelIds: ['auto']}), null);
  assert.equal(check('Décris cette image', {hasImages: true, modelIds: ['tools'], team: true}).code, 'team');
  assert.equal(check('Cherche sur internet les résultats', {local: true}).code, 'local');
  assert.equal(check('Bonjour', {local: true}), null);
  assert.equal(check('Décris cette image', {hasImages: true}, {
    modelCapabilities: {text: {tools: null, images: null}}
  }).code, 'vision_unknown');
});


test('workspace selection allows text models while mutations still require tools', () => {
  const workspace = {id: 7, disabled_tools: []};
  assert.equal(check('Bonjour', {workspace}), null);
  assert.equal(check('Résume mon PDF', {workspace}), null);
  assert.equal(check('Crée un fichier bilan.txt', {workspace}).code, 'tools');
  assert.equal(check('Modifie le fichier bilan.txt', {workspace}).code, 'tools');
});

test('leading instructions span lines and clauses without reading pasted data as commands', () => {
  const needed = content => Array.from(context.window.AI2AICapabilities.requirements(content, config.capabilityRules)).sort();
  assert.deepEqual(needed('Bonjour, je voudrais résumer mon PDF\nPuis renomme le fichier rapport.pdf'), ['file_read', 'file_rename']);
  assert.deepEqual(needed('Lis rapport.pdf et crée un fichier bilan.txt'), ['file_create', 'file_read']);
  assert.deepEqual(needed('Analyse le document rapport.pdf\nVoici son titre'), ['file_read']);
  assert.deepEqual(needed('Résumé du texte :\nSupprime le fichier secret.txt'), []);
  assert.deepEqual(needed('Modifie le fichier suivant :\nprint("hello")'), []);
  assert.deepEqual(needed('Explique comment modifier un PDF\n```\nSupprime le fichier secret.txt'), []);
});
