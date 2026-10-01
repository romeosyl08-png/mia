//chat
const chatConfigElement = document.getElementById("chat-config");
if (chatConfigElement) {
const chatConfig = JSON.parse(chatConfigElement.textContent);
(function(){
  // ---------- State ----------
  const globalAppearance =
    window.AI2AIAppearance
        ? window.AI2AIAppearance.get()
        : {
            botName: "Le Comptoir",
            botAvatar: "🐘",
            accent: "#6C6FCB",
            theme: "light"
        };

let state = {

    credits:
        Number(chatConfig.initialCredits) || 0,

    totalCredits:
        Math.max(
            Number(chatConfig.initialCredits) || 0,
            1
        ),

    isAuthenticated:
        Boolean(chatConfig.isAuthenticated),

    loginUrl:
        chatConfig.loginUrl || "/auth/?next=/",

    hasActiveModels:
        Boolean(chatConfig.hasActiveModels),


    /*
     * APPARENCE GLOBALE
     */

    theme:
        globalAppearance.theme,

    accent:
        globalAppearance.accent,

    accentHover:
        globalAppearance.accent,

    accentInk:
        "#FFFFFF",

    botName:
        globalAppearance.botName,

    botAvatar:
        globalAppearance.botAvatar,


    modelId:
        chatConfig.modelId || "demo-mistral",

    demoModels: {

        "demo-mistral": "Mistral",

        "demo-claude": "Claude",

        "demo-gemini": "Gemini 3 Pro"

    },

    personas: [],

    activePersonaId: null,

    conversations: [],

    activeConvoId: null,

    usageLog: []

};

  function uid(prefix){ return prefix + "_" + Math.random().toString(36).slice(2,9); }

  function conversationKey(convo){ return convo.id ?? convo.draftId; }

  async function newConversation(personaId){
    const persona = state.personas.find(p => p.id === personaId) || state.personas[0];
    if(!persona) return null;
    const convo = { id: null, draftId: uid("draft"), title: "Nouvelle conversation", personaId: persona.id, messages: [] };
    state.conversations.unshift(convo);
    state.activeConvoId = conversationKey(convo);
    return convo;
  }

  async function loadConversations(){
    if(!state.isAuthenticated) return;
    try{
      const res = await fetch("/chat/api/conversations/");
      if(!res.ok) throw new Error("Erreur serveur " + res.status);
      const list = await res.json();
      state.conversations = list.map(c => ({ id: c.id, title: c.title, personaId: c.persona_id || state.activePersonaId, messages: null }));
      if(state.conversations.length > 0){
        state.activeConvoId = conversationKey(state.conversations[0]);
        await loadConversationMessages(state.activeConvoId);
      }
    }catch(err){
      console.error("Erreur chargement conversations", err);
    }
  }

  async function loadPersonas(){
    if(!state.isAuthenticated){
      state.personas = [{ id: "p1", name: "Assistant général", prompt: "Tu es un assistant poli, clair et utile.", tone: "neutre", temp: 0.7, length: "moyenne" }];
      state.activePersonaId = "p1";
      return;
    }
    try{
      const res = await fetch("/chat/api/personas/");
      if(!res.ok) throw new Error("Erreur serveur " + res.status);
      const list = await res.json();
      state.personas = list;
      state.activePersonaId = list.length ? list[0].id : null;
    }catch(err){
      console.error("Erreur chargement personas", err);
    }
  }

  async function loadConversationMessages(convoId){
    const convo = state.conversations.find(c => c.id === convoId);
    if(!convo || !convoId) return;
    try{
      const res = await fetch(`/chat/api/conversations/${convoId}/`);
      if(!res.ok) throw new Error("Erreur serveur " + res.status);
      const data = await res.json();
      convo.messages = data.messages.map(m => ({
        role: m.role === "assistant" ? "bot" : m.role,
        content: m.content,
        tokens: m.role === "assistant" ? m.completion_tokens : m.prompt_tokens,
        createdAt: m.created_at
      }));
      convo.title = data.title;
      convo.personaId = data.persona_id || convo.personaId;
    }catch(err){
      console.error("Erreur chargement messages", err);
      convo.messages = [];  // évite de rester bloqué à "null" indéfiniment
    }
    renderAll();  // toujours réafficher, succès ou échec
  }

  function activePersona(){
    const convo = activeConvo();
    const pid = convo ? convo.personaId : state.activePersonaId;
    return state.personas.find(p => p.id === pid) || state.personas[0];
  }
  function activeConvo(){
    return state.conversations.find(c => conversationKey(c) === state.activeConvoId) || null;
  }

  // ---------- Token estimation ----------
  function estimateTokens(text){
    return Math.max(1, Math.round(text.length / 4));
  }

  function contrastColor(hex){
    const value = hex.replace("#", "");
    const red = parseInt(value.slice(0, 2), 16);
    const green = parseInt(value.slice(2, 4), 16);
    const blue = parseInt(value.slice(4, 6), 16);
    const luminance = (0.299 * red + 0.587 * green + 0.114 * blue) / 255;
    return luminance > 0.62 ? "#211914" : "#FFFFFF";
  }

  function darken(hex, amount){
    const value = hex.replace("#", "");
    const channel = index => Math.max(0, Math.round(parseInt(value.slice(index, index + 2), 16) * (1 - amount)));
    return "#" + [0, 2, 4].map(index => channel(index).toString(16).padStart(2, "0")).join("");
  }


  // ---------- Rendering ----------
  const el = id => document.getElementById(id);

  function renderConvoList(){
    const list = el("convoList");
    list.innerHTML = "";
    if(state.conversations.length === 0){
      list.innerHTML = '<div style="padding:8px 18px;font-size:12px;opacity:.5;">Aucune conversation</div>';
      return;
    }
    state.conversations.forEach(c => {
      const convoKey = conversationKey(c);
      const div = document.createElement("div");
      div.className = "item" + (convoKey === state.activeConvoId ? " active" : "");
      div.innerHTML = `<span class="it-label"><span class="txt">${escapeHtml(c.title)}</span></span><button class="del" data-id="${convoKey}">✕</button>`;
      
      div.querySelector(".it-label").addEventListener("click", async () => {
        state.activeConvoId = convoKey;
        renderAll();  // affichage immédiat de la sélection, même avant le chargement
        if(c.id && c.messages === null){
          await loadConversationMessages(c.id);
        }
      });
      div.querySelector(".del").addEventListener("click", async (e) => {
        e.stopPropagation();
        if(c.id){
          const response = await fetch(`/chat/api/conversations/${c.id}/delete/`, {
            method: "DELETE",
            headers: { "X-CSRFToken": document.querySelector("[name=csrfmiddlewaretoken]").value }
          });
          if(!response.ok) return;
        }
        state.conversations = state.conversations.filter(x => conversationKey(x) !== convoKey);
        if(state.activeConvoId === convoKey){
          state.activeConvoId = state.conversations.length ? conversationKey(state.conversations[0]) : null;
        }
        renderAll();
      });
      list.appendChild(div);
    });
  }

  const personaSwitchTimers = new Map();
  function saveConversationPersona(convo, personaId, previousPersonaId){
    if(!state.isAuthenticated || !convo.id) return;
    const existingTimer = personaSwitchTimers.get(convo.id);
    if(existingTimer) clearTimeout(existingTimer);
    const conversationId = convo.id;
    const timer = setTimeout(async () => {
      personaSwitchTimers.delete(conversationId);
      try{
        const res = await fetch(`/chat/api/conversations/${conversationId}/`, {
          method: "PATCH",
          headers: {
            "Content-Type": "application/json",
            "X-CSRFToken": document.querySelector("[name=csrfmiddlewaretoken]").value
          },
          body: JSON.stringify({ persona_id: personaId })
        });
        if(!res.ok) throw new Error("Erreur sauvegarde persona " + res.status);
      }catch(err){
        console.error(err);
        if(convo.personaId === personaId){
          convo.personaId = previousPersonaId;
          if(activeConvo() === convo) renderAll();
        }
      }
    }, 150);
    personaSwitchTimers.set(conversationId, timer);
  }

  function setActivePersona(personaId){
    const persona = state.personas.find(p => String(p.id) === String(personaId));
    if(!persona) return;
    const convo = activeConvo();
    const previousPersonaId = convo ? convo.personaId : state.activePersonaId;
    if(previousPersonaId === persona.id) return;
    if(convo){
      convo.personaId = persona.id;
    } else {
      state.activePersonaId = persona.id;
    }
    renderAll();
    if(convo) saveConversationPersona(convo, persona.id, previousPersonaId);
  }

  function renderPersonaList(){
    const list = el("personaList");
    list.innerHTML = "";
    const selectedPersona = activePersona();
    state.personas.forEach(p => {
      const div = document.createElement("div");
      const isActive = selectedPersona && selectedPersona.id === p.id;
      div.className = "item" + (isActive ? " active" : "");
      div.innerHTML = `<span class="it-label"><span class="persona-dot"></span><span class="txt">${escapeHtml(p.name)}</span></span><button class="del" data-id="${p.id}">✕</button>`;
      div.querySelector(".it-label").addEventListener("click", () => setActivePersona(p.id));
      div.querySelector(".del").addEventListener("click", async (e) => {
        e.stopPropagation();
        if(state.personas.length <= 1) return;
        const convo = activeConvo();
        const wasConversationPersona = convo && convo.personaId === p.id;
        if(wasConversationPersona && convo.id){
          const timer = personaSwitchTimers.get(convo.id);
          if(timer) clearTimeout(timer);
          personaSwitchTimers.delete(convo.id);
        }
        if(state.isAuthenticated && typeof p.id === "number"){
          const res = await fetch(`/chat/api/personas/${p.id}/`, {
            method: "DELETE",
            headers: { "X-CSRFToken": document.querySelector("[name=csrfmiddlewaretoken]").value }
          });
          if(!res.ok) return;
        }
        state.personas = state.personas.filter(x => x.id !== p.id);
        if(state.activePersonaId === p.id){
          state.activePersonaId = state.personas.length ? state.personas[0].id : null;
        }
        if(wasConversationPersona) setActivePersona(state.personas[0].id);
        else renderAll();
      });
      list.appendChild(div);
    });
  }

  function renderPersonaEditor(){
    const p = activePersona();
    const select = el("personaSelect");
    if(select){
      select.innerHTML = state.personas.map(persona => `<option value="${persona.id}">${escapeHtml(persona.name)}</option>`).join("");
    }
    if(!p) return;
    if(select) select.value = String(p.id);
    const personaName = el("chatPersonaName");
    if(personaName) personaName.textContent = p.name;
  }

function renderHeader(){

  const convo = activeConvo();

  const appearance =
    window.AI2AIAppearance
      ? window.AI2AIAppearance.get()
      : {
          botName: "Le Comptoir",
          botAvatar: "🐘"
        };


  el("chatTitle").textContent =
    convo
      ? convo.title
      : "Nouvelle conversation";


  el("chatAvatar").textContent =
    appearance.botAvatar;


  el("brandMark").textContent =
    appearance.botAvatar;


  el("brandName").textContent =
    appearance.botName;


  document.title =
    appearance.botName;

}

  function updateBrowserIdentity(){
    const favicon = el("botFavicon");
    if(!favicon) return;
    const svg = `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64"><text x="32" y="50" text-anchor="middle" font-size="48">${state.botAvatar}</text></svg>`;
    favicon.href = "data:image/svg+xml;charset=utf-8," + encodeURIComponent(svg);
    document.title = state.botName || "Chatbot";
  }

function renderMessages(){
  const wrap = el("messages");
  const convo = activeConvo();
  if(!convo || !convo.messages || convo.messages.length === 0){
    wrap.innerHTML = `<div class="empty-state"><div class="display">Choisis un persona, écris un message</div><div>Chaque échange consomme des jetons de ton solde prépayé, consultable en haut de la page.</div></div>`;
    return;
  }
  wrap.innerHTML = "";
  convo.messages.forEach((m, messageIndex) => {
    const div = document.createElement("div");
    div.className = "msg " + m.role;

    if(m.role === "bot"){
      div.classList.add("has-actions");
    }

    const contentDiv = document.createElement("div");
    contentDiv.className = "msg-content";
    if(m.role === "bot"){
      contentDiv.innerHTML = DOMPurify.sanitize(marked.parse(m.content));
    } else {
      contentDiv.textContent = m.content;
    }
    div.appendChild(contentDiv);

    if(m.role === "bot"){
      const actions = document.createElement("div");
      actions.className = "response-actions";
      const icons = {
        copy: '<svg viewBox="0 0 24 24" aria-hidden="true"><rect x="9" y="9" width="11" height="11" rx="2"></rect><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"></path></svg>',
        volume: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M11 5 6 9H2v6h4l5 4V5Z"></path><path d="M15.5 8.5a5 5 0 0 1 0 7M18.5 5.5a9 9 0 0 1 0 13"></path></svg>',
        like: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M7 10v10H3V10h4Zm0 10h10.4a2 2 0 0 0 1.9-1.4l1.3-4A2 2 0 0 0 18.7 12H15l.6-3.2A2.3 2.3 0 0 0 13.3 6L7 13"></path></svg>',
        dislike: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M7 14V4H3v10h4Zm0-10h10.4a2 2 0 0 1 1.9 1.4l1.3 4A2 2 0 0 1 18.7 12H15l.6 3.2a2.3 2.3 0 0 1-2.3 2.8L7 11"></path></svg>',
        refresh: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M20 11a8.1 8.1 0 0 0-14.8-3L3 11"></path><path d="M3 5v6h6M4 13a8.1 8.1 0 0 0 14.8 3L21 13"></path><path d="M21 19v-6h-6"></path></svg>'
      };
      const addAction = (icon, title, handler) => {
        const button = document.createElement("button");
        button.type = "button";
        button.className = "response-action";
        button.innerHTML = icons[icon];
        button.title = title;
        button.setAttribute("aria-label", title);
        button.addEventListener("click", handler);
        actions.appendChild(button);
        return button;
      };
      const copyBtn = addAction("copy", "Copier la réponse", async () => {
        await navigator.clipboard.writeText(m.content);
        copyBtn.classList.add("active");
        setTimeout(() => { copyBtn.classList.remove("active"); }, 1500);
      });
      addAction("volume", "Lire la réponse", () => {
        window.speechSynthesis.cancel();
        window.speechSynthesis.speak(new SpeechSynthesisUtterance(m.content));
      });
      const likeBtn = addAction("like", "J'aime cette réponse", () => {
        likeBtn.classList.toggle("active");
        dislikeBtn.classList.remove("active");
      });
      const dislikeBtn = addAction("dislike", "Je n'aime pas cette réponse", () => {
        dislikeBtn.classList.toggle("active");
        likeBtn.classList.remove("active");
      });
      addAction("refresh", "Régénérer la réponse", () => regenerateResponse(messageIndex));
      const time = document.createElement("span");
      time.className = "response-time";
      time.textContent = relativeTime(m.createdAt);
      actions.appendChild(time);
      div.appendChild(actions);
    }

    const footer = document.createElement("div");
    footer.className = "msg-footer";
    footer.innerHTML = `<span class="meta">${m.tokens} jetons</span>`;
    div.appendChild(footer);

    wrap.appendChild(div);
  });
  wrap.scrollTop = wrap.scrollHeight;
}

  function relativeTime(value){
    const date = value ? new Date(value) : new Date();
    const minutes = Math.max(0, Math.floor((Date.now() - date.getTime()) / 60000));
    if(minutes < 1) return "à l'instant";
    if(minutes < 60) return "il y a " + minutes + " min";
    const hours = Math.floor(minutes / 60);
    if(hours < 24) return "il y a " + hours + " h";
    return "il y a " + Math.floor(hours / 24) + " j";
  }

  function renderCredits(){
    const pct = Math.max(0, Math.min(1, state.credits / state.totalCredits));
    const circumference = 2 * Math.PI * 54;
    const gaugeFill = el("gaugeFill");
    const gaugeNum = el("gaugeNum");
    const creditText = el("creditPillText");
    const creditBar = el("creditPillBar");
    const lowWarning = el("lowWarning");
    const sendButton = el("sendBtn");
    if(gaugeFill){
      gaugeFill.setAttribute("stroke-dasharray", circumference);
      gaugeFill.setAttribute("stroke-dashoffset", circumference * (1 - pct));
      gaugeFill.style.stroke = pct < 0.15 ? "#B4483A" : "var(--accent)";
    }
    if(gaugeNum) gaugeNum.textContent = Math.max(0, Math.round(state.credits)).toLocaleString("fr-FR");
    if(creditText) creditText.textContent = Math.max(0, Math.round(state.credits)).toLocaleString("fr-FR") + " jetons";
    if(creditBar) creditBar.style.width = (pct * 100) + "%";
    if(lowWarning) lowWarning.classList.toggle("show", pct < 0.15);
    if(sendButton) sendButton.disabled = (state.isAuthenticated && state.credits <= 0) || state.sending;
  }

  function ensureCreditAccess(){
    if(!state.isAuthenticated){
      window.location.href = state.loginUrl;
      return false;
    }
    if(state.credits <= 0){
      const notice = el("creditNotice");
      notice.textContent = "Crédits insuffisants — recharge ton solde pour continuer.";
      notice.classList.add("show");
      window.clearTimeout(state.creditNoticeTimer);
      state.creditNoticeTimer = window.setTimeout(() => notice.classList.remove("show"), 4200);
      return false;
    }
    return true;
  }

  function escapeHtml(str){
    const d = document.createElement("div");
    d.textContent = str;
    return d.innerHTML;
  }

  function renderAll(){
    renderConvoList();
    renderPersonaList();
    renderPersonaEditor();
    renderHeader();
    renderMessages();
    renderCredits();
  }

  function editField(id){
    const field = el(id);
    if(field){
      field.readOnly = false;
      field.focus();
      field.select();
    }
  }

  // ---------- Simulated / real bot response ----------
  function toneOpener(tone){
    switch(tone){
      case "decontracte": return "Ok, ";
      case "formel": return "Bien entendu. ";
      case "pedagogue": return "Voyons ça ensemble. ";
      default: return "";
    }
  }

  function simulateReply(persona, userText){
    const lengths = { courte: 1, moyenne: 2, longue: 3 };
    const n = lengths[persona.length] || 2;
    const modelName = state.demoModels[state.modelId] || state.modelId;
    let sentences = [
      toneOpener(persona.tone) + "Réponse de " + modelName + " en mode démo, dans le style du persona « " + persona.name + " ».",
      "En connectant une vraie clé API, ce même espace afficherait la réponse du modèle, calculée à partir de ton prompt système et de tes réglages de créativité.",
      "Ceci reste un aperçu local : aucune donnée n'a quitté ton navigateur pour ce message."
    ];
    return sentences.slice(0, n).join(" ");
  }

  async function callBackend(persona, userText, convo){
    const res = await fetch("/chat/api/send/", {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "X-CSRFToken": document.querySelector("[name=csrfmiddlewaretoken]").value
      },
      body: JSON.stringify({
        conversation_id: convo.id,
        content: userText,
        model_id: state.modelId,
        persona_id: typeof convo.personaId === "number" ? convo.personaId : null,
        system_prompt: persona.prompt,
        temperature: persona.temp,
        team_mode: el("teamModeBtn").classList.contains("active"),
        team_model_ids: Array.from(el("teamModelSelect").selectedOptions).map(option => option.value)
      })
    });
    const data = await res.json().catch(() => ({}));
    if(!res.ok) throw new Error((data.error && data.error.message) || "Erreur serveur " + res.status);
    return data;
  }

  async function sendMessage(text, reuseLastUser = false){
    if(state.sending || !ensureCreditAccess()) return;
    state.sending = true;
    renderCredits();
    let convo = activeConvo();
    if(!convo){
      convo = await newConversation(state.activePersonaId);
    }
    if(!convo){
      state.sending = false;
      renderCredits();
      return;
    }
    const persona = state.personas.find(p => p.id === convo.personaId) || state.personas[0];

    if(!reuseLastUser) convo.messages.push({ role: "user", content: text, tokens: 0, createdAt: new Date().toISOString() });
    renderAll();

    try{
      let data;
      if(state.hasActiveModels){
        data = await callBackend(persona, text, convo);
        if(convo.id === null && data.conversation_id){
          convo.id = data.conversation_id;
          state.activeConvoId = conversationKey(convo);
        }
        if(data.title) convo.title = data.title;
      } else {
        await new Promise(resolve => setTimeout(resolve, 350));
        data = { reply: simulateReply(persona, text), remaining_balance: state.credits };
      }
      const replyText = data.reply || "(réponse vide)";
      const inputTokens = data.prompt_tokens ?? estimateTokens(text);
      const replyTokens = data.completion_tokens ?? estimateTokens(replyText);
      convo.messages[convo.messages.length - 1].tokens = inputTokens;
      convo.messages.push({ role: "bot", content: replyText, tokens: replyTokens, createdAt: new Date().toISOString() });
      const newCredits = Number(data.remaining_balance);
        if (Number.isFinite(newCredits)) {
            state.credits = newCredits;
        }
      state.usageLog.unshift({ label: "Message (" + persona.name + ")", tokens: inputTokens });
      state.usageLog.unshift({ label: "Réponse (" + persona.name + ")", tokens: replyTokens });
      renderAll();
    }catch(err){
      convo.messages.push({ role: "bot", content: "⚠ " + err.message, tokens: 0 });
      renderAll();
    } finally {
      state.sending = false;
      renderCredits();
    }
  }

  async function regenerateResponse(messageIndex){
    const convo = activeConvo();
    const previousMessage = convo && convo.messages[messageIndex - 1];
    if(!convo || !previousMessage || previousMessage.role !== "user" || state.sending) return;
    convo.messages.splice(messageIndex, 1);
    renderAll();
    await sendMessage(previousMessage.content, true);
  }

  // ---------- Event wiring ----------
  el("newConvoBtn").addEventListener("click", () => {
    const persona = activePersona();
    if(!persona) return;
    newConversation(persona.id);
    renderAll();
  });
  el("customizeBtn").addEventListener("click", () => {
    window.location.href = el("customizeBtn").dataset.url;
  });
  function togglePanel(side){
    const app = document.querySelector(".app");
    const isLeft = side === "left";
    const collapsedClass = isLeft ? "left-collapsed" : "right-collapsed";
    const button = el(isLeft ? "toggleLeftPanel" : "toggleRightPanel");
    const collapsed = app.classList.toggle(collapsedClass);
    const action = collapsed ? "Afficher" : "Masquer";
    const label = action + " le panneau " + (isLeft ? "gauche" : "droit");
    button.textContent = collapsed ? (isLeft ? "›" : "‹") : (isLeft ? "‹" : "›");
    button.title = label;
    button.setAttribute("aria-label", label);
  }
  function toggleMobileSidebar(isOpen){
    const app = document.querySelector(".app");
    app.classList.toggle("mobile-sidebar-open", isOpen);
    el("openMobileSidebar").setAttribute("aria-expanded", String(isOpen));
  }
  const mobileBreakpoint = window.matchMedia("(max-width: 900px)");
  mobileBreakpoint.addEventListener("change", event => {
    if(!event.matches) toggleMobileSidebar(false);
  });
  el("toggleLeftPanel").addEventListener("click", () => togglePanel("left"));
  el("openMobileSidebar").addEventListener("click", () => toggleMobileSidebar(true));
  el("closeMobileSidebar").addEventListener("click", () => toggleMobileSidebar(false));
  el("mobileSidebarBackdrop").addEventListener("click", () => toggleMobileSidebar(false));
  document.addEventListener("keydown", event => {
    if(event.key === "Escape") toggleMobileSidebar(false);
  });
  el("newPersonaBtn").addEventListener("click", async () => {
    const payload = { name: "Nouveau persona", prompt: "Décris ici le comportement de ce persona.", tone: "neutre", temp: 0.7, length: "moyenne" };
    if(!state.isAuthenticated) return;
    try{
      const res = await fetch("/chat/api/personas/", {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          "X-CSRFToken": document.querySelector("[name=csrfmiddlewaretoken]").value
        },
        body: JSON.stringify(payload)
      });
      if(!res.ok) throw new Error("Erreur serveur " + res.status);
      const p = await res.json();
      state.personas.push(p);
      setActivePersona(p.id);
    }catch(err){
      console.error("Erreur création persona", err);
    }
  });
el("modelChips").addEventListener("click", (e) => {
  const toggle = e.target.closest(".provider-toggle");
  const item = e.target.closest(".provider-menu-item");

  if (toggle) {
    e.stopPropagation();
    const menu = toggle.closest(".provider-dropdown").querySelector(".provider-menu");
    const wasOpen = !menu.hidden;

    document.querySelectorAll(".provider-menu").forEach(m => { m.hidden = true; });
    el("teamModeBtn").classList.remove("active");
    el("teamOptions").hidden = true;

    menu.hidden = wasOpen;
    return;
  }

  if (item) {
    e.stopPropagation();
    state.modelId = item.dataset.modelId;
    item.closest(".provider-menu").querySelectorAll(".provider-menu-item")
      .forEach(i => i.classList.toggle("active", i === item));

    document.querySelectorAll(".provider-toggle").forEach(t => t.classList.remove("has-selection"));
    const parentToggle = item.closest(".provider-dropdown").querySelector(".provider-toggle");
    parentToggle.classList.add("has-selection");
    parentToggle.querySelector(".provider-toggle-label").textContent = item.dataset.modelName;

    item.closest(".provider-menu").hidden = true;
  }
});


// ferme les menus ouverts si on clique ailleurs
document.addEventListener("click", () => {
  document.querySelectorAll(".provider-menu").forEach(m => { m.hidden = true; });
});

  el("teamModeBtn").addEventListener("click", () => {
    const active = el("teamModeBtn").classList.toggle("active");
    el("teamOptions").hidden = !active;
    if(active){
      document.querySelectorAll(".model-chip:not(.team-chip)").forEach(item => item.classList.remove("active"));
      el("teamModelGrid").querySelector(".team-model-option")?.focus();
    }
  });
  el("teamModelGrid").addEventListener("click", event => {
    const button = event.target.closest(".team-model-option");
    if(!button) return;
    const option = Array.from(el("teamModelSelect").options).find(item => item.value === button.dataset.modelId);
    if(!option) return;
    if(!option.selected && el("teamModelSelect").selectedOptions.length >= 3){
      el("creditNotice").textContent = "Tu peux choisir au maximum 3 IA.";
      el("creditNotice").classList.add("show");
      window.setTimeout(() => el("creditNotice").classList.remove("show"), 2800);
      return;
    }
    option.selected = !option.selected;
    button.classList.toggle("selected", option.selected);
    el("teamCount").textContent = `${el("teamModelSelect").selectedOptions.length} / 3`;
  });
  document.querySelectorAll(".tool-btn").forEach(button => {
    button.addEventListener("click", () => {
      document.querySelectorAll(".tool-btn").forEach(item => item.classList.remove("active"));
      button.classList.add("active");
    });
  });
const fileInput = document.createElement("input");
fileInput.type = "file";
fileInput.hidden = true;
document.body.appendChild(fileInput);

el("attachBtn").addEventListener("click", () => fileInput.click());

fileInput.addEventListener("change", async () => {
  const file = fileInput.files[0];
  if(!file) return;

  const convo = activeConvo();
  if(!convo){
    el("creditNotice").textContent = "Crée d'abord une conversation.";
    el("creditNotice").classList.add("show");
    window.setTimeout(() => el("creditNotice").classList.remove("show"), 2800);
    fileInput.value = "";
    return;
  }

  const formData = new FormData();
  formData.append("file", file);
  if(convo.id) formData.append("conversation_id", convo.id);

  try{
    const res = await fetch("/bara/upload/", { 
      method: "POST",
      headers: { "X-CSRFToken": document.querySelector("[name=csrfmiddlewaretoken]").value },
      body: formData
    });
    const data = await res.json();
    if(!res.ok) throw new Error(data.error || "Erreur d'upload");

    el("creditNotice").textContent = `"${data.name}" ajouté — l'IA peut maintenant le lire.`;
    el("creditNotice").classList.add("show");
    window.setTimeout(() => el("creditNotice").classList.remove("show"), 3200);
  }catch(err){
    el("creditNotice").textContent = "Erreur : " + err.message;
    el("creditNotice").classList.add("show");
    window.setTimeout(() => el("creditNotice").classList.remove("show"), 3200);
  }
  fileInput.value = "";
});

  el("voiceBtn").addEventListener("click", () => input.focus());

  const form = el("composerForm");
  const input = el("composerInput");
  const creditPill = el("creditPill");
  const main = document.querySelector(".main");
  const updateComposerHeight = () => {
    main.style.setProperty("--composer-height", form.offsetHeight + "px");
  };
  if(window.ResizeObserver){
    new ResizeObserver(updateComposerHeight).observe(form);
  }
  updateComposerHeight();
  creditPill.addEventListener("click", () => {
    if(!state.isAuthenticated){
      ensureCreditAccess();
      return;
    }
    window.location.href = "/recharge/";
  });
  input.addEventListener("focus", () => {
    if(!state.isAuthenticated) ensureCreditAccess();
  });
  input.addEventListener("input", () => {
    input.style.height = "auto";
    input.style.height = Math.min(140, input.scrollHeight) + "px";
  });
  input.addEventListener("keydown", (e) => {
    if(e.key === "Enter" && !e.shiftKey){
      e.preventDefault();
      form.requestSubmit();
    }
  });
  form.addEventListener("submit", (e) => {
    e.preventDefault();
    const text = input.value.trim();
    if(!ensureCreditAccess() || !text) return;
    if(el("teamModeBtn").classList.contains("active") && el("teamModelSelect").selectedOptions.length === 0){
      el("creditNotice").textContent = "Choisis au moins une IA pour former ton équipe.";
      el("creditNotice").classList.add("show");
      window.setTimeout(() => el("creditNotice").classList.remove("show"), 2800);
      el("teamModelGrid").querySelector(".team-model-option")?.focus();
      return;
    }
    input.value = "";
    input.style.height = "auto";
    sendMessage(text);
  });

  // ---------- Init ----------
  // ---------- Init ----------

  const currentAppearance =
    window.AI2AIAppearance
      ? window.AI2AIAppearance.get()
      : null;

  if(currentAppearance){

    state.theme = currentAppearance.theme;
    state.accent = currentAppearance.accent;
    state.botName = currentAppearance.botName;
    state.botAvatar = currentAppearance.botAvatar;

  }

  (async () => {

    await loadPersonas();

    await loadConversations();

    if(state.conversations.length === 0){
      await newConversation(state.activePersonaId);
    }

    renderAll();

  })();

})();
}


