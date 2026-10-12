(function(){
  const form = document.getElementById('editor-form');
  const textarea = document.getElementById('editor-textarea');
  const saveBtn = document.getElementById('save-btn');
  const status = document.getElementById('save-status');

  form.addEventListener('submit', async (e) => {
    e.preventDefault();
    saveBtn.disabled = true;
    status.textContent = 'Enregistrement…';
    try{
      const res = await fetch(form.action || window.location.href, {
        method: 'POST',
        headers: { 'X-CSRFToken': form.querySelector('[name=csrfmiddlewaretoken]').value },
        body: new URLSearchParams({ content: textarea.value })
      });
      if(res.status === 413){ status.textContent = await res.text(); }
      else { status.textContent = res.ok ? 'Enregistré.' : "Échec de l'enregistrement."; }
    } catch(err){
      status.textContent = 'Échec de l\'enregistrement.';
    } finally {
      saveBtn.disabled = false;
      setTimeout(() => { status.textContent = ''; }, 2500);
    }
  });

  document.addEventListener('keydown', (e) => {
    if((e.ctrlKey || e.metaKey) && e.key === 's'){
      e.preventDefault();
      form.requestSubmit();
    }
  });
})();
