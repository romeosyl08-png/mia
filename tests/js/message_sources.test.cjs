const assert = require('node:assert/strict');
const test = require('node:test');
require('../../static/app/js/message_sources.js');

test('groups citations next to a claim and keeps stable source numbers', () => {
  const sources = [{link:'https://youtube.com/watch?v=1', title:'Video'}, {link:'https://alloprof.qc.ca/vitesse', title:'Vitesse'}];
  const groups = AI2AISources.citationGroups('Une affirmation. [[sources:1,2,1]] Une autre. [[sources:2]]', sources);
  assert.deepEqual(groups[0].sources.map(source => source.publisher), ['YouTube', 'Alloprof']);
  assert.equal(groups[0].sources.length, 2);
  assert.equal(groups[1].sources[0].id, 2);
});

test('citation matching safely reuses its scanner across separate text nodes', () => {
  const sources = [{link:'https://example.test/a'}, {link:'https://example.test/b'}];
  assert.equal(AI2AISources.citationGroups('Premier [[source:2]]', sources)[0].sources[0].id, 2);
  assert.equal(AI2AISources.citationGroups('Second [[sources:1]]', sources)[0].sources[0].id, 1);
});

test('never turns an invented citation or a script URL into a source', () => {
  const sources = [{link:'javascript:alert(1)', title:'Unsafe'}, {link:'https://example.test/page', title:'Real'}];
  assert.deepEqual(AI2AISources.normalizeSources(sources).map(source => source.id), [2]);
  assert.deepEqual(AI2AISources.citationGroups('[[sources:1,999]]', sources)[0].sources, []);
  assert.equal(AI2AISources.safeURL('https://secret@example.test/page'), null);
});
