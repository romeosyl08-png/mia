# Deployer sur Render

## 1. Publier le depot

Pousse le projet sur GitHub ou GitLab, puis cree un Blueprint Render avec `render.yaml`.
Render creera le service web, la base PostgreSQL et un disque persistant de 1 Go.
Le service web utilise le plan payant `starter`, nécessaire pour attacher un disque ; vérifie son tarif dans Render avant de déployer.

## 2. Variables obligatoires

Dans le service web Render, renseigne `FERNET_KEY` avec une cle Fernet valide. Tu peux en generer une localement :

```bash
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

Renseigne également `OPENROUTER_API_KEY` pour les appels cloud.

`SECRET_KEY`, `DATABASE_URL`, `DEBUG`, `ALLOWED_HOSTS`, `CSRF_TRUSTED_ORIGINS`, `MEDIA_ROOT` et `PRIVATE_MEDIA_ROOT` sont deja configurees par `render.yaml`.

## 3. Premier deploiement

Le build installe les dependances et collecte les fichiers statiques. Le processus de demarrage execute les migrations, initialise les fournisseurs et leurs clés, puis synchronise le catalogue OpenRouter avant de lancer Gunicorn et le worker de tâches via `run_services`. Les deux processus partagent le même disque. Une panne d’un processus arrête le service pour permettre son redémarrage. L'option `--allow-stale` conserve le catalogue existant si OpenRouter est indisponible. Une panne du catalogue empêche le premier démarrage tant qu'aucun catalogue n'a été initialisé.

Les fichiers Bara sont conservés sous `/var/data/media` et les médias générés privés sous `/var/data/private_media`, sur le disque persistant. Pour mettre à jour une instance existante, sauvegarde les anciens répertoires `media/` et `private_media/`, puis transfère leur contenu vers ces répertoires en conservant les chemins relatifs. Les chemins enregistrés en base restent valides. Le disque est accessible au runtime ; le build ne doit pas y écrire.

Apres le premier lancement, ouvre `/admin/` et cree un compte administrateur si necessaire :

```bash
python manage.py createsuperuser
```

Sur Render, execute cette commande depuis le Shell du service web.

## 4. Integrations optionnelles

Ajoute les secrets Stripe, Telegram et API dans les variables d'environnement Render si ces fonctions sont utilisees. Configure ensuite l'URL publique du service dans les webhooks Stripe et Telegram.

Pour activer PayDunya, ajoute `PAYDUNYA_MASTER_KEY`, `PAYDUNYA_PRIVATE_KEY`, `PAYDUNYA_TOKEN` et, si nécessaire, `PAYDUNYA_STORE_NAME` et `PAYDUNYA_XOF_PER_USD`. Le minimum est de `PAYDUNYA_MINIMUM_XOF=200` FCFA par défaut. Les clés privées commençant par `test_private_` utilisent automatiquement l'endpoint sandbox; les clés live utilisent l'endpoint live. Utilise `PAYDUNYA_CHECKOUT_URL` seulement pour remplacer explicitement ce choix. PayDunya doit pouvoir appeler l'URL publique `https://<domaine-render>/recharge/paydunya-webhook/`; l'application vérifie le hash IPN, le token et le montant avant de créditer les jetons.

Pour activer le chat cloud et les générations d'images et de vidéos, configure `OPENROUTER_API_KEY`. Les modèles média se règlent avec `OPENROUTER_IMAGE_MODEL` et `OPENROUTER_VIDEO_MODEL` (par défaut : `bytedance-seed/seedream-4.5` et `alibaba/wan-3.0`). L’audio utilise toujours les clés et modèles audio configurés séparément.

Pour utiliser l'API compatible OpenAI, chaque utilisateur crée sa clé depuis `/api-keys/` et l'envoie dans `Authorization: Bearer sk-ai2ai-...`. Les requetes sont limitees a 60 par minute et par clé par defaut. Ajuste `API_RATE_LIMIT_REQUESTS` et `API_RATE_LIMIT_WINDOW_SECONDS` si necessaire. Configure `REDIS_URL` pour partager ces compteurs entre les processus applicatifs.

Pour activer Gmail et Outlook dans Bara, renseigne `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET`, `MICROSOFT_CLIENT_ID` et `MICROSOFT_CLIENT_SECRET` dans les variables du service. `MICROSOFT_TENANT_ID` est facultatif et vaut `common` par defaut. Enregistre les URI de redirection OAuth suivantes dans les consoles Google et Microsoft :

```text
https://<domaine-render>/bara/plugins/email/google_mail/callback/
https://<domaine-render>/bara/plugins/email/microsoft_mail/callback/
```

Google demande les scopes Gmail `gmail.readonly` et `gmail.compose`; Microsoft demande `Mail.ReadWrite` et `Mail.Send`. `Mail.ReadWrite` est plus large que les actions Outlook offertes dans Bara. Les envois restent soumis a une confirmation dans l'interface.

Pour Blender, telecharge le module complementaire depuis `AUTOMATISATIONS`, installe-le depuis les preferences Blender, puis configure l'URL Render et le jeton genere dans Bara. L'extension se connecte au service par HTTPS sortant. Elle ne recoit que les actions autorisees par Bara, et les modifications de scene necessitent une confirmation.

2AI est installable comme PWA sur le domaine HTTPS Render. Le service worker met uniquement en cache les ressources publiques sous `/static/` ; il ne met pas en cache les pages authentifiees ni les conversations.

## Notes

- PostgreSQL est la base persistante de production ; SQLite reste uniquement un fallback local.
- Ne commit jamais `.env`, les cles API ou la base SQLite.
- Les répertoires `media/`, `private_media/`, `build/`, `dist/`, `*.egg-info/` et les fichiers Python compilés sont exclus de Git ; sauvegarde les documents via le stockage persistant.
- `CHAT_HISTORY_MAX_MESSAGES` (48) et `CHAT_HISTORY_MAX_CHARS` (48000) limitent le contexte transmis aux modèles. L'historique enregistré en base reste complet.
- Le plan PostgreSQL `free` peut dependre de la disponibilite de Render. Si Render le refuse, choisis un plan PostgreSQL disponible dans le tableau de bord.

## Tâches et nouveaux services

Le worker est lancé par `run_services`; aucun service de file externe n’est requis. Configure `REDIS_URL` en production pour partager les compteurs de limitation entre processus. Les tâches et leurs étapes sont enregistrées dans PostgreSQL. Les programmations utilisent UTC.

Drive et Calendar utilisent la même application Google, avec des autorisations de lecture distinctes. Ajoute ces redirections :

```text
https://<domaine-render>/bara/plugins/email/google_drive/callback/
https://<domaine-render>/bara/plugins/email/google_calendar/callback/
```

La recherche sémantique nécessite `EMBEDDING_MODEL`, `EMBEDDING_API_URL` et `EMBEDDING_API_KEY`, puis l’activation par projet et l’indexation explicite. La voix utilise les fonctions du navigateur.

L’exécution de code et l’OCR exigent un hôte pouvant lancer les conteneurs préconstruits. Le service Python Render standard n’est pas configuré pour Docker ; laisse `BARA_CODE_EXECUTION_ENABLED=false` sur cette configuration. Pour ces fonctions, utilise une infrastructure disposant d’un runtime isolé (de préférence rootless) et d’un volume local accessible au worker. Ne monte jamais le socket Docker dans le conteneur qui exécute le code utilisateur.
