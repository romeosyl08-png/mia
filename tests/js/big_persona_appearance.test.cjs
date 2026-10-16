const assert = require('node:assert/strict');
const test = require('node:test');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const accents = ['#6C6FCB', '#E8A33D', '#3F8E8A', '#B4483A', '#4E9F3D'];
const flush = () => new Promise(resolve => setImmediate(resolve));

function element(dataset = {}) {
  const classes = new Set();
  return {
    dataset, style: {}, attributes: {}, handlers: {}, children: [], textContent: '', disabled: false,
    classList: {
      contains(value) { return classes.has(value); },
      toggle(value, force) {
        const enabled = force ?? !classes.has(value);
        enabled ? classes.add(value) : classes.delete(value);
        return enabled;
      }
    },
    addEventListener(name, handler) { this.handlers[name] = handler; },
    setAttribute(name, value) { this.attributes[name] = value; },
    replaceChildren(...children) { this.children = children; },
    append(child) { this.children.push(child); }
  };
}

function harness() {
  const state = {
    persona: {
      id: 12, is_big_persona: true,
      appearance_theme: 'dark', appearance_accent: '#3F8E8A',
      bara_appearance_theme: 'light', bara_appearance_accent: '#E8A33D'
    },
    requests: [], central: [], dispatched: [], timers: []
  };
  const ids = {
    bigPersonaAppearanceStatus: element(),
    webSwatches: element(), baraSwatches: element(),
    webThemeLight: element({theme: 'light'}), webThemeDark: element({theme: 'dark'}),
    baraThemeLight: element({theme: 'light'}), baraThemeDark: element({theme: 'dark'})
  };
  const document = {
    getElementById: id => ids[id] || null,
    querySelector: () => ({value: 'csrf-fixture'}),
    createElement: () => element(),
    dispatchEvent: event => state.dispatched.push(event)
  };
  const window = {
    AI2AIAppearance: {
      getAccents: () => [...accents],
      setCentralAppearance: persona => state.central.push({...persona})
    },
    setTimeout(callback, delay) {
      const id = state.timers.length + 1;
      state.timers.push({id, callback, delay});
      return id;
    },
    clearTimeout(id) { state.timers = state.timers.filter(timer => timer.id !== id); }
  };
  const fetch = async (url, options = {}) => {
    state.requests.push({url, options});
    if (options.method === 'PATCH') {
      const payload = JSON.parse(options.body);
      state.persona = {...state.persona, ...payload};
      return {ok: true, json: async () => ({...state.persona})};
    }
    return {ok: true, json: async () => ({persona: {...state.persona}})};
  };
  const CustomEvent = class { constructor(type, init) { this.type = type; this.detail = init.detail; } };
  const source = fs.readFileSync(path.join(__dirname, '../../static/app/js/big_persona_appearance.js'), 'utf8');
  vm.runInNewContext(source, {window, document, fetch, CustomEvent}, {filename: 'big_persona_appearance.js'});
  return {state, ids};
}

test('central panel loads separate web and Bara themes and the VS Code inherited preference', async () => {
  const app = harness();
  await flush();
  assert.equal(app.state.requests[0].url, '/chat/api/big-persona/');
  assert.deepEqual(app.state.central[0], app.state.persona);
  assert.equal(app.ids.webThemeDark.classList.contains('active'), true);
  assert.equal(app.ids.baraThemeLight.classList.contains('active'), true);
  assert.equal(app.ids.webSwatches.children.length, 5);
  assert.equal(app.ids.baraSwatches.children.length, 6);
  assert.equal(app.ids.baraSwatches.children.find(button => button.dataset.color === '#E8A33D').classList.contains('active'), true);
});

test('changing a surface theme saves only the independent Big Persona appearance profile', async () => {
  const app = harness();
  await flush();
  app.ids.baraThemeDark.handlers.click();
  assert.equal(app.ids.bigPersonaAppearanceStatus.textContent, 'Enregistrement…');
  const timer = app.state.timers.find(item => item.delay === 350);
  assert.ok(timer);
  timer.callback();
  await flush();
  const patch = app.state.requests.find(request => request.options.method === 'PATCH');
  assert.equal(patch.url, '/chat/api/personas/12/');
  assert.deepEqual(JSON.parse(patch.options.body), {
    appearance_theme: 'dark',
    appearance_accent: '#3F8E8A',
    bara_appearance_theme: 'dark',
    bara_appearance_accent: '#E8A33D'
  });
  assert.equal(app.ids.bigPersonaAppearanceStatus.textContent, 'Préférences enregistrées.');
  assert.equal(app.state.dispatched[0].type, 'ai2ai:persona-saved');
});
