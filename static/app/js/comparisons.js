(() => {
  'use strict';
  const app = document.getElementById('comparisonApp');
  if (!app) return;
  const form = document.getElementById('comparisonForm');
  const voteForm = document.getElementById('voteForm');
  const notice = document.getElementById('comparisonNotice');
  const inputs = document.getElementById('comparisonInputs');
  const selectors = [document.getElementById('modelA'), document.getElementById('modelB')];
  const csrf = form.querySelector('[name=csrfmiddlewaretoken]').value;
  const draftStorageKey = `2ai-comparison-draft:${app.dataset.userId}`;
  const terminal = new Set(['completed', 'failed', 'cancelled']);
  const labels = {pending: 'En attente', processing: 'Génération en cours…', completed: 'Terminée', failed: 'Échec', cancelled: 'Annulée'};
  let mode = 'manual', current = null, timer = null, draftKey = null, pollVersion = 0;
  async function api(url, options = {}) {
    const result = await fetch(url, {credentials: 'same-origin', ...options, headers: {'Content-Type': 'application/json', 'X-CSRFToken': csrf, ...options.headers}});
    if (!result.ok) {
      const error = await result.json().catch(() => ({}));
      throw new Error(error.error || `La requête a échoué (${result.status}).`);
    }
    return result.json();
  }
  function distinct() {
    selectors.forEach((select, i) => Array.from(select.options).forEach(option => {
      option.disabled = Boolean(option.value && option.value === selectors[1 - i].value);
    }));
  }
  async function loadModels() {
    const data = await api(app.dataset.modelsUrl);
    if (mode !== 'manual') return;
    selectors.forEach((select, i) => {
      select.replaceChildren(new Option('Choisir un modèle', ''));
      data.models.forEach(m => select.add(new Option(`${m.name} · ${m.provider} · ~${m.estimated_credits} jetons + question`, String(m.id))));
      if (data.models[i]) select.value = String(data.models[i].id);
    });
    distinct();
  }
  selectors.forEach(select => select.addEventListener('change', distinct));
  function setMode(value) {
    mode = value;
    app.querySelectorAll('[data-mode]').forEach(b => b.setAttribute('aria-pressed', String(b.dataset.mode === mode)));
    document.getElementById('manualModels').hidden = mode !== 'manual';
    selectors.forEach(select => { select.required = mode === 'manual'; });
    if (mode === 'automatic') selectors.forEach(select => select.replaceChildren());
    else if (!selectors[0].options.length) loadModels().catch(error => { notice.textContent = error.message; });
  }
  app.querySelectorAll('[data-mode]').forEach(button => button.addEventListener('click', () => {
    setMode(button.dataset.mode);
    draftKey = null;
  }));
  form.addEventListener('input', () => { draftKey = null; });
  function show(data) {
    current = data;
    form.hidden = true;
    document.getElementById('comparisonResults').hidden = false;
    document.getElementById('comparisonQuestion').textContent = data.prompt;
    data.responses.forEach(r => {
      document.getElementById(`title${r.slot}`).textContent = r.model ? `${r.slot} · ${r.model.name} (${r.model.provider})` : `Modèle ${r.slot}`;
      document.getElementById(`response${r.slot}`).textContent = r.response_text || r.error_message || 'La réponse apparaîtra ici.';
      document.getElementById(`status${r.slot}`).textContent = `${labels[r.status] || r.status}${r.latency_ms ? ` · ${(r.latency_ms / 1000).toFixed(1)} s` : ''}`;
      document.getElementById(`cost${r.slot}`).textContent = r.model ? `${r.credits_charged} jeton${r.credits_charged === 1 ? '' : 's'} · coût fournisseur estimé ${r.provider_cost} USD${r.usage_reliable ? '' : ' (consommation non vérifiée)'}` : '';
    });
    voteForm.hidden = !data.can_vote;
    inputs.disabled = !terminal.has(data.status);
    document.getElementById('compareButton').disabled = true;
    document.getElementById('newComparison').disabled = !terminal.has(data.status);
    if (data.voted) {
      notice.textContent = 'Votre évaluation a été enregistrée. Les modèles sont révélés.';
      if (data.reward) notice.textContent += ` Bonus : ${data.reward.credits} jetons · ${data.reward.status === 'credited' ? 'crédité' : data.reward.status === 'pending' ? 'en attente de validation humaine' : data.reward.status === 'approved' ? 'validé' : 'non éligible'}.`;
    } else if (data.status === 'failed') notice.textContent = 'Comparaison incomplète. Les appels échoués sont remboursés ; aucun vote n’est possible.';
    else if (data.status === 'cancelled') notice.textContent = 'Comparaison annulée. La réservation inutilisée est remboursée.';
    else notice.textContent = terminal.has(data.status) ? 'Les deux réponses sont prêtes. À vous de choisir.' : 'Comparaison en cours. Vous pouvez revenir sur cette page sans nouveau débit.';
  }
  async function poll(url, version) {
    try {
      const data = await api(url);
      if (version !== pollVersion) return;
      show(data);
      if (!terminal.has(data.status)) timer = setTimeout(() => poll(url, version), 1500);
      else history();
    } catch (error) {
      if (version !== pollVersion) return;
      notice.textContent = `${error.message} Nouvelle vérification dans quelques secondes.`;
      timer = setTimeout(() => poll(url, version), 5000);
    }
  }
  function follow(data) {
    clearTimeout(timer); pollVersion += 1;
    setMode(data.selection_mode);
    voteForm.reset(); show(data);
    const url = new URL(location.href); url.searchParams.set('session', data.id); window.history.replaceState(null, '', url);
    if (!terminal.has(data.status)) poll(data.state_url, pollVersion);
  }
  form.addEventListener('submit', async event => {
    event.preventDefault();
    if (inputs.disabled || current) return;
    const values = new FormData(form);
    draftKey ||= crypto.randomUUID();
    const payload = {request_key: draftKey, prompt: values.get('prompt'), category: values.get('category'), selection_mode: mode,
      analysis_consent: values.get('analysis_consent') === 'on', model_ids: mode === 'manual' ? selectors.map(s => Number(s.value)) : []};
    inputs.disabled = true; notice.textContent = 'Réservation des jetons et préparation…';
    // Persist the exact retry payload before dispatch. A network failure/reload
    // reuses its key; GET requests never start a generation.
    try { sessionStorage.setItem(draftStorageKey, JSON.stringify({...payload, saved_at: Date.now()})); } catch { /* The server still enforces idempotence. */ }
    try {
      const data = await api(app.dataset.sessionsUrl, {method: 'POST', body: JSON.stringify(payload)});
      try { sessionStorage.removeItem(draftStorageKey); } catch { /* Storage may be disabled. */ }
      follow(data); history();
    } catch (error) { inputs.disabled = false; notice.textContent = error.message; }
  });
  voteForm.addEventListener('submit', async event => {
    event.preventDefault();
    if (!current) return;
    const values = new FormData(voteForm), payload = Object.fromEntries(values);
    ['accuracy_score', 'relevance_score', 'clarity_score'].forEach(k => { payload[k] = payload[k] ? Number(payload[k]) : null; });
    const field = voteForm.querySelector('fieldset'); field.disabled = true;
    try { show(await api(current.vote_url, {method: 'POST', body: JSON.stringify(payload)})); }
    catch (error) { notice.textContent = error.message; }
    finally { field.disabled = false; }
  });
  document.getElementById('newComparison').addEventListener('click', () => {
    clearTimeout(timer); pollVersion += 1; current = null; draftKey = null; inputs.disabled = false;
    form.hidden = false;
    document.getElementById('compareButton').disabled = false;
    document.getElementById('comparisonResults').hidden = true;
    const url = new URL(location.href); url.searchParams.delete('session'); window.history.replaceState(null, '', url);
    notice.textContent = ''; form.querySelector('textarea').focus();
  });
  async function history() {
    const list = document.getElementById('comparisonHistory');
    try {
      const data = await api(app.dataset.sessionsUrl); list.replaceChildren();
      data.comparisons.forEach(s => {
        const item = document.createElement('li'), open = document.createElement('button'), remove = document.createElement('button');
        const url = `${app.dataset.sessionsUrl}${s.id}/`;
        open.type = remove.type = 'button'; open.textContent = `#${s.id} · ${labels[s.status]} · ${new Date(s.created_at).toLocaleString('fr')}`;
        open.addEventListener('click', () => api(url).then(follow).catch(e => { notice.textContent = e.message; }));
        remove.textContent = 'Supprimer le contenu';
        remove.addEventListener('click', async () => {
          if (!window.confirm('Supprimer la question, les réponses et la justification, et retirer votre consentement aux statistiques ?')) return;
          try {
            await api(url, {method: 'DELETE'});
            if (current?.id === s.id) {
              document.getElementById('newComparison').disabled = false;
              document.getElementById('newComparison').click();
            }
            await history();
          }
          catch (error) { notice.textContent = error.message; }
        });
        item.append(open, remove); list.append(item);
      });
    } catch (error) { notice.textContent = error.message; }
  }
  async function start() {
    const id = new URLSearchParams(location.search).get('session');
    if (id && /^\d+$/.test(id)) {
      const data = await api(`${app.dataset.sessionsUrl}${id}/`);
      if (data.selection_mode === 'manual') await loadModels();
      follow(data); await history(); return;
    }
    await loadModels(); await history();
    let stored = null;
    try { stored = sessionStorage.getItem(draftStorageKey); } catch { /* Storage may be disabled. */ }
    if (stored) {
      // Restore for an explicit retry, never silently create on page load.
      let p;
      try { p = JSON.parse(stored); } catch { return; }
      if (Date.now() - (p.saved_at || 0) > 86400000) { sessionStorage.removeItem(draftStorageKey); return; }
      draftKey = p.request_key;
      form.elements.prompt.value = p.prompt; form.elements.category.value = p.category;
      form.elements.analysis_consent.checked = p.analysis_consent;
      const button = app.querySelector(`[data-mode="${p.selection_mode === 'automatic' ? 'automatic' : 'manual'}"]`);
      if (button) button.click();
      draftKey = p.request_key;
      if (Array.isArray(p.model_ids)) p.model_ids.forEach((v, i) => { if (selectors[i]) selectors[i].value = String(v); });
      distinct(); notice.textContent = 'Demande précédente retrouvée. Cliquez sur Comparer pour vérifier ou réessayer avec le même identifiant.';
    }
  }
  start().catch(error => { notice.textContent = error.message; });
})();
