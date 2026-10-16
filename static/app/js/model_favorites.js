(function(root){
  function create({initialIds = [], onChange = () => {}, onError = () => {}} = {}){
    let favorites = new Set(initialIds);
    let saving = false;
    const buttons = () => Array.from(document.querySelectorAll('[data-model-favorite]'));

    function render(){
      for(const button of buttons()){
        const selected = favorites.has(button.dataset.modelFavorite);
        const name = button.dataset.modelName;
        const label = selected ? `Retirer ${name} des favoris` : `Ajouter ${name} aux favoris`;
        button.setAttribute('aria-pressed', String(selected));
        button.setAttribute('aria-label', label);
        button.title = label;
        button.disabled = saving;
      }
    }

    async function toggle(modelId){
      if(saving) return;
      const previous = new Set(favorites);
      const favorite = !favorites.has(modelId);
      favorite ? favorites.add(modelId) : favorites.delete(modelId);
      saving = true;
      render();
      onChange();
      try{
        const response = await fetch('/chat/api/preferences/', {
          method: 'PATCH',
          headers: {'Content-Type': 'application/json', 'X-CSRFToken': document.querySelector('[name=csrfmiddlewaretoken]')?.value || ''},
          body: JSON.stringify({favorite_model_id: modelId, favorite})
        });
        const data = await response.json();
        if(!response.ok || !Array.isArray(data.favorite_model_ids)){
          throw new Error(data.error?.message || 'Impossible d’enregistrer ce favori. Réessaie.');
        }
        favorites = new Set(data.favorite_model_ids);
      }catch(error){
        favorites = previous;
        onError(error.message || 'Impossible d’enregistrer ce favori. Réessaie.');
      }finally{
        saving = false;
        render();
        onChange();
      }
    }

    for(const id of ['modelPickerList', 'teamModelGrid']){
      document.getElementById(id)?.addEventListener('click', event => {
        const button = event.target.closest('[data-model-favorite]');
        if(!button) return;
        event.preventDefault();
        event.stopPropagation();
        toggle(button.dataset.modelFavorite).then(() => button.focus({preventScroll: true}));
      });
    }
    render();
    return {has: modelId => favorites.has(modelId), toggle};
  }
  root.AI2AIModelFavorites = {create};
})(window);
