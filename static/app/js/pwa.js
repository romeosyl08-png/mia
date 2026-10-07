(() => {
  const controls = document.getElementById('pwa-install-controls');
  const installButton = document.getElementById('pwa-install-button');
  const iosHelp = document.getElementById('pwa-ios-install-help');
  const status = document.getElementById('pwa-install-status');
  if(!controls || !installButton || !iosHelp || !status) return;

  const standalone = window.matchMedia('(display-mode: standalone)').matches || navigator.standalone === true;
  const ios = /iPhone|iPad|iPod/.test(navigator.userAgent) ||
    (navigator.platform === 'MacIntel' && navigator.maxTouchPoints > 1);
  let deferredPrompt = null;

  if(!standalone && ios){
    controls.hidden = false;
    iosHelp.hidden = false;
  }

  if('serviceWorker' in navigator){
    navigator.serviceWorker.register('/service-worker.js', {scope:'/'})
      .catch(error => {
        console.error('2AI service worker registration failed.', error);
        controls.hidden = false;
        status.textContent = 'Installation PWA indisponible : ouvre 2AI en HTTPS ou sur localhost.';
      });
  }

  window.addEventListener('beforeinstallprompt', event => {
    if(standalone) return;
    event.preventDefault();
    deferredPrompt = event;
    controls.hidden = false;
    installButton.hidden = false;
  });

  installButton.addEventListener('click', async () => {
    if(!deferredPrompt){
      status.textContent = 'Utilise le menu du navigateur pour installer 2AI.';
      return;
    }
    try{
      const promptEvent = deferredPrompt;
      deferredPrompt = null;
      await promptEvent.prompt();
      const choice = await promptEvent.userChoice;
      if(choice.outcome === 'accepted'){
        status.textContent = 'Installation de 2AI lancée.';
        installButton.hidden = true;
      } else {
        status.textContent = 'Installation annulée. Tu peux réessayer depuis le menu du navigateur.';
        installButton.hidden = true;
      }
    }catch(error){
      console.error('2AI installation prompt failed.', error);
      status.textContent = 'Le navigateur n’a pas pu lancer l’installation. Essaie depuis son menu.';
      installButton.hidden = true;
    }
  });

  window.addEventListener('appinstalled', () => {
    installButton.hidden = true;
    status.textContent = '2AI est installée.';
  });
})();
