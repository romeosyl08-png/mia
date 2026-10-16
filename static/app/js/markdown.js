/* Rust/Wasm parses long Markdown; long streams are throttled and DOMPurify still sanitizes HTML. */
(function (root) {
  'use strict';

  const MIN_RUST_LENGTH = 8192;
  const LONG_STREAM_RENDER_INTERVAL_MS = 100;

  function createStreamingScheduler(options = {}) {
    const {
      render,
      getLength,
      requestFrame = callback => root.requestAnimationFrame(callback),
      cancelFrame = id => root.cancelAnimationFrame(id),
      setTimer = (callback, delay) => root.setTimeout(callback, delay),
      clearTimer = id => root.clearTimeout(id),
      now = () => root.performance?.now?.() ?? Date.now(),
      threshold = MIN_RUST_LENGTH,
      interval = LONG_STREAM_RENDER_INTERVAL_MS
    } = options;
    if (typeof render !== 'function' || typeof getLength !== 'function') {
      throw new TypeError('A streaming renderer requires render and getLength functions');
    }

    let frameId = null;
    let timerId = null;
    let lastRenderAt = -Infinity;
    let pending = false;
    let cancelled = false;

    function paint() {
      frameId = null;
      if (cancelled || !pending) return;
      pending = false;
      lastRenderAt = now();
      render();
    }

    function scheduleFrame() {
      if (cancelled || frameId !== null) return;
      frameId = requestFrame(paint);
    }

    function schedule() {
      if (cancelled) return;
      pending = true;
      const isLongResponse = Number(getLength()) >= threshold;
      const delay = isLongResponse ? Math.max(0, interval - (now() - lastRenderAt)) : 0;
      if (delay > 0) {
        if (timerId === null) {
          timerId = setTimer(() => {
            timerId = null;
            scheduleFrame();
          }, delay);
        }
        return;
      }
      if (timerId !== null) {
        clearTimer(timerId);
        timerId = null;
      }
      scheduleFrame();
    }

    schedule.cancel = () => {
      if (cancelled) return;
      cancelled = true;
      pending = false;
      if (timerId !== null) clearTimer(timerId);
      if (frameId !== null) cancelFrame(frameId);
      timerId = null;
      frameId = null;
    };

    return schedule;
  }

  function create({url, fallback}) {
    let renderer = null, loading = null, failed = false;

    function load() {
      if (loading) return loading;
      loading = (async () => {
        try {
          if (!url || !root.WebAssembly || !root.TextEncoder || !root.TextDecoder) return false;
          const response = await root.fetch(url, {credentials: 'same-origin'});
          if (!response.ok) return false;
          // arrayBuffer also works when a static server sends the wrong Wasm MIME type.
          const {instance} = await root.WebAssembly.instantiate(await response.arrayBuffer(), {});
          const api = instance.exports;
          if (!api.memory || !api.markdown_input || !api.markdown_render || !api.markdown_output_len) return false;
          const encoder = new root.TextEncoder(), decoder = new root.TextDecoder();
          renderer = text => {
            const bytes = encoder.encode(text);
            const input = api.markdown_input(bytes.length);
            new Uint8Array(api.memory.buffer, input, bytes.length).set(bytes);
            const output = api.markdown_render();
            // Rendering may grow memory, so create a fresh view after the call.
            return decoder.decode(new Uint8Array(api.memory.buffer, output, api.markdown_output_len()));
          };
          return true;
        } catch (_error) {
          return false;
        }
      })();
      return loading;
    }

    function parse(text) {
      if (typeof text !== 'string' || text.length < MIN_RUST_LENGTH || failed) return fallback(text);
      if (!renderer) {
        void load();
        return fallback(text);
      }
      try {
        return renderer(text);
      } catch (_error) {
        // A failed/blocked Wasm runtime must never interrupt a conversation.
        renderer = null;
        failed = true;
        return fallback(text);
      }
    }

    return {
      parse,
      load,
      createStreamingScheduler,
      get backend() { return renderer ? 'rust' : 'javascript'; }
    };
  }

  if (typeof module !== 'undefined' && module.exports) {
    module.exports = {create, createStreamingScheduler, MIN_RUST_LENGTH, LONG_STREAM_RENDER_INTERVAL_MS};
  }
  else root.AI2AIMarkdown = create({
    url: root.document.currentScript?.dataset.wasmUrl,
    fallback: text => root.marked.parse(text)
  });
})(globalThis);
