(function(){
  const workspaceRoot = document.querySelector('[data-workspace-id]');
  const workspaceId = workspaceRoot.dataset.workspaceId;
  const messagesDiv = document.getElementById('messages');
  const form = document.getElementById('agent-form');
  const textarea = document.getElementById('message-content');
  const submitBtn = document.getElementById('submit-btn');
  const stopBtn = document.getElementById('stop-btn');
  const csrfToken = form.querySelector('[name=csrfmiddlewaretoken]').value;
  const messageUrl = form.action;
  let currentController = null;

  // ---------- Markdown + coloration + bouton copier ----------
  function enhance(container){
    container.querySelectorAll('pre').forEach(pre => {
      if(pre.dataset.enhanced) return;
      pre.dataset.enhanced = '1';
      const code = pre.querySelector('code');
      if(code && window.hljs) hljs.highlightElement(code);
      const btn = document.createElement('button');
      btn.type = 'button';
      btn.className = 'code-copy-btn';
      btn.textContent = 'Copier';
      btn.addEventListener('click', async () => {
        try{
          await navigator.clipboard.writeText((code || pre).innerText);
          btn.textContent = 'Copié ✓';
        } catch(err){
          btn.textContent = 'Échec';
        }
        setTimeout(() => { btn.textContent = 'Copier'; }, 1500);
      });
      pre.appendChild(btn);
    });
  }

  function renderMarkdown(el, text){
    if(window.marked && window.DOMPurify){
      el.innerHTML = DOMPurify.sanitize(marked.parse(text));
      enhance(el);
    } else {
      el.textContent = text;
    }
  }

  // Messages déjà présents dans la page (rendus en texte brut par le serveur)
  document.querySelectorAll('.bara-msg.assistant .bara-msg-content').forEach(el => {
    renderMarkdown(el, el.textContent);
  });
  messagesDiv.scrollTop = messagesDiv.scrollHeight;

  function clearEmptyState(){
    const empty = messagesDiv.querySelector('.bara-empty-state');
    if(empty) empty.remove();
  }

  function appendMessage(role, content, isThinking){
    clearEmptyState();
    const div = document.createElement('div');
    div.className = 'bara-msg ' + role + (isThinking ? ' thinking' : '');
    const label = document.createElement('span');
    label.className = 'role-label';
    label.textContent = role === 'user' ? 'Toi' : (role === 'error' ? 'Erreur' : 'Assistant');
    const body = document.createElement('div');
    body.className = 'bara-msg-content';
    if(role === 'assistant' && !isThinking && content){
      renderMarkdown(body, content);
    } else {
      body.textContent = content;
    }
    div.appendChild(label);
    div.appendChild(body);
    messagesDiv.appendChild(div);
    messagesDiv.scrollTop = messagesDiv.scrollHeight;
    return div;
  }

  function addChartCard(chart){
    const card = AI2AICharts.createChart(chart);
    if(!card) return;
    clearEmptyState();
    messagesDiv.appendChild(card);
    messagesDiv.scrollTop = messagesDiv.scrollHeight;
  }

  // ---------- Saisie ----------
  textarea.addEventListener('input', () => {
    textarea.style.height = 'auto';
    textarea.style.height = Math.min(120, textarea.scrollHeight) + 'px';
  });
  textarea.addEventListener('keydown', (e) => {
    if(e.key === 'Enter' && !e.shiftKey){
      e.preventDefault();
      form.requestSubmit();
    }
  });

  function setStreaming(on){
    submitBtn.style.display = on ? 'none' : '';
    stopBtn.style.display = on ? '' : 'none';
  }

  stopBtn.addEventListener('click', () => {
    if(currentController) currentController.abort();
  });

  // ---------- Carte de confirmation (suppression demandée par l'agent) ----------
  function addConfirmCard(event){
    clearEmptyState();
    const card = document.createElement('div');
    card.className = 'bara-msg assistant';
    card.style.borderColor = 'var(--accent)';

    const label = document.createElement('span');
    label.className = 'role-label';
    label.textContent = 'Confirmation requise';
    const text = document.createElement('div');
    text.textContent = event.description + ' ?';
    text.style.cssText = 'white-space:pre-wrap;overflow-wrap:anywhere;max-height:240px;overflow:auto;';

    const actions = document.createElement('div');
    actions.style.cssText = 'display:flex;gap:8px;margin-top:10px;';
    const yes = document.createElement('button');
    yes.type = 'button';
    yes.textContent = 'Confirmer';
    yes.style.cssText = 'background:var(--danger);color:#fff;border:0;padding:7px 14px;border-radius:8px;cursor:pointer;font-size:12px;';
    const no = document.createElement('button');
    no.type = 'button';
    no.textContent = 'Refuser';
    no.style.cssText = 'background:none;border:1px solid var(--line);color:var(--ink);padding:7px 14px;border-radius:8px;cursor:pointer;font-size:12px;';
    actions.append(yes, no);
    card.append(label, text, actions);
    messagesDiv.appendChild(card);
    messagesDiv.scrollTop = messagesDiv.scrollHeight;

    async function resolveAction(decision){
      yes.disabled = no.disabled = true;
      try{
        const res = await fetch(`/bara/${workspaceId}/actions/${event.action_id}/${decision}/`, {
          method: 'POST',
          headers: { 'X-CSRFToken': csrfToken }
        });
        const data = await res.json().catch(() => ({}));
        if(!res.ok){
          const error = new Error(data.error || 'Échec de la confirmation.');
          error.status = res.status;
          throw error;
        }
        actions.remove();
        if(decision === 'confirm'){
          text.textContent = (event.mcp || event.plugin)
            ? event.description + ' : ' + JSON.stringify(data.result || {})
            : event.description + ' : fait.';
          if(event.file_id){
            const btn = document.querySelector(`.file-delete-btn[data-file-id="${event.file_id}"]`);
            if(btn) btn.closest('.file-item').remove();
          }
        } else {
          text.textContent = event.description + ' : refusé.';
        }
      } catch(err){
        if((decision === 'confirm' && event.plugin) || err.status === 409){
          actions.remove();
          text.textContent = `${event.description} : ${err.message} Vérifie le résultat avant toute nouvelle action.`;
        } else {
          yes.disabled = no.disabled = false;
          text.textContent = event.description + ' ? (échec, réessaie)';
        }
      }
    }
    yes.addEventListener('click', () => resolveAction('confirm'));
    no.addEventListener('click', () => resolveAction('reject'));
  }

  // ---------- Envoi + lecture du flux ----------
  form.addEventListener('submit', async function(e){
    e.preventDefault();
    const content = textarea.value.trim();
    if(!content || currentController) return;

    appendMessage('user', content);
    textarea.value = '';
    textarea.style.height = 'auto';

    const controller = new AbortController();
    currentController = controller;
    setStreaming(true);

    const assistantDiv = appendMessage('assistant', '', false);
    const bodyEl = assistantDiv.querySelector('.bara-msg-content');
    let rawText = '';
    let stepEl = null;

    try{
      const res = await fetch(messageUrl, {
        method: 'POST',
        headers: { 'X-CSRFToken': csrfToken },
        body: new URLSearchParams({ content }),
        signal: controller.signal
      });

      if(!res.ok || !res.body){
        const data = await res.json().catch(() => ({}));
        const message = (data.error && data.error.message) || data.error || 'Une erreur est survenue.';
        assistantDiv.remove();
        appendMessage('error', message);
        return;
      }

      const reader = res.body.getReader();
      const decoder = new TextDecoder();
      let buffer = '';

      while(true){
        const { value, done } = await reader.read();
        if(done) break;
        buffer += decoder.decode(value, { stream: true });

        const parts = buffer.split('\n\n');
        buffer = parts.pop();

        for(const part of parts){
          const line = part.trim();
          if(!line.startsWith('data:')) continue;
          let event;
          try{ event = JSON.parse(line.slice(5).trim()); } catch(err){ continue; }

          if(event.type === 'token'){
            rawText += event.text;
            bodyEl.textContent = rawText;
            messagesDiv.scrollTop = messagesDiv.scrollHeight;
          } else if(event.type === 'step'){
            if(!stepEl){
              stepEl = document.createElement('div');
              stepEl.style.cssText = 'font-size:11.5px;opacity:.5;font-style:italic;margin-top:4px;';
              bodyEl.parentNode.appendChild(stepEl);
            }
            stepEl.textContent = event.status === 'calling_tool'
              ? `Utilise l'outil « ${event.tool} »…`
              : event.status === 'tool_blocked'
                ? `Outil « ${event.tool} » bloqué : il n'est pas activé.`
              : `Outil « ${event.tool} » terminé.`;
          } else if(event.type === 'confirm'){
            addConfirmCard(event);
          } else if(event.type === 'visualization'){
            addChartCard(event.chart);
          } else if(event.type === 'error'){
            assistantDiv.remove();
            appendMessage('error', event.message);
            if(event.billing_warning) appendMessage('error', event.billing_warning);
          } else if(event.type === 'done'){
            if(stepEl) stepEl.remove();
            if(rawText){ renderMarkdown(bodyEl, rawText); } else { assistantDiv.remove(); }
            if(typeof event.remaining_balance === 'number'){
              document.getElementById('creditPillText').textContent =
                Math.max(0, Math.round(event.remaining_balance)).toLocaleString('fr-FR') + ' jetons';
            }
            if(event.title){
              const titleEl = document.querySelector('.conv-item.active .conv-title');
              if(titleEl) titleEl.textContent = event.title;
            }
            if(event.billing_warning) appendMessage('error', event.billing_warning);
          }
        }
      }
    } catch(err){
      if(err.name === 'AbortError'){
        if(stepEl) stepEl.remove();
        if(rawText){
          renderMarkdown(bodyEl, rawText + '\n\n*(interrompu)*');
        } else {
          assistantDiv.remove();
        }
      } else {
        appendMessage('error', 'Connexion interrompue. Si des crédits ont été débités, ils sont ajustés automatiquement. Réessaie ton message.');
      }
    } finally {
      currentController = null;
      setStreaming(false);
    }
  });

  // ---------- Upload (recharge la page pour reconstruire l'arborescence) ----------
  const uploadForm = document.getElementById('upload-form');
  const fileInput = document.getElementById('file-input');

  fileInput.addEventListener('change', async () => {
    if(!fileInput.files.length) return;
    try{
      const res = await fetch(uploadForm.action, {
        method: 'POST',
        headers: { 'X-CSRFToken': csrfToken },
        body: new FormData(uploadForm)
      });
      const data = await res.json().catch(() => ({}));
      if(!res.ok) throw new Error(data.error || "Échec de l'upload.");
      window.location.reload();
    } catch(err){
      appendMessage('error', 'Upload échoué : ' + err.message);
      fileInput.value = '';
    }
  });

  // ---------- Arborescence repliable ----------
  const fileList = document.getElementById('file-list');
  const treeKey = `bara-tree-${workspaceId}`;
  let treeState = {};
  try{ treeState = JSON.parse(localStorage.getItem(treeKey) || '{}'); } catch(err){}

  function applyTree(){
    fileList.querySelectorAll('[data-parent]').forEach(li => {
      const parts = li.dataset.parent ? li.dataset.parent.split('/') : [];
      let hidden = false;
      for(let i = 1; i <= parts.length; i++){
        if(treeState[parts.slice(0, i).join('/')]){ hidden = true; break; }
      }
      li.style.display = hidden ? 'none' : '';
    });
    fileList.querySelectorAll('.tree-folder').forEach(li => {
      li.querySelector('.folder-toggle').textContent = treeState[li.dataset.path] ? '▸' : '▾';
    });
  }
  applyTree();

  fileList.addEventListener('click', async (e) => {
    const folder = e.target.closest('.tree-folder');
    if(folder){
      treeState[folder.dataset.path] = !treeState[folder.dataset.path];
      try{ localStorage.setItem(treeKey, JSON.stringify(treeState)); } catch(err){}
      applyTree();
      return;
    }

    const previewBtn = e.target.closest('.file-preview-btn');
    if(previewBtn){
      const previewFrame = document.getElementById('preview-iframe');
      previewFrame.setAttribute('sandbox', 'allow-scripts');
      previewFrame.src = previewBtn.dataset.previewUrl;
      document.getElementById('preview-title').textContent = 'Aperçu HTML';
      document.getElementById('site-preview-url').value = '';
      document.getElementById('site-preview-notice').hidden = true;
      document.getElementById('site-open-new-tab').hidden = true;
      document.getElementById('preview-panel').style.display = 'block';
      return;
    }
    const publishBtn = e.target.closest('.file-publish-btn');
if(publishBtn){
  if(!publishBtn.dataset.publicUrl){
    try{
      const res = await fetch(`/bara/${workspaceId}/files/${publishBtn.dataset.fileId}/publish/`, {
        method: 'POST', headers: { 'X-CSRFToken': csrfToken }
      });
      const data = await res.json().catch(() => ({}));
      if(!res.ok) throw new Error(data.error || 'Échec de la publication.');
      publishBtn.dataset.publicUrl = new URL(data.url).pathname;
      publishBtn.classList.add('is-public');
    } catch(err){ alert(err.message); return; }
  }
  showPublish(publishBtn);
  return;
}
    const deleteBtn = e.target.closest('.file-delete-btn');
    if(deleteBtn){
      if(!confirm('Supprimer ce fichier ?')) return;
      try{
        const res = await fetch(`/bara/${workspaceId}/files/${deleteBtn.dataset.fileId}/delete/`, {
          method: 'POST',
          headers: { 'X-CSRFToken': csrfToken }
        });
        if(!res.ok) throw new Error();
        deleteBtn.closest('.file-item').remove();
      } catch(err){
        alert('Échec de la suppression.');
      }
    }
  });

  document.getElementById('close-preview').addEventListener('click', () => {
    document.getElementById('preview-panel').style.display = 'none';
    document.getElementById('preview-iframe').src = 'about:blank';
    document.getElementById('site-open-new-tab').hidden = true;
    document.getElementById('site-preview-notice').hidden = true;
  });

  document.getElementById('open-site-preview').addEventListener('click', () => {
    document.getElementById('preview-title').textContent = 'Aperçu de site web';
    document.getElementById('preview-panel').style.display = 'block';
    document.getElementById('site-preview-url').focus();
  });

  document.getElementById('site-preview-form').addEventListener('submit', event => {
    event.preventDefault();
    const notice = document.getElementById('site-preview-notice');
    const openLink = document.getElementById('site-open-new-tab');
    let url;
    try{
      url = new URL(document.getElementById('site-preview-url').value.trim());
      if(!['http:', 'https:'].includes(url.protocol) || url.username || url.password){
        throw new Error('Saisis une URL HTTP ou HTTPS sans identifiant.');
      }
    }catch(error){
      notice.textContent = error.message || 'URL invalide. Utilise une adresse HTTP ou HTTPS.';
      notice.hidden = false;
      openLink.hidden = true;
      return;
    }
    const previewFrame = document.getElementById('preview-iframe');
    previewFrame.setAttribute('sandbox', 'allow-scripts allow-forms');
    previewFrame.src = url.href;
    openLink.href = url.href;
    openLink.hidden = false;
    notice.textContent = 'Le site est chargé directement dans ton navigateur, sans passer par le serveur 2AI. Le cadre isole son contenu de 2AI ; l’authentification peut ne pas fonctionner et certains sites refusent l’intégration. Ouvre-les dans un nouvel onglet dans ce cas.';
    notice.hidden = false;
    document.getElementById('preview-title').textContent = url.hostname;
  });

  document.getElementById('chart-prompt-button').addEventListener('click', () => {
    const prompt = 'Crée un graphique interactif à partir de ces données : ';
    textarea.value = textarea.value.trim()
      ? `${textarea.value.trim()}\n${prompt}`
      : prompt;
    textarea.dispatchEvent(new Event('input', {bubbles:true}));
    textarea.focus();
  });

  // ---------- Instructions par workspace ----------
  document.getElementById('instructions-save').addEventListener('click', async () => {
    const status = document.getElementById('instructions-status');
    status.textContent = '…';
    try{
      const res = await fetch(workspaceRoot.dataset.instructionsUrl, {
        method: 'POST',
        headers: { 'X-CSRFToken': csrfToken },
        body: new URLSearchParams({ instructions: document.getElementById('instructions-input').value })
      });
      status.textContent = res.ok ? 'Enregistré ✓' : 'Échec';
    } catch(err){
      status.textContent = 'Échec';
    }
    setTimeout(() => { status.textContent = ''; }, 2000);
  });

  const toolSelectionForm = document.getElementById('tool-selection-form');
  const mcpAddForm = document.getElementById('mcp-add-form');
  const mcpStatus = document.getElementById('mcp-status');
  const blenderPairButton = document.getElementById('blender-pair-button');
  const blenderStatus = document.getElementById('blender-status');
  const blenderPairing = document.getElementById('blender-pairing');
  const blenderToken = document.getElementById('blender-pair-token');
  const blenderBackendUrl = document.getElementById('blender-backend-url');

  blenderPairButton.addEventListener('click', async () => {
    if(blenderPairButton.dataset.connected === 'true' &&
       !window.confirm('Renouveler le jeton ? Le module Blender actuellement connecté sera déconnecté.')) return;
    blenderPairButton.disabled = true;
    blenderStatus.textContent = 'Préparation de la connexion Blender…';
    try{
      const response = await fetch(blenderPairButton.dataset.pairUrl, {
        method: 'POST',
        headers: { 'X-CSRFToken': csrfToken }
      });
      const data = await response.json().catch(() => ({}));
      if(!response.ok) throw new Error(data.error || 'Connexion Blender impossible.');
      blenderToken.textContent = data.token;
      blenderBackendUrl.textContent = data.backend_url;
      blenderPairing.hidden = false;
      blenderPairButton.dataset.connected = 'true';
      blenderStatus.textContent = 'Jeton créé. Il est visible uniquement sur cette page ; configure le module Blender puis connecte-le.';
    }catch(error){
      blenderStatus.textContent = error.message;
    }finally{
      blenderPairButton.disabled = false;
    }
  });

  document.getElementById('blender-copy-token').addEventListener('click', async () => {
    try{
      await navigator.clipboard.writeText(blenderToken.textContent);
      blenderStatus.textContent = 'Jeton copié.';
    }catch(error){
      blenderStatus.textContent = 'Copie impossible dans ce navigateur. Sélectionne le jeton affiché et copie-le manuellement.';
    }
  });

  document.getElementById('blender-copy-url').addEventListener('click', async () => {
    try{
      await navigator.clipboard.writeText(blenderBackendUrl.textContent);
      blenderStatus.textContent = 'URL copiée.';
    }catch(error){
      blenderStatus.textContent = 'Copie impossible dans ce navigateur. Sélectionne l’URL affichée et copie-la manuellement.';
    }
  });

  toolSelectionForm.addEventListener('submit', async event => {
    event.preventDefault();
    const button = toolSelectionForm.querySelector('button[type="submit"]');
    button.disabled = true;
    mcpStatus.textContent = 'Enregistrement des outils…';
    try{
      const response = await fetch(toolSelectionForm.action, {
        method: 'POST',
        headers: { 'X-CSRFToken': csrfToken },
        body: new FormData(toolSelectionForm)
      });
      const data = await response.json().catch(() => ({}));
      if(!response.ok) throw new Error(data.error || 'Échec de l’enregistrement.');
      mcpStatus.textContent = 'Outils enregistrés.';
    }catch(error){
      mcpStatus.textContent = error.message;
    }finally{
      button.disabled = false;
    }
  });

  mcpAddForm.addEventListener('submit', async event => {
    event.preventDefault();
    const button = mcpAddForm.querySelector('button[type="submit"]');
    button.disabled = true;
    mcpStatus.textContent = 'Connexion et découverte des outils…';
    try{
      const response = await fetch(mcpAddForm.action, {
        method: 'POST',
        headers: { 'X-CSRFToken': csrfToken },
        body: new FormData(mcpAddForm)
      });
      const data = await response.json().catch(() => ({}));
      if(!response.ok) throw new Error(data.error || 'Connexion MCP impossible.');
      mcpStatus.textContent = `${data.name} connecté : ${data.tools.length} outil(s) découvert(s).`;
      window.location.reload();
    }catch(error){
      mcpStatus.textContent = error.message;
    }finally{
      button.disabled = false;
    }
  });

  document.getElementById('mcp-server-list').addEventListener('change', async event => {
    const checkbox = event.target.closest('.mcp-enabled');
    if(!checkbox) return;
    const server = checkbox.closest('.mcp-server');
    const body = new URLSearchParams({ enabled: String(checkbox.checked) });
    try{
      const response = await fetch(server.dataset.toggleUrl, {
        method: 'POST', headers: { 'X-CSRFToken': csrfToken }, body
      });
      if(!response.ok) throw new Error();
      mcpStatus.textContent = checkbox.checked ? 'Serveur MCP activé.' : 'Serveur MCP désactivé.';
    }catch(error){
      checkbox.checked = !checkbox.checked;
      mcpStatus.textContent = 'Échec du changement d’état MCP.';
    }
  });

  document.getElementById('mcp-server-list').addEventListener('click', async event => {
    const server = event.target.closest('.mcp-server');
    if(!server) return;
    if(event.target.closest('.mcp-refresh')){
      mcpStatus.textContent = 'Actualisation des outils MCP…';
      try{
        const response = await fetch(server.dataset.refreshUrl, {
          method: 'POST', headers: { 'X-CSRFToken': csrfToken }
        });
        const data = await response.json().catch(() => ({}));
        if(!response.ok) throw new Error(data.error || 'Actualisation impossible.');
        mcpStatus.textContent = `${data.tools.length} outil(s) découvert(s).`;
        window.location.reload();
      }catch(error){
        mcpStatus.textContent = error.message;
      }
    }
    if(event.target.closest('.mcp-remove')){
      if(!window.confirm(`Supprimer le serveur MCP « ${server.querySelector('strong').textContent} » ?`)) return;
      try{
        const response = await fetch(server.dataset.deleteUrl, {
          method: 'POST', headers: { 'X-CSRFToken': csrfToken }
        });
        if(!response.ok) throw new Error();
        server.remove();
        mcpStatus.textContent = 'Serveur MCP supprimé.';
      }catch(error){
        mcpStatus.textContent = 'Échec de la suppression du serveur.';
      }
    }
  });

  const publishPanel = document.getElementById('publish-panel');
const publishUrl = document.getElementById('publish-url');
let publishBtnCurrent = null;

function showPublish(btn){
  publishBtnCurrent = btn;
  publishUrl.value = window.location.origin + btn.dataset.publicUrl;
  publishPanel.style.display = 'block';
}

document.getElementById('copy-publish').addEventListener('click', async (ev) => {
  try{ await navigator.clipboard.writeText(publishUrl.value); ev.target.textContent = 'Copié ✓'; }
  catch(err){ publishUrl.select(); }
  setTimeout(() => { ev.target.textContent = 'Copier'; }, 1500);
});
document.getElementById('close-publish').addEventListener('click', () => { publishPanel.style.display = 'none'; });
document.getElementById('unpublish-btn').addEventListener('click', async () => {
  if(!publishBtnCurrent || !confirm('Dépublier cette page ? Le lien cessera de fonctionner.')) return;
  try{
    const res = await fetch(`/bara/${workspaceId}/files/${publishBtnCurrent.dataset.fileId}/unpublish/`, {
      method: 'POST', headers: { 'X-CSRFToken': csrfToken }
    });
    if(!res.ok) throw new Error();
    publishBtnCurrent.dataset.publicUrl = '';
    publishBtnCurrent.classList.remove('is-public');
    publishPanel.style.display = 'none';
  } catch(err){ alert('Échec de la dépublication.'); }
});
})();
