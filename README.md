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

Les modèles par défaut sont configurables avec `FAL_IMAGE_MODEL`, `FAL_VIDEO_MODEL`, `FAL_AUDIO_MODEL`, `REPLICATE_IMAGE_MODEL`, `REPLICATE_VIDEO_MODEL`, `REPLICATE_AUDIO_MODEL`, `APIFRAME_IMAGE_MODEL`, `APIFRAME_VIDEO_MODEL` et `APIFRAME_AUDIO_MODEL`. Les crédits internes par génération se règlent avec `MEDIA_IMAGE_CREDITS` (630), `MEDIA_VIDEO_CREDITS` (10000) et `MEDIA_AUDIO_CREDITS` (2000). Ajuste ces montants aux modèles et aux coûts réels des comptes fournisseurs avant d’activer la génération en production.

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
