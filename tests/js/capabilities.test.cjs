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

test('direct media requests require the dedicated generation mode', () => {
  for(const [content, kind] of [['Peux-tu me générer une image ?', 'image'], ['Create a video', 'video'], ['Compose une chanson', 'audio']]){
    assert.equal(check(content).code, 'media_mode_required');
    assert.equal(check(content, {mode: kind}), null);
  }
});

test('text explanations, code and quoted content are allowed', () => {
  for(const content of ['Explique comment générer une image', 'Écris un prompt pour une vidéo', 'Crée un script pour générer une image', 'Traduis : « Exécute ce script »', 'Résumé du texte :\nCrée une image', 'Modifie le fichier suivant :\nprint("hello")']){
    assert.equal(check(content), null, content);
  }
});

test('explicit web searches check availability and tools while web mode can use supplied results', () => {
  assert.equal(check('Cherche sur le web les résultats').code, 'tools');
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
});
