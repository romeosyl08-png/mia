(() => {
  window.AI2AIPersonaContext = {
    async load(persona, conversation) {
      if (!Number.isInteger(persona?.id)) return persona?.prompt || '';
      const query = conversation?.temporary
        ? `?temporary=1&memory_persona_id=${encodeURIComponent(conversation.temporaryMemoryPersonaId || '')}`
        : '';
      const response = await fetch(`/chat/api/personas/${persona.id}/context/${query}`, {cache: 'no-store'});
      if (!response.ok) throw new Error('Impossible de charger le contexte autorisé du persona.');
      const data = await response.json();
      if (typeof data.system_prompt !== 'string') throw new Error('Contexte du persona invalide.');
      return data.system_prompt;
    }
  };
})();
