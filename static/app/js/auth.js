(() => {
  const tabs = [document.getElementById('tabLogin'), document.getElementById('tabSignup')];
  const panels = [document.getElementById('panelLogin'), document.getElementById('panelSignup')];
  if (tabs.some(tab => !tab) || panels.some(panel => !panel)) return;

  tabs[0].parentElement.setAttribute('role', 'tablist');
  tabs[0].parentElement.setAttribute('aria-label', 'Accès au compte');
  function selectTab(index) {
    tabs.forEach((tab, i) => {
      const selected = i === index;
      tab.classList.toggle('active', selected);
      tab.setAttribute('aria-selected', String(selected));
      tab.tabIndex = selected ? 0 : -1;
      panels[i].style.display = selected ? 'block' : 'none';
    });
  }
  tabs.forEach((tab, i) => {
    tab.setAttribute('role', 'tab');
    tab.setAttribute('aria-controls', panels[i].id);
    panels[i].setAttribute('role', 'tabpanel');
    panels[i].setAttribute('aria-labelledby', tab.id);
    tab.addEventListener('click', () => selectTab(i));
    tab.addEventListener('keydown', event => {
      if (!['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(event.key)) return;
      event.preventDefault();
      const next = event.key === 'Home' ? 0 : event.key === 'End' ? 1 : 1 - i;
      selectTab(next);
      tabs[next].focus();
    });
  });
  // Preserve the signup panel when the server returns validation errors.
  selectTab(tabs[1].classList.contains('active') ? 1 : 0);
  ['linkToSignup', 'linkToLogin'].forEach((id, i) => {
    document.getElementById(id)?.addEventListener('click', event => {
      event.preventDefault();
      selectTab(1 - i);
      tabs[1 - i].focus();
    });
  });

  const passwordToggles = document.querySelectorAll ? document.querySelectorAll('.password-toggle') : [];
  passwordToggles.forEach(button => {
    const input = document.getElementById(button.dataset.passwordTarget);
    if (!input) return;
    button.addEventListener('click', () => {
      const showing = input.type === 'text';
      input.type = showing ? 'password' : 'text';
      button.textContent = showing ? 'Afficher' : 'Masquer';
      button.setAttribute('aria-label', showing ? 'Afficher le mot de passe' : 'Masquer le mot de passe');
      button.setAttribute('aria-pressed', String(!showing));
      input.focus({preventScroll: true});
      const end = input.value.length;
      input.setSelectionRange(end, end);
    });
  });

  const telegramWrappers = document.querySelectorAll ? document.querySelectorAll('[id^="telegram-login-wrap"]') : [];
  telegramWrappers.forEach(wrapper => {
    const fallback = wrapper.querySelector('.telegram-fallback');
    const loading = wrapper.querySelector('.telegram-loading');
    if (!fallback) return;
    const updateTelegramState = () => {
      const loaded = Boolean(wrapper.querySelector('iframe'));
      if (loading) loading.hidden = loaded;
      if (loaded) fallback.hidden = true;
      return loaded;
    };
    const observer = new MutationObserver(() => {
      if (updateTelegramState()) observer.disconnect();
    });
    observer.observe(wrapper, {childList: true, subtree: true});
    window.setTimeout(() => {
      observer.disconnect();
      if (!updateTelegramState()) {
        if (loading) loading.hidden = true;
        fallback.hidden = false;
      }
    }, 5000);
  });

  ['', 'Signup'].forEach(suffix => {
    const toggle = document.getElementById('waToggle' + suffix);
    const panel = document.getElementById('waPanel' + suffix);
    const send = document.getElementById('waSendOtp' + suffix);
    if (!toggle || !panel) return;
    toggle.setAttribute('aria-controls', panel.id);
    toggle.setAttribute('aria-expanded', 'false');
    toggle.addEventListener('click', () => {
      const open = panel.classList.toggle('open');
      toggle.setAttribute('aria-expanded', String(open));
      if (open) panel.querySelector('input')?.focus();
    });
    if (!send) return;
    send.addEventListener('click', async () => {
      const input = panel.querySelector('input');
      const status = panel.querySelector('.otp-status');
      const phone = input.value.trim();
      if (!phone) {
        status.textContent = 'Renseigne ton numéro WhatsApp.';
        input.focus();
        return;
      }
      send.disabled = true;
      status.textContent = 'Envoi en cours…';
      try {
        const response = await fetch(send.dataset.otpUrl, {
          method: 'POST',
          headers: {'Content-Type': 'application/json', 'X-CSRFToken': document.querySelector('[name=csrfmiddlewaretoken]').value},
          body: JSON.stringify({phone})
        });
        const data = await response.json();
        status.textContent = response.ok ? 'Code envoyé.' : data.error || 'L’envoi du code a échoué.';
      } catch {
        status.textContent = 'Connexion interrompue. Réessaie dans un instant.';
      } finally {
        send.disabled = false;
      }
    });
  });
})();
