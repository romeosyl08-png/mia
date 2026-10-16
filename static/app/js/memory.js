(() => {
  const prompt = document.getElementById('memoryExportPrompt');
  const copy = document.getElementById('copyMemoryPrompt');
  const status = document.getElementById('memoryCopyStatus');
  if (copy && prompt && status) {
    copy.hidden = false;
    copy.addEventListener('click', async () => {
      try {
        await navigator.clipboard.writeText(prompt.value);
        status.textContent = 'Consigne copiée. Colle-la dans ton assistant actuel.';
      } catch {
        prompt.focus();
        prompt.select();
        status.textContent = 'Sélectionne et copie la consigne avec le menu de ton appareil.';
      }
    });
  }
  const preview = document.getElementById('memoryImportPreview');
  const input = document.getElementById('importContent');
  const count = document.getElementById('memoryImportCount');
  const mode = document.getElementById('importMode');
  const current = document.getElementById('memoryContent');
  if (preview && input && count && mode && current) {
    // Count Unicode characters in the same way as the server, including emoji.
    const length = text => Array.from(text).length;
    const update = () => {
      const text = input.value.trim();
      const existing = current.defaultValue;
      let combined = text;
      if (mode.value === 'append' && existing.trim()) {
        combined = text === existing.trim() || existing.trimEnd().endsWith('\n\n' + text)
          ? existing : existing.trimEnd() + '\n\n' + text;
      }
      const total = length(combined);
      count.textContent = `${length(text)} caractères à importer · ${total} / 8 000 dans la mémoire finale`;
      count.classList.toggle('memory-error', total > 8000);
      preview.querySelector('button[type="submit"]').disabled = !text || total > 8000;
    };
    input.addEventListener('input', update);
    mode.addEventListener('change', update);
    update();
    input.focus();
  }
})();
