// Measures parsing plus UTF-8 transfers; excludes DOMPurify, DOM work and first load.
const fs = require('node:fs');
const path = require('node:path');
const {performance} = require('node:perf_hooks');
const zlib = require('node:zlib');
const marked = require('../../static/app/vendor/marked.min.js');
const {create} = require('../../static/app/js/markdown.js');

async function main() {
  const bytes = fs.readFileSync(path.join(__dirname, '../../static/app/wasm/markdown_core.wasm'));
  const renderer = create({url: `data:application/wasm;base64,${bytes.toString('base64')}`, fallback: marked.parse});
  if (!await renderer.load()) throw Error('Rust/Wasm could not be loaded');
  const block = '# Une réponse détaillée 🐘\n\nUn paragraphe **important**, avec [un lien](https://example.com), du `code` et une source [[sources:1,2]].\n\n'
    + '| Modèle | Résultat |\n|:---|---:|\n| A | 42 |\n| B | 18 |\n\n- [x] Vérifié\n- [ ] À faire\n\n'
    + '```python\ndef salut(nom):\n    return "Bonjour " + nom\n```\n\n';
  const rows = [];
  for (const repetitions of [32, 100, 400]) {
    const text = block.repeat(repetitions);
    const iterations = Math.max(25, Math.floor(20000 / repetitions));
    function time(parse) {
      for (let i = 0; i < 20; i++) parse(text);
      const samples = [];
      for (let round = 0; round < 5; round++) {
        const start = performance.now();
        for (let i = 0; i < iterations; i++) parse(text);
        samples.push((performance.now() - start) / iterations);
      }
      return samples.sort((a, b) => a - b)[2];
    }
    const javascript = time(marked.parse), rust = time(renderer.parse);
    rows.push({characters: text.length, javascript_ms: +javascript.toFixed(3), rust_ms: +rust.toFixed(3), speedup: +(javascript / rust).toFixed(2)});
  }
  console.log(JSON.stringify({node: process.version, wasm_bytes: bytes.length, wasm_gzip_bytes: zlib.gzipSync(bytes).length, samples: rows}, null, 2));
}

main().catch(error => { console.error(error); process.exitCode = 1; });
