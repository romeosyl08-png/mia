# Logo 2AI

`logo-source.png` conserve le fichier fourni et `logo.webp` sa version réduite
pour l’interface. Ces images restent inchangées lors du changement de thème.

Le filtre dans `chat/templates/includes/logo_filters.html` rend le fond noir
transparent et colore les lettres avec `--logo-ink`. L’orbite et l’étoile gardent
leurs couleurs. Le composant `app_logo.html` utilise ce rendu sur chaque page.

`favicon-light.svg` et `favicon-dark.svg` embarquent la même image et le même
filtre, avec leur couleur de lettres respective. `appearance.js` sélectionne
le favicon du thème actif. Après une modification du filtre ou de l’image,
régénérer les favicons avec les mêmes définitions et une image intégrée en
URI de données, pour qu’ils restent autonomes.
