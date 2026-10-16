# Moteur Markdown Rust/WebAssembly

Le chat et Bara analysent les réponses Markdown longues avec
[Comrak](https://github.com/kivikakk/comrak), compilé pour
[`wasm32-unknown-unknown`](https://doc.rust-lang.org/rustc/platform-support/wasm32-unknown-unknown.html).
L'analyse des titres, listes, tableaux et blocs de code est un traitement répété
à chaque image pendant le streaming. C'est la partie JavaScript migrée vers Rust.

`static/app/js/markdown.js` conserve l'API synchrone `AI2AIMarkdown.parse(text)`.
À partir de 8 192 unités UTF-16 (`text.length`), il charge le module local une seule
fois, sans bloquer le rendu courant. Une fois chargé, Rust traite les prochains
rendus longs. Les textes courts restent dans Marked, même après chargement : ils
ne justifient pas les transferts de chaînes. Une erreur réseau, une politique CSP
qui bloque Wasm, un navigateur incompatible ou une erreur d'exécution réutilise
Marked. Le chargement ne relance aucun appel IA et n'envoie aucun texte au serveur.

Le module ne modifie ni le DOM ni les styles. Son HTML passe **toujours par
DOMPurify** dans les trois points de rendu existants. Les tableaux GFM, liens
automatiques, listes de tâches et texte barré sont activés. Les marqueurs de
sources et blocs `2ai-preview` sont préservés. Comrak suit CommonMark/GFM : les
détails HTML et certains cas Markdown ambigus peuvent différer de Marked.

## Construction et vérification

La chaîne Rust et la cible sont fixées dans `rust-toolchain.toml`. Avec rustup
installé, depuis la racine du dépôt :

```sh
python scripts/build_markdown_wasm.py
python scripts/build_markdown_wasm.py --check
cargo test --locked --manifest-path rust/markdown/Cargo.toml
cargo clippy --locked --release --target wasm32-unknown-unknown --manifest-path rust/markdown/Cargo.toml -- -D warnings
node --test tests/js/markdown.test.cjs tests/js/service_worker.test.cjs
python tests/browser/markdown.py
node tests/bench/markdown.cjs
```

Le script produit `static/app/wasm/markdown_core.wasm`, les notices de licences et
un manifeste SHA-256 des sources et de l'artefact. Ils sont versionnés : le serveur
Django et le navigateur n'ont pas besoin de Rust installé. `--check` ne demande
pas Rust ; la CI vérifie aussi les tests natifs et la compilation pour Wasm.
`collectstatic` distribue le module comme les autres ressources locales, avec une
adresse portant son empreinte en production. Le service worker rafraîchit les
modules Wasm comme les scripts.

## Mesures

Le benchmark compare Marked à Rust/Wasm avec les mêmes textes mêlant paragraphes,
tableaux, listes et code. Il inclut l'encodage UTF-8 et la copie de la sortie HTML.
Il exclut le premier téléchargement, la compilation WebAssembly, DOMPurify et les
modifications du DOM. Il affiche la médiane de cinq séries après échauffement.
Les gains concernent cette analyse, sans prédire la vitesse de génération de l'IA.

La CSP inclut [`'wasm-unsafe-eval'`](https://www.w3.org/TR/CSP/#framework-directive-source-list)
dans `script-src` pour permettre la compilation WebAssembly. Elle ne contient
ni `'unsafe-eval'` ni `'unsafe-inline'` pour JavaScript. Une politique imposée par
le proxy qui refuse Wasm active le repli JavaScript.

Le module initial pèse environ 476 ko, soit 152 ko avec gzip. Il n'est téléchargé
qu'au premier rendu long ; la compression dépend du serveur de fichiers statiques.
Les petits traitements d'interface, appels réseau, graphiques et calculs de
timeline restent en JavaScript : leur migration n'apporte pas de gain démontré.
