/* Browser-only monitor and timeline. Scene edits still use the Studio API. */
(function (global) {
  'use strict';
  function timecode(seconds, fps = 24) {
    const frames = Math.floor(Math.max(0, Number(seconds) || 0) * fps + 0.00001);
    return [Math.floor(frames / fps / 3600), Math.floor(frames / fps / 60) % 60,
      Math.floor(frames / fps) % 60, frames % fps].map(value => String(value).padStart(2, '0')).join(':');
  }
  function segments(scene, assets) {
    const assetsById = new Map(assets.map(asset => [asset.id, asset]));
    let start = 0;
    const visuals = scene.visuals.map(row => {
      const segment = {row, start, duration: row.duration, asset: assetsById.get(row.asset_id)};
      start += row.duration;
      return segment;
    });
    const audio = scene.audio.map(row => {
      const asset = assetsById.get(row.asset_id);
      return {row, asset, start: row.start, duration: Math.min(180 - row.start, Number(asset?.metadata?.duration) || scene.settings.duration)};
    });
    return {visuals, audio, duration: visuals.length ? start : audio.length ? scene.settings.duration : 0};
  }

  function visualAt(visuals, position, totalDuration) {
    let low = 0, high = visuals.length - 1;
    while(low <= high){
      const middle = Math.floor((low + high) / 2);
      const visual = visuals[middle];
      if(position < visual.start) high = middle - 1;
      else if(position >= visual.start + visual.duration) low = middle + 1;
      else return visual;
    }
    return position === totalDuration ? visuals.at(-1) || null : null;
  }
  function create({root, getScene, getAssets}) {
    const $ = id => root.querySelector(`#studio-${id}`);
    let mode = 'project', selectedAssetId = null, selectedTrackId = null, position = 0;
    let playing = false, frame = 0, anchor = 0, monitorKey = '', mediaElement = null, signature = '';
    let cachedScene = null, cachedAssets = null, cachedTimeline = null;
    let audioContext = null;
    const sounds = new Map();
    function data() {
      const scene = getScene(), assets = getAssets();
      if (!cachedTimeline || scene !== cachedScene || assets !== cachedAssets) {
        cachedScene = scene;
        cachedAssets = assets;
        cachedTimeline = segments(scene, assets);
      }
      return cachedTimeline;
    }
    const element = (tag, text, className) => {
      const item = document.createElement(tag);
      if (text !== undefined) item.textContent = text;
      if (className) item.className = className;
      return item;
    };
    function activateTab(group, target, focus = false) {
      root.querySelectorAll(`[data-tab-group="${group}"]`).forEach(tab => {
        const selected = tab.dataset.tabTarget === target;
        tab.setAttribute('aria-selected', String(selected)); tab.tabIndex = selected ? 0 : -1;
        document.getElementById(tab.dataset.tabTarget).hidden = !selected;
        if (selected && focus) tab.focus();
      });
    }
    function keyboardTabs(tabs, activate) {
      tabs.forEach(tab => {
        tab.addEventListener('click', () => activate(tab));
        tab.addEventListener('keydown', event => {
          const index = tabs.indexOf(tab);
          const next = event.key === 'Home' ? 0 : event.key === 'End' ? tabs.length - 1
            : event.key === 'ArrowRight' ? (index + 1) % tabs.length
              : event.key === 'ArrowLeft' ? (index + tabs.length - 1) % tabs.length : -1;
          if (next < 0) return;
          event.preventDefault(); activate(tabs[next]); tabs[next].focus();
        });
      });
    }
    for (const group of ['bin', 'inspector']) {
      keyboardTabs([...root.querySelectorAll(`[data-tab-group="${group}"]`)], tab => activateTab(group, tab.dataset.tabTarget));
    }
    function selection() {
      root.querySelectorAll('[data-asset-id]').forEach(card => card.classList.toggle('is-selected', Number(card.dataset.assetId) === selectedAssetId));
      root.querySelectorAll('.studio-track[data-track-id]').forEach(row => row.classList.toggle('is-selected', row.dataset.trackId === selectedTrackId));
      root.querySelectorAll('.studio-timeline-clip').forEach(clip => clip.setAttribute('aria-pressed', String(clip.dataset.trackId === selectedTrackId)));
    }
    function emptyMonitor(text) {
      const empty = element('div', undefined, 'studio-monitor-empty');
      const icon = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
      icon.setAttribute('viewBox', '0 0 64 64'); icon.setAttribute('aria-hidden', 'true');
      icon.innerHTML = '<rect x="5" y="11" width="54" height="38" rx="3" fill="none" stroke="currentColor" stroke-width="1.5"/><path d="m26 22 15 8-15 8zM23 55h18" fill="none" stroke="currentColor" stroke-width="1.5"/>';
      empty.append(icon, element('strong', text), element('p', 'Importe un média, puis ajoute-le à la timeline.'));
      $('monitor-stage').replaceChildren(empty);
    }
    function syncVideo(offset, force = false) {
      if (mode !== 'project' || mediaElement?.tagName !== 'VIDEO') return;
      const target = Number.isFinite(mediaElement.duration) && mediaElement.duration > 0 ? offset % mediaElement.duration : offset;
      if (force || Math.abs(mediaElement.currentTime - target) > .4) mediaElement.currentTime = Math.max(0, target);
      if (playing && mediaElement.paused && !mediaElement.ended) mediaElement.play().catch(() => {});
    }
    function renderMonitor(force = false) {
      const timeline = data();
      const current = visualAt(timeline.visuals, position, timeline.duration);
      const item = mode === 'source' ? getAssets().find(asset => asset.id === selectedAssetId) : current?.asset;
      const key = `${mode}:${item?.id || 'empty'}:${current?.row.id || ''}`;
      if (key !== monitorKey) {
        mediaElement?.pause?.(); monitorKey = key; mediaElement = null;
        if (!item) emptyMonitor(mode === 'source' ? 'Sélectionne un média' : timeline.audio.length ? 'Montage audio' : 'Ton montage commence ici');
        else {
          mediaElement = element(item.kind === 'image' ? 'img' : item.kind);
          mediaElement.src = item.url;
          if (item.kind === 'image') mediaElement.alt = item.name;
          else {
            mediaElement.controls = mode === 'source'; mediaElement.preload = 'metadata';
            mediaElement.muted = mode === 'project';
            mediaElement.loop = mode === 'project';
            const player = mediaElement;
            player.addEventListener('loadedmetadata', () => { if (player === mediaElement) syncVideo(position - (current?.start || 0), true); });
            player.addEventListener('error', () => { $('status').textContent = 'Ce média ne peut pas être lu dans ce navigateur.'; });
          }
          $('monitor-stage').replaceChildren(mediaElement);
        }
      }
      if (current) syncVideo(position - current.start, force);
      $('monitor-name').textContent = item?.name || (mode === 'source' ? 'Moniteur source' : 'Moniteur du projet');
      $('timecode').textContent = timecode(position, getScene().settings.fps);
      $('project-length').textContent = timecode(timeline.duration, getScene().settings.fps);
      $('preview-seek').max = timeline.duration; $('preview-seek').value = position;
      $('preview-seek').step = 1 / getScene().settings.fps;
      $('playhead').style.left = `${80 + position * Number($('timeline-zoom').value)}px`;
      $('preview-play').disabled = mode === 'source' || !timeline.duration;
      $('preview-start').disabled = $('preview-next').disabled = mode === 'source' || !timeline.duration;
      $('preview-seek').disabled = mode === 'source' || !timeline.duration;
    }
    function stop() {
      playing = false; cancelAnimationFrame(frame); mediaElement?.pause?.();
      sounds.forEach(sound => sound.player.pause());
      $('preview-play').textContent = '▶'; $('preview-play').setAttribute('aria-label', 'Lire le montage');
      $('preview-play').setAttribute('aria-pressed', 'false');
    }
    function clearSounds() {
      sounds.forEach(sound => { sound.player.pause(); sound.player.removeAttribute('src'); sound.player.load(); sound.gain?.disconnect(); sound.source?.disconnect(); });
      sounds.clear();
    }
    function syncAudio(force = false) {
      for (const clip of data().audio) {
        if (!clip.asset) continue;
        let sound = sounds.get(clip.row.id);
        if (!sound) {
          const player = new Audio(clip.asset.url); player.preload = 'metadata';
          sound = {player};
          if (audioContext) {
            sound.source = audioContext.createMediaElementSource(player); sound.gain = audioContext.createGain();
            sound.source.connect(sound.gain); sound.gain.connect(audioContext.destination);
          }
          sounds.set(clip.row.id, sound);
        }
        if (sound.gain) sound.gain.gain.value = clip.row.volume;
        else sound.player.volume = Math.min(1, clip.row.volume);
        const offset = position - clip.start;
        if (!playing || offset < 0 || offset >= clip.duration) { sound.player.pause(); continue; }
        if (force || Math.abs(sound.player.currentTime - offset) > .4) sound.player.currentTime = offset;
        if (sound.player.paused && !sound.player.ended) sound.player.play().catch(() => { $('status').textContent = 'Une piste audio ne peut pas être lue dans ce navigateur.'; });
      }
    }
    function setPosition(value) {
      position = Math.min(data().duration, Math.max(0, Number(value) || 0));
      if (playing) anchor = performance.now() - position * 1000;
      renderMonitor(true); if (playing) syncAudio(true);
    }
    function setMode(value) {
      stop(); mode = value; monitorKey = '';
      root.querySelectorAll('[data-monitor-mode]').forEach(tab => {
        const active = tab.dataset.monitorMode === mode;
        tab.setAttribute('aria-selected', String(active)); tab.tabIndex = active ? 0 : -1;
        if (active) $('monitor-stage').setAttribute('aria-labelledby', tab.id);
      });
      renderMonitor();
    }
    function selectAsset(id) { selectedAssetId = id; setMode('source'); selection(); }
    function selectTrack(id) {
      const clip = [...data().visuals, ...data().audio].find(item => item.row.id === id);
      if (!clip) return;
      selectedTrackId = id; selectedAssetId = clip.row.asset_id; setMode('project'); setPosition(clip.start);
      activateTab('inspector', 'studio-clips-panel'); selection();
      const row = [...root.querySelectorAll('.studio-track')].find(item => item.dataset.trackId === id);
      if (row) $('clips-panel').scrollTop = row.offsetTop - $('clips-panel').offsetTop;
    }
    function renderTimeline() {
      const timeline = data(), scale = Number($('timeline-zoom').value);
      const length = Math.max(12, timeline.duration, ...timeline.audio.map(clip => clip.start + clip.duration)) + 2;
      const width = Math.max($('timeline-scroll').clientWidth, 80 + length * scale);
      $('timeline-content').style.width = `${width}px`;
      const step = scale < 25 ? 5 : scale < 65 ? 2 : 1;
      $('timeline-content').style.setProperty('--grid-step', `${step * scale}px`);
      $('timeline-ruler').replaceChildren(); $('timeline-lanes').replaceChildren();
      for (let second = 0; second < (width - 80) / scale; second += step) {
        const mark = element('span', timecode(second, getScene().settings.fps).slice(3, 8));
        mark.style.left = `${second * scale}px`; $('timeline-ruler').append(mark);
      }
      function lane(title, subtitle, clips, audio = false) {
        const row = element('div', undefined, 'studio-timeline-row');
        const label = element('div', title, 'studio-lane-label'); label.append(element('small', subtitle));
        const area = element('div', undefined, 'studio-timeline-lane');
        area.addEventListener('click', event => { if (!event.target.closest('button')) { setMode('project'); setPosition((event.clientX - area.getBoundingClientRect().left) / scale); } });
        if (!clips.length) area.append(element('span', audio ? 'Ajoute une piste audio' : 'Ajoute un plan depuis les médias du projet', 'studio-timeline-placeholder'));
        for (const clip of clips) {
          const block = element('button', undefined, `studio-timeline-clip${audio ? ' audio-clip' : ''}`);
          block.type = 'button'; block.dataset.trackId = clip.row.id;
          block.style.left = `${clip.start * scale}px`; block.style.width = `${Math.max(8, clip.duration * scale - 2)}px`;
          block.setAttribute('aria-label', `${clip.row.label}, ${audio ? 'piste audio' : 'plan'}, ${clip.duration} secondes${clip.row.locked ? ', verrouillé' : ''}`);
          if (clip.asset?.kind === 'image') { const thumb = element('img'); thumb.src = clip.asset.url; thumb.alt = ''; thumb.loading = 'lazy'; block.append(thumb); }
          const text = element('span', undefined, 'studio-timeline-clip-copy');
          text.append(element('strong', `${clip.row.locked ? '▣ ' : ''}${clip.row.label}`), element('small', `${clip.duration.toFixed(1)} s${audio ? ` · ${Math.round(clip.row.volume * 100)} %` : ''}`));
          block.append(text); block.addEventListener('click', () => selectTrack(clip.row.id)); area.append(block);
        }
        row.append(label, area); $('timeline-lanes').append(row);
      }
      lane('V1', 'Vidéo / image', timeline.visuals);
      if (timeline.audio.length) timeline.audio.forEach((clip, index) => lane(`A${index + 1}`, {voice:'Voix',music:'Musique',ambience:'Ambiance',effect:'Effet'}[clip.row.role], [clip], true));
      else lane('A1', 'Audio', [], true);
      $('timeline-summary').textContent = `${timeline.visuals.length} plan${timeline.visuals.length > 1 ? 's' : ''} · ${timeline.audio.length} piste${timeline.audio.length > 1 ? 's' : ''}`;
      $('timeline-duration').textContent = timecode(timeline.duration, getScene().settings.fps);
      selection(); renderMonitor();
    }
    function refresh() {
      const scene = getScene(), assets = getAssets();
      const next = JSON.stringify([scene, assets.map(asset => [asset.id, asset.metadata])]);
      if (next !== signature || scene !== cachedScene || assets !== cachedAssets) {
        signature = next;
        cachedTimeline = null;
        stop(); clearSounds(); position = Math.min(position, data().duration); renderTimeline();
      }
      const settings = scene.settings;
      $('monitor-info').textContent = `${settings.width} × ${settings.height} · ${settings.fps} fps`;
      $('reference-count').textContent = `${scene.references.length} référence${scene.references.length > 1 ? 's' : ''}`;
      selection();
    }
    $('timeline-ruler').addEventListener('click', event => { setMode('project'); setPosition((event.clientX - $('timeline-ruler').getBoundingClientRect().left) / Number($('timeline-zoom').value)); });
    $('timeline-zoom').addEventListener('input', renderTimeline);
    $('timeline-fit').addEventListener('click', () => {
      const timeline = data(), length = Math.max(12, timeline.duration, ...timeline.audio.map(clip => clip.start + clip.duration)) + 2;
      $('timeline-zoom').value = Math.min(120, Math.max(12, ($('timeline-scroll').clientWidth - 80) / length)); renderTimeline();
    });
    $('preview-seek').addEventListener('input', event => setPosition(event.target.value));
    $('preview-start').addEventListener('click', () => setPosition(0));
    $('preview-next').addEventListener('click', () => setPosition(data().visuals.find(clip => clip.start > position + .001)?.start ?? data().duration));
    keyboardTabs([...root.querySelectorAll('[data-monitor-mode]')], tab => setMode(tab.dataset.monitorMode));
    $('preview-play').addEventListener('click', () => {
      if (playing) { stop(); return; }
      if (!data().duration) return;
      if (position >= data().duration) setPosition(0);
      if (!audioContext && (global.AudioContext || global.webkitAudioContext)) audioContext = new (global.AudioContext || global.webkitAudioContext)();
      audioContext?.resume().catch(() => {});
      playing = true; anchor = performance.now() - position * 1000;
      $('preview-play').textContent = 'Ⅱ'; $('preview-play').setAttribute('aria-label', 'Mettre le montage en pause');
      $('preview-play').setAttribute('aria-pressed', 'true'); renderMonitor(true); syncAudio(true);
      function advance(now) {
        if (!playing) return;
        position = Math.min(data().duration, (now - anchor) / 1000); renderMonitor(); syncAudio();
        if (position >= data().duration) stop(); else frame = requestAnimationFrame(advance);
      }
      frame = requestAnimationFrame(advance);
    });
    document.addEventListener('visibilitychange', () => { if (document.hidden) stop(); });
    global.addEventListener('pagehide', () => { stop(); clearSounds(); audioContext?.close(); });
    if (global.ResizeObserver) new ResizeObserver(renderTimeline).observe($('timeline-scroll'));
    return {refresh, selectAsset, selectTrack, activateTab, stop};
  }
  const api = {create, timecode, segments, visualAt};
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
  else global.AI2AIStudioEditor = api;
})(typeof window !== 'undefined' ? window : globalThis);
