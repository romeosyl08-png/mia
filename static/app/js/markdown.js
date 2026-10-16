/* Long Markdown responses are parsed in Rust/Wasm; DOMPurify still sanitizes HTML. */
(function (root) {
  'use strict';

  const MIN_RUST_LENGTH = 8192;

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

    return {parse, load, get backend() { return renderer ? 'rust' : 'javascript'; }};
  }

  if (typeof module !== 'undefined' && module.exports) module.exports = {create, MIN_RUST_LENGTH};
  else root.AI2AIMarkdown = create({
    url: root.document.currentScript?.dataset.wasmUrl,
    fallback: text => root.marked.parse(text)
  });
})(globalThis);
