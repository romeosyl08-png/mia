const assert = require('node:assert/strict');
const test = require('node:test');
const {
  createStreamingScheduler,
  MIN_RUST_LENGTH,
  LONG_STREAM_RENDER_INTERVAL_MS
} = require('../../static/app/js/markdown.js');

function harness(initialLength, onRender = () => {}) {
  let time = 0;
  let sequence = 0;
  let length = initialLength;
  let renders = 0;
  const frames = new Map();
  const timers = new Map();
  const schedule = createStreamingScheduler({
    getLength: () => length,
    render: () => { renders += 1; onRender(length); },
    requestFrame: callback => {
      const id = ++sequence;
      frames.set(id, callback);
      return id;
    },
    cancelFrame: id => frames.delete(id),
    setTimer: (callback, delay) => {
      const id = ++sequence;
      timers.set(id, {callback, due: time + delay});
      return id;
    },
    clearTimer: id => timers.delete(id),
    now: () => time
  });

  return {
    schedule,
    frames,
    timers,
    get renders() { return renders; },
    set length(value) { length = value; },
    flushFrames() {
      const callbacks = Array.from(frames.values());
      frames.clear();
      callbacks.forEach(callback => callback());
    },
    advance(milliseconds) {
      const target = time + milliseconds;
      while (true) {
        const next = Array.from(timers.entries())
          .filter(([, timer]) => timer.due <= target)
          .sort((left, right) => left[1].due - right[1].due)[0];
        if (!next) break;
        const [id, timer] = next;
        timers.delete(id);
        time = timer.due;
        timer.callback();
      }
      time = target;
    }
  };
}

test('short responses coalesce token updates to one animation frame without a timer throttle', () => {
  const stream = harness(MIN_RUST_LENGTH - 1);
  stream.schedule();
  stream.schedule();
  stream.schedule();
  assert.equal(stream.frames.size, 1);
  assert.equal(stream.timers.size, 0);
  stream.flushFrames();
  assert.equal(stream.renders, 1);

  stream.advance(1);
  stream.schedule();
  stream.flushFrames();
  assert.equal(stream.renders, 2);
});

test('long responses render promptly, then at most once per interval using the latest text', () => {
  const renderedLengths = [];
  const stream = harness(MIN_RUST_LENGTH, length => renderedLengths.push(length));
  stream.schedule();
  stream.flushFrames();
  assert.deepEqual(renderedLengths, [MIN_RUST_LENGTH]);

  stream.advance(35);
  stream.schedule();
  stream.length = MIN_RUST_LENGTH + 1000;
  stream.schedule();
  assert.equal(stream.frames.size, 0);
  assert.equal(stream.timers.size, 1);

  stream.advance(LONG_STREAM_RENDER_INTERVAL_MS - 36);
  assert.equal(stream.frames.size, 0);
  stream.advance(1);
  assert.equal(stream.frames.size, 1);
  stream.flushFrames();
  assert.deepEqual(renderedLengths, [MIN_RUST_LENGTH, MIN_RUST_LENGTH + 1000]);
  assert.equal(stream.timers.size, 0);
});

test('cancel prevents queued frames and delayed long-response renders', () => {
  const shortStream = harness(100);
  shortStream.schedule();
  shortStream.schedule.cancel();
  shortStream.flushFrames();
  assert.equal(shortStream.renders, 0);

  const longStream = harness(MIN_RUST_LENGTH);
  longStream.schedule();
  longStream.flushFrames();
  longStream.advance(10);
  longStream.schedule();
  assert.equal(longStream.timers.size, 1);
  longStream.schedule.cancel();
  assert.equal(longStream.timers.size, 0);
  longStream.advance(LONG_STREAM_RENDER_INTERVAL_MS * 2);
  longStream.flushFrames();
  assert.equal(longStream.renders, 1);
});
