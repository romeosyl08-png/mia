(() => {
  const root = document.querySelector('[data-workspace-id]');
  if(!root) return;
  const nav = root.querySelector('.conversation-tabs');
  const tabs = new Map(Array.from(nav.querySelectorAll('.editor-tab'), tab => [tab.dataset.conversationId, tab]));
  const links = Array.from(root.querySelectorAll('.sidebar-conversation'));
  const active = root.dataset.conversationId;
  const key = `bara.conversation.tabs.v1.${root.dataset.userId}.${root.dataset.workspaceId}`;
  let opened = active ? [active] : tabs.size ? [tabs.keys().next().value] : [];
  try {
    const stored = JSON.parse(localStorage.getItem(key));
    if(Array.isArray(stored)) opened = [...new Set(stored.filter(id => typeof id === 'string' && tabs.has(id)))];
  } catch(error) {}
  if(active && !opened.includes(active)) opened.push(active);

  function save() {
    try { localStorage.setItem(key, JSON.stringify(opened)); } catch(error) {}
  }

  function render() {
    const visible = new Set(opened);
    tabs.forEach((tab, id) => { tab.hidden = !visible.has(id); });
    opened.forEach(id => nav.appendChild(tabs.get(id)));
    let closed = 0;
    links.forEach(link => {
      link.hidden = visible.has(link.dataset.conversationId);
      if(!link.hidden) closed++;
    });
    document.getElementById('closed-conversation-count').textContent = closed ? `(${closed})` : '';
    document.getElementById('closed-conversations-empty').hidden = closed > 0;
    document.dispatchEvent(new CustomEvent('bara:tabs-changed'));
  }

  tabs.forEach((tab, id) => {
    tab.querySelector('.tab-close').addEventListener('click', () => {
      const position = opened.indexOf(id);
      if(position < 0) return;
      opened.splice(position, 1);
      save();
      render();
      const title = tab.querySelector('.tab-title').textContent;
      document.getElementById('conversation-tab-status').textContent = `${title} rangée dans les conversations à gauche.`;
      document.dispatchEvent(new CustomEvent('bara:conversation-closed'));
      if(id === active) {
        const next = opened[Math.min(position, opened.length - 1)];
        window.location.assign(next ? tabs.get(next).querySelector('.editor-tab-link').href : root.dataset.conversationsUrl);
      } else {
        links.find(link => link.dataset.conversationId === id)?.focus({preventScroll: true});
      }
    });
    tab.querySelector('.editor-tab-link').addEventListener('click', () => {
      if(!opened.includes(id)) opened.push(id);
      save();
    });
  });

  links.forEach(link => link.addEventListener('click', () => {
    const id = link.dataset.conversationId;
    if(!opened.includes(id)) opened.push(id);
    save();
  }));
  save();
  render();
  // Entering from the workspace list restores open tabs; an empty saved list
  // keeps the workspace overview empty after the last tab has been closed.
  if(!active && opened.length) {
    window.location.replace(tabs.get(opened.at(-1)).querySelector('.editor-tab-link').href);
  }
})();
