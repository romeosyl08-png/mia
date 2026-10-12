(() => {
  const root = document.querySelector('[data-tasks-root]').dataset.tasksRoot;
  const workspaceRoot = root.replace(/tasks\/$/, '');
  const csrf = document.querySelector('[name=csrfmiddlewaretoken]').value;
  const labels = {queued:'En attente', running:'En cours', waiting:'Confirmation attendue', paused:'En pause', completed:'Terminée', failed:'Échec', cancelled:'Annulée', pause_requested:'Pause demandée', cancel_requested:'Annulation demandée'};
  const polling = new WeakSet();
  async function poll(card) {
    if (polling.has(card)) return;
    polling.add(card);
    try {
      const r = await fetch(root + card.dataset.task + '/?after=' + (card.dataset.cursor || '0'));
      if (!r.ok) return;
      const t = await r.json();
      card.dataset.status = t.status;
      if(t.events.length) card.dataset.cursor = t.events.at(-1).id;
      card.querySelector('.task-status').textContent = `${labels[t.status] || t.status} · ${t.steps}/${t.max_steps} étapes · ${t.credits}/${t.credit_limit} crédits`;
      card.querySelector('.task-result').textContent = t.result;
      card.querySelector('.task-error').textContent = t.error;
      for (const ev of t.events) {
        const result = ev.data.result;
        if(result?.status === 'visualization_ready' && result.chart && globalThis.AI2AICharts){
          card.querySelector('.task-actions').append(AI2AICharts.createChart(result.chart));
        }
        if(result?.preview_url?.startsWith(workspaceRoot + 'documents/')){
          const a = document.createElement('a'); a.href = result.preview_url; a.textContent = 'Ouvrir '+(result.filename || 'le document');
          const p = document.createElement('p'); p.append(a); card.querySelector('.task-actions').append(p);
        }
        if (!result || result.status !== 'pending_confirmation' || card.querySelector(`[data-confirm="${result.action_id}"]`)) continue;
        const row = document.createElement('div'); row.dataset.confirm = result.action_id;
        const text = document.createElement('p'); text.textContent = result.description; row.append(text);
        for (const decision of ['confirm', 'reject']) {
          const b = document.createElement('button'); b.textContent = decision === 'confirm' ? 'Confirmer' : 'Refuser';
          b.onclick = async () => {
            b.disabled = true;
            try {
              const response = await fetch(`${workspaceRoot}actions/${result.action_id}/${decision}/`, {method:'POST', headers:{'X-CSRFToken':csrf}});
              if (response.ok) { row.replaceChildren(document.createTextNode('Décision enregistrée. La tâche reprendra automatiquement.')); }
              else { text.textContent = 'Action indisponible ; consulte le journal.'; }
            } catch { text.textContent = 'Connexion interrompue. Vérifie le journal avant de réessayer.'; }
          }; row.append(b);
        }
        card.querySelector('.task-actions').append(row);
      }
      card.querySelector('[data-action=pause]').disabled = !['queued','running'].includes(t.status);
      card.querySelector('[data-action=resume]').disabled = !['paused','waiting','failed'].includes(t.status);
      card.querySelector('[data-action=cancel]').disabled = ['completed','cancelled'].includes(t.status);
    } catch { card.querySelector('.task-error').textContent = 'Connexion perdue. La tâche reste enregistrée.'; }
    finally { polling.delete(card); }
  }
  document.querySelectorAll('[data-task]').forEach(card => {
    card.querySelectorAll('[data-action]').forEach(b => b.onclick = async () => {
      b.disabled = true;
      try {
        const body = new URLSearchParams({max_steps:card.querySelector('.task-steps').value, credit_limit:card.querySelector('.task-budget').value});
        const r = await fetch(root + card.dataset.task + '/' + b.dataset.action + '/', {method:'POST',headers:{'X-CSRFToken':csrf},body});
        if (!r.ok) card.querySelector('.task-error').textContent = (await r.json()).error;
      } catch { card.querySelector('.task-error').textContent = 'Connexion perdue.'; }
      await poll(card);
    });
    // Start the next request after the previous one completes; terminal tasks stop polling.
    async function refresh() {
      if (!document.hidden) await poll(card);
      if (['completed', 'cancelled'].includes(card.dataset.status)) return;
      window.setTimeout(refresh, ['paused', 'failed'].includes(card.dataset.status) ? 15000 : 3000);
    }
    refresh();
  });
})();
