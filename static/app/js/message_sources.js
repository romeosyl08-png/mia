(function(root){
  "use strict";

  function safeURL(value){
    if(typeof value !== "string") return null;
    try{
      const url = new URL(value);
      if(!["http:", "https:"].includes(url.protocol) || url.username || url.password) return null;
      return url;
    }catch(_error){ return null; }
  }

  function normalizeSources(sources){
    if(!Array.isArray(sources)) return [];
    return sources.slice(0, 24).map((source, index) => {
      const url = safeURL(source?.link);
      if(!url) return null;
      const host = url.hostname.replace(/^www\./, "");
      const known = {"youtube.com":"YouTube", "youtu.be":"YouTube", "fr.wikipedia.org":"Wikipédia", "alloprof.qc.ca":"Alloprof", "larousse.fr":"Larousse"};
      const publisher = Object.prototype.hasOwnProperty.call(known, host) ? known[host] : host;
      return { id:index + 1, link:url.href, host, publisher, title:String(source.title || publisher), snippet:String(source.snippet || "") };
    }).filter(Boolean);
  }

  function citationGroups(text, sources){
    const available = new Map(normalizeSources(sources).map(source => [source.id, source]));
    return Array.from(text.matchAll(/\[\[sources?:\s*(\d+(?:\s*,\s*\d+)*)\s*\]\]/gi), match => ({
      index:match.index, marker:match[0],
      sources:Array.from(new Set(match[1].split(",").map(Number))).map(id => available.get(id)).filter(Boolean)
    }));
  }

  let activeButton = null;
  let popover = null;
  let listenersReady = false;

  function closePopover(restoreFocus=false){
    if(activeButton){
      activeButton.setAttribute("aria-expanded", "false");
      if(restoreFocus && activeButton.isConnected) activeButton.focus();
    }
    popover?.remove();
    popover = null;
    activeButton = null;
  }

  function showPopover(button, sources){
    if(activeButton === button){ closePopover(); return; }
    closePopover();
    activeButton = button;
    button.setAttribute("aria-expanded", "true");
    popover = document.createElement("section");
    popover.className = "citation-popover";
    popover.id = "citation-popover";
    popover.setAttribute("role", "dialog");
    popover.setAttribute("aria-label", sources.length > 1 ? `${sources.length} sources` : "Source");
    const header = document.createElement("div");
    header.className = "citation-popover-header";
    const title = document.createElement("strong");
    title.textContent = sources.length > 1 ? `${sources.length} sources` : "Source";
    const close = document.createElement("button");
    close.type = "button";
    close.className = "citation-close";
    close.textContent = "×";
    close.setAttribute("aria-label", "Fermer les sources");
    close.addEventListener("click", () => closePopover(true));
    header.append(title, close);
    popover.appendChild(header);
    sources.forEach(source => {
      const link = document.createElement("a");
      link.className = "citation-source-card";
      link.href = source.link;
      link.target = "_blank";
      link.rel = "noopener noreferrer";
      const site = document.createElement("span");
      site.className = "citation-source-site";
      site.append(icon(source), document.createTextNode(source.publisher));
      const heading = document.createElement("strong");
      heading.textContent = source.title;
      link.append(site, heading);
      if(source.snippet){
        const snippet = document.createElement("span");
        snippet.className = "citation-snippet";
        snippet.textContent = source.snippet;
        link.appendChild(snippet);
      }
      popover.appendChild(link);
    });
    document.body.appendChild(popover);
    const rect = button.getBoundingClientRect();
    const width = Math.min(360, root.innerWidth - 24);
    popover.style.width = width + "px";
    popover.style.left = Math.max(12, Math.min(rect.left, root.innerWidth - width - 12)) + "px";
    const height = popover.getBoundingClientRect().height;
    const top = rect.bottom + height + 8 < root.innerHeight ? rect.bottom + 8 : Math.max(12, rect.top - height - 8);
    popover.style.top = top + "px";
    linkFocus();
    if(!listenersReady){
      listenersReady = true;
      document.addEventListener("click", event => {
        if(popover && !popover.contains(event.target) && !activeButton?.contains(event.target)) closePopover();
      });
      document.addEventListener("keydown", event => { if(event.key === "Escape") closePopover(true); });
      root.addEventListener("resize", () => closePopover());
      document.addEventListener("scroll", event => {
        if(popover && !popover.contains(event.target)) closePopover();
      }, true);
    }
  }

  function linkFocus(){ popover?.querySelector("a")?.focus({preventScroll:true}); }

  function icon(source){
    const wrap = document.createElement("span");
    wrap.className = "citation-icon-wrap";
    wrap.setAttribute("aria-hidden", "true");
    const fallback = document.createElement("span");
    fallback.className = "citation-icon citation-icon-fallback";
    fallback.textContent = source.publisher.slice(0, 1).toUpperCase();
    if(source.publisher === "YouTube"){ fallback.textContent = "▶"; fallback.style.background = "#ff0033"; }
    if(source.publisher === "Alloprof") fallback.style.background = "#11ad78";
    if(source.publisher === "Larousse") fallback.style.background = "#284991";
    const img = document.createElement("img");
    img.className = "citation-icon";
    img.hidden = true;
    img.alt = "";
    img.loading = "lazy";
    img.referrerPolicy = "no-referrer";
    img.src = `https://www.google.com/s2/favicons?domain=${encodeURIComponent(source.host)}&sz=32`;
    img.addEventListener("load", () => { img.hidden = false; fallback.hidden = true; });
    wrap.append(fallback, img);
    return wrap;
  }

  function pill(sources){
    const button = document.createElement("button");
    button.type = "button";
    button.className = "citation-pill";
    button.setAttribute("aria-expanded", "false");
    button.setAttribute("aria-controls", "citation-popover");
    button.setAttribute("aria-haspopup", "dialog");
    button.setAttribute("aria-label", `Afficher ${sources.length > 1 ? `les ${sources.length} sources` : "la source"} : ${sources[0].title}`);
    button.title = sources.map(source => source.title).join("\n");
    const label = document.createElement("span");
    label.className = "citation-label";
    label.textContent = sources[0].publisher;
    button.append(icon(sources[0]), label);
    if(sources.length > 1){
      const count = document.createElement("span");
      count.className = "citation-count";
      count.textContent = `+${sources.length - 1}`;
      button.appendChild(count);
    }
    button.addEventListener("click", () => showPopover(button, sources));
    return button;
  }

  function attach(content, rawSources, options={}){
    if(activeButton && !activeButton.isConnected) closePopover();
    const sources = normalizeSources(rawSources);
    let attached = false;
    const walker = document.createTreeWalker(content, NodeFilter.SHOW_TEXT, {
      acceptNode(node){ return node.parentElement.closest("pre,code,a,button") ? NodeFilter.FILTER_REJECT : NodeFilter.FILTER_ACCEPT; }
    });
    const nodes = [];
    while(walker.nextNode()) nodes.push(walker.currentNode);
    for(const node of nodes){
      const groups = citationGroups(node.textContent, rawSources);
      if(!groups.length) continue;
      const fragment = document.createDocumentFragment();
      let offset = 0;
      for(const group of groups){
        fragment.appendChild(document.createTextNode(node.textContent.slice(offset, group.index)));
        if(group.sources.length){ fragment.appendChild(pill(group.sources)); attached = true; }
        offset = group.index + group.marker.length;
      }
      fragment.appendChild(document.createTextNode(node.textContent.slice(offset)));
      node.replaceWith(fragment);
    }
    for(const link of Array.from(content.querySelectorAll("a[href]"))){
      if(link.closest("pre,code")) continue;
      const source = sources.find(entry => entry.link === safeURL(link.getAttribute("href"))?.href);
      if(source){ link.replaceWith(pill([source])); attached = true; }
    }
    if(sources.length && !attached && options.fallback !== false){
      const row = document.createElement("div");
      row.className = "citation-source-row";
      const label = document.createElement("span");
      label.textContent = "Sources consultées";
      row.appendChild(label);
      sources.forEach(source => row.appendChild(pill([source])));
      content.appendChild(row);
    }
  }

  root.AI2AISources = {attach, normalizeSources, citationGroups, safeURL, closePopover};
})(globalThis);
