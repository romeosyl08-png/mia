(() => {
  const configElement = document.getElementById('creation-config');
  if (!configElement) return;
  const config = JSON.parse(configElement.textContent);
  const byId = id => document.getElementById(id);
  const csrf = document.querySelector('[name=csrfmiddlewaretoken]').value;
  const labels = {image: 'Image', video: 'Vidéo', audio: 'Son'};
  byId('creationBalance').textContent = Number(config.balance).toLocaleString('fr-FR');

  function status(element, message, error = false) {
    element.textContent = message;
    element.dataset.error = String(error);
  }
  async function post(url, payload) {
    const response = await fetch(url, {method: 'POST', headers: {
      'Content-Type': 'application/json', 'X-CSRFToken': csrf,
    }, body: JSON.stringify(payload)});
    const data = await response.json().catch(() => ({}));
    if (Number.isFinite(data.remaining_balance)) byId('creationBalance').textContent = data.remaining_balance.toLocaleString('fr-FR');
    if (!response.ok) throw new Error(data.error?.message || 'Cette action a échoué. Réessaie dans un instant.');
    return data;
  }
  function preview(item) {
    const media = document.createElement(item.kind === 'video' ? 'video' : item.kind === 'audio' ? 'audio' : 'img');
    media.src = item.media_url;
    if (item.kind === 'image') {
      media.alt = item.prompt || item.name || 'Image générée';
      media.loading = 'lazy';
    } else {
      media.controls = true;
      media.preload = 'metadata';
      media.setAttribute('aria-label', item.prompt || item.name || labels[item.kind]);
    }
    return media;
  }
  function button(text, callback) {
    const node = document.createElement('button');
    node.type = 'button'; node.className = 'creation-button'; node.textContent = text;
    node.addEventListener('click', callback);
    return node;
  }
  let studioItem = null;
  const studioDialog = byId('creationStudioDialog');
  byId('closeCreationStudio').addEventListener('click', () => studioDialog.close());
  byId('creationStudioForm').addEventListener('submit', async event => {
    event.preventDefault();
    if (!studioItem || byId('creationStudioSubmit').disabled) return;
    byId('creationStudioSubmit').disabled = true;
    status(byId('creationStudioStatus'), 'Ajout au projet…');
    try {
      const project = byId('creationProject').value;
      const data = await post(config.urls['use-in-studio'], {id: studioItem.id, source: studioItem.source, project_id: project ? Number(project) : null});
      window.location.assign(data.url);
    } catch (error) {
      status(byId('creationStudioStatus'), error.message, true);
      byId('creationStudioSubmit').disabled = false;
    }
  });
  function actions(item, target) {
    const download = document.createElement('a');
    download.className = 'creation-button'; download.textContent = 'Télécharger';
    download.href = item.download_url || item.media_url + '?download=1'; download.setAttribute('download', '');
    const notice = document.createElement('p'); notice.className = 'creation-status'; notice.setAttribute('role', 'status');
    const chat = button('Utiliser dans une conversation', async () => {
      chat.disabled = true; status(notice, 'Préparation de la conversation…');
      try {
        const data = await post(config.urls['use-in-chat'], {id: item.id, source: item.source});
        window.location.assign(data.url);
      } catch (error) { status(notice, error.message, true); chat.disabled = false; }
    });
    const studio = button('Utiliser dans le Studio', () => {
      studioItem = item;
      status(byId('creationStudioStatus'), '');
      byId('creationStudioSubmit').disabled = false;
      studioDialog.showModal();
    });
    const reuse = button('Réutiliser la description', () => {
      try {
        sessionStorage.setItem(`ai2ai-creation-draft:${config.userId}`, JSON.stringify({prompt: item.prompt, kind: item.kind}));
        window.location.assign(config.urls.index + '?draft=1');
      } catch { status(notice, 'Le navigateur ne permet pas de transférer cette description.', true); }
    });
    target.append(download, chat, studio, reuse, notice);
  }

  if (config.tab === 'generate') {
    const form = byId('creationForm'), prompt = byId('creationPrompt'), submit = byId('creationSubmit');
    const radios = [...form.querySelectorAll('[name=kind]')];
    radios.find(input => input.value === config.kind).checked = true;
    let busy = false;
    const kind = () => radios.find(input => input.checked).value;
    const promptLimits = {image: 2000, video: 4000, audio: 5000};
    function updateKind() {
      const selected = kind();
      form.querySelectorAll('[data-kind-options]').forEach(fieldset => {
        fieldset.hidden = fieldset.dataset.kindOptions !== selected;
        fieldset.disabled = busy || fieldset.hidden;
      });
      // Keep the complete draft visible when changing to a type with a shorter limit.
      prompt.maxLength = promptLimits[selected];
      prompt.setCustomValidity(prompt.value.length > prompt.maxLength ? `Raccourcis ta description à ${prompt.maxLength} caractères.` : '');
      submit.textContent = busy ? 'Création en cours…' : {image: 'Générer une image ↗', video: 'Générer une vidéo ↗', audio: 'Générer un son ↗'}[selected];
      submit.disabled = busy || !config.available[selected];
      byId('creationAvailability').textContent = config.available[selected]
        ? 'Utilise les jetons de ton compte 2AI.'
        : 'Ce type de génération n’est pas encore configuré. Tu peux retrouver tes médias dans la médiathèque.';
    }
    radios.forEach(input => input.addEventListener('change', updateKind));
    prompt.addEventListener('input', updateKind);
    if (config.draft) {
      try {
        const key = `ai2ai-creation-draft:${config.userId}`;
        const draft = JSON.parse(sessionStorage.getItem(key) || 'null');
        if (typeof draft?.prompt === 'string') {
          prompt.value = draft.prompt.slice(0, 5000);
          const radio = radios.find(input => input.value === draft.kind);
          if (radio) radio.checked = true;
        }
        sessionStorage.removeItem(key);
      } catch { status(byId('creationStatus'), 'La description n’a pas pu être récupérée. Tu peux la saisir ici.', true); }
    }
    updateKind();
    form.addEventListener('submit', async event => {
      event.preventDefault();
      if (busy || !config.available[kind()] || !prompt.value.trim()) return;
      const selected = kind(), text = prompt.value.trim();
      let options;
      if (selected === 'image') options = {image_size: byId('creationImageSize').value};
      else if (selected === 'video') options = {
        aspect_ratio: byId('creationVideoAspect').value, resolution: byId('creationVideoResolution').value,
        duration_seconds: Number(byId('creationVideoDuration').value), generate_audio: byId('creationVideoAudio').checked,
      };
      else options = {style: byId('creationAudioStyle').value, instrumental: byId('creationInstrumental').checked};
      busy = true; radios.forEach(input => { input.disabled = true; }); prompt.readOnly = true; updateKind();
      document.querySelector('.creation-preview').setAttribute('aria-busy', 'true');
      status(byId('creationStatus'), 'Création en cours. Cela peut prendre quelques minutes. Garde cette page ouverte.');
      try {
        const data = await post(config.urls.generate, {content: text, kind: selected,
          generation_options: options, conversation_id: config.conversationId});
        const item = {id: data.media_id, source: 'generated', kind: data.media_kind, prompt: text, media_url: data.media_url};
        byId('creationPreview').replaceChildren(preview(item));
        byId('creationResultActions').replaceChildren();
        actions(item, byId('creationResultActions'));
        byId('creationResultActions').hidden = false;
        byId('creationResultBadge').hidden = false;
        status(byId('creationStatus'), 'Création enregistrée dans ta médiathèque.');
      } catch (error) {
        status(byId('creationStatus'), error.message, true);
      } finally {
        busy = false; radios.forEach(input => { input.disabled = false; }); prompt.readOnly = false; updateKind();
        document.querySelector('.creation-preview').setAttribute('aria-busy', 'false');
      }
    });
    // Avoid leaving a paid generation without its result on an accidental navigation.
    window.addEventListener('beforeunload', event => {
      if (busy) { event.preventDefault(); event.returnValue = ''; }
    });
  } else {
    const grid = byId('libraryGrid'), more = byId('libraryMore');
    let offset = 0, controller = null, revision = 0;
    function card(item) {
      const article = document.createElement('article'); article.className = 'creation-media';
      const frame = document.createElement('div'); frame.className = 'creation-media-preview'; frame.append(preview(item));
      const body = document.createElement('div'); body.className = 'creation-media-body';
      const meta = document.createElement('div'); meta.className = 'creation-media-meta';
      const type = document.createElement('span'); type.textContent = labels[item.kind];
      const date = document.createElement('time'); date.dateTime = item.created_at; date.textContent = new Date(item.created_at).toLocaleDateString('fr-FR'); meta.append(type, date);
      const title = document.createElement('h2'); title.textContent = item.prompt || item.name; title.title = title.textContent;
      const origin = document.createElement(item.origin_url ? 'a' : 'span'); origin.className = 'creation-media-origin'; origin.textContent = item.origin_label;
      if (item.origin_url) origin.href = item.origin_url;
      const controls = document.createElement('div'); controls.className = 'creation-media-actions'; actions(item, controls);
      body.append(meta, title, origin, controls); article.append(frame, body); return article;
    }
    async function load(reset = false) {
      controller?.abort(); controller = new AbortController();
      const current = ++revision;
      if (reset) { offset = 0; grid.replaceChildren(); }
      more.disabled = true; grid.setAttribute('aria-busy', 'true'); byId('libraryEmpty').hidden = true;
      status(byId('libraryStatus'), 'Chargement des médias…');
      const params = new URLSearchParams({kind: byId('libraryKind').value, q: byId('librarySearch').value.trim(), offset: String(offset)});
      try {
        const response = await fetch(config.urls['media-list'] + '?' + params, {signal: controller.signal});
        if (!response.ok) throw new Error('Impossible de charger les médias. Relance la recherche.');
        const data = await response.json();
        if (current !== revision) return;
        data.items.forEach(item => grid.append(card(item)));
        offset = data.next_offset; more.hidden = offset === null;
        const empty = grid.children.length === 0;
        byId('libraryEmpty').hidden = !empty;
        const filtered = Boolean(params.get('kind') || params.get('q'));
        byId('libraryEmptyTitle').textContent = filtered ? 'Aucun média ne correspond' : 'Ta médiathèque commence ici';
        byId('libraryEmptyCopy').textContent = filtered ? 'Essaie un autre type ou une autre recherche.' : 'Crée ton premier média. Tu pourras le retrouver et le réutiliser à tout moment.';
        status(byId('libraryStatus'), empty ? '' : `${grid.children.length} média${grid.children.length > 1 ? 's' : ''} affiché${grid.children.length > 1 ? 's' : ''}`);
      } catch (error) {
        if (error.name !== 'AbortError' && current === revision) {
          status(byId('libraryStatus'), error.message, true);
          // Preserve the cursor so a failed next page can be retried.
          more.hidden = offset === null || (reset && !grid.children.length);
        }
      } finally {
        if (current === revision) { more.disabled = false; grid.setAttribute('aria-busy', 'false'); }
      }
    }
    byId('libraryFilters').addEventListener('submit', event => { event.preventDefault(); load(true); });
    byId('libraryKind').addEventListener('change', () => load(true));
    more.addEventListener('click', () => load());
    load(true);
  }
})();
