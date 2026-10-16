(() => {
  const appearance = window.AI2AIAppearance;
  const status = document.getElementById('bigPersonaAppearanceStatus');
  const webSwatches = document.getElementById('webSwatches');
  const baraSwatches = document.getElementById('baraSwatches');
  if (!appearance || !status || !webSwatches || !baraSwatches) return;

  const webThemeButtons = [
    document.getElementById('webThemeLight'),
    document.getElementById('webThemeDark')
  ].filter(Boolean);
  const baraThemeButtons = [
    document.getElementById('baraThemeLight'),
    document.getElementById('baraThemeDark')
  ].filter(Boolean);
  const baraAccents = [...appearance.getAccents(), '#007ACC'];
  const csrfToken = document.querySelector('[name=csrfmiddlewaretoken]')?.value || '';
  let bigPersona = null;
  let saveTimer = null;
  let saveRevision = 0;
  let saving = false;

  function renderThemeButtons(buttons, selectedTheme) {
    buttons.forEach(button => {
      const selected = button.dataset.theme === selectedTheme;
      button.classList.toggle('active', selected);
      button.setAttribute('aria-pressed', String(selected));
    });
  }

  function renderSwatches(container, colors, selectedColor, choose) {
    container.replaceChildren();
    colors.forEach(color => {
      const button = document.createElement('button');
      button.type = 'button';
      button.className = 'swatch';
      button.dataset.color = color;
      button.style.background = color;
      button.title = color;
      button.setAttribute('aria-label', `Choisir la couleur ${color}`);
      button.setAttribute('aria-pressed', String(color === selectedColor));
      button.classList.toggle('active', color === selectedColor);
      button.addEventListener('click', () => choose(color));
      container.append(button);
    });
  }

  function render() {
    if (!bigPersona) return;
    renderThemeButtons(webThemeButtons, bigPersona.appearance_theme || 'light');
    renderThemeButtons(baraThemeButtons, bigPersona.bara_appearance_theme || 'dark');
    renderSwatches(webSwatches, appearance.getAccents(), bigPersona.appearance_accent, color => {
      bigPersona.appearance_accent = color;
      appearance.setCentralAppearance(bigPersona);
      render();
      scheduleSave();
    });
    renderSwatches(baraSwatches, baraAccents, bigPersona.bara_appearance_accent || '#007ACC', color => {
      bigPersona.bara_appearance_accent = color;
      render();
      scheduleSave();
    });
  }

  function scheduleSave() {
    saveRevision += 1;
    status.textContent = 'Enregistrement…';
    window.clearTimeout(saveTimer);
    saveTimer = window.setTimeout(save, 350);
  }

  async function save() {
    if (!bigPersona || saving) return;
    const revision = saveRevision;
    const payload = {
      appearance_theme: bigPersona.appearance_theme,
      appearance_accent: bigPersona.appearance_accent,
      bara_appearance_theme: bigPersona.bara_appearance_theme,
      bara_appearance_accent: bigPersona.bara_appearance_accent
    };
    saving = true;
    try {
      const response = await fetch(`/chat/api/personas/${bigPersona.id}/`, {
        method: 'PATCH',
        headers: {
          'Content-Type': 'application/json',
          'X-CSRFToken': csrfToken
        },
        body: JSON.stringify(payload)
      });
      const data = await response.json();
      if (!response.ok) throw new Error(data.error?.message || 'Enregistrement impossible.');
      if (revision === saveRevision) Object.assign(bigPersona, data);
      const selected = window.AI2AIPersonaSettings?.getSelected();
      if (selected?.id === bigPersona.id) Object.assign(selected, data);
      document.dispatchEvent(new CustomEvent('ai2ai:persona-saved', {detail: bigPersona}));
      if (revision === saveRevision) {
        status.textContent = 'Préférences enregistrées.';
        render();
      }
    } catch (error) {
      if (revision === saveRevision) status.textContent = error.message;
    } finally {
      saving = false;
      if (revision !== saveRevision) window.setTimeout(save, 0);
    }
  }

  webThemeButtons.forEach(button => button.addEventListener('click', () => {
    if (!bigPersona) return;
    bigPersona.appearance_theme = button.dataset.theme;
    appearance.setCentralAppearance(bigPersona);
    render();
    scheduleSave();
  }));

  baraThemeButtons.forEach(button => button.addEventListener('click', () => {
    if (!bigPersona) return;
    bigPersona.bara_appearance_theme = button.dataset.theme;
    render();
    scheduleSave();
  }));

  async function load() {
    try {
      const response = await fetch('/chat/api/big-persona/', {cache: 'no-store'});
      if (!response.ok) throw new Error('Chargement des préférences Big Persona impossible.');
      const data = await response.json();
      bigPersona = data.persona;
      appearance.setCentralAppearance(bigPersona);
      render();
      status.textContent = '';
    } catch (error) {
      status.textContent = error.message;
      webSwatches.replaceChildren();
      baraSwatches.replaceChildren();
      [...webThemeButtons, ...baraThemeButtons].forEach(button => { button.disabled = true; });
    }
  }

  load();
})();
