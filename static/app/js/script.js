//chat
const chatConfigElement = document.getElementById("chat-config");
if (chatConfigElement) {
const chatConfig = JSON.parse(chatConfigElement.textContent);
(function(){
  const localStorageScope = chatConfig.userId || "anonymous";
  const localAISettingsKey = `ai2ai-local-model-v1:${localStorageScope}`;
  const localConversationsKey = `ai2ai-local-conversations-v1:${localStorageScope}`;

  function showChatNotice(message){
    const notice = document.getElementById("creditNotice");
    if(!notice) return;
    notice.textContent = message;
    notice.classList.add("show");
    window.clearTimeout(showChatNotice.timer);
    showChatNotice.timer = window.setTimeout(() => notice.classList.remove("show"), 5000);
  }

  function readLocalAISettings(){
    try{
      const value = JSON.parse(localStorage.getItem(localAISettingsKey) || "null");
      return value && typeof value.baseUrl === "string" && typeof value.modelId === "string" ? value : null;
    }catch(error){
      console.error("Impossible de lire la configuration IA locale", error);
      showChatNotice("Impossible de lire la configuration locale du navigateur.");
      return null;
    }
  }

  function readLocalConversations(){
    try{
      const value = JSON.parse(localStorage.getItem(localConversationsKey) || "[]");
      if(!Array.isArray(value)) throw new Error("Le stockage des conversations locales est invalide.");
      return value.filter(item =>
        item && typeof item.localId === "string" && Array.isArray(item.messages)
      ).map(item => ({
        ...item,
        id: null,
        title: typeof item.title === "string" ? item.title.slice(0, 200) : "Conversation locale",
        folder: typeof item.folder === "string" ? item.folder.slice(0, 80) : "",
        isArchived: item.isArchived === true,
        localModel: item.localModel && typeof item.localModel.baseUrl === "string" &&
          typeof item.localModel.modelId === "string" ? item.localModel : null,
        messages: item.messages.filter(message =>
          message && ["user", "bot"].includes(message.role) && typeof message.content === "string"
        ).map(message => ({
          role: message.role,
          content: message.content,
          tokens: Number.isFinite(message.tokens) ? message.tokens : 0,
          createdAt: typeof message.createdAt === "string" ? message.createdAt : new Date().toISOString()
        })),
        localOnly: true,
        temporary: false
      }));
    }catch(error){
      console.error("Impossible de lire les conversations locales", error);
      showChatNotice("Impossible de lire les conversations locales de ce navigateur.");
      return [];
    }
  }

  function persistLocalConversations(){
    const local = state.conversations.filter(conversation => conversation.localOnly && !conversation.temporary);
    try{
      localStorage.setItem(localConversationsKey, JSON.stringify(local));
      return true;
    }catch(error){
      console.error("Impossible d’enregistrer les conversations locales", error);
      showChatNotice("Le navigateur n’a pas pu enregistrer la conversation locale. Vérifie l’espace disponible.");
      return false;
    }
  }

  // ---------- State ----------
  const globalAppearance =
    window.AI2AIAppearance
        ? window.AI2AIAppearance.get()
        : {
            botName: "2AI",
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

    localAI:
        readLocalAISettings(),

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

    sidebarConversations: [],

    conversationSearch: "",

    selectedFolder: "",

    showArchived: false,

    conversationFolders: [],

    activeConvoId: null,

    temporaryChat: false,

    temperatureOverride: null,

    workspaces: [],

    selectedWorkspaceId: null,

    usageLog: []

};

  function uid(prefix){ return prefix + "_" + Math.random().toString(36).slice(2,9); }

  function conversationKey(convo){ return convo.id ?? convo.localId ?? convo.draftId; }

  async function newConversation(personaId, temporary = state.temporaryChat){
    const persona = state.personas.find(p => p.id === personaId) || state.personas[0];
    if(!persona) return null;
    const localOnly = state.modelId.startsWith("local:");
    const convo = {
      id: null,
      localId: localOnly && !temporary ? uid("local") : null,
      draftId: uid("draft"),
      title: "Nouvelle conversation",
      personaId: persona.id,
      workspaceId: localOnly ? null : state.selectedWorkspaceId,
      messages: [],
      temporary,
      localOnly,
      folder: "",
      isArchived: false,
      localModel: localOnly ? { ...state.localAI } : null,
      syncedConversationId: null,
      syncedMessageCount: 0
    };
    state.conversations.unshift(convo);
    state.activeConvoId = conversationKey(convo);
    return convo;
  }

  async function loadConversations(){
    const localConversations = readLocalConversations();
    state.conversations = localConversations;
    if(!state.isAuthenticated) return;
    try{
      const res = await fetch("/chat/api/conversations/");
      if(!res.ok) throw new Error("Erreur serveur " + res.status);
      const list = await res.json();
      state.conversations = list.map(c => ({
        id: c.id,
        title: c.title,
        personaId: c.persona_id || state.activePersonaId,
        workspaceId: c.workspace_id || null,
        folder: c.folder || "",
        isArchived: Boolean(c.is_archived),
        messages: null
      })).concat(localConversations);
      if(state.conversations.length > 0){
        state.activeConvoId = conversationKey(state.conversations[0]);
        state.activePersonaId = state.conversations[0].personaId;
        state.selectedWorkspaceId = state.conversations[0].workspaceId;
        await loadConversationMessages(state.activeConvoId);
      }
      await loadConversationFolders();
      await loadConversationHistory();
    }catch(err){
      console.error("Erreur chargement conversations", err);
      state.sidebarConversations = localHistoryRows();
      if(!state.activeConvoId && localConversations.length){
        state.activeConvoId = conversationKey(localConversations[0]);
        state.activePersonaId = localConversations[0].personaId;
      }
    }
  }

  async function loadConversationFolders(){
    if(!state.isAuthenticated) return;
    try{
      const response = await fetch("/chat/api/folders/");
      if(!response.ok) throw new Error("Erreur chargement des dossiers");
      state.conversationFolders = await response.json();
      state.conversationFolders = [...new Set(state.conversationFolders.concat(
        state.conversations.filter(conversation => conversation.localOnly && conversation.folder).map(conversation => conversation.folder)
      ))].sort((a, b) => a.localeCompare(b));
    }catch(error){
      console.error("Erreur chargement des dossiers de conversations", error);
    }
  }

  function localHistoryRows(){
    const persona = activePersona();
    const search = state.conversationSearch.toLowerCase();
    return state.conversations.filter(conversation =>
      conversation.localOnly &&
      conversation.isArchived === state.showArchived &&
      (!persona || conversation.personaId === persona.id) &&
      (!state.selectedFolder || conversation.folder === state.selectedFolder) &&
      (!search || conversation.title.toLowerCase().includes(search) ||
        conversation.messages.some(message => message.content.toLowerCase().includes(search)))
    );
  }

  async function loadConversationHistory(){
    if(!state.isAuthenticated) return;
    const params = new URLSearchParams({ archived: state.showArchived ? "1" : "0" });
    const persona = activePersona();
    if(persona && typeof persona.id === "number") params.set("persona_id", String(persona.id));
    if(state.conversationSearch) params.set("q", state.conversationSearch);
    if(state.selectedFolder) params.set("folder", state.selectedFolder);
    try{
      const response = await fetch(`/chat/api/conversations/?${params.toString()}`);
      if(!response.ok) throw new Error("Erreur recherche des conversations " + response.status);
      const rows = await response.json();
      state.sidebarConversations = rows.map(row => {
        let conversation = state.conversations.find(item => item.id === row.id);
        if(!conversation){
          conversation = {
            id: row.id,
            title: row.title,
            personaId: row.persona_id || state.activePersonaId,
            workspaceId: row.workspace_id || null,
            folder: row.folder || "",
            isArchived: Boolean(row.is_archived),
            messages: null
          };
          state.conversations.push(conversation);
        }else{
          conversation.title = row.title;
          conversation.personaId = row.persona_id || conversation.personaId;
          conversation.workspaceId = row.workspace_id || null;
          conversation.folder = row.folder || "";
          conversation.isArchived = Boolean(row.is_archived);
        }
        return conversation;
      });
      state.sidebarConversations.push(...localHistoryRows());
    }catch(error){
      console.error("Erreur recherche des conversations", error);
      state.sidebarConversations = localHistoryRows();
    }
  }

  async function loadWorkspaces(){
    if(!state.isAuthenticated) return;
    try{
      const res = await fetch("/chat/api/workspaces/");
      if(!res.ok) throw new Error("Erreur serveur " + res.status);
      state.workspaces = await res.json();
    }catch(err){
      console.error("Erreur chargement des espaces de fichiers", err);
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
    if(!convo || !convoId || convo.localOnly) return;
    try{
      const res = await fetch(`/chat/api/conversations/${convoId}/`);
      if(!res.ok) throw new Error("Erreur serveur " + res.status);
      const data = await res.json();
      convo.messages = data.messages.map(m => ({
        id: m.id,
        role: m.role === "assistant" ? "bot" : m.role,
        content: m.content,
        mediaUrl: m.media_url || null,
        mediaKind: m.media_kind || "image",
          routingObservationId: m.routing_observation_id || null,
          feedback: m.routing_feedback || null,
        sources: Array.isArray(m.sources) ? m.sources : [],
        tokens: m.role === "assistant" ? m.completion_tokens : m.prompt_tokens,
        createdAt: m.created_at
      }));
      convo.title = data.title;
      convo.personaId = data.persona_id || convo.personaId;
      convo.workspaceId = data.workspace_id || null;
      if(activeConvo() === convo) state.selectedWorkspaceId = convo.workspaceId;
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
  function isLocalModelSelected(){
    return typeof state.modelId === "string" && state.modelId.startsWith("local:");
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
  const modelRankingsById = JSON.parse(el("openrouterModelRankings").textContent);

  const initialTopP = Number(chatConfig.defaultTopP ?? 1);
  el("topPControl").value = String(initialTopP);
  el("topPValue").value = initialTopP.toFixed(2).replace(".", ",");
  el("maxTokensControl").value = String(chatConfig.defaultMaxTokens ?? 500);
  const autoModelBtn = el("autoModelBtn");
  function modelUsage(modelId, category){
    const tokens = Number(modelRankingsById[modelId]?.[category]?.total_tokens || 0);
    return Number.isFinite(tokens) ? tokens : 0;
  }
  function sortModelsForTask(category){
    const pickerList = el("modelPickerList");
    const models = Array.from(pickerList.querySelectorAll(".model-picker-item[data-model-id]:not(#localModelDialogBtn)"));
    const priceFor = item => {
      const price = Number(item.dataset.outputPrice);
      return Number.isFinite(price) ? price : 0;
    };
    const usageFor = item => modelUsage(item.dataset.modelId, category);
    models.sort((left, right) => {
      const usageDifference = usageFor(right) - usageFor(left);
      if(usageDifference) return usageDifference;
      const priceDifference = priceFor(right) - priceFor(left);
      if(priceDifference) return priceDifference;
      return left.dataset.modelName.localeCompare(right.dataset.modelName);
    });

    const rankLabels = models.filter(item => usageFor(item) > 0);
    const ranks = new Map(rankLabels.map((item, index) => [item, index + 1]));
    models.forEach(item => {
      const label = item.querySelector(".model-picker-ranking");
      const rank = ranks.get(item);
      label.hidden = !rank;
      label.textContent = rank ? `Classement d’usage #${rank}` : "";
      pickerList.append(item);
    });
    const localModelButton = el("localModelDialogBtn");
    if(localModelButton) pickerList.append(localModelButton);

    const teamModels = new Map(
      Array.from(el("teamModelGrid").querySelectorAll(".team-model-option"))
        .map(item => [item.dataset.modelId, item]),
    );
    const modelSelect = el("teamModelSelect");
    const modelOptions = new Map(Array.from(modelSelect.options).map(option => [option.value, option]));
    models.forEach(item => {
      const teamModel = teamModels.get(item.dataset.modelId);
      const modelOption = modelOptions.get(item.dataset.modelId);
      if(teamModel) el("teamModelGrid").append(teamModel);
      if(modelOption) modelSelect.append(modelOption);
    });

    const selectedOption = el("modelTaskCategory").selectedOptions[0];
    const asOf = selectedOption?.dataset.asOf || "";
    const rankedCount = rankLabels.length;
    el("modelRankingStatus").textContent = rankedCount
      ? category === "all"
        ? "Tri hebdomadaire par volume de tokens texte. L’usage indique la popularité, pas la qualité."
        : "Tri hebdomadaire estimé à partir d’un échantillon de requêtes de cette tâche; ce n’est pas un benchmark."
      : "Classement non synchronisé pour cette tâche; les modèles sont alors triés par prix de sortie.";
    el("modelRankingAttribution").textContent = asOf
      ? `Source: OpenRouter (openrouter.ai/rankings), as of ${asOf}.`
      : "";
  }
  function renderModelPickerState(){
    const conversation = activeConvo();
    const localModelId = conversation?.localOnly
      ? conversation.localModel?.modelId || state.localAI?.modelId
      : state.localAI?.modelId;
    el("localModelLabel").textContent = localModelId || "Configurer ou utiliser un modèle sur ton appareil";
    document.querySelectorAll(".model-picker-item").forEach(item => {
      const selected = item.id === "localModelDialogBtn"
        ? isLocalModelSelected()
        : item.dataset.modelId === state.modelId;
      item.classList.toggle("active", selected);
      item.setAttribute("aria-pressed", String(selected));
    });
    if(autoModelBtn) autoModelBtn.classList.toggle("active", state.modelId === "auto");
  }
  function compactModelLabel(value){
    let label = String(value || "").trim();
    const separator = label.indexOf(":");
    if(separator > 0){
      const prefix = label.slice(0, separator).trim();
      const remainder = label.slice(separator + 1).trim();
      if(prefix && remainder.toLowerCase().startsWith(`${prefix.toLowerCase()} `)) label = remainder;
    }
    return label.replace(/\s*(?:\(free\)|:free)\s*$/i, "").trim();
  }
  function renderSendButton(){
    const button = el("sendBtn");
    const labelElement = el("sendButtonLabel");
    if(!button || !labelElement) return;
    const mode = document.querySelector(".tool-btn[data-mode].active")?.dataset.mode || "chat";
    const conversation = activeConvo();
    const modelId = conversation?.localOnly
      ? `local:${conversation.localModel?.modelId || state.localAI?.modelId || ""}`
      : state.modelId;
    const selectedModel = Array.from(document.querySelectorAll(".model-picker-item[data-model-id]"))
      .find(item => item.dataset.modelId === modelId);
    const modelName = modelId === "auto"
      ? "Auto 2AI"
      : modelId.startsWith("local:")
        ? modelId.slice("local:".length) || "IA locale"
        : compactModelLabel(selectedModel?.dataset.modelName || selectedModel?.querySelector("strong")?.textContent)
          || "Choisir une IA";
    let label = modelName;
    let details = modelName;
    if(el("teamModeBtn").classList.contains("active")){
      const count = el("teamModelSelect").selectedOptions.length;
      label = count ? `Équipe IA · ${count}` : "Équipe IA";
      details = label;
    }else if(mode === "web"){
      label = "Recherche web";
      details = label;
    }else if(mode === "deep"){
      label = "Réponse détaillée";
      details = label;
    }else if(mode === "image"){
      const size = el("imageSizeSelect").value.replace("x", "×");
      label = `Image ${size}`;
      details = `Générer une image · ${size}`;
    }else if(mode === "video"){
      const resolution = el("videoResolution").value;
      const duration = el("videoDuration").value;
      label = `Vidéo ${resolution} · ${duration}s`;
      details = `Générer une vidéo · ${el("videoAspectRatio").value} · ${resolution} · ${duration} secondes`;
    }else if(mode === "audio"){
      const style = el("audioStyle").selectedOptions[0]?.textContent.trim();
      const instrumental = el("audioInstrumental").checked;
      label = `Audio${style ? ` · ${style}` : ""}${instrumental ? " · instrumental" : ""}`;
      details = `Générer un audio${style ? ` · ${style}` : ""}${instrumental ? " · instrumental" : ""}`;
    }
    if(state.sending) label = "Envoi…";
    labelElement.textContent = label;
    button.title = state.sending ? "Envoi en cours…" : `Envoyer avec ${details}`;
    button.setAttribute("aria-label", button.title);
  }
  sortModelsForTask(el("modelTaskCategory").value);
  renderModelPickerState();
  renderSendButton();

  function renderConvoList(){
    const list = el("convoList");
    list.innerHTML = "";
    const persona = activePersona();
    const title = el("conversationsSectionTitle");
    if(title) title.firstChild.textContent = state.showArchived ? "Archives" : (persona ? `Conversations · ${persona.name}` : "Conversations");
    const savedConversations = state.sidebarConversations;
    const folderSelect = el("folderFilter");
    const selectedFolder = state.selectedFolder;
    folderSelect.replaceChildren(new Option("Tous les dossiers", ""));
    state.conversationFolders.forEach(folder => folderSelect.add(new Option(folder, folder)));
    folderSelect.value = selectedFolder;
    if(savedConversations.length === 0){
      list.innerHTML = `<div class="history-empty">${state.conversationSearch ? "Aucun résultat" : state.showArchived ? "Aucune conversation archivée" : "Aucune conversation pour ce persona"}</div>`;
      return;
    }
    savedConversations.forEach(c => {
      const convoKey = conversationKey(c);
      const div = document.createElement("div");
      div.className = "item" + (convoKey === state.activeConvoId ? " active" : "");
      const copy = document.createElement("div");
      copy.className = "conversation-copy";
      const label = document.createElement("button");
      label.type = "button";
      label.className = "conversation-open";
      label.textContent = c.title;
      copy.appendChild(label);
      if(c.folder){
        const folderLabel = document.createElement("span");
        folderLabel.className = "conversation-folder-label";
        folderLabel.textContent = c.folder;
        copy.appendChild(folderLabel);
      }
      div.appendChild(copy);

      const actions = document.createElement("div");
      actions.className = "conversation-actions";
      if(c.localOnly && state.isAuthenticated){
        const syncButton = document.createElement("button");
        syncButton.type = "button";
        syncButton.className = "conversation-action";
        syncButton.title = c.syncedConversationId ? "Mettre à jour la copie du compte" : "Copier dans mon compte";
        syncButton.setAttribute("aria-label", syncButton.title);
        syncButton.textContent = "↥";
        syncButton.addEventListener("click", event => {
          event.stopPropagation();
          syncLocalConversation(c);
        });
        actions.appendChild(syncButton);
      }
      const archiveButton = document.createElement("button");
      archiveButton.type = "button";
      archiveButton.className = "conversation-action";
      archiveButton.title = c.isArchived ? "Désarchiver" : "Archiver";
      archiveButton.setAttribute("aria-label", archiveButton.title);
      archiveButton.innerHTML = c.isArchived
        ? '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M3 4h18v4H3zM5 8v12h14V8M12 12v5m-3-3 3 3 3-3"></path></svg>'
        : '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M3 4h18v4H3zM5 8v12h14V8M9 12h6"></path></svg>';
      archiveButton.addEventListener("click", event => {
        event.stopPropagation();
        updateConversation(c, { is_archived: !c.isArchived });
      });
      const folderButton = document.createElement("button");
      folderButton.type = "button";
      folderButton.className = "conversation-action";
      folderButton.title = "Classer dans un dossier";
      folderButton.setAttribute("aria-label", folderButton.title);
      folderButton.innerHTML = '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M3 6h7l2 2h9v11H3z"></path></svg>';
      folderButton.addEventListener("click", event => {
        event.stopPropagation();
        const folder = window.prompt("Nom du dossier (vide pour retirer)", c.folder || "");
        if(folder === null) return;
        updateConversation(c, { folder: folder.trim().slice(0, 80) });
      });
      const deleteButton = document.createElement("button");
      deleteButton.type = "button";
      deleteButton.className = "conversation-action conversation-delete";
      deleteButton.title = "Supprimer";
      deleteButton.setAttribute("aria-label", `Supprimer ${c.title}`);
      deleteButton.textContent = "×";
      deleteButton.addEventListener("click", event => deleteConversation(c, event));
      actions.append(archiveButton, folderButton, deleteButton);
      div.appendChild(actions);

      label.addEventListener("click", async () => {
        state.temporaryChat = false;
        state.activePersonaId = c.personaId;
        state.activeConvoId = convoKey;
        if(c.localOnly && c.localModel?.modelId){
          state.modelId = `local:${c.localModel.modelId}`;
        }else if(isLocalModelSelected()){
          state.modelId = chatConfig.modelId || "auto";
        }
        renderAll();  // affichage immédiat de la sélection, même avant le chargement
        if(c.id && c.messages === null){
          await loadConversationMessages(c.id);
        }
      });
      list.appendChild(div);
    });
  }

  async function updateConversation(conversation, changes){
    if(conversation.localOnly){
      if(Object.hasOwn(changes, "folder")) conversation.folder = changes.folder;
      if(Object.hasOwn(changes, "is_archived")) conversation.isArchived = Boolean(changes.is_archived);
      persistLocalConversations();
      await loadConversationFolders();
      await loadConversationHistory();
      renderAll();
      return;
    }
    if(!conversation.id) return;
    try{
      const response = await fetch(`/chat/api/conversations/${conversation.id}/`, {
        method: "PATCH",
        headers: {
          "Content-Type": "application/json",
          "X-CSRFToken": document.querySelector("[name=csrfmiddlewaretoken]").value
        },
        body: JSON.stringify(changes)
      });
      const data = await response.json();
      if(!response.ok) throw new Error(data.error?.message || "Modification impossible.");
      conversation.folder = data.folder || "";
      conversation.isArchived = Boolean(data.is_archived);
      await loadConversationFolders();
      await loadConversationHistory();
      renderAll();
    }catch(error){
      console.error("Erreur mise à jour conversation", error);
    }
  }

  async function deleteConversation(conversation, event){
    event.stopPropagation();
    if(conversation.localOnly){
      const message = conversation.syncedConversationId
        ? "Supprimer uniquement la copie locale de « " + conversation.title + " » ? La copie synchronisée sur le compte restera disponible."
        : `Supprimer « ${conversation.title} » de ce navigateur ?`;
      if(!window.confirm(message)) return;
      state.conversations = state.conversations.filter(item => item.localId !== conversation.localId);
      persistLocalConversations();
      if(state.activeConvoId === conversationKey(conversation)){
        state.activeConvoId = null;
        await newConversation(state.activePersonaId, state.temporaryChat);
      }
      await loadConversationHistory();
      renderAll();
      return;
    }
    if(!conversation.id || !window.confirm(`Supprimer « ${conversation.title} » ?`)) return;
    const response = await fetch(`/chat/api/conversations/${conversation.id}/delete/`, {
      method: "DELETE",
      headers: { "X-CSRFToken": document.querySelector("[name=csrfmiddlewaretoken]").value }
    });
    if(!response.ok) return;
    state.conversations = state.conversations.filter(item => item.id !== conversation.id);
    if(state.activeConvoId === conversation.id){
      state.activeConvoId = null;
      await newConversation(state.activePersonaId, state.temporaryChat);
    }
    await loadConversationFolders();
    await loadConversationHistory();
    renderAll();
  }

  async function syncLocalConversation(conversation){
    if(state.sending || !conversation.localOnly || !state.isAuthenticated) return;
    const messages = conversation.messages
      .filter(message => !message.streaming && ["user", "bot"].includes(message.role))
      .map(message => ({ role: message.role === "bot" ? "assistant" : "user", content: message.content }));
    const unsyncedMessages = messages.slice(conversation.syncedMessageCount || 0);
    if(!unsyncedMessages.length){
      showChatNotice("Cette conversation est déjà synchronisée avec le compte.");
      return;
    }
    try{
      const response = await fetch("/chat/api/conversations/sync-local/", {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          "X-CSRFToken": document.querySelector("[name=csrfmiddlewaretoken]").value
        },
        body: JSON.stringify({
          conversation_id: conversation.syncedConversationId,
          title: conversation.title,
          persona_id: typeof conversation.personaId === "number" ? conversation.personaId : null,
          messages: unsyncedMessages
        })
      });
      const data = await response.json();
      if(!response.ok) throw new Error(data.error?.message || "Synchronisation impossible.");
      conversation.syncedConversationId = data.conversation_id;
      conversation.syncedMessageCount = messages.length;
      persistLocalConversations();
      await loadConversationHistory();
      showChatNotice("Copie de la conversation mise à jour dans ton compte.");
      renderAll();
    }catch(error){
      showChatNotice(error.message);
    }
  }

  const personaSwitchTimers = new Map();
  const personaSaveStates = new Map();
  function saveConversationPersona(convo, personaId, previousPersonaId){
    if(convo.localOnly){
      persistLocalConversations();
      return;
    }
    if(!state.isAuthenticated || !convo.id) return;
    const conversationId = convo.id;
    let saveState = personaSaveStates.get(conversationId);
    if(!saveState){
      saveState = {
        convo,
        personaId,
        persistedPersonaId: previousPersonaId,
        version: 0,
        saving: false,
        timer: null
      };
      personaSaveStates.set(conversationId, saveState);
    }
    saveState.personaId = personaId;
    saveState.version += 1;
    if(saveState.timer) clearTimeout(saveState.timer);
    saveState.timer = setTimeout(() => {
      saveState.timer = null;
      personaSwitchTimers.delete(conversationId);
      flushConversationPersona(conversationId, saveState);
    }, 150);
    personaSwitchTimers.set(conversationId, saveState.timer);
  }

  async function flushConversationPersona(conversationId, saveState){
    if(saveState.saving || saveState.timer) return;
    saveState.saving = true;
    const version = saveState.version;
    const personaId = saveState.personaId;
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), 10000);
    try{
      const response = await fetch(`/chat/api/conversations/${conversationId}/`, {
        method: "PATCH",
        headers: {
          "Content-Type": "application/json",
          "X-CSRFToken": document.querySelector("[name=csrfmiddlewaretoken]").value
        },
        body: JSON.stringify({ persona_id: personaId }),
        signal: controller.signal
      });
      if(!response.ok) throw new Error("Erreur sauvegarde persona " + response.status);
      saveState.persistedPersonaId = personaId;
    }catch(err){
      console.error(err);
      if(saveState.version === version && saveState.convo.personaId === personaId){
        saveState.convo.personaId = saveState.persistedPersonaId;
        if(activeConvo() === saveState.convo) renderAll();
      }
    }finally{
      clearTimeout(timeout);
      saveState.saving = false;
      if(saveState.timer) return;
      if(saveState.version !== version){
        flushConversationPersona(conversationId, saveState);
      }else{
        personaSaveStates.delete(conversationId);
      }
    }
  }

  async function setActivePersona(personaId){
    const persona = state.personas.find(p => String(p.id) === String(personaId));
    if(!persona) return;
    const convo = activeConvo();
    if(convo && String(convo.personaId) === String(persona.id)) return;
    state.activePersonaId = persona.id;
    const existing = state.temporaryChat ? null : state.conversations.find(item =>
      !item.temporary && String(item.personaId) === String(persona.id)
    );
    if(existing){
      state.activeConvoId = conversationKey(existing);
      state.selectedWorkspaceId = existing.workspaceId;
      if(existing.id && existing.messages === null){
        await loadConversationMessages(existing.id);
      }
    }else{
      await newConversation(persona.id, state.temporaryChat);
    }
    await loadConversationHistory();
    renderAll();
  }

  function renderPersonaList(){
    const select = el("personaTopSelect");
    if(!select) return;
    select.replaceChildren();
    const selectedPersona = activePersona();
    state.personas.forEach(p => {
      select.add(new Option(p.name, String(p.id)));
    });
    if(selectedPersona) select.value = String(selectedPersona.id);
    const activeName = el("activePersonaName");
    if(activeName) activeName.textContent = selectedPersona?.name || "Choisir un persona";
    const renameButton = el("renamePersonaBtn");
    if(renameButton){
      renameButton.disabled = !state.isAuthenticated || typeof selectedPersona?.id !== "number";
    }
    const deleteButton = el("deletePersonaBtn");
    if(deleteButton){
      deleteButton.disabled = !state.isAuthenticated || state.personas.length <= 1 || typeof selectedPersona?.id !== "number";
    }
  }

  function renderPersonaEditor(){
    const p = activePersona();
    const select = el("personaSelect");
    if(select){
      select.innerHTML = state.personas.map(persona => `<option value="${persona.id}">${escapeHtml(persona.name)}</option>`).join("");
    }
    if(!p) return;
    if(select) select.value = String(p.id);
    if(typeof window.AI2AIAppearance?.applyPersona === "function"){
      window.AI2AIAppearance.applyPersona(p);
    }
    if(state.temperatureOverride === null){
      const temperature = Number(p.temp ?? 0.7);
      el("temperatureControl").value = String(temperature);
      el("temperatureValue").value = temperature.toFixed(1).replace(".", ",");
    }
    const personaName = el("chatPersonaName");
    if(personaName) personaName.textContent = p.name;
  }

function renderHeader(){

  const convo = activeConvo();
  const temporaryButton = el("temporaryChatBtn");
  temporaryButton.classList.toggle("active", Boolean(convo?.temporary));
  temporaryButton.setAttribute("aria-pressed", String(Boolean(convo?.temporary)));
  el("temporaryBadge").hidden = !convo?.temporary;
  const memoryButton = el("memorySuggestBtn");
  if(memoryButton){
    memoryButton.disabled = !convo || convo.localOnly || !convo.messages?.some(message => message.role === "user") || state.sending;
    memoryButton.title = convo?.localOnly
      ? "La suggestion de mémoire enverrait la conversation au serveur et est désactivée en mode local."
      : "Proposer une mémoire pour ce persona";
  }

  const appearance =
    window.AI2AIAppearance
      ? window.AI2AIAppearance.get()
      : {
          botName: "2AI",
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
    div.dataset.messageKey = m.clientId || (m.id ? `message-${m.id}` : `draft-${messageIndex}`);

    if(m.role === "bot"){
      div.classList.add("has-actions");
      if(m.streaming) div.classList.add("streaming");
    }

    const contentDiv = document.createElement("div");
    contentDiv.className = "msg-content";
    if(m.role === "bot"){
      contentDiv.innerHTML = DOMPurify.sanitize(marked.parse(m.content));
    } else {
      contentDiv.textContent = m.content;
    }
    div.appendChild(contentDiv);
    if(m.mediaUrl){
      const media = document.createElement("div");
      media.className = "msg-media";
      const mediaElement = document.createElement(m.mediaKind === "video" ? "video" : m.mediaKind === "audio" ? "audio" : "img");
      mediaElement.src = m.mediaUrl;
      if(m.mediaKind === "video" || m.mediaKind === "audio"){
        mediaElement.controls = true;
        mediaElement.preload = "metadata";
      }else{
        mediaElement.alt = "Image générée";
        mediaElement.loading = "lazy";
      }
      media.appendChild(mediaElement);
      div.appendChild(media);
    }
    if(m.role === "bot" && Array.isArray(m.sources) && m.sources.length){
      const sources = document.createElement("div");
      sources.className = "message-sources";
      const label = document.createElement("span");
      label.textContent = "Sources";
      sources.appendChild(label);
      const list = document.createElement("ul");
      m.sources.slice(0, 5).forEach(source => {
        try{
          const url = new URL(source.link, window.location.origin);
          if(!["http:", "https:"].includes(url.protocol)) return;
          const item = document.createElement("li");
          const link = document.createElement("a");
          link.href = url.href;
          link.target = "_blank";
          link.rel = "noopener noreferrer";
          link.textContent = source.title || url.hostname;
          item.appendChild(link);
          if(source.snippet){
            const snippet = document.createElement("span");
            snippet.textContent = source.snippet;
            item.appendChild(snippet);
          }
          list.appendChild(item);
        }catch(_error){}
      });
      if(list.childElementCount){
        sources.appendChild(list);
        div.appendChild(sources);
      }
    }

    if(m.role === "user" && m.id && convo.id){
      const actions = document.createElement("div");
      actions.className = "response-actions";
      const editButton = document.createElement("button");
      editButton.type = "button";
      editButton.className = "response-action";
      editButton.textContent = "✎";
      editButton.title = "Modifier et régénérer";
      editButton.setAttribute("aria-label", "Modifier et régénérer ce message");
      editButton.addEventListener("click", () => {
        if(state.sending) return;
        const form = document.createElement("form");
        form.className = "message-edit-form";
        const textarea = document.createElement("textarea");
        textarea.className = "message-edit-input";
        textarea.value = m.content;
        textarea.setAttribute("aria-label", "Modifier le prompt");
        const controls = document.createElement("div");
        controls.className = "message-edit-controls";
        const submit = document.createElement("button");
        submit.type = "submit";
        submit.className = "message-edit-submit";
        submit.textContent = "Enregistrer et régénérer";
        const cancel = document.createElement("button");
        cancel.type = "button";
        cancel.className = "message-edit-cancel";
        cancel.textContent = "Annuler";
        cancel.addEventListener("click", renderAll);
        form.addEventListener("submit", event => {
          event.preventDefault();
          const updatedText = textarea.value.trim();
          if(!updatedText || updatedText === m.content) return;
          sendMessage(updatedText, false, { messageId: m.id, messageIndex });
        });
        controls.append(submit, cancel);
        form.append(textarea, controls);
        div.replaceChild(form, contentDiv);
        textarea.focus();
        textarea.setSelectionRange(textarea.value.length, textarea.value.length);
      });
      actions.appendChild(editButton);
      div.appendChild(actions);
    }

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
      const likeBtn = addAction("like", "J'aime cette réponse", () => saveRoutingFeedback(m, "positive", likeBtn, dislikeBtn));
      const dislikeBtn = addAction("dislike", "Je n'aime pas cette réponse", () => saveRoutingFeedback(m, "negative", dislikeBtn, likeBtn));
      likeBtn.classList.toggle("active", m.feedback === "positive");
      dislikeBtn.classList.toggle("active", m.feedback === "negative");
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

async function saveRoutingFeedback(message, rating, selectedButton, otherButton){
  const previousRating = message.feedback || null;
  const nextRating = previousRating === rating ? null : rating;
  const updateButtons = value => {
    selectedButton.classList.toggle("active", value === rating);
    otherButton.classList.toggle("active", value && value !== rating);
  };
  message.feedback = nextRating;
  updateButtons(nextRating);
  if(!message.routingObservationId || !message.id) return;
  try{
    const response = await fetch(`/chat/api/messages/${message.id}/feedback/`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "X-CSRFToken": document.querySelector("[name=csrfmiddlewaretoken]").value
      },
      body: JSON.stringify({ rating: nextRating })
    });
    const data = await response.json();
    if(!response.ok) throw new Error(data.error?.message || "Enregistrement du feedback impossible.");
    message.feedback = data.rating;
    updateButtons(data.rating);
  }catch(error){
    message.feedback = previousRating;
    updateButtons(previousRating);
    const notice = document.getElementById("creditNotice");
    if(notice){
      notice.textContent = error.message;
      notice.classList.add("show");
      window.setTimeout(() => notice.classList.remove("show"), 3200);
    }
  }
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
    if(sendButton) sendButton.disabled = (state.isAuthenticated && state.credits <= 0 && !isLocalModelSelected()) || state.sending;
    renderSendButton();
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
    renderWorkspaceSelector();
    renderMessages();
    renderCredits();
    const conversation = activeConvo();
    const localConversation = Boolean(conversation?.localOnly && !conversation.temporary);
    el("localChatNotice").hidden = !localConversation;
    el("syncLocalConversationBtn").hidden = !localConversation || !state.isAuthenticated;
    if(localConversation){
      el("localChatNotice").firstChild.textContent = conversation.syncedConversationId
        ? "Original dans le navigateur, avec une copie sur le compte. "
        : "Cette conversation reste dans le navigateur. ";
      el("syncLocalConversationBtn").textContent = conversation.syncedConversationId
        ? "Mettre à jour la copie du compte"
        : "Copier dans mon compte";
    }
    renderModelPickerState();
    renderSendButton();
  }

  function renderWorkspaceSelector(){
    const select = el("workspaceSelect");
    if(!select) return;
    const convo = activeConvo();
    const localOnly = Boolean(convo?.localOnly);
    const workspaceId = convo ? convo.workspaceId : state.selectedWorkspaceId;
    const emptyOption = new Option(
      state.workspaces.length ? "Désactivés" : "Aucun espace disponible",
      ""
    );
    emptyOption.disabled = state.workspaces.length === 0;
    select.replaceChildren(emptyOption);
    state.workspaces.forEach(workspace => {
      select.add(new Option(workspace.title, String(workspace.id)));
    });
    select.value = workspaceId ? String(workspaceId) : "";
    select.disabled = localOnly;
    el("createWorkspaceBtn").disabled = localOnly;
    el("workspaceOpen").hidden = localOnly;
    const openLink = el("workspaceOpen");
    if(openLink){
      openLink.href = workspaceId ? `/bara/${workspaceId}/` : "/dap/";
      openLink.title = workspaceId ? "Ouvrir les fichiers dans Bara" : "Ouvrir Bara";
    }
  }

  function createStreamingRenderer(convo, message){
    let scheduled = false;
    return () => {
      if(scheduled) return;
      scheduled = true;
      window.requestAnimationFrame(() => {
        scheduled = false;
        if(activeConvo() !== convo) return;
        const wrap = el("messages");
        const messageElement = Array.from(wrap.querySelectorAll(".msg.bot"))
          .find(element => element.dataset.messageKey === message.clientId);
        const content = messageElement?.querySelector(".msg-content");
        if(!content) return;
        content.textContent = message.content;
        wrap.scrollTop = wrap.scrollHeight;
      });
    };
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

  async function callBackend(persona, userText, convo, onDelta, editMessageId){
    const mode = document.querySelector(".tool-btn[data-mode].active")?.dataset.mode || "chat";
    const isMediaMode = ["image", "video", "audio"].includes(mode);
    const endpoint = isMediaMode ? "/chat/api/media/generate/" : "/chat/api/send/";
    const res = await fetch(endpoint, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "Accept": "text/event-stream",
        "X-CSRFToken": document.querySelector("[name=csrfmiddlewaretoken]").value
      },
      body: JSON.stringify({
        conversation_id: convo.id,
        workspace_id: convo.workspaceId || null,
        edit_message_id: editMessageId,
        temporary: Boolean(convo.temporary),
        history: convo.temporary ? convo.messages
          .filter(message => !message.streaming)
          .slice(0, -1)
          .map(message => ({ role: message.role === "bot" ? "assistant" : message.role, content: message.content })) : [],
        content: userText,
        kind: isMediaMode ? mode : null,
        generation_options: isMediaMode ? collectMediaOptions(mode) : null,
        model_id: state.modelId,
        persona_id: typeof convo.personaId === "number" ? convo.personaId : null,
        system_prompt: persona.prompt,
        temperature: state.temperatureOverride ?? Number(el("temperatureControl").value),
        top_p: Number(el("topPControl").value),
        max_tokens: Number(el("maxTokensControl").value),
        mode: document.querySelector(".tool-btn[data-mode].active")?.dataset.mode || "chat",
        team_mode: el("teamModeBtn").classList.contains("active"),
        team_strategy: el("teamStrategySelect").value,
        team_model_ids: Array.from(el("teamModelSelect").selectedOptions).map(option => option.value)
      })
    });
    if(!res.ok){
      const data = await res.json().catch(() => ({}));
      throw new Error((data.error && data.error.message) || "Erreur serveur " + res.status);
    }
    if(res.headers.get("Content-Type")?.includes("application/json")){
      const data = await res.json();
      if(data.reply) onDelta(data.reply);
      return data;
    }

    const reader = res.body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";
    let result = null;
    const handleEvent = eventText => {
      const dataLine = eventText.split("\n").find(line => line.startsWith("data:"));
      if(!dataLine) return;
      const event = JSON.parse(dataLine.slice(5).trim());
      if(event.type === "delta") onDelta(event.content || "");
      if(event.type === "done") result = event;
      if(event.type === "error") throw new Error(event.error?.message || "La génération a échoué.");
    };

    while(true){
      const { value, done } = await reader.read();
      buffer += decoder.decode(value || new Uint8Array(), { stream: !done });
      let boundary = buffer.indexOf("\n\n");
      while(boundary !== -1){
        handleEvent(buffer.slice(0, boundary));
        buffer = buffer.slice(boundary + 2);
        boundary = buffer.indexOf("\n\n");
      }
      if(done) break;
    }
    if(buffer.trim()) handleEvent(buffer);
    if(!result) throw new Error("Le flux s’est terminé avant la réponse finale.");
    return result;
  }

  function normalizeLocalBaseUrl(value){
    let url;
    try{
      url = new URL(value);
    }catch(_error){
      throw new Error("L’adresse de l’API locale est invalide.");
    }
    if(!["http:", "https:"].includes(url.protocol) || url.username || url.password || url.search || url.hash){
      throw new Error("L’adresse locale doit être une URL HTTP(S) sans identifiant ni paramètre.");
    }
    const path = url.pathname.replace(/\/+$/, "");
    url.pathname = path.endsWith("/v1") ? path : `${path}/v1`;
    return url.toString().replace(/\/+$/, "");
  }

  async function callLocalModel(persona, convo, onDelta){
    const localModel = convo.localModel || state.localAI;
    if(!localModel?.modelId || !localModel.baseUrl) throw new Error("Configure d’abord ton modèle local.");
    const baseUrl = normalizeLocalBaseUrl(localModel.baseUrl);
    const messages = convo.messages
      .filter(message => !message.streaming && ["user", "bot"].includes(message.role))
      .slice(-40)
      .map(message => ({ role: message.role === "bot" ? "assistant" : "user", content: message.content }));
    if(persona?.prompt) messages.unshift({ role: "system", content: persona.prompt });

    let response;
    try{
      response = await fetch(`${baseUrl}/chat/completions`, {
        method: "POST",
        headers: { "Content-Type": "application/json", "Accept": "text/event-stream, application/json" },
        body: JSON.stringify({
          model: localModel.modelId,
          messages,
          temperature: state.temperatureOverride ?? Number(el("temperatureControl").value),
          top_p: Number(el("topPControl").value),
          max_tokens: Number(el("maxTokensControl").value),
          stream: true
        })
      });
    }catch(error){
      if(error instanceof TypeError){
        throw new Error("Connexion locale bloquée. Vérifie que le serveur IA tourne et autorise ce site avec CORS (OLLAMA_ORIGINS pour Ollama).");
      }
      throw error;
    }
    if(!response.ok){
      const detail = (await response.text()).slice(0, 300);
      throw new Error(`Le modèle local a renvoyé une erreur (${response.status})${detail ? ` : ${detail}` : "."}`);
    }
    if(response.headers.get("Content-Type")?.includes("application/json")){
      const data = await response.json();
      const reply = data.choices?.[0]?.message?.content;
      if(typeof reply !== "string") throw new Error("Le serveur local a renvoyé une réponse non reconnue.");
      onDelta(reply);
      return {
        reply,
        prompt_tokens: data.usage?.prompt_tokens,
        completion_tokens: data.usage?.completion_tokens
      };
    }
    if(!response.body) throw new Error("Le serveur local n’a pas fourni de flux de réponse.");

    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";
    let reply = "";
    let usage = {};
    const handleEvent = eventText => {
      const dataLine = eventText.split("\n").find(line => line.startsWith("data:"));
      if(!dataLine) return;
      const payload = dataLine.slice(5).trim();
      if(!payload || payload === "[DONE]") return;
      const event = JSON.parse(payload);
      if(event.error) throw new Error(event.error.message || "Erreur du modèle local.");
      const delta = event.choices?.[0]?.delta?.content;
      if(typeof delta === "string" && delta){
        reply += delta;
        onDelta(delta);
      }
      if(event.usage) usage = event.usage;
    };

    while(true){
      const { value, done } = await reader.read();
      buffer = (buffer + decoder.decode(value || new Uint8Array(), { stream: !done })).replace(/\r\n/g, "\n");
      let boundary = buffer.indexOf("\n\n");
      while(boundary !== -1){
        handleEvent(buffer.slice(0, boundary));
        buffer = buffer.slice(boundary + 2);
        boundary = buffer.indexOf("\n\n");
      }
      if(done) break;
    }
    if(buffer.trim()) handleEvent(buffer);
    if(!reply) throw new Error("Le modèle local a terminé sans renvoyer de texte.");
    return { reply, prompt_tokens: usage.prompt_tokens, completion_tokens: usage.completion_tokens };
  }

  async function sendMessage(text, reuseLastUser = false, editTarget = null){
    const localModelSelected = isLocalModelSelected();
    if(state.sending || (localModelSelected ? !state.isAuthenticated : !ensureCreditAccess())) return;
    state.sending = true;
    renderCredits();
    let convo = activeConvo();
    if(!convo){
      convo = await newConversation(state.activePersonaId, state.temporaryChat);
    }
    if(!convo){
      state.sending = false;
      renderCredits();
      return;
    }
    const persona = state.personas.find(p => p.id === convo.personaId) || state.personas[0];

    let previousMessages = null;
    let userMessage;
    if(editTarget){
      const originalMessage = convo.messages[editTarget.messageIndex];
      if(!convo.id || !originalMessage || originalMessage.id !== editTarget.messageId || originalMessage.role !== "user"){
        state.sending = false;
        renderCredits();
        return;
      }
      previousMessages = convo.messages.slice();
      convo.messages = convo.messages.slice(0, editTarget.messageIndex);
      userMessage = { ...originalMessage, content: text, tokens: 0 };
      convo.messages.push(userMessage);
    }else if(!reuseLastUser){
      userMessage = { role: "user", content: text, tokens: 0, createdAt: new Date().toISOString() };
      convo.messages.push(userMessage);
    }else{
      userMessage = convo.messages[convo.messages.length - 1];
    }
    const streamingMessage = {
      role: "bot",
      content: "",
      tokens: 0,
      createdAt: new Date().toISOString(),
      clientId: uid("stream"),
      streaming: true
    };
    const renderStreamingMessage = createStreamingRenderer(convo, streamingMessage);
    convo.messages.push(streamingMessage);
    renderAll();

    try{
      let data;
      const selectedMode = document.querySelector(".tool-btn[data-mode].active")?.dataset.mode || "chat";
      if(localModelSelected){
        if(selectedMode !== "chat" || el("teamModeBtn").classList.contains("active") || convo.workspaceId){
          throw new Error("L’IA locale est disponible en chat simple, sans recherche web, équipe IA ni fichiers Bara.");
        }
        data = await callLocalModel(persona, convo, chunk => {
          streamingMessage.content += chunk;
          renderStreamingMessage();
        });
      }else if(state.hasActiveModels || ["image", "video", "audio"].includes(selectedMode)){
        data = await callBackend(persona, text, convo, chunk => {
          streamingMessage.content += chunk;
          renderStreamingMessage();
        }, editTarget?.messageId);
        if(convo.id === null && data.conversation_id){
          convo.id = data.conversation_id;
          state.activeConvoId = conversationKey(convo);
        }
        if(data.title) convo.title = data.title;
        if(data.user_message_id) userMessage.id = data.user_message_id;
      } else {
        const simulatedReply = simulateReply(persona, text);
        const chunks = simulatedReply.match(/\S+\s*/g) || [simulatedReply];
        for(const chunk of chunks){
          streamingMessage.content += chunk;
          renderStreamingMessage();
          await new Promise(resolve => setTimeout(resolve, 35));
        }
        data = { reply: simulatedReply, remaining_balance: state.credits };
      }
      const replyText = data.reply || "(réponse vide)";
      streamingMessage.mediaUrl = data.media_url || null;
      streamingMessage.mediaKind = data.media_kind || "image";
      streamingMessage.sources = Array.isArray(data.sources) ? data.sources : [];
      const inputTokens = data.prompt_tokens ?? estimateTokens(text);
      const replyTokens = data.completion_tokens ?? estimateTokens(replyText);
      userMessage.tokens = inputTokens;
      streamingMessage.content = replyText;
      if(data.assistant_message_id) streamingMessage.id = data.assistant_message_id;
      streamingMessage.tokens = replyTokens;
      streamingMessage.routingObservationId = data.routing_observation_id || null;
      streamingMessage.feedback = data.routing_feedback || null;
      streamingMessage.streaming = false;
      if(convo.localOnly && !convo.temporary){
        if(convo.title === "Nouvelle conversation") convo.title = text.slice(0, 200);
        persistLocalConversations();
      }
      const newCredits = Number(data.remaining_balance);
        if (Number.isFinite(newCredits)) {
            state.credits = newCredits;
        }
      state.usageLog.unshift({ label: "Message (" + persona.name + ")", tokens: inputTokens });
      state.usageLog.unshift({ label: "Réponse (" + persona.name + ")", tokens: replyTokens });
      renderAll();
    }catch(err){
      if(previousMessages){
        convo.messages = previousMessages;
        convo.messages.push({ role: "bot", content: "⚠ " + err.message, tokens: 0 });
      }else{
        streamingMessage.content = (streamingMessage.content ? streamingMessage.content + "\n\n" : "") + "⚠ " + err.message;
        streamingMessage.streaming = false;
      }
      if(convo.localOnly && !convo.temporary) persistLocalConversations();
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
    newConversation(persona.id, state.temporaryChat);
    renderAll();
  });
  let conversationSearchTimer = null;
  el("conversationSearch").addEventListener("input", event => {
    state.conversationSearch = event.currentTarget.value.trim();
    window.clearTimeout(conversationSearchTimer);
    conversationSearchTimer = window.setTimeout(async () => {
      await loadConversationHistory();
      renderAll();
    }, 250);
  });
  el("folderFilter").addEventListener("change", async event => {
    state.selectedFolder = event.currentTarget.value;
    await loadConversationHistory();
    renderAll();
  });
  el("archiveFilterBtn").addEventListener("click", async () => {
    state.showArchived = !state.showArchived;
    el("archiveFilterBtn").classList.toggle("active", state.showArchived);
    el("archiveFilterBtn").setAttribute("aria-pressed", String(state.showArchived));
    await loadConversationHistory();
    renderAll();
  });
  el("temporaryChatBtn").addEventListener("click", () => {
    state.temporaryChat = !state.temporaryChat;
    if(!state.temporaryChat){
      state.conversations = state.conversations.filter(convo => !convo.temporary);
    }
    const persona = activePersona();
    newConversation(persona?.id, state.temporaryChat);
    renderAll();
  });
  el("generationControlsBtn").addEventListener("click", () => {
    const panel = el("generationControlsPanel");
    panel.hidden = !panel.hidden;
    el("generationControlsBtn").setAttribute("aria-expanded", String(!panel.hidden));
  });
  el("temperatureControl").addEventListener("input", event => {
    state.temperatureOverride = Number(event.currentTarget.value);
    el("temperatureValue").value = state.temperatureOverride.toFixed(1).replace(".", ",");
  });
  el("topPControl").addEventListener("input", event => {
    el("topPValue").value = Number(event.currentTarget.value).toFixed(2).replace(".", ",");
  });
  el("customizeBtn").addEventListener("click", () => {
    window.location.href = el("customizeBtn").dataset.url;
  });
  const memoryDialog = el("memorySuggestionDialog");
  if(memoryDialog){
    const memoryText = el("memorySuggestionText");
    const memoryStatus = el("memorySuggestionStatus");
    const memorySave = el("saveMemorySuggestion");

    async function proposePersonaMemory(){
      const convo = activeConvo();
      const persona = activePersona();
        if(convo?.localOnly){
          showChatNotice("Cette action enverrait l’historique au serveur; elle est désactivée pour une conversation locale.");
          return;
        }
      if(!convo || !persona || !convo.messages?.some(message => message.role === "user")) return;
      memoryDialog.hidden = false;
      memoryText.value = "";
      memoryText.disabled = true;
      memorySave.disabled = true;
      el("regenerateMemorySuggestion").disabled = true;
      memoryStatus.textContent = "Analyse de la conversation…";
      const history = convo.messages
        .filter(message => !message.streaming && (message.role === "user" || message.role === "bot"))
        .map(message => ({ role: message.role === "bot" ? "assistant" : "user", content: message.content }));
      const requestBody = {
        model_id: state.modelId,
        history: convo.id && !convo.temporary ? undefined : history
      };
      if(convo.id && !convo.temporary) requestBody.conversation_id = convo.id;
      try{
        const response = await fetch(`/chat/api/personas/${persona.id}/suggest-memory/`, {
          method: "POST",
          headers: {
            "Content-Type": "application/json",
            "X-CSRFToken": document.querySelector("[name=csrfmiddlewaretoken]").value
          },
          body: JSON.stringify(requestBody)
        });
        const data = await response.json();
        if(!response.ok) throw new Error(data.error?.message || "Proposition de mémoire impossible.");
        const balance = Number(data.remaining_balance);
        if(Number.isFinite(balance)) state.credits = balance;
        memoryText.value = data.suggestion || "";
        memoryText.disabled = false;
        memorySave.disabled = !memoryText.value.trim();
        memoryStatus.textContent = memoryText.value.trim()
          ? `Proposition prête avec ${data.model_id}. Modifie-la puis confirme pour enregistrer.`
          : "Aucune préférence durable explicite n’a été trouvée. Tu peux saisir la mémoire manuellement.";
        renderCredits();
        memoryText.focus();
      }catch(error){
        memoryText.disabled = false;
        memoryStatus.textContent = error.message;
      }finally{
        el("regenerateMemorySuggestion").disabled = false;
      }
    }

    el("memorySuggestBtn").addEventListener("click", proposePersonaMemory);
    el("regenerateMemorySuggestion").addEventListener("click", proposePersonaMemory);
    el("cancelMemorySuggestion").addEventListener("click", () => {
      memoryDialog.hidden = true;
    });
    el("closeMemorySuggestion").addEventListener("click", () => {
      memoryDialog.hidden = true;
    });
    memoryDialog.addEventListener("click", event => {
      if(event.target === memoryDialog) memoryDialog.hidden = true;
    });
    memoryText.addEventListener("input", () => {
      memorySave.disabled = !memoryText.value.trim();
    });
    memorySave.addEventListener("click", async () => {
      const persona = activePersona();
      if(!persona || !memoryText.value.trim()) return;
      memorySave.disabled = true;
      memoryStatus.textContent = "Enregistrement…";
      try{
        const response = await fetch(`/chat/api/personas/${persona.id}/`, {
          method: "PATCH",
          headers: {
            "Content-Type": "application/json",
            "X-CSRFToken": document.querySelector("[name=csrfmiddlewaretoken]").value
          },
          body: JSON.stringify({ memory: memoryText.value.trim() })
        });
        const data = await response.json();
        if(!response.ok) throw new Error(data.error?.message || "Enregistrement impossible.");
        Object.assign(persona, data);
        memoryStatus.textContent = "Mémoire enregistrée pour ce persona.";
        window.setTimeout(() => { memoryDialog.hidden = true; }, 700);
      }catch(error){
        memoryStatus.textContent = error.message;
        memorySave.disabled = false;
      }
    });
  }
  const imageGallery = el("imageGallery");
  const imageGalleryStatus = el("imageGalleryStatus");
  const imageGalleryGrid = el("imageGalleryGrid");
  let imageGalleryKind = "image";
  async function openImageGallery(kind = "image"){
    imageGalleryKind = kind;
    const isVideo = kind === "video";
    const isAudio = kind === "audio";
    el("imageGalleryTitle").textContent = isVideo ? "Vidéos" : isAudio ? "Audio généré" : "Images générées";
    el("imageGalleryDescription").textContent = isVideo
      ? "Les vidéos enregistrées dans tes conversations"
      : isAudio ? "Les fichiers audio enregistrés dans tes conversations" : "Les images enregistrées dans tes conversations";
    document.querySelectorAll("[data-gallery-kind]").forEach(button => {
      const selected = button.dataset.galleryKind === kind;
      button.classList.toggle("active", selected);
      button.setAttribute("aria-selected", String(selected));
    });
    imageGallery.hidden = false;
    imageGalleryStatus.textContent = "Chargement…";
    imageGalleryGrid.replaceChildren();
    try{
      const response = await fetch(`/chat/api/media/gallery/?kind=${encodeURIComponent(kind)}`);
      if(!response.ok || !response.headers.get("Content-Type")?.includes("application/json")){
        throw new Error("Connecte-toi pour voir tes images générées.");
      }
      const images = await response.json();
      if(!images.length){
        imageGalleryStatus.textContent = isVideo
          ? "Aucune vidéo générée pour l’instant."
          : isAudio ? "Aucun audio généré pour l’instant." : "Aucune image générée pour l’instant.";
        return;
      }
      imageGalleryStatus.textContent = "";
      images.forEach(item => {
        const link = document.createElement("a");
        link.className = isVideo ? "image-gallery-item video-gallery-item" : isAudio ? "image-gallery-item audio-gallery-item" : "image-gallery-item";
        link.href = item.media_url;
        link.target = "_blank";
        link.rel = "noopener noreferrer";
        const media = document.createElement(isVideo ? "video" : isAudio ? "audio" : "img");
        media.src = item.media_url;
        if(isVideo || isAudio){
          media.controls = true;
          media.preload = "metadata";
          media.setAttribute("aria-label", item.prompt || (isVideo ? "Vidéo générée" : "Audio généré"));
          media.addEventListener("click", event => event.stopPropagation());
        }else{
          media.alt = item.prompt || "Image générée";
          media.loading = "lazy";
        }
        const prompt = document.createElement("strong");
        prompt.textContent = item.prompt || "Image générée";
        const conversation = document.createElement("span");
        conversation.textContent = item.conversation_title || "Conversation";
        link.append(media, prompt, conversation);
        imageGalleryGrid.appendChild(link);
      });
    }catch(error){
      imageGalleryStatus.textContent = error.message || "Chargement de la galerie impossible.";
    }
  }
  el("openImageGalleryBtn").addEventListener("click", () => openImageGallery("image"));
  el("openVideoGalleryBtn").addEventListener("click", () => openImageGallery("video"));
  el("openAudioGalleryBtn").addEventListener("click", () => openImageGallery("audio"));
  document.querySelectorAll("[data-gallery-kind]").forEach(button => {
    button.addEventListener("click", () => openImageGallery(button.dataset.galleryKind));
  });
  el("closeImageGallery").addEventListener("click", () => {
    imageGallery.hidden = true;
  });
  imageGallery.addEventListener("click", event => {
    if(event.target === imageGallery) imageGallery.hidden = true;
  });
  document.addEventListener("keydown", event => {
    if(event.key === "Escape" && !imageGallery.hidden) imageGallery.hidden = true;
  });
  el("createWorkspaceBtn").addEventListener("click", () => {
    window.location.href = "/dap/";
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
  el("personaTopSelect").addEventListener("change", event => {
    setActivePersona(event.currentTarget.value);
  });
  el("workspaceSelect").addEventListener("change", async event => {
    const workspaceId = event.currentTarget.value || null;
    const convo = activeConvo();
    const previousWorkspaceId = convo ? convo.workspaceId : state.selectedWorkspaceId;
    if(String(workspaceId || "") === String(previousWorkspaceId || "")) return;

    state.selectedWorkspaceId = workspaceId;
    if(convo) convo.workspaceId = workspaceId;
    renderWorkspaceSelector();

    if(convo?.id && !convo.temporary){
      try{
        const response = await fetch(`/chat/api/conversations/${convo.id}/`, {
          method: "PATCH",
          headers: {
            "Content-Type": "application/json",
            "X-CSRFToken": document.querySelector("[name=csrfmiddlewaretoken]").value
          },
          body: JSON.stringify({ workspace_id: workspaceId })
        });
        if(!response.ok) throw new Error("Enregistrement du workspace impossible.");
      }catch(error){
        convo.workspaceId = previousWorkspaceId;
        state.selectedWorkspaceId = previousWorkspaceId;
        renderWorkspaceSelector();
        const notice = el("creditNotice");
        notice.textContent = error.message;
        notice.classList.add("show");
        window.setTimeout(() => notice.classList.remove("show"), 3000);
      }
    }
  });
  el("deletePersonaBtn").addEventListener("click", async () => {
    const persona = activePersona();
    if(!persona || state.personas.length <= 1 || typeof persona.id !== "number") return;
    const confirmed = window.confirm(
      `Supprimer le persona « ${persona.name} » ? Les conversations associées resteront dans l’historique, sans ce persona.`
    );
    if(!confirmed) return;
    try{
      const response = await fetch(`/chat/api/personas/${persona.id}/`, {
        method: "DELETE",
        headers: { "X-CSRFToken": document.querySelector("[name=csrfmiddlewaretoken]").value }
      });
      if(!response.ok) throw new Error("Suppression du persona impossible.");
      state.personas = state.personas.filter(item => item.id !== persona.id);
      const nextPersona = state.personas[0];
      state.activePersonaId = nextPersona.id;
      await setActivePersona(nextPersona.id);
    }catch(error){
      console.error("Erreur suppression persona", error);
    }
  });
  el("renamePersonaBtn").addEventListener("click", () => {
    const persona = activePersona();
    if(!persona || !state.isAuthenticated || typeof persona.id !== "number") return;
    const form = el("personaRenameForm");
    const input = el("personaRenameInput");
    input.value = persona.name;
    form.hidden = false;
    input.focus();
    input.select();
  });
  el("cancelPersonaRename").addEventListener("click", () => {
    el("personaRenameForm").hidden = true;
  });
  el("personaRenameForm").addEventListener("submit", async event => {
    event.preventDefault();
    const persona = activePersona();
    const name = el("personaRenameInput").value.trim();
    if(!persona || !name || typeof persona.id !== "number") return;
    try{
      const response = await fetch(`/chat/api/personas/${persona.id}/`, {
        method: "PATCH",
        headers: {
          "Content-Type": "application/json",
          "X-CSRFToken": document.querySelector("[name=csrfmiddlewaretoken]").value
        },
        body: JSON.stringify({ name })
      });
      const data = await response.json();
      if(!response.ok) throw new Error(data.error?.message || "Renommage du persona impossible.");
      Object.assign(persona, data);
      el("personaRenameForm").hidden = true;
      renderAll();
    }catch(error){
      const notice = el("creditNotice");
      notice.textContent = error.message;
      notice.classList.add("show");
      window.setTimeout(() => notice.classList.remove("show"), 3000);
    }
  });
  async function switchModel(modelId){
    const localSelection = modelId.startsWith("local:");
    const conversation = activeConvo();
    const localModelChanged = localSelection && conversation?.localOnly && conversation.localModel &&
      (conversation.localModel.modelId !== modelId.slice("local:".length) ||
        conversation.localModel.baseUrl !== state.localAI?.baseUrl);
    const needsNewConversation = conversation &&
      (Boolean(conversation.localOnly) !== localSelection || localModelChanged) &&
      (conversation.id || conversation.messages?.length);
    if(needsNewConversation){
      state.modelId = modelId;
      await newConversation(state.activePersonaId, state.temporaryChat);
    }else if(conversation && !conversation.id && !conversation.messages?.length){
      conversation.localOnly = localSelection;
      conversation.localId = localSelection && !conversation.temporary ? uid("local") : null;
      conversation.localModel = localSelection ? { ...state.localAI } : null;
      conversation.workspaceId = localSelection ? null : state.selectedWorkspaceId;
    }
    state.modelId = modelId;
    if(localSelection){
      el("teamModeBtn").classList.remove("active");
      el("teamOptions").hidden = true;
    }
    renderAll();
  }

  async function queryLocalModels(baseUrl, timeoutMs = 1800){
    const controller = new AbortController();
    const timeout = window.setTimeout(() => controller.abort(), timeoutMs);
    try{
      const response = await fetch(`${baseUrl}/models`, {
        headers: { "Accept": "application/json" },
        credentials: "omit",
        cache: "no-store",
        signal: controller.signal
      });
      if(!response.ok) throw new Error(`HTTP ${response.status}`);
      const data = await response.json();
      const models = Array.isArray(data.data)
        ? data.data.map(item => item?.id || item?.name).filter(id => typeof id === "string" && id)
        : [];
      return models.map(modelId => ({ modelId, baseUrl }));
    }finally{
      window.clearTimeout(timeout);
    }
  }

  async function queryOllamaTags(){
    const controller = new AbortController();
    const timeout = window.setTimeout(() => controller.abort(), 1800);
    try{
      const response = await fetch("http://localhost:11434/api/tags", {
        headers: { "Accept": "application/json" },
        credentials: "omit",
        cache: "no-store",
        signal: controller.signal
      });
      if(!response.ok) return [];
      const data = await response.json();
      return (Array.isArray(data.models) ? data.models : [])
        .map(item => item?.name || item?.model)
        .filter(modelId => typeof modelId === "string" && modelId)
        .map(modelId => ({ modelId, baseUrl: "http://localhost:11434/v1" }));
    }catch(_error){
      return [];
    }finally{
      window.clearTimeout(timeout);
    }
  }

  async function discoverLocalModels(){
    const status = el("localModelStatus");
    const button = el("discoverLocalModelsBtn");
    const select = el("localModelSelect");
    button.disabled = true;
    el("saveLocalModelBtn").disabled = true;
    select.disabled = true;
    status.textContent = "Recherche des IA locales sur cet appareil…";
    try{
      const addresses = [
        { baseUrl: "http://localhost:11434/v1", engine: "Ollama" },
        { baseUrl: "http://localhost:1234/v1", engine: "LM Studio" },
        { baseUrl: "http://localhost:1337/v1", engine: "Jan" },
        { baseUrl: "http://localhost:8080/v1", engine: "LocalAI / llama.cpp" },
        { baseUrl: "http://localhost:4891/v1", engine: "GPT4All" }
      ];
      const customBaseUrl = normalizeLocalBaseUrl(el("localModelBaseUrl").value.trim());
      if(!addresses.some(address => address.baseUrl === customBaseUrl)){
        addresses.unshift({ baseUrl: customBaseUrl, engine: "Adresse personnalisée" });
      }
      const results = await Promise.all(addresses.map(async address => {
        try{
          const models = await queryLocalModels(address.baseUrl);
          return models.map(model => ({ ...model, engine: address.engine }));
        }catch(_error){
          return [];
        }
      }));
      const ollamaIndex = addresses.findIndex(address => address.baseUrl === "http://localhost:11434/v1");
      if(ollamaIndex >= 0){
        results[ollamaIndex].push(...await queryOllamaTags());
        results[ollamaIndex] = results[ollamaIndex].map(model => ({
          ...model,
          engine: model.engine || "Ollama"
        }));
      }
      const seen = new Set();
      discoveredLocalModels = results.flat().filter(model => {
        const key = `${model.baseUrl}\n${model.modelId}`;
        if(seen.has(key)) return false;
        seen.add(key);
        return true;
      });
      if(!discoveredLocalModels.length){
        select.replaceChildren(new Option("Aucune IA détectée", ""));
        status.textContent = "Aucune IA trouvée sur les adresses locales habituelles. Lance Ollama ou une autre IA locale, vérifie l’accès CORS, ou saisis son adresse ci-dessus puis relance l’analyse.";
        return;
      }
      select.replaceChildren(...discoveredLocalModels.map((model, index) => {
        const option = new Option(`${model.modelId} · ${model.engine}`, String(index));
        option.dataset.modelId = model.modelId;
        option.dataset.baseUrl = model.baseUrl;
        return option;
      }));
      const savedIndex = discoveredLocalModels.findIndex(model =>
        model.modelId === state.localAI?.modelId && model.baseUrl === state.localAI?.baseUrl
      );
      if(savedIndex >= 0) select.value = String(savedIndex);
      const selected = discoveredLocalModels[Number(select.value)];
      if(selected) el("localModelBaseUrl").value = selected.baseUrl;
      select.disabled = false;
      el("saveLocalModelBtn").disabled = false;
      status.textContent = `${discoveredLocalModels.length} modèle${discoveredLocalModels.length > 1 ? "s" : ""} trouvé${discoveredLocalModels.length > 1 ? "s" : ""} sur cet appareil.`;
    }catch(error){
      status.textContent = error.message;
    }finally{
      button.disabled = false;
    }
  }

  function openLocalModelDialog(){
    if(state.localAI){
      el("localModelBaseUrl").value = state.localAI.baseUrl;
      const savedModel = { ...state.localAI, engine: "Modèle enregistré" };
      discoveredLocalModels = [savedModel];
      const option = new Option(state.localAI.modelId, "0");
      option.dataset.modelId = state.localAI.modelId;
      option.dataset.baseUrl = state.localAI.baseUrl;
      el("localModelSelect").replaceChildren(option);
      el("localModelSelect").value = "0";
      el("localModelSelect").disabled = false;
      el("saveLocalModelBtn").disabled = false;
      el("localModelStatus").textContent = "Modèle enregistré sur cet appareil.";
    }else{
      discoveredLocalModels = [];
      el("localModelStatus").textContent = "";
      el("localModelSelect").replaceChildren(new Option("Teste la connexion pour charger les modèles", ""));
      el("localModelSelect").disabled = true;
      el("saveLocalModelBtn").disabled = true;
    }
    el("localModelDialog").showModal();
  }

let discoveredLocalModels = [];
el("modelChips").addEventListener("click", (e) => {
  const autoButton = e.target.closest("#autoModelBtn");
  const pickerButton = e.target.closest("#modelPickerBtn");

  if(autoButton){
    e.stopPropagation();
    el("teamModeBtn").classList.remove("active");
    el("teamOptions").hidden = true;
    switchModel("auto");
    return;
  }

  if(pickerButton){
    e.stopPropagation();
    el("modelPickerSearch").value = "";
    document.querySelectorAll(".model-picker-item").forEach(item => { item.hidden = false; });
    el("modelPickerDialog").showModal();
    el("modelPickerSearch").focus();
  }
});

el("modelPickerList").addEventListener("click", event => {
  const item = event.target.closest(".model-picker-item");
  if(!item) return;
  event.stopPropagation();
  if(item.id === "localModelDialogBtn"){
    el("modelPickerDialog").close();
    el("teamModeBtn").classList.remove("active");
    el("teamOptions").hidden = true;
    if(state.localAI) switchModel(`local:${state.localAI.modelId}`);
    else openLocalModelDialog();
    return;
  }
  el("modelPickerDialog").close();
  el("teamModeBtn").classList.remove("active");
  el("teamOptions").hidden = true;
  switchModel(item.dataset.modelId);
});

el("modelPickerSearch").addEventListener("input", event => {
  const query = event.target.value.trim().toLocaleLowerCase();
  document.querySelectorAll(".model-picker-item").forEach(item => {
    item.hidden = !item.textContent.toLocaleLowerCase().includes(query);
  });
});
el("modelTaskCategory").addEventListener("change", event => {
  sortModelsForTask(event.target.value);
});
el("closeModelPickerBtn").addEventListener("click", () => el("modelPickerDialog").close());
el("modelPickerDialog").addEventListener("click", event => {
  if(event.target === el("modelPickerDialog")) el("modelPickerDialog").close();
});

el("discoverLocalModelsBtn").addEventListener("click", discoverLocalModels);
el("localModelBaseUrl").addEventListener("input", () => {
  let editedBaseUrl = "";
  try{
    editedBaseUrl = normalizeLocalBaseUrl(el("localModelBaseUrl").value.trim());
  }catch(_error){
    editedBaseUrl = "";
  }
  if(editedBaseUrl !== state.localAI?.baseUrl){
    el("localModelSelect").replaceChildren(new Option("Teste la connexion pour charger les modèles", ""));
    el("localModelSelect").disabled = true;
    el("saveLocalModelBtn").disabled = true;
    el("localModelStatus").textContent = "";
  }
});
el("localModelSelect").addEventListener("change", event => {
  const model = discoveredLocalModels[Number(event.currentTarget.value)];
  if(model){
    el("localModelBaseUrl").value = model.baseUrl;
    el("saveLocalModelBtn").disabled = false;
  }
});
el("cancelLocalModelBtn").addEventListener("click", () => el("localModelDialog").close());
el("saveLocalModelBtn").addEventListener("click", async () => {
  const model = discoveredLocalModels[Number(el("localModelSelect").value)];
  if(!model) return;
  try{
    const settings = { baseUrl: normalizeLocalBaseUrl(model.baseUrl), modelId: model.modelId };
    localStorage.setItem(localAISettingsKey, JSON.stringify(settings));
    state.localAI = settings;
    el("localModelDialog").close();
    await switchModel(`local:${model.modelId}`);
  }catch(error){
    showChatNotice(error.message);
  }
});
el("syncLocalConversationBtn").addEventListener("click", () => {
  const conversation = activeConvo();
  if(conversation) syncLocalConversation(conversation);
});

  el("teamModeBtn").addEventListener("click", () => {
    if(isLocalModelSelected()){
      showChatNotice("L’équipe IA utilise les modèles cloud. Reviens à un modèle cloud pour l’activer.");
      return;
    }
    const active = el("teamModeBtn").classList.toggle("active");
    if(active && autoModelBtn) autoModelBtn.classList.remove("active");
    if(!active && autoModelBtn && state.modelId === "auto") autoModelBtn.classList.add("active");
    el("teamOptions").hidden = !active;
    if(active){
      document.querySelectorAll(".model-chip:not(.team-chip)").forEach(item => item.classList.remove("active"));
      el("teamModelGrid").querySelector(".team-model-option")?.focus();
    }
    renderSendButton();
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
    renderSendButton();
  });
  const mediaOptionsPanel = el("mediaOptionsPanel");
  function updateMediaOptionControls(mode){
    const mediaMode = ["image", "video", "audio"].includes(mode);
    mediaOptionsPanel.hidden = !mediaMode;
    el("imageOptions").hidden = mode !== "image";
    el("videoOptions").hidden = mode !== "video";
    el("audioOptions").hidden = mode !== "audio";
  }
  function collectMediaOptions(mode){
    if(mode === "image") return { image_size: el("imageSizeSelect").value };
    if(mode === "video") return {
      aspect_ratio: el("videoAspectRatio").value,
      resolution: el("videoResolution").value,
      duration_seconds: Number(el("videoDuration").value),
      generate_audio: el("videoGenerateAudio").checked,
    };
    return {
      style: el("audioStyle").value,
      instrumental: el("audioInstrumental").checked,
    };
  }
  updateMediaOptionControls("chat");
  document.querySelectorAll(".tool-btn[data-mode]").forEach(button => {
    button.addEventListener("click", () => {
      document.querySelectorAll(".tool-btn[data-mode]").forEach(item => {
        item.classList.remove("active");
        item.setAttribute("aria-pressed", "false");
      });
      button.classList.add("active");
      button.setAttribute("aria-pressed", "true");
      updateMediaOptionControls(button.dataset.mode);
      renderSendButton();
    });
  });
  ["imageSizeSelect", "videoAspectRatio", "videoResolution", "videoDuration", "videoGenerateAudio", "audioStyle", "audioInstrumental"]
    .forEach(id => el(id).addEventListener("change", renderSendButton));
const fileInput = document.createElement("input");
fileInput.type = "file";
fileInput.accept = ".txt,.md,.csv,.json,.py,.js,.html,.css,.pdf,.docx,.xlsx,.pptx";
fileInput.hidden = true;
document.body.appendChild(fileInput);

const composerPlusMenu = el("composerPlusMenu");
function closeComposerPlusMenu(){
  composerPlusMenu.hidden = true;
  el("attachBtn").setAttribute("aria-expanded", "false");
}
el("attachBtn").addEventListener("click", () => {
  composerPlusMenu.hidden = !composerPlusMenu.hidden;
  el("attachBtn").setAttribute("aria-expanded", String(!composerPlusMenu.hidden));
});
el("plusFilesBtn").addEventListener("click", () => {
  closeComposerPlusMenu();
  fileInput.click();
});
el("plusImageBtn").addEventListener("click", () => {
  if(el("plusImageBtn").disabled) return;
  document.querySelectorAll(".tool-btn[data-mode]").forEach(button => {
    const selected = button.dataset.mode === "image";
    button.classList.toggle("active", selected);
    button.setAttribute("aria-pressed", String(selected));
  });
  closeComposerPlusMenu();
  el("composerInput").focus();
});
document.addEventListener("click", event => {
  if(!el("composerPlusWrap").contains(event.target)) closeComposerPlusMenu();
});

fileInput.addEventListener("change", async () => {
  const file = fileInput.files[0];
  if(!file) return;

  const convo = activeConvo();
  if(!convo){
    showChatNotice("Crée d'abord une conversation.");
    fileInput.value = "";
    return;
  }
  if(!convo.workspaceId){
    showChatNotice("Sélectionne ou crée un espace Bara dans « Fichiers » avant de joindre un document.");
    fileInput.value = "";
    return;
  }
  if(file.size > 10 * 1024 * 1024){
    showChatNotice("Le fichier dépasse la limite de 10 Mo.");
    fileInput.value = "";
    return;
  }

  const formData = new FormData();
  formData.append("file", file);

  try{
    const res = await fetch(`/bara/${encodeURIComponent(convo.workspaceId)}/upload/`, {
      method: "POST",
      headers: { "X-CSRFToken": document.querySelector("[name=csrfmiddlewaretoken]").value },
      body: formData
    });
    const data = await res.json();
    if(!res.ok) throw new Error(data.error || "Erreur d'upload");

    showChatNotice(data.has_text
      ? `"${data.name}" ajouté à l'espace Bara et disponible pour l'IA.`
      : `"${data.name}" est enregistré, mais son texte n'a pas pu être extrait.`);
  }catch(err){
    showChatNotice("Erreur d'ajout du fichier : " + err.message);
  }
  fileInput.value = "";
});

  const form = el("composerForm");
  const input = el("composerInput");
  const SpeechRecognition = window.SpeechRecognition || window.webkitSpeechRecognition;
  el("voiceBtn").addEventListener("click", () => {
    closeComposerPlusMenu();
    if (!SpeechRecognition) {
      el("creditNotice").textContent = "La dictée vocale n’est pas prise en charge par ce navigateur.";
      el("creditNotice").classList.add("show");
      window.setTimeout(() => el("creditNotice").classList.remove("show"), 3200);
      return;
    }

    const recognition = new SpeechRecognition();
    recognition.lang = document.documentElement.lang || "fr-FR";
    recognition.interimResults = false;
    el("voiceBtn").disabled = true;
    recognition.addEventListener("result", event => {
      const transcript = event.results?.[0]?.[0]?.transcript || "";
      input.setRangeText(transcript, input.selectionStart, input.selectionEnd, "end");
      input.dispatchEvent(new Event("input", { bubbles: true }));
      input.focus();
    });
    recognition.addEventListener("error", () => {
      el("creditNotice").textContent = "La dictée vocale n’a pas pu démarrer. Vérifie l’autorisation du microphone.";
      el("creditNotice").classList.add("show");
      window.setTimeout(() => el("creditNotice").classList.remove("show"), 3200);
    });
    recognition.addEventListener("end", () => {
      el("voiceBtn").disabled = false;
    });
    recognition.start();
  });

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
    const localModelSelected = isLocalModelSelected();
    if((localModelSelected ? !state.isAuthenticated : !ensureCreditAccess()) || !text) return;
    if(el("teamModeBtn").classList.contains("active") && el("teamModelSelect").selectedOptions.length === 0){
      el("creditNotice").textContent = "Choisis au moins une IA pour former ton équipe.";
      el("creditNotice").classList.add("show");
      window.setTimeout(() => el("creditNotice").classList.remove("show"), 2800);
      el("teamModelGrid").querySelector(".team-model-option")?.focus();
      return;
    }
    const selectedMode = document.querySelector(".tool-btn[data-mode].active")?.dataset.mode || "chat";
    const activeConversation = activeConvo();
    if((localModelSelected || activeConversation?.localOnly) && selectedMode !== "chat"){
      showChatNotice("Une conversation locale ne peut pas utiliser les outils hébergés. Reviens au mode chat simple.");
      return;
    }
    if(["video", "audio"].includes(selectedMode) && (state.temporaryChat || activeConversation?.temporary)){
      el("creditNotice").textContent = "Les vidéos et audios doivent être générés dans une conversation enregistrée.";
      el("creditNotice").classList.add("show");
      window.setTimeout(() => el("creditNotice").classList.remove("show"), 3200);
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
    await loadWorkspaces();

    await loadConversations();

    if(state.conversations.length === 0){
      await newConversation(state.activePersonaId);
    }

    renderAll();

  })();

})();
}


//recharge
    const currencyToggleOptions = [...document.querySelectorAll("[data-payment-currency]")];
    const paymentCards = {
      XOF: document.getElementById("paydunyaPayment"),
      USD: document.getElementById("stripePayment"),
    };
    function selectPaymentCurrency(currency){
      if(!paymentCards[currency]) return;
      currencyToggleOptions.forEach(option => {
        const selected = option.dataset.paymentCurrency === currency;
        option.classList.toggle("active", selected);
        option.setAttribute("aria-pressed", String(selected));
      });
      Object.entries(paymentCards).forEach(([cardCurrency, card]) => {
        if(!card) return;
        const selected = cardCurrency === currency;
        card.hidden = !selected;
        card.classList.toggle("active", selected);
      });
    }
    currencyToggleOptions.forEach(option => {
      option.addEventListener("click", () => selectPaymentCurrency(option.dataset.paymentCurrency));
    });

    const formatCredits = value => new Intl.NumberFormat("fr-FR").format(value);
    const stripeAmountInput = document.getElementById("stripe-amount");
    const stripeCreditsOutput = document.getElementById("stripeCreditsEstimate");
    function updateStripeCredits(){
      if(!stripeAmountInput || !stripeCreditsOutput) return;
      const amount = Number(stripeAmountInput.value);
      const creditsPerDollar = Number(stripeCreditsOutput.dataset.creditsPerDollar);
      if(!Number.isFinite(amount) || amount < Number(stripeAmountInput.min)){
        stripeCreditsOutput.textContent = `Minimum ${stripeAmountInput.min} USD`;
        return;
      }
      stripeCreditsOutput.textContent = `${formatCredits(Math.floor(amount * creditsPerDollar))} jetons`;
    }
    stripeAmountInput?.addEventListener("input", updateStripeCredits);
    updateStripeCredits();

    const paydunyaAmountInput = document.getElementById("paydunya-amount");
    const paydunyaCreditsOutput = document.getElementById("paydunyaCreditsEstimate");
    function updatePaydunyaCredits(){
      if(!paydunyaAmountInput || !paydunyaCreditsOutput) return;
      const amount = Number(paydunyaAmountInput.value);
      const xofPerUsd = Number(paydunyaCreditsOutput.dataset.xofPerUsd);
      const creditsPerDollar = Number(paydunyaCreditsOutput.dataset.creditsPerDollar);
      if(!Number.isFinite(xofPerUsd) || xofPerUsd <= 0){
        paydunyaCreditsOutput.textContent = "Indisponible";
        return;
      }
      if(!Number.isFinite(amount) || amount < Number(paydunyaAmountInput.min)){
        paydunyaCreditsOutput.textContent = `Minimum ${new Intl.NumberFormat("fr-FR").format(Number(paydunyaAmountInput.min))} FCFA`;
        return;
      }
      paydunyaCreditsOutput.textContent = `${formatCredits(Math.floor(amount / xofPerUsd * creditsPerDollar))} jetons`;
    }
    paydunyaAmountInput?.addEventListener("input", updatePaydunyaCredits);
    updatePaydunyaCredits();

    document.querySelectorAll("[data-set-amount]").forEach(button => {
      button.addEventListener("click", () => {
        const input = document.getElementById(button.dataset.setAmount);
        if(!input || input.disabled) return;
        input.value = button.dataset.amount;
        input.dispatchEvent(new Event("input", { bubbles: true }));
      });
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
  