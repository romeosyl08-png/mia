(function(root) {
  'use strict';
  const cache = new Map();
  const text = (value, max, fallback = '') => {
    if (value === undefined) return fallback;
    if (typeof value !== 'string' || value.length > max) throw new Error('Texte invalide');
    return value;
  };
  function number(value, fallback) {
    const result = value === undefined ? fallback : value;
    if (typeof result !== 'number' || !Number.isFinite(result) || Math.abs(result) > 1000000) throw new Error('Nombre invalide');
    return result;
  }
  function normalize(value) {
    try {
      if (!value || typeof value !== 'object' || Array.isArray(value) || JSON.stringify(value).length > 16000) return null;
      const spec = {kind: value.kind, title: text(value.title, 100), description: text(value.description, 500)};
      if (!spec.title.trim()) return null;
      if (spec.kind === 'comparison') {
        if (!Array.isArray(value.models) || value.models.length < 2 || value.models.length > 8) return null;
        spec.models = value.models.map(m => ({name: text(m.name, 100), text: text(m.text, 2000)}));
        if (spec.models.some(m => !m.name.trim())) return null;
      } else if (spec.kind === 'prototype') {
        if (!Array.isArray(value.screens) || !value.screens.length || value.screens.length > 8) return null;
        spec.screens = value.screens.map(s => {
          if (s.actions !== undefined && (!Array.isArray(s.actions) || s.actions.length > 6)) throw new Error('Actions invalides');
          return {id: text(s.id, 40), title: text(s.title, 100), body: text(s.body, 2000),
            actions: (s.actions || []).map(a => ({label: text(a.label, 60), target: text(a.target, 40)}))};
        });
        const ids = new Set(spec.screens.map(s => s.id));
        if (ids.size !== spec.screens.length || spec.screens.some(s => !s.id || !s.title.trim() || s.actions.some(a => !a.label.trim() || !ids.has(a.target)))) return null;
      } else if (spec.kind === 'calculator') {
        if (!Array.isArray(value.inputs) || !value.inputs.length || value.inputs.length > 6) return null;
        spec.inputs = value.inputs.map(i => {
          const input = {label: text(i.label, 80), min: number(i.min, 0), max: number(i.max, 100),
            step: number(i.step, 1), value: number(i.value, 0), coefficient: number(i.coefficient, 1)};
          if (!input.label.trim() || input.min >= input.max || input.step <= 0 || input.value < input.min || input.value > input.max) throw new Error('Curseur invalide');
          return input;
        });
        spec.offset = number(value.offset, 0);
        spec.result_label = text(value.result_label, 100, 'Résultat estimé');
        spec.unit = text(value.unit, 20);
      } else return null;
      return spec;
    } catch { return null; }
  }
  function calculate(spec, values) {
    return spec.offset + spec.inputs.reduce((sum, input, i) => {
      const value = Math.min(input.max, Math.max(input.min, Number(values[i])));
      if (!Number.isFinite(value)) throw new Error('Valeur invalide');
      return sum + value * input.coefficient;
    }, 0);
  }
  function node(tag, content, className) {
    const el = document.createElement(tag);
    if (content !== undefined) el.textContent = content;
    if (className) el.className = className;
    return el;
  }
  function button(label, action) {
    const el = node('button', label); el.type = 'button'; el.addEventListener('click', action); return el;
  }
  function comparison(container, spec) {
    let mode = 'manual', pair = [0, 1], revealed = false;
    const modes = node('div', undefined, 'idea-preview-modes');
    const results = node('div', undefined, 'idea-preview-columns');
    const choices = node('div', undefined, 'idea-preview-votes');
    const status = node('p', '', 'idea-preview-status'); status.setAttribute('aria-live', 'polite');
    const controls = node('div', undefined, 'idea-preview-columns');
    const selectors = [0, 1].map(i => {
      const label = node('label', `Option ${i ? 'B' : 'A'}`), select = node('select');
      select.setAttribute('aria-label', `Choisir l’option ${i ? 'B' : 'A'}`);
      select.addEventListener('change', () => { pair[i] = Number(select.value); revealed = false; update(); });
      label.append(select); controls.append(label); return select;
    });
    const modeButtons = ['manual', 'automatic'].map(m => {
      const b = button(m === 'manual' ? 'Manuel' : 'Surprise', () => {
        mode = m; revealed = false;
        if (m === 'automatic') {
          const values = new Uint32Array(2);
          if (root.crypto?.getRandomValues) root.crypto.getRandomValues(values);
          else { values[0] = Math.floor(Math.random() * 100000); values[1] = Math.floor(Math.random() * 100000); }
          pair = [values[0] % spec.models.length, values[1] % (spec.models.length - 1)];
          if (pair[1] >= pair[0]) pair[1] += 1;
        }
        update();
      }); modes.append(b); return b;
    });
    ['Je préfère A', 'Je préfère B', 'Égalité', 'Les deux sont mauvais'].forEach(label => {
      choices.append(button(label, () => {
        revealed = true; update();
        status.textContent = `Préférence simulée : ${label}. Aucun vote réel enregistré.`;
      }));
    });
    function update() {
      modeButtons.forEach((b, i) => b.setAttribute('aria-pressed', String(mode === (i ? 'automatic' : 'manual'))));
      controls.hidden = mode !== 'manual';
      selectors.forEach((select, i) => {
        select.replaceChildren();
        if (mode === 'manual') spec.models.forEach((model, index) => {
          const option = node('option', model.name); option.value = String(index); option.disabled = index === pair[1 - i]; select.append(option);
        });
        select.value = String(pair[i]);
      });
      results.replaceChildren(); status.textContent = '';
      pair.forEach((index, i) => {
        const card = node('section', undefined, 'idea-preview-response');
        const name = mode === 'manual' || revealed ? spec.models[index].name : `Modèle ${i ? 'B' : 'A'}`;
        card.append(node('h4', name), node('p', spec.models[index].text)); results.append(card);
      });
    }
    container.append(modes, controls, results, choices, status); update();
  }
  function prototype(container, spec) {
    let index = 0;
    const tabs = node('div', undefined, 'idea-preview-tabs'), panel = node('section', undefined, 'idea-preview-screen');
    const tabButtons = spec.screens.map((screen, i) => {
      const b = button(screen.title, () => show(i)); tabs.append(b); return b;
    });
    function show(i) {
      index = i;
      tabButtons.forEach((b, j) => b.setAttribute('aria-pressed', String(index === j)));
      const screen = spec.screens[index]; panel.replaceChildren(node('h4', screen.title), node('p', screen.body));
      const actions = node('div', undefined, 'idea-preview-actions');
      screen.actions.forEach(action => actions.append(button(action.label, () => show(spec.screens.findIndex(s => s.id === action.target)))));
      panel.append(actions);
    }
    container.append(tabs, panel); show(0);
  }
  function calculator(container, spec) {
    const values = spec.inputs.map(i => i.value), result = node('output', '', 'idea-preview-result');
    result.setAttribute('aria-live', 'polite');
    const update = () => { result.textContent = `${spec.result_label} : ${new Intl.NumberFormat('fr', {maximumFractionDigits: 2}).format(calculate(spec, values))} ${spec.unit}`; };
    spec.inputs.forEach((input, i) => {
      const label = node('label', undefined, 'idea-preview-slider'), caption = node('span', `${input.label} : ${input.value}`), range = node('input');
      range.type = 'range'; range.min = input.min; range.max = input.max; range.step = input.step; range.value = input.value;
      range.setAttribute('aria-label', input.label);
      range.addEventListener('input', () => { values[i] = Number(range.value); caption.textContent = `${input.label} : ${range.value}`; update(); });
      label.append(caption, range); container.append(label);
    });
    container.append(result); update();
  }
  function create(value, key) {
    const spec = normalize(value);
    if (!spec) return null;
    const signature = JSON.stringify(spec);
    if (key && cache.get(key)?.signature === signature) return cache.get(key).element;
    const element = node('section', undefined, 'idea-preview');
    element.setAttribute('aria-label', spec.title);
    element.append(node('p', 'Aperçu interactif · démonstration', 'idea-preview-eyebrow'), node('h3', spec.title));
    if (spec.description) element.append(node('p', spec.description, 'idea-preview-description'));
    ({comparison, prototype, calculator})[spec.kind](element, spec);
    element.append(node('p', 'Interactions locales uniquement : aucun appel IA, paiement ou enregistrement de vote.', 'idea-preview-note'));
    if (key) {
      if (cache.size >= 40) cache.delete(cache.keys().next().value);
      cache.set(key, {signature, element});
    }
    return element;
  }
  function enhance(container, prefix = '') {
    const blocks = container.querySelectorAll('pre code.language-2ai-preview');
    Array.from(blocks).slice(0, 3).forEach((code, index) => {
      if (code.textContent.length > 16000) return;
      try {
        const widget = create(JSON.parse(code.textContent), prefix ? `${prefix}:preview:${index}` : undefined);
        if (widget) code.closest('pre').replaceWith(widget);
      } catch { /* Incomplete or invalid JSON remains readable as ordinary code. */ }
    });
  }
  root.AI2AIIdeaPreviews = {normalize, calculate, create, enhance};
  if (typeof module !== 'undefined') module.exports = root.AI2AIIdeaPreviews;
  if (typeof document !== 'undefined') {
    document.getElementById('ideaPreviewBtn')?.addEventListener('click', () => {
      const input = document.getElementById('composerInput');
      if (!input || input.disabled) return;
      input.value = input.value.trim() ? input.value.trim() + '\n\nAjoute un aperçu interactif de cette idée.' : 'Crée un aperçu interactif pour cette idée : ';
      input.dispatchEvent(new Event('input', {bubbles: true})); input.focus();
      document.getElementById('composerPlusMenu').hidden = true;
      document.getElementById('attachBtn').setAttribute('aria-expanded', 'false');
    });
  }
})(globalThis);
