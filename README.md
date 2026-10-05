# ai_2ai
# mia

## PostgreSQL local

Le fichier `.env` à la racine du projet (`ai_2ai/.env`) peut sélectionner une base PostgreSQL locale :

```dotenv
DATABASE_URL=postgresql://postgres:mot_de_passe@127.0.0.1:5432/ai_2ai
```

Crée d'abord la base `ai_2ai` dans PostgreSQL, puis applique les migrations avec `python manage.py migrate`. Si `DATABASE_URL` est absent, le projet utilise SQLite (`db.sqlite3`).

## Génération de médias

Les images, vidéos et audios utilisent les clés serveur des fournisseurs. Configure une ou plusieurs clés dans l'environnement, puis lance `python manage.py seed_providers` et `python manage.py sync_provider_keys` :

```dotenv
FAL_API_KEY=
REPLICATE_API_TOKEN=
APIFRAME_API_KEY=
```

Les modèles par défaut sont configurables avec `FAL_IMAGE_MODEL`, `FAL_VIDEO_MODEL`, `FAL_AUDIO_MODEL`, `REPLICATE_IMAGE_MODEL`, `REPLICATE_VIDEO_MODEL`, `REPLICATE_AUDIO_MODEL`, `APIFRAME_IMAGE_MODEL`, `APIFRAME_VIDEO_MODEL` et `APIFRAME_AUDIO_MODEL`. Le compositeur propose des dimensions image standard (carré, portrait ou paysage), et pour la vidéo un ratio, une résolution, une durée et l’activation du son. L’audio propose un style et une option instrumentale. Les options disponibles sont restreintes selon les capacités du modèle choisi; par exemple Replicate MiniMax Video-01 reste en 16:9, 720p, six secondes, sans son.

Les crédits internes se règlent avec `MEDIA_IMAGE_CREDITS` (630 pour 1 MP), `MEDIA_VIDEO_CREDITS` (10000 pour 8 secondes en 720p) et `MEDIA_AUDIO_CREDITS` (2000 par génération). Le débit image évolue avec le nombre de pixels et le débit vidéo avec la durée et la résolution sélectionnées. Ces montants sont des crédits internes, pas un devis du fournisseur : ajuste-les à tes modèles et coûts réels avant la production.

## AI Router — Phases 1 à 6

Les requêtes standard du chat, Bara et API enregistrent une recommandation shadow sans changer leur modèle sélectionné. Dans le chat, le choix explicite `Auto 2AI` (ou le modèle par défaut `Auto 2AI` dans les paramètres) active le routage. Dans Bara, chaque workspace peut être basculé de son modèle manuel vers `Auto 2AI`; le modèle sélectionné précédemment reste son fallback. Les modèles candidats doivent être actifs, avoir une clé serveur active (sauf fournisseur local) et respecter les contraintes outils/streaming de la surface.

Les observations enregistrent la surface, la catégorie, le modèle réellement exécuté, la recommandation, le résultat technique, la durée, les tokens, les crédits estimés/débités et les outils réellement utilisés; elles ne stockent ni prompt ni réponse. Les conversations temporaires ne créent pas d’observation. Le score combine fiabilité technique, coût et durée avec un seuil de cinq observations et un repli utilisateur/catégorie/global.

Dans le chat, les votes J’aime/Je n’aime pas sont désormais persistés par réponse; un vote négatif est marqué comme correction nécessaire. Après au moins cinq feedbacks dans la portée considérée, le score ajoute un signal de qualité lissé. Ce signal vient uniquement de ces votes explicites; une réussite fournisseur seule n’est pas considérée comme une réponse de qualité.

L’équipe garde « Comparer et synthétiser » comme stratégie par défaut. Une option distincte « Planifier, réaliser, vérifier » orchestre trois étapes avec deux ou trois modèles sélectionnés; le plan et le brouillon restent internes et seule la réponse vérifiée est affichée. Le mode est également transmis lors du streaming.

L’API compatible OpenAI continue d’exiger un identifiant de modèle explicite : `model="auto"` n’est pas encore activé. L’Auto actif concerne le chat et les workspaces Bara; aucune orchestration automatique n’est ajoutée à l’API.

## Barrassons CLI

Barrassons is an AI2AI coding assistant that runs in the current project directory.
It can inspect and search source files, propose edits, and run development commands.
File changes and shell commands always require confirmation in the terminal.

Install from this repository with `pipx`:

```sh
cd ai_2ai
pipx install .
barrassons
```

On first launch, choose browser login to open the AI2AI API-key page, or paste an
existing AI2AI API key. Barrassons stores the key in
`~/.config/barrassons/config.json` with owner-only permissions. To use another
AI2AI deployment, set `BARRASSONS_API_BASE` to its `/v1` API URL or enter it during
login. Run `barrassons login` to change credentials and `barrassons logout` to
remove the locally stored key.

The selected model must support OpenAI-compatible function tools. API usage is
charged to the AI2AI account associated with the key. Anthropic models and streamed
tool calls are not currently supported by this CLI.
