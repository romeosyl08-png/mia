/* Studio: local draft, immutable snapshots and explicitly confirmed jobs. */
(() => {
  'use strict';
  const root = document.getElementById('studio');
  if (!root) return;
  const $ = id => document.getElementById(`studio-${id}`);
  const copy = value => structuredClone(value);
  let state = JSON.parse($('state').textContent);
  let scene = copy(state.scene), version = state.version_id, dirty = false, plan = null;
  let source = null, painted = false, librarySignature = '', jobSignature = '', exportSignature = '';
  const roles = {voice:'Voix',music:'Musique',ambience:'Ambiance',effect:'Effet'};
  const titles = {generate_image:'Créer une image',generate_video:'Créer une vidéo',generate_audio:'Créer un son',
    edit_image:'Modifier une image',recolor_image:'Recolorer une zone',update_track:'Régler une piste',render:'Exporter le montage'};
  const statuses = {queued:'En attente',running:'En cours',completed:'Terminée',failed:'Échec',cancelled:'Annulée'};
  const csrf = () => document.querySelector('[name=csrfmiddlewaretoken]').value;
  const notice = (message, error = false) => {
    $('notice').hidden = !message;
    $('notice').textContent = message;
    $('notice').dataset.error = String(error);
    $('status').textContent = message || 'Projet chargé';
    window.clearTimeout(notice.timer);
    if (message && !error) notice.timer = window.setTimeout(() => { $('notice').hidden = true; }, 6000);
  };
  async function api(path, data, isFile = false) {
    const response = await fetch(root.dataset.base + path, {
      method:data === undefined ? 'GET' : 'POST', credentials:'same-origin', cache:'no-store',
      headers:data === undefined ? {} : {'X-CSRFToken':csrf(), ...(isFile ? {} : {'Content-Type':'application/json'})},
      body:data === undefined ? undefined : isFile ? data : JSON.stringify(data)
    });
    if (!response.headers.get('Content-Type')?.includes('application/json')) throw new Error('Ta session a expiré ou le serveur est indisponible. Reconnecte-toi puis recharge le projet.');
    const value = await response.json();
    if (!response.ok) throw new Error(value.error || 'Cette opération a échoué.');
    return value;
  }
  function bind(id, event, action) {
    $(id).addEventListener(event, async e => {
      e.preventDefault();
      const buttons = [...$(id).querySelectorAll('button')];
      if ($(id).tagName === 'BUTTON') buttons.push($(id));
      buttons.forEach(button => button.disabled = true);
      try { await action(e); } catch (error) { notice(error.message || 'Opération impossible.', true); }
      finally { buttons.forEach(button => button.disabled = false); }
    });
  }
  function node(tag, text, className) {
    const item = document.createElement(tag);
    if (text !== undefined) item.textContent = text;
    if (className) item.className = className;
    return item;
  }
  function button(text, action, label) {
    const item = node('button', text);
    item.type = 'button';
    if (label) item.setAttribute('aria-label',label);
    item.addEventListener('click', async () => {
      item.disabled = true;
      try { await action(); } catch (error) { notice(error.message, true); }
      finally { item.disabled = false; }
    });
    return item;
  }
  const editor = window.AI2AIStudioEditor.create({root, getScene: () => scene, getAssets: () => state.assets});
  function change() {
    dirty = true;
    $('dirty').textContent = 'Modifications à enregistrer';
    if (plan) {
      plan = null;
      $('plan').hidden = true;
    }
    editor.refresh();
  }
  function preview(item) {
    const tag = item.kind === 'image' ? 'img' : item.kind;
    const element = node(tag);
    element.src = item.url;
    if (tag === 'img') { element.alt = item.name; element.loading = 'lazy'; }
    else { element.controls = true; element.preload = 'metadata'; }
    return element;
  }
  function track(item) {
    const value = {id:`track_${crypto.randomUUID().replaceAll('-','').slice(0,12)}`,asset_id:item.id,label:item.name,locked:false};
    if (item.kind === 'audio') scene.audio.push({...value,role:'music',volume:1,start:0});
    else scene.visuals.push({...value,duration:Math.min(30,Math.max(1,item.metadata.duration || 4))});
    change(); renderTracks(); editor.selectTrack(value.id);
  }
  function filterLibrary() {
    const query = $('library-search').value.trim().toLocaleLowerCase();
    const cards = [...$('library').querySelectorAll('[data-asset-id]')];
    cards.forEach(card => { card.hidden = !card.dataset.assetName.toLocaleLowerCase().includes(query); });
    const count = cards.filter(card => !card.hidden).length;
    $('asset-count').textContent = `${count} média${count > 1 ? 's' : ''}`;
  }
  $('library-search').addEventListener('input', filterLibrary);
  function renderLibrary() {
    const signature = JSON.stringify([state.assets.map(a => a.id),scene.references]);
    if (signature === librarySignature) return;
    librarySignature = signature;
    $('library').replaceChildren();
    const assets = state.assets.filter(item => item.role !== 'mask' && item.role !== 'export');
    if (!assets.length) $('library').append(node('p','Importe une référence ou crée ton premier média.','studio-hint'));
    for (const item of assets) {
      const card = node('article',undefined,'studio-asset');
      card.dataset.assetId = item.id; card.dataset.assetName = item.name;
      const view = button('', () => editor.selectAsset(item.id), `Prévisualiser ${item.name}`);
      view.className = 'studio-asset-preview';
      const thumbnail = item.kind === 'audio' ? node('span', '♫', 'studio-asset-symbol') : preview(item);
      if (item.kind === 'video') { thumbnail.controls = false; thumbnail.muted = true; }
      const information = node('span', undefined, 'studio-asset-copy');
      const duration = Number(item.metadata.duration);
      information.append(node('p', item.name), node('span', `${{image:'Image',video:'Vidéo',audio:'Audio'}[item.kind]}${duration ? ` · ${duration.toFixed(1)} s` : ''}`));
      view.append(thumbnail, information); card.append(view);
      const controls = node('div',undefined,'studio-actions');
      controls.append(button(item.kind === 'audio' ? '＋ Piste' : '＋ Plan',() => track(item),item.kind === 'audio' ? 'Ajouter une piste' : 'Ajouter un plan'));
      if (item.kind === 'image') {
        controls.append(button('Masque',() => openMask(item),'Peindre un masque'));
        const label = node('label','Réf.','studio-check'); label.title = 'Utiliser cette image comme référence IA';
        const check = node('input'); check.type = 'checkbox'; check.checked = scene.references.includes(item.id);
        check.addEventListener('change',() => {
          if (check.checked && scene.references.length >= 8) { check.checked = false; notice('Huit références maximum.',true); return; }
          scene.references = check.checked ? [...scene.references,item.id] : scene.references.filter(id => id !== item.id);
          change();
        });
        label.prepend(check); controls.append(label);
      }
      card.append(controls); $('library').append(card);
    }
    const selection = $('first-frame').value;
    $('first-frame').replaceChildren(new Option('Sans image imposée',''));
    state.assets.filter(item => item.kind === 'image' && item.role !== 'mask').forEach(item => $('first-frame').add(new Option(item.name,item.id)));
    $('first-frame').value = selection;
    filterLibrary(); editor.refresh();
  }
  function field(row, key, title, min, max, step, options) {
    const label = node('label',title);
    const input = node(options ? 'select' : 'input');
    if (options) Object.entries(options).forEach(([value,text]) => input.add(new Option(text,value)));
    else { input.type = 'number'; input.min = min; input.max = max; input.step = step; }
    input.value = row[key]; input.disabled = row.locked;
    input.addEventListener('change',() => { row[key] = options ? input.value : Number(input.value); change(); });
    label.append(input); return label;
  }
  function renderTracks() {
    for (const key of ['visuals','audio']) {
      const area = $(key); area.replaceChildren();
      if (!scene[key].length) area.append(node('p',key === 'audio' ? 'Ajoute une voix, une musique ou une ambiance depuis la médiathèque.' : 'Ajoute des images ou des vidéos depuis la médiathèque.','studio-hint'));
      scene[key].forEach((row,index) => {
        const line = node('div',undefined,`studio-track ${key === 'audio' ? 'studio-track-audio' : ''}`);
        line.dataset.trackId = row.id;
        const label = node('label','Nom');
        const name = node('input'); name.type = 'text'; name.value = row.label; name.maxLength = 120; name.disabled = row.locked;
        name.addEventListener('change',() => { row.label = name.value; change(); }); label.append(name); line.append(label);
        if (key === 'audio') line.append(field(row,'role','Type',null,null,null,roles),field(row,'volume','Volume',0,2,.05),field(row,'start','Début (s)',0,179,.1));
        else line.append(field(row,'duration','Durée (s)',.2,30,.1));
        const controls = node('div',undefined,'studio-track-controls');
        for (const [delta,text,title] of [[-1,'↑','Déplacer avant'],[1,'↓','Déplacer après']]) {
          const move = button(text,() => {
            [scene[key][index],scene[key][index+delta]] = [scene[key][index+delta],scene[key][index]];
            change(); renderTracks();
          },title);
          move.disabled = row.locked || !scene[key][index+delta] || scene[key][index+delta].locked;
          controls.append(move);
        }
        controls.append(button(row.locked ? 'Déverrouiller' : 'Verrouiller',() => { row.locked = !row.locked; change(); renderTracks(); }));
        const remove = button('Retirer',() => { scene[key].splice(index,1); change(); renderTracks(); },'Retirer du montage sans supprimer le média');
        remove.disabled = row.locked; controls.append(remove); line.append(controls); area.append(line);
      });
    }
    editor.refresh();
  }
  function renderJobs() {
    const signature = JSON.stringify(state.jobs);
    if (signature === jobSignature) return;
    jobSignature = signature; $('jobs').replaceChildren();
    if (!state.jobs.length) $('jobs').append(node('p','Les générations et les exports apparaîtront ici.','studio-hint'));
    for (const job of state.jobs) {
      const area = node('article',undefined,'studio-job');
      area.append(node('strong',`#${job.id} · ${statuses[job.status] || job.status}`),node('p',job.progress));
      if (job.status === 'running') area.append(node('progress'));
      area.append(node('p',['queued','running'].includes(job.status) ? `${job.reserved_credits} jetons réservés` : `${job.spent_credits} jetons utilisés`));
      if (job.error) area.append(node('p',job.error));
      if (job.warning) area.append(node('p',job.warning));
      if (job.status === 'queued') area.append(button('Annuler',async () => absorb(await api(`jobs/${job.id}/cancel/`,{}))));
      if (job.result_version_id && job.result_version_id !== version) area.append(node('p','Résultat conservé dans l’historique des versions.'));
      $('jobs').append(area);
    }
  }
  function renderExports() {
    const signature = JSON.stringify(scene.exports);
    if (signature === exportSignature) return;
    exportSignature = signature; $('exports').replaceChildren();
    if (!scene.exports.length) $('exports').append(node('p','Prépare un export du montage pour obtenir un fichier à partager.','studio-hint'));
    for (const id of scene.exports) {
      const item = state.assets.find(item => item.id === id); if (!item) continue;
      const card = node('article',undefined,'studio-asset');
      const download = node('a','Télécharger ↓','studio-button'); download.href = item.url + '?download=1';
      card.append(preview(item),node('p',item.name),button('Voir dans le moniteur',() => editor.selectAsset(item.id)),download); $('exports').append(card);
    }
  }
  function renderScene() {
    $('brief').value = scene.brief;
    $('format').value = `${scene.settings.width}x${scene.settings.height}`;
    $('fps').value = scene.settings.fps; $('duration').value = scene.settings.duration;
    $('dirty').textContent = dirty ? 'Modifications à enregistrer' : 'Version enregistrée';
    renderTracks(); renderLibrary(); renderExports();
    editor.refresh();
  }
  function absorb(value, force = false) {
    const changed = value.version_id !== version;
    state = value;
    $('balance').textContent = `${value.balance.toLocaleString('fr-FR')} jetons`;
    if (force || (changed && !dirty)) {
      scene = copy(value.scene); version = value.version_id; dirty = false;
      plan = null; $('plan').hidden = true; renderScene();
    } else if (changed && dirty) notice('Une nouvelle version est disponible. Enregistre ou recharge avant de lancer une autre opération.',true);
    renderLibrary(); renderJobs(); renderExports();
    const selected = $('version').value;
    $('version').replaceChildren();
    value.versions.forEach(row => $('version').add(new Option(`v${row.number} · ${row.label}${row.id === version ? ' · actuelle' : ''}`,row.id)));
    $('version').value = !force && !changed && value.versions.some(row => String(row.id) === selected) ? selected : String(version);
    $('render-video').disabled = $('render-audio').disabled = !value.capabilities.render;
  }
  async function save() {
    if (!dirty) return;
    const value = await api('scene/',{expected_version:version,scene});
    dirty = false; absorb(value,true); notice('Montage enregistré dans une nouvelle version.');
  }
  async function prepare(operations, message = '') {
    await save();
    plan = null; $('plan').hidden = true;
    const value = await api('quote/',{operations});
    if (value.expected_version !== version) throw new Error('Le projet a changé. Recharge puis prépare à nouveau ton plan.');
    plan = value;
    $('plan-message').textContent = message;
    $('plan-steps').replaceChildren();
    value.operations.forEach(row => {
      let text = titles[row.operation] + (row.prompt ? ` : ${row.prompt}` : '');
      if (row.source_id) text += ` · image #${row.source_id}`;
      if (row.mask_id) text += ` · masque #${row.mask_id}`;
      if (row.color) text += ` · ${row.color}`;
      if (row.kind) text += ` · ${row.kind}`;
      if (row.changes) text += ` · ${JSON.stringify(row.changes)}`;
      if (row.reference_ids?.length) text += ` · références ${row.reference_ids.join(', ')}`;
      if (row.first_frame_id) text += ` · première image #${row.first_frame_id}`;
      if (row.options) text += ` · ${JSON.stringify(row.options)}`;
      if (row.model_id) text += ` · ${row.model_id}`;
      text += ` (${row.estimated_credits} jetons estimés)`;
      $('plan-steps').append(node('li',text));
    });
    $('estimate').textContent = `Estimation : ${value.estimated_credits.toLocaleString('fr-FR')} jetons. Le coût final est limité au plafond choisi.`;
    editor.activateTab('inspector', 'studio-ai-panel');
    $('plan').hidden = false; $('plan').scrollIntoView({behavior:'smooth',block:'nearest'});
  }
  bind('upload','submit',async () => {
    const form = new FormData($('upload')); form.set('role','source');
    absorb((await api('upload/',form,true)).state);
    $('upload').reset(); notice('Média importé. Tu peux l’ajouter au montage.');
  });
  bind('save','click',save);
  bind('reload','click',async () => { absorb(await api('state/'),true); notice('Version actuelle chargée.'); });
  bind('restore','click',async () => {
    const value = await api('restore/',{version_id:Number($('version').value),expected_version:version});
    dirty = false; absorb(value,true); notice('Version restaurée. Les versions précédentes sont conservées.');
  });
  bind('render-video','click',() => prepare([{operation:'render',kind:'video'}]));
  bind('render-audio','click',() => prepare([{operation:'render',kind:'audio'}]));
  bind('launch','submit',async () => {
    if (!plan) throw new Error('Prépare un plan avant de le lancer.');
    const value = await api('jobs/',{operations:plan.operations,expected_version:plan.expected_version,credit_limit:Number($('budget').value)});
    plan = null; $('plan').hidden = true; absorb(value.state);
    editor.activateTab('inspector', 'studio-project-panel');
    notice('Tâche ajoutée. Tu peux continuer à travailler ; son résultat apparaîtra dans l’historique.');
  });
  bind('director','submit',async () => {
    await save();
    const value = await api('director/',{request:$('request').value,credit_limit:Number($('director-budget').value)});
    $('balance').textContent = `${value.balance.toLocaleString('fr-FR')} jetons`;
    notice(`Plan préparé : ${value.charged_credits} jetons utilisés. ${value.message}`);
    if (!value.operations.length) { plan = null; $('plan').hidden = true; return; }
    if (value.expected_version !== version) throw new Error('Le projet a changé pendant la préparation. Recharge avant de relancer le directeur.');
    await prepare(value.operations,`${value.message} Préparation du plan : ${value.charged_credits} jetons.`);
  });
  bind('generate','submit',() => {
    const kind = $('kind').value, options = {};
    const row = {operation:`generate_${kind}`,prompt:$('prompt').value,options};
    if (kind === 'image') options.image_size = $('image-size').value;
    if (kind === 'video') {
      Object.assign(options,{duration_seconds:Number($('video-duration').value),resolution:$('video-resolution').value,aspect_ratio:$('video-aspect').value,generate_audio:false});
      if ($('first-frame').value) row.first_frame_id = Number($('first-frame').value);
    }
    if (kind === 'audio') { row.role = $('audio-role').value; row.reference_ids = []; Object.assign(options,{style:$('audio-style').value,instrumental:$('instrumental').checked}); }
    return prepare([row]);
  });
  $('kind').addEventListener('change',() => root.querySelectorAll('[data-options]').forEach(element => element.hidden = element.dataset.options !== $('kind').value));
  $('audio-role').addEventListener('change',() => { $('instrumental').checked = $('audio-role').value !== 'voice'; });
  $('brief').addEventListener('input',() => { scene.brief = $('brief').value; change(); });
  $('format').addEventListener('change',() => { [scene.settings.width,scene.settings.height] = $('format').value.split('x').map(Number); change(); });
  $('fps').addEventListener('change',() => { scene.settings.fps = Number($('fps').value); change(); });
  $('duration').addEventListener('change',() => { scene.settings.duration = Number($('duration').value); change(); });
  async function openMask(item) {
    source = item; painted = false;
    const image = $('mask-image'); image.src = item.url; await image.decode();
    const canvas = $('mask-canvas'); canvas.width = image.naturalWidth; canvas.height = image.naturalHeight;
    $('mask-dialog').showModal();
  }
  let drawing = false, last = null;
  const canvas = $('mask-canvas');
  function paint(event) {
    if (!drawing) return;
    const rect = canvas.getBoundingClientRect();
    const point = {x:(event.clientX-rect.left)*canvas.width/rect.width,y:(event.clientY-rect.top)*canvas.height/rect.height};
    const ctx = canvas.getContext('2d'); ctx.globalCompositeOperation = $('eraser').checked ? 'destination-out' : 'source-over';
    ctx.lineWidth = Number($('brush').value); ctx.lineCap = ctx.lineJoin = 'round'; ctx.strokeStyle = '#fff'; ctx.fillStyle = '#fff';
    if (last) { ctx.beginPath(); ctx.moveTo(last.x,last.y); ctx.lineTo(point.x,point.y); ctx.stroke(); }
    else { ctx.beginPath(); ctx.arc(point.x,point.y,ctx.lineWidth/2,0,Math.PI*2); ctx.fill(); }
    last = point; painted = true;
  }
  canvas.addEventListener('pointerdown',event => { drawing = true; last = null; canvas.setPointerCapture(event.pointerId); paint(event); });
  canvas.addEventListener('pointermove',paint);
  for (const type of ['pointerup','pointercancel','lostpointercapture']) canvas.addEventListener(type,() => { drawing = false; last = null; });
  bind('mask-clear','click',() => { canvas.getContext('2d').clearRect(0,0,canvas.width,canvas.height); painted = false; });
  async function maskOperation(edit = false) {
    if (!painted || !source) throw new Error('Peins une zone sur l’image avant de préparer la modification.');
    if (edit && !$('edit-prompt').value.trim()) throw new Error('Décris la modification à réaliser.');
    const exported = document.createElement('canvas'); exported.width = canvas.width; exported.height = canvas.height;
    const ctx = exported.getContext('2d'); ctx.fillStyle = '#000'; ctx.fillRect(0,0,exported.width,exported.height); ctx.drawImage(canvas,0,0);
    const blob = await new Promise(resolve => exported.toBlob(resolve,'image/png'));
    if (!blob) throw new Error('Le masque n’a pas pu être enregistré.');
    const form = new FormData(); form.set('file',blob,`mask-${source.id}.png`); form.set('role','mask');
    const value = await api('upload/',form,true); absorb(value.state);
    const row = {operation:edit ? 'edit_image' : 'recolor_image',source_id:source.id,mask_id:value.asset_id};
    if (edit) row.prompt = $('edit-prompt').value; else row.color = $('color').value;
    await prepare([row]); $('mask-dialog').close();
  }
  bind('recolor','click',() => maskOperation());
  bind('edit','click',() => maskOperation(true));
  window.addEventListener('beforeunload',event => { if (dirty) { event.preventDefault(); event.returnValue = ''; } });
  renderScene(); absorb(state);
  async function poll() {
    try { if (!document.hidden) absorb(await api('state/')); }
    catch (error) { notice(error.message,true); }
    setTimeout(poll,state.jobs.some(job => ['queued','running'].includes(job.status)) ? 2500 : 10000);
  }
  setTimeout(poll,2500);
})();
