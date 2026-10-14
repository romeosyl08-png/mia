const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');

function harness(initialIds = []){
  const requests = [], errors = [], buttons = [0, 1].map(() => ({
    dataset: {modelFavorite: 'model-a', modelName: 'Model A'}, attributes: {},
    setAttribute(name, value){this.attributes[name] = value;}, focus(){}
  }));
  const lists = [0, 1].map(() => ({addEventListener(_name, callback){this.click = callback;}}));
  const context = {window: {}, document: {
    querySelectorAll: () => buttons,
    querySelector: () => ({value: 'fixture-csrf'}),
    getElementById: id => lists[id === 'modelPickerList' ? 0 : 1]
  }, fetch(url, options){return new Promise((resolve, reject) => requests.push({url, options, resolve, reject}));}};
  vm.runInNewContext(fs.readFileSync(path.join(__dirname, '../../static/app/js/model_favorites.js'), 'utf8'), context);
  const favorites = context.window.AI2AIModelFavorites.create({initialIds, onError: message => errors.push(message)});
  return {favorites, requests, errors, buttons, lists};
}

test('both lists show the saved favorite and removing it persists only the favorite setting', async () => {
  const app = harness(['model-a']);
  assert.ok(app.buttons.every(button => button.attributes['aria-pressed'] === 'true'));
  const pending = app.favorites.toggle('model-a');
  const request = app.requests[0];
  assert.equal(request.url, '/chat/api/preferences/');
  assert.equal(request.options.method, 'PATCH');
  assert.deepEqual(JSON.parse(request.options.body), {favorite_model_id: 'model-a', favorite: false});
  request.resolve({ok: true, json: async () => ({favorite_model_ids: []})});
  await pending;
  assert.ok(app.buttons.every(button => button.attributes['aria-pressed'] === 'false' && !button.disabled));
});

test('a failed save restores both stars and reports the failure', async () => {
  const app = harness();
  const pending = app.favorites.toggle('model-a');
  assert.ok(app.buttons.every(button => button.attributes['aria-pressed'] === 'true' && button.disabled));
  app.requests[0].reject(new Error('Connexion perdue'));
  await pending;
  assert.equal(app.favorites.has('model-a'), false);
  assert.ok(app.buttons.every(button => button.attributes['aria-pressed'] === 'false' && !button.disabled));
  assert.deepEqual(app.errors, ['Connexion perdue']);
});

test('clicking a star does not propagate to model selection or send duplicate requests', async () => {
  const app = harness();
  let stopped = false, prevented = false;
  app.lists[0].click({target: {closest: () => app.buttons[0]}, preventDefault(){prevented = true;}, stopPropagation(){stopped = true;}});
  await app.favorites.toggle('model-a');
  assert.ok(stopped && prevented);
  assert.equal(app.requests.length, 1);
  app.requests[0].resolve({ok: true, json: async () => ({favorite_model_ids: ['model-a']})});
});
