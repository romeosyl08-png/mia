(() => {
  const checkbox = document.getElementById('personaShareContext');
  const controls = document.getElementById('personaSharingControls');
  const status = document.getElementById('personaShareStatus');
  const bigStatus = document.getElementById('bigPersonaStatus');
  const sources = document.getElementById('bigPersonaSources');
  const snapshot = document.getElementById('bigPersonaSnapshot');
  const chatLink = document.getElementById('bigPersonaChatLink');
  if (!checkbox || !controls || !status || !bigStatus || !sources || !snapshot || !chatLink) return;
  let selected = window.AI2AIPersonaSettings?.getSelected() || null;
  let saving = false;
  let revision = 0;

  function renderSelection() {
    controls.hidden = !selected || Boolean(selected.is_big_persona);
    checkbox.checked = Boolean(selected?.share_with_personas);
    checkbox.disabled = saving || !selected || Boolean(selected.is_big_persona);
  }

  async function refresh() {
    const currentRevision = ++revision;
    try {
      const response = await fetch('/chat/api/big-persona/', {cache: 'no-store'});
      if (!response.ok) throw new Error('Chargement de Big Persona impossible.');
      const data = await response.json();
      if (currentRevision !== revision) return;
      chatLink.href = `/?persona=${data.persona.id}`;
      bigStatus.textContent = data.personas.length ? `${data.personas.length} persona${data.personas.length > 1 ? 's' : ''} autorisé${data.personas.length > 1 ? 's' : ''}.` : 'Aucune liaison autorisée. Choisis un persona ci-dessus pour activer son partage.';
      sources.replaceChildren();
      data.personas.forEach(persona => {
        const item = document.createElement('li');
        item.textContent = persona.name;
        sources.append(item);
      });
      snapshot.textContent = JSON.stringify({profil: data.user, réglages: data.site}, null, 2);
    } catch (error) {
      if (currentRevision === revision) bigStatus.textContent = error.message;
    }
  }

  document.addEventListener('ai2ai:persona-selected', event => {
    selected = event.detail;
    status.textContent = '';
    renderSelection();
  });
  document.addEventListener('ai2ai:persona-saved', event => {
    if (selected?.id === event.detail.id) {
      selected = event.detail;
      renderSelection();
    }
    refresh();
  });

  checkbox.addEventListener('change', async () => {
    if (!selected || selected.is_big_persona || saving) return;
    const persona = selected;
    const requested = checkbox.checked;
    saving = true;
    checkbox.disabled = true;
    status.textContent = 'Enregistrement de l’autorisation…';
    try {
      const response = await fetch(`/chat/api/personas/${persona.id}/`, {
        method: 'PATCH',
        headers: {'Content-Type': 'application/json', 'X-CSRFToken': document.querySelector('[name=csrfmiddlewaretoken]').value},
        body: JSON.stringify({share_with_personas: requested})
      });
      const data = await response.json();
      if (!response.ok) throw new Error(data.error?.message || 'Enregistrement de l’autorisation impossible.');
      Object.assign(persona, data);
      if (selected?.id === persona.id) {
        selected = persona;
        status.textContent = persona.share_with_personas ? 'Liaison autorisée.' : 'Liaison désactivée pour les prochaines demandes.';
      }
      await refresh();
    } catch (error) {
      if (selected?.id === persona.id) status.textContent = error.message;
    } finally {
      saving = false;
      renderSelection();
    }
  });
  renderSelection();
  refresh();
})();