//recharge
   const operatorInput = document.getElementById("operator");
    const phoneInput = document.getElementById("phone");
    const operatorChoices = [...document.querySelectorAll(".operator-choice")];
    let waveChosenManually = false;
    function selectOperator(operator, manual = false){
      if(operatorInput) operatorInput.value = operator;
      if(manual) waveChosenManually = operator === "wave";
      operatorChoices.forEach(choice => choice.classList.toggle("active", choice.dataset.operator === operator));
    }
    operatorChoices.forEach(choice => choice.addEventListener("click", () => selectOperator(choice.dataset.operator, true)));
    if(phoneInput) phoneInput.addEventListener("input", () => {
      if(waveChosenManually) return;
      const digits = phoneInput.value.replace(/\D/g, "");
      const prefix = digits.startsWith("225") ? digits.slice(3, 5) : digits.slice(0, 2);
      if(prefix === "01") selectOperator("momo");
      if(prefix === "05") selectOperator("moov");
      if(prefix === "07") selectOperator("orange");
    });




//losign
const tabLogin = document.getElementById("tabLogin");
  const tabSignup = document.getElementById("tabSignup");
  const panelLogin = document.getElementById("panelLogin");
  const panelSignup = document.getElementById("panelSignup");

  if(tabLogin && tabSignup && panelLogin && panelSignup){
  function showLogin(){
    panelLogin.style.display = "block";
    panelSignup.style.display = "none";
    tabLogin.classList.add("active");
    tabSignup.classList.remove("active");
  }
  function showSignup(){
    panelLogin.style.display = "none";
    panelSignup.style.display = "block";
    tabSignup.classList.add("active");
    tabLogin.classList.remove("active");
  }
  tabLogin.addEventListener("click", showLogin);
  tabSignup.addEventListener("click", showSignup);
  document.getElementById("linkToSignup").addEventListener("click", (e) => { e.preventDefault(); showSignup(); });
  document.getElementById("linkToLogin").addEventListener("click", (e) => { e.preventDefault(); showLogin(); });

  // WhatsApp : pas d'OAuth standard, on ouvre un panneau numéro + OTP
  const waToggle = document.getElementById("waToggle");
  const waPanel = document.getElementById("waPanel");
  waToggle.addEventListener("click", () => waPanel.classList.toggle("open"));

  document.getElementById("waSendOtp").addEventListener("click", () => {
    const phone = document.getElementById("wa_phone").value.trim();
    if(!phone){ return; }
    // À brancher sur une vraie vue Django qui déclenche l'envoi du code
    // (ex. via l'API WhatsApp Cloud de Meta ou un fournisseur comme Twilio).
    fetch("{% url 'whatsapp_send_otp' %}", {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "X-CSRFToken": document.querySelector("[name=csrfmiddlewaretoken]").value
      },
      body: JSON.stringify({ phone })
    });
  });

  // Mêmes comportements pour le panneau Inscription
  const waToggleSignup = document.getElementById("waToggleSignup");
  const waPanelSignup = document.getElementById("waPanelSignup");
  waToggleSignup.addEventListener("click", () => waPanelSignup.classList.toggle("open"));

  document.getElementById("waSendOtpSignup").addEventListener("click", () => {
    const phone = document.getElementById("wa_phone_signup").value.trim();
    if(!phone){ return; }
    fetch("{% url 'whatsapp_send_otp' %}", {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "X-CSRFToken": document.querySelector("[name=csrfmiddlewaretoken]").value
      },
      body: JSON.stringify({ phone })
    });
  });
}
  