const assert = require('node:assert/strict');
const test = require('node:test');
require('../../static/app/js/chat_transport.js');

function response(parts) {
  return new Response(new ReadableStream({
    start(controller) {
      for (const part of parts) controller.enqueue(part);
      controller.close();
    }
  }));
}

test('reads CRLF events and UTF-8 text split between network chunks', async () => {
  const bytes = new TextEncoder().encode('data: {"type":"delta","content":"Été"}\r\n\r\ndata: {"type":"done","reply":"Été"}\r\n\r\n');
  const parts = Array.from(bytes, byte => Uint8Array.of(byte));
  const deltas = [];
  const result = await AI2AITransport.readCompletionStream(response(parts), text => deltas.push(text));
  assert.equal(result.reply, 'Été');
  assert.deepEqual(deltas, ['Été']);
});

test('propagates a provider error instead of returning a partial success', async () => {
  const bytes = new TextEncoder().encode('data: {"type":"error","error":{"message":"Provider failed"}}\n\n');
  await assert.rejects(AI2AITransport.readCompletionStream(response([bytes]), () => {}), /Provider failed/);
});

test('rejects a stream missing its final event', async () => {
  const bytes = new TextEncoder().encode('data: {"type":"delta","content":"Partial"}\n\n');
  await assert.rejects(AI2AITransport.readCompletionStream(response([bytes]), () => {}), /avant la réponse finale/);
});

test('delivers tool progress and sources independently of answer deltas', async () => {
  const bytes = new TextEncoder().encode('data: {"type":"tool_status","tool":"web_search","status":"running"}\n\ndata: {"type":"sources","sources":[]}\n\ndata: {"type":"done","reply":"Answer"}\n\n');
  const events = [];
  await AI2AITransport.readCompletionStream(response([bytes]), () => {}, event => events.push(event.type));
  assert.deepEqual(events, ['tool_status', 'sources', 'done']);
});
