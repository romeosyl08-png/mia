const assert = require('node:assert/strict');
const test = require('node:test');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

function run(file, globals) {
  const source = fs.readFileSync(path.join(__dirname, '../../static/app/js', file), 'utf8');
  vm.runInNewContext(source, {console, ...globals}, {filename: file});
}
const flush = () => new Promise(resolve => setImmediate(resolve));

function element(active = false) {
  const classes = new Set(active ? ['active'] : []);
  return {
    dataset: {}, style: {}, attributes: {}, handlers: {}, children: [], hidden: true,
    classList: {
      contains: value => classes.has(value),
      toggle(value, force) {
        const enabled = force ?? !classes.has(value);
        enabled ? classes.add(value) : classes.delete(value);
        return enabled;
      }
    },
    setAttribute(name, value) { this.attributes[name] = value; },
    addEventListener(name, handler) { this.handlers[name] = handler; },
    focus() { this.focused = true; },
    append(...children) { this.children.push(...children); }
  };
}

function taskHarness({hidden = false, status = 'queued'} = {}) {
  const timers = [], requests = [];
  const card = element();
  card.dataset = {task: '8', status};
  const fields = Object.fromEntries(['.task-status', '.task-result', '.task-error', '.task-actions', '.task-steps', '.task-budget'].map(selector => [selector, element()]));
  const buttons = ['pause', 'resume', 'cancel'].map(action => Object.assign(element(), {dataset: {action}}));
  fields['.task-steps'].value = '12';
  fields['.task-budget'].value = '1000';
  card.querySelector = selector => fields[selector] || buttons.find(button => selector === `[data-action=${button.dataset.action}]`) || null;
  card.querySelectorAll = () => buttons;
  const document = {
    hidden,
    querySelector: selector => selector === '[data-tasks-root]' ? {dataset: {tasksRoot: '/bara/7/tasks/'}} : {value: 'fixture-csrf'},
    querySelectorAll: () => [card],
    createElement: () => element()
  };
  run('tasks.js', {
    document, URLSearchParams,
    window: {setTimeout(callback, delay) { timers.push({callback, delay}); }},
    fetch(url, options) {
      return new Promise((resolve, reject) => requests.push({url, options, resolve, reject}));
    }
  });
  async function respond(index, status, events = []) {
    requests[index].resolve({ok: true, json: async () => ({status, events, steps: 4, max_steps: 12, credits: 10, credit_limit: 1000, result: 'Résultat', error: ''})});
    await flush();
  }
  return {card, fields, buttons, document, timers, requests, respond};
}

test('task polling waits for the previous response and uses the event cursor', async () => {
  const app = taskHarness();
  assert.equal(app.requests.length, 1);
  assert.equal(app.timers.length, 0);
  await app.respond(0, 'running', [{id: 31, data: {}}]);
  const timer = app.timers.shift();
  assert.equal(timer.delay, 3000);
  const pending = timer.callback();
  assert.equal(app.requests.length, 2);
  assert.equal(app.timers.length, 0);
  assert.equal(app.requests[1].url, '/bara/7/tasks/8/?after=31');
  await app.respond(1, 'completed');
  await pending;
  assert.equal(app.timers.length, 0);
});

test('completed and cancelled tasks render their final result then stop polling', async () => {
  for (const status of ['completed', 'cancelled']) {
    const app = taskHarness();
    await app.respond(0, status);
    assert.equal(app.fields['.task-result'].textContent, 'Résultat');
    assert.equal(app.timers.length, 0);
    assert.ok(app.buttons.every(button => button.disabled));
  }
});

test('hidden pages avoid task requests and paused tasks refresh less often', async () => {
  const app = taskHarness({hidden: true});
  assert.equal(app.requests.length, 0);
  const timer = app.timers.shift();
  app.document.hidden = false;
  const pending = timer.callback();
  await app.respond(0, 'paused');
  await pending;
  assert.equal(app.timers[0].delay, 15000);
  assert.equal(app.buttons.find(button => button.dataset.action === 'resume').disabled, false);
});

test('a failed task request keeps the task visible and schedules a retry', async () => {
  const app = taskHarness();
  app.requests[0].reject(new Error('offline'));
  await flush();
  assert.match(app.fields['.task-error'].textContent, /Connexion perdue/);
  assert.equal(app.card.dataset.status, 'queued');
  assert.equal(app.timers[0].delay, 3000);
});

function themeHarness(saved, unavailable = false) {
  const properties = new Map();
  const root = {dataset: {theme: 'light'}, style: {setProperty: (name, value) => properties.set(name, value)}};
  run('theme.js', {document: {documentElement: root}, localStorage: {getItem() { if (unavailable) throw Error('Storage unavailable'); return saved; }}});
  return {root, properties};
}

test('the saved dark theme and accent apply before body creation', () => {
  const app = themeHarness(JSON.stringify({theme: 'dark', accent: '#E8A33D'}));
  assert.equal(app.root.dataset.theme, 'dark');
  assert.equal(app.properties.get('--accent'), '#E8A33D');
  assert.equal(app.properties.get('--accent-ink'), '#211914');
});

