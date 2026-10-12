(function (root) {
  "use strict";

  async function readCompletionStream(response, onDelta, onEvent=()=>{}) {
    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";
    let result = null;
    const handleEvent = text => {
      const lines = text.split(/\r?\n/).filter(line => line.startsWith("data:"));
      if (!lines.length) return;
      const data = lines.map(line => line.slice(5).trimStart()).join("\n");
      if (!data.trim() || data.trim() === "[DONE]") return;
      const event = JSON.parse(data);
      if (event.type === "delta") onDelta(event.content || "");
      if (event.type === "done") result = event;
      if (event.type === "error") {
        onEvent(event);
        const error = new Error(event.error?.message || "La génération a échoué.");
        error.recovery = event.recovery;
        throw error;
      }
      onEvent(event);
    };
    try {
      while (true) {
        const { value, done } = await reader.read();
        buffer += decoder.decode(value || new Uint8Array(), { stream: !done });
        let boundary = /\r?\n\r?\n/.exec(buffer);
        while (boundary) {
          handleEvent(buffer.slice(0, boundary.index));
          buffer = buffer.slice(boundary.index + boundary[0].length);
          boundary = /\r?\n\r?\n/.exec(buffer);
        }
        if (done) break;
      }
      if (buffer.trim()) handleEvent(buffer);
      if (!result) throw new Error("Le flux s’est terminé avant la réponse finale.");
      return result;
    } catch (error) {
      await reader.cancel().catch(() => {});
      throw error;
    } finally {
      reader.releaseLock();
    }
  }

  root.AI2AITransport = { readCompletionStream };
})(globalThis);
