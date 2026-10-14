/* Capability preflight: only metadata and direct request patterns, no AI calls. */
(function(root){
  function requirements(content, rules){
    let text = content.trim().toLowerCase().normalize('NFD').replace(/[\u0300-\u036f]/g, '').replace(/’/g, "'");
    text = text.split('\n', 1)[0].slice(0, 600).replace(new RegExp(rules.prefix), '');
    return new Set(Object.entries(rules.rules)
      .filter(([name, pattern]) => new RegExp(pattern).test(text) && !(name.startsWith('file_') && content.trim().includes('\n')))
      .map(([name]) => name));
  }

  function check(content, options, config){
    const rules = config.capabilityRules;
    if(!rules) return null;
    const needed = requirements(content, rules);
    const blocked = (code, message) => ({code, message: (message || rules.messages[code]) + ' ' + rules.messages.no_charge});
    const {mode = 'chat', local = false, team = false, workspace = null, hasImages = false, modelIds = []} = options;
    if(local && (mode !== 'chat' || team || workspace || hasImages || needed.size)) return blocked('local');
    if(['image', 'video', 'audio'].includes(mode)) return null; // Dedicated generation endpoint.
    for(const kind of ['image', 'video', 'audio']){
      if(needed.has(kind)) return blocked('media_mode_required', rules.mediaMessages[kind]);
    }
    const disabled = new Set(workspace?.disabled_tools || []);
    if((mode === 'web' || needed.has('web')) && (!config.webSearchAvailable || disabled.has('web_search'))) return blocked('web');
    if(needed.has('execute') && (!workspace || !config.codeExecutionAvailable || ['run_python', 'run_command'].every(name => disabled.has(name)))) return blocked('execute');
    const fileRequests = Array.from(needed).filter(name => name.startsWith('file_'));
    if(fileRequests.length && !workspace) return blocked('workspace');
    if(fileRequests.some(name => rules.toolRequirements[name].every(tool => disabled.has(tool)))) return blocked('file_tool');
    const needsTools = Boolean(workspace) || needed.has('execute') || (needed.has('web') && mode !== 'web');
    if(team && (needsTools || hasImages)) return blocked('team');
    const models = modelIds.filter(id => id !== 'auto').map(id => config.modelCapabilities?.[id]).filter(Boolean);
    if(hasImages && models.some(model => model.images === false)) return blocked('vision');
    if(needsTools && models.some(model => model.tools === false)) return blocked('tools');
    return null;
  }
  root.AI2AICapabilities = {check, requirements};
})(typeof window === 'undefined' ? globalThis : window);