test('invalid appearance data and unavailable storage keep usable defaults', () => {
  for (const app of [themeHarness('{broken'), themeHarness(null, true)]) assert.equal(app.root.dataset.theme, 'light');
  const app = themeHarness(JSON.stringify({theme: 'invalid', accent: 'url(unsafe)'}));
  assert.equal(app.root.dataset.theme, 'light');
  assert.equal(app.properties.get('--accent'), '#6C6FCB');
});

function appearanceHarness(saved = null) {
  const properties = new Map(), rootAttributes = {}, bodyAttributes = {};
  let persisted = saved;
  const root = {
    style: {setProperty(name, value) { properties.set(name, value); }},
    setAttribute(name, value) { rootAttributes[name] = value; }
  };
  const body = {
    setAttribute(name, value) { bodyAttributes[name] = value; },
    hasAttribute(name) { return Object.hasOwn(bodyAttributes, name); }
  };
  const window = {};
  run('appearance.js', {
    window,
    document: {
      documentElement: root, body, title: '',
      querySelectorAll: () => [], querySelector: () => null, getElementById: () => null
    },
    localStorage: {
      getItem() { return persisted; },
      setItem(_key, value) { persisted = value; }
    }
  });
  return {window, properties, rootAttributes, bodyAttributes, get persisted() { return persisted; }};
}

test('Big Persona surface appearance remains the Barrassons theme across persona switches', () => {
  const app = appearanceHarness();
  const appearance = app.window.AI2AIAppearance;
  appearance.setCentralAppearance({appearance_theme: 'dark', appearance_accent: '#3F8E8A'});
  appearance.applyPersona({
    appearance_name: 'Coach code', appearance_avatar: '⌘',
    appearance_theme: 'light', appearance_accent: '#E8A33D'
  });
  assert.equal(appearance.get().botName, 'Coach code');
  assert.equal(appearance.get().botAvatar, '⌘');
  assert.equal(appearance.get().theme, 'dark');
  assert.equal(appearance.get().accent, '#3F8E8A');
  assert.equal(app.rootAttributes['data-theme'], 'dark');
  assert.equal(app.properties.get('--accent'), '#3F8E8A');
  assert.equal(JSON.parse(app.persisted).theme, 'dark');
});

function authHarness(signup = false) {
  const ids = ['tabLogin', 'tabSignup', 'panelLogin', 'panelSignup', 'linkToSignup', 'linkToLogin'];
  const nodes = Object.fromEntries(ids.map(id => [id, Object.assign(element(signup && id === 'tabSignup'), {id})]));
  nodes.tabLogin.parentElement = nodes.tabSignup.parentElement = element();
  nodes.panelSignup.errorText = 'Mot de passe trop court';
  run('auth.js', {document: {getElementById: id => nodes[id]}});
  return nodes;
}

test('server signup errors remain visible after initializing authentication tabs', () => {
  const app = authHarness(true);
  assert.equal(app.panelSignup.style.display, 'block');
  assert.equal(app.panelLogin.style.display, 'none');
  assert.equal(app.panelSignup.errorText, 'Mot de passe trop court');
  assert.equal(app.tabSignup.attributes['aria-selected'], 'true');
});

test('authentication tabs support keyboard navigation and account-switch links', () => {
  const app = authHarness();
  let prevented = false;
  app.tabLogin.handlers.keydown({key: 'ArrowRight', preventDefault() { prevented = true; }});
  assert.ok(prevented);
  assert.equal(app.panelSignup.style.display, 'block');
  assert.equal(app.tabSignup.tabIndex, 0);
  assert.ok(app.tabSignup.focused);
  app.linkToLogin.handlers.click({preventDefault() {}});
  assert.equal(app.panelLogin.style.display, 'block');
  assert.equal(app.tabLogin.attributes['aria-selected'], 'true');
});

function pwaHarness(readyState, idle = true) {
  const listeners = {}, registrations = [], scheduled = [];
  const nodes = Object.fromEntries(['pwa-install-controls', 'pwa-install-button', 'pwa-ios-install-help', 'pwa-install-status'].map(id => [id, element()]));
  const window = {
    matchMedia: () => ({matches: false}),
    addEventListener(name, callback) { listeners[name] = callback; },
    setTimeout(callback, delay) { scheduled.push({callback, delay}); }
  };
  if (idle) window.requestIdleCallback = (callback, options) => scheduled.push({callback, options});
  run('pwa.js', {
    window, document: {readyState, getElementById: id => nodes[id]},
    navigator: {userAgent: 'Chrome', platform: 'Linux', maxTouchPoints: 0, serviceWorker: {register(url, options) { registrations.push({url, options}); return Promise.resolve(); }}}
  });
  return {listeners, registrations, scheduled};
}

test('service worker registration waits for load and an idle callback', () => {
  const app = pwaHarness('loading');
  assert.equal(app.registrations.length, 0);
  assert.equal(app.scheduled.length, 0);
  app.listeners.load();
  assert.equal(app.registrations.length, 0);
  assert.equal(app.scheduled[0].options.timeout, 2000);
  app.scheduled[0].callback();
  assert.equal(app.registrations[0].url, '/service-worker.js');
  assert.equal(app.registrations[0].options.scope, '/');
});

test('service worker registration has a fallback without requestIdleCallback', () => {
  const app = pwaHarness('complete', false);
  assert.equal(app.registrations.length, 0);
  assert.equal(app.scheduled[0].delay, 0);
  app.scheduled[0].callback();
  assert.equal(app.registrations.length, 1);
});
