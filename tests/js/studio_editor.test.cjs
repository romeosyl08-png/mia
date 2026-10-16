const test = require('node:test');
const assert = require('node:assert/strict');
const {timecode, segments, visualAt} = require('../../static/app/js/studio_editor.js');

test('timecodes respect the project frame rate and minute boundaries', () => {
  assert.equal(timecode(1.5,24), '00:00:01:12');
  assert.equal(timecode(1.5,30), '00:00:01:15');
  assert.equal(timecode(60,24), '00:01:00:00');
  assert.equal(timecode(3661.25,24), '01:01:01:06');
  assert.equal(timecode(-10,24), '00:00:00:00');
});

test('visual clips follow scene order and audio keeps its independent offset', () => {
  const scene = {visuals:[{id:'a',asset_id:1,duration:2.5},{id:'b',asset_id:2,duration:4}],
    audio:[{id:'music',asset_id:3,start:1.25}],settings:{duration:20}};
  const assets = [{id:1},{id:2},{id:3,metadata:{duration:12}}];
  const result = segments(scene,assets);
  assert.deepEqual(result.visuals.map(clip => clip.start),[0,2.5]);
  assert.equal(result.duration,6.5);
  assert.equal(result.audio[0].start,1.25);
  assert.equal(result.audio[0].duration,12);
  assert.equal(result.visuals[0].asset,assets[0]);
  assert.equal(result.audio[0].asset,assets[2]);
  assert.equal(scene.visuals[0].duration,2.5);
});

test('playhead lookup handles clip boundaries and long timelines', () => {
  const scene = {visuals:Array.from({length:4096}, (_, index) => ({id:`clip-${index}`,asset_id:index,duration:1})),
    audio:[],settings:{duration:60}};
  const timeline = segments(scene, []);
  assert.equal(visualAt(timeline.visuals, 2048.5, timeline.duration).row.id, 'clip-2048');
  assert.equal(visualAt(timeline.visuals, 2049, timeline.duration).row.id, 'clip-2049');
  assert.equal(visualAt(timeline.visuals, timeline.duration, timeline.duration).row.id, 'clip-4095');
  assert.equal(visualAt(timeline.visuals, timeline.duration + 0.1, timeline.duration), null);
  assert.equal(visualAt([], 0, 0), null);
});

test('audio-only previews use the export duration and a new project is empty', () => {
  assert.equal(segments({visuals:[],audio:[{asset_id:1,start:0}],settings:{duration:15}},[{id:1,metadata:{duration:3}}]).duration,15);
  assert.equal(segments({visuals:[],audio:[],settings:{duration:20}},[]).duration,0);
});

test('audio clips end within the three-minute editing limit', () => {
  const result = segments({visuals:[],audio:[{asset_id:1,start:175}],settings:{duration:180}},[{id:1,metadata:{duration:30}}]);
  assert.equal(result.audio[0].duration,5);
});
