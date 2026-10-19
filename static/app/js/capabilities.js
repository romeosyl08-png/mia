/* Capability preflight: only metadata and direct request patterns, no AI calls. */
(function(root){
  function requirements(content, rules){
    let text = content.trim().toLowerCase().normalize('NFD').replace(/[\u0300-\u036f]/g, '').replace(/’/g, "'");
    text = text.slice(0, 4000);
    const needed = new Set();
    for(let line of text.split('\n').slice(0, 16)){
      line = line.trim().replace(new RegExp(rules.listPrefix), '');
      line = line.replace(new RegExp(rules.prefix), '').trim();
      if(!line) continue;
      if(new RegExp(rules.dataStart).test(line)) break;
      let found = [];
      for(let clause of line.split(new RegExp(rules.clauseSeparator))){
        clause = clause.trim().replace(new RegExp(rules.prefix), '');
        found = Object.entries(rules.rules).filter(([name, pattern]) =>
          new RegExp(pattern).test(clause) && !(name.startsWith('file_') && new RegExp(rules.inlineData).test(clause) && (text.includes('\n') || clause.slice(clause.indexOf(':') + 1).trim()))
        ).map(([name]) => name);
        found.forEach(name => needed.add(name));
      }
      if(line.includes(':') || !found.length) break;
    }
    return needed;
  }

  function generationKind(content, mode, rules){
    if(['image', 'video', 'audio'].includes(mode)) return mode;
    const needed = requirements(content, rules);
    return needed.size === 1 ? ['image', 'video', 'audio'].find(kind => needed.has(kind)) || null : null;
  }

  function check(content, options, config){
    const rules = config.capabilityRules;
    if(!rules) return null;
    const needed = requirements(content, rules);
    const blocked = (code, message) => ({code, message: (message || rules.messages[code]) + ' ' + rules.messages.no_charge});
    const {mode = 'chat', local = false, team = false, workspace = null, hasImages = false, modelIds = []} = options;
    if(local && (mode !== 'chat' || team || workspace || hasImages || needed.size)) return blocked('local');
    const kind = generationKind(content, mode, rules);
    if(kind){
      if(options.temporary && kind !== 'image') return blocked('temporary_media');
      const available = {image: config.imageGenerationAvailable, video: config.videoGenerationAvailable, audio: config.audioProviderAvailable};
      if(available[kind] === false) return blocked('media_unavailable');
      return null;
    }
    for(const kind of ['image', 'video', 'audio']){
      if(needed.has(kind)) return blocked('media_mode_required', rules.mediaMessages[kind]);
    }
    const disabled = new Set(workspace?.disabled_tools || []);
    if((mode === 'web' || needed.has('web')) && (!config.webSearchAvailable || disabled.has('web_search'))) return blocked('web');
    if(needed.has('execute') && (!workspace || !config.codeExecutionAvailable || ['run_python', 'run_command'].every(name => disabled.has(name)))) return blocked('execute');
    const fileRequests = Array.from(needed).filter(name => name.startsWith('file_'));
    if(fileRequests.length && !workspace) return blocked('workspace');
    if(fileRequests.some(name => rules.toolRequirements[name].every(tool => disabled.has(tool)))) return blocked('file_tool');
    const needsTools = Array.from(needed).some(name => !['file_read', 'web'].includes(name));
    if(team && (workspace || needsTools || hasImages)) return blocked('team');
    const models = modelIds.filter(id => id !== 'auto').map(id => config.modelCapabilities?.[id]).filter(Boolean);
    if(hasImages && models.some(model => model.images === false)) return blocked('vision');
    if(hasImages && models.some(model => model.images == null)) return blocked('vision_unknown');
    if(needsTools && models.some(model => model.tools === false)) return blocked('tools');
    return null;
  }
  root.AI2AICapabilities = {check, requirements, generationKind};
})(typeof window === 'undefined' ? globalThis : window);
