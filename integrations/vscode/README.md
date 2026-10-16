# Barrassons AI2AI pour VS Code

Suggestions de code inline et assistant de programmation dans le chat VS Code, reliés directement à l’API AI2AI. Les commandes Barrassons dans le terminal intégré restent disponibles.

## Installation

- **Essayer en développement :** ouvre `integrations/vscode/` dans VS Code et appuie sur **F5** pour démarrer l’Extension Development Host.
- **Créer un VSIX :** dans ce dossier, lance `npx --yes @vscode/vsce package`, puis installe le fichier `barrassons-0.2.0.vsix` avec **Extensions → … → Install from VSIX**.
- Nécessite VS Code 1.90 ou plus récent. Cette extension n’est pas publiée sur le Marketplace.

## Connexion et modèles

Lance **Barrassons : configurer la clé API** pour enregistrer une clé AI2AI dans le coffre sécurisé de VS Code (`SecretStorage`). L’extension peut aussi réutiliser la clé du CLI dans `~/.config/barrassons/config.json` ou `$XDG_CONFIG_HOME/barrassons/config.json` après `barrassons login`. Le serveur par défaut est Render ; utilise le réglage `barrassons.apiBase` pour un autre déploiement. Les clés ne sont jamais intégrées à l’URL.

Le modèle `auto` est choisi par défaut. **Barrassons : choisir le modèle** affiche ceux disponibles avec ta clé.

## Utilisation

- **Chat :** ouvre la vue Chat et écris `@barrassons` suivi de ta question. `/explain` explique le code sélectionné/référencé ; `/fix` propose une correction à appliquer manuellement.
- **Complétions inline :** lance **Barrassons : activer/désactiver les suggestions inline**. L’activation demande confirmation et reste désactivable par la même commande. Les suggestions VS Code habituelles ne sont pas remplacées ; Barrassons ajoute ses propositions en ligne.
- **Terminal :** **Barrassons : démarrer dans le projet** et **Barrassons : reprendre une session** nécessitent le CLI `barrassons` dans le `PATH`.

Chaque requête au modèle peut consommer les crédits associés à la clé. Les suggestions inline sont désactivées par défaut et utilisent un délai d’inactivité (350 ms par défaut). Réduis les appels ou la taille du contexte avec `barrassons.inlineCompletions.debounceMs`, `barrassons.inlineCompletions.maxContextChars` et `barrassons.inlineCompletions.maxTokens`.

## Données et confidentialité

Le chat envoie la demande, l’historique récent, la sélection active et les fichiers explicitement référencés dans la conversation. Les fichiers référencés doivent appartenir au workspace. Les extraits de contexte sont bornés à 24 000 caractères au total. Les emplacements et noms usuels de secrets (`.env`, `.git`, `.ssh`, `.aws`, `.netrc`, `.npmrc`, clés privées, jetons ou identifiants) sont ignorés. Le workspace n’est pas parcouru automatiquement.

Les complétions inline envoient le langage, le nom relatif du fichier et jusqu’à 4 500 caractères de code autour du curseur (réglable). Aucun code n’est ajouté au projet par le chat : il propose des réponses et des blocs de code ; seule l’acceptation d’une suggestion inline insère du texte. Toutes les données envoyées sont destinées au serveur indiqué par `barrassons.apiBase`.

## Tests

Depuis ce dossier :

```sh
npm test
```
