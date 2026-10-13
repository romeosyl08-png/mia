# Analyse technique du dépôt `mia` (2AI)

Réalisée le 13 octobre 2026 — branche `arena/89454cbe-mia` @ `076c4be`.

> **Mise à jour** : les points marqués ✅ ci-dessous ont été corrigés dans la
> branche. La suite de tests est passée de 290 tests (1 échec, 15 erreurs selon
> l'environnement) à **323 tests, tous verts**, avec une intégration continue.

---

## 1. Vue d'ensemble

| | |
|---|---|
| **Type** | Monolithe Django 6.1 + frontend JS vanilla (pas de framework) |
| **Apps** | `chat` (assistant/personas/routage IA), `bara` (workspaces, agents, outils, MCP), `studio` (montage multimédia), `ai_user` (auth, portefeuille, paiements), `api` (proxy compatible OpenAI) + `barrassons` (CLI) |
| **Python** | ~21 400 lignes (196 fichiers) |
| **JS/CSS** | ~6 400 lignes JS + 17 fichiers CSS ; `script.js` = 2 764 lignes |
| **Tests** | 290 tests Django (25 fichiers) + 7 scénarios Playwright + 6 suites JS |
| **Déploiement** | Render (`render.yaml`) : 1 service web Gunicorn + 1 worker supervisés par `run_services`, PostgreSQL, volume 1 Go |
| **Historique Git** | **1 seul commit** (tout est squashe) |

### Points forts observés (à préserver)

- Chiffrement Fernet des clés fournisseur (`api/crypto.py`), clés API utilisateur stockées en SHA-256 (`ai_user/models.py:157`), **aucun secret en dur** dans le dépôt.
- Contrôle d'accès systématique : `get_object_or_404(..., user=request.user)` et `accessible_workspaces()` (`bara/access.py`). Aucun IDOR trouvé sur les vues.
- Signatures correctes : HMAC Telegram, validation `state` OAuth Google, `stripe.Webhook.construct_event`, hash SHA-512 PayDunya, `url_has_allowed_host_and_scheme` sur toutes les redirections.
- Sandbox d'exécution de code réellement durci (`bara/tools/sandbox.py`) : `--network=none`, `--read-only`, `--cap-drop=ALL`, limites mémoire/CPU/PID, sortie bornée.
- Facturation centralisée (`chat/billing.py`) avec réservation, ajustement et remboursement sur erreur fournisseur.
- Protection SSRF présente (résolution DNS + `is_global`) sur `fetch_url` et les endpoints MCP.
- Rendu Markdown sanitisé par DOMPurify avant `innerHTML`.
- Documentation produit de bonne qualité (`README.md`, `FEATURES.md`, `PLAYWRIGHT_AUDIT.md`).

---

## 2. Problèmes

### 🔴 P0 — Critiques

#### 2.1 Les fichiers déposés dans Bara sont servis publiquement

`ai_2ai/urls.py:35`

```python
urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
```

Cette ligne est ajoutée **sans condition sur `DEBUG`** : en production sur Render, tout le contenu de `MEDIA_ROOT` est téléchargeable sans authentification.

Or `WorkFile.file` (`bara/models.py:144`) et `FileVersion.binary_file` (`bara/models.py:267`) utilisent le stockage **par défaut** (donc `MEDIA_ROOT`) avec `upload_to="workspaces/%Y/%m/%d/"` et le **nom d'origine du fichier**.

Conséquence : n'importe qui peut accéder à
`/media/workspaces/2026/10/13/contrat-client.pdf` **sans être membre du workspace**, en contournant totalement `get_workspace()` qui protège pourtant `download_file` (`bara/views.py:157`) et `image_file` (`bara/views.py:164`). Les répertoires sont indexables par date et les noms de fichiers sont prévisibles → énumération triviale. C'est une fuite de données confidentielles (documents d'entreprise, pièces jointes, exports).

Le projet possède **déjà la bonne brique** : `PrivateGeneratedMediaStorage` (`chat/storage.py`), utilisé par `GeneratedMedia` et `studio.Asset`, dont `.url()` lève volontairement une erreur pour forcer le passage par une vue qui vérifie le propriétaire.

**Correctif**
1. Créer un `PrivateWorkspaceStorage` sur le même modèle et l'appliquer à `WorkFile.file` et `FileVersion.binary_file` (migration de schéma + script de déplacement des fichiers existants).
2. Supprimer le `static(...)` de production, ou le conditionner à `settings.DEBUG`.
3. Rendre les noms de fichiers non devinables (`uuid4` + conservation du nom lisible en base).
4. Servir les binaires via une vue qui reprend `get_workspace()` (comme `image_file`), avec `Content-Disposition: attachment` et `X-Content-Type-Options: nosniff`.

> À faire aussi : vérifier que `PRIVATE_MEDIA_ROOT` (/var/data/private_media) est bien sur le volume persistant et exclu de tout servage statique.

---

#### 2.2 `marked` et `DOMPurify` chargés depuis un CDN sans SRI, et aucune CSP

`chat/templates/includes/markdown_scripts.html`

```html
<script defer src="https://cdnjs.cloudflare.com/ajax/libs/marked/12.0.0/marked.min.js"></script>
<script defer src="https://cdnjs.cloudflare.com/ajax/libs/dompurify/3.1.0/purify.min.js"></script>
```

Aucun attribut `integrity`/`crossorigin`, et **aucune `Content-Security-Policy`** n'est définie (ni en en-tête, ni en balise `<meta>`).

Toutes les réponses de l'IA passent par `DOMPurify.sanitize(marked.parse(...))` puis `innerHTML` (`static/app/js/script.js:1020`, `:1319`, `static/app/js/workspace.js:69`). Si le CDN est compromis — ou si un réseau le remplace — **la seule barrière XSS de l'application disparaît**, sur du contenu généré par des modèles externes (donc contrôlable par un utilisateur via du prompt indirect depuis une page web aspirée par `fetch_url`).

**Correctif**
1. Self-héberger les deux bibliothèques dans `static/app/vendor/` (WhiteNoise les sert déjà, avec empreinte via `ManifestStaticFilesStorage`).
2. Ajouter `integrity="sha384-…" crossorigin="anonymous"` si le CDN est conservé, avec repli local.
3. Définir une CSP : `script-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'` (adapter pour le Studio/Bara si des iframes sont nécessaires) + `django-csp` pour la gérer proprement.

---

#### 2.3 Aucune déconnexion, aucun changement ni réinitialisation de mot de passe

`ai_user/urls.py` ne contient **ni** `logout`, **ni** `password_change`, **ni** `password_reset`. Seul le CLI `barrassons logout` existe.

Conséquences : un utilisateur ne peut pas se déconnecter d'un poste partagé, ne peut pas changer son mot de passe, et **ne peut pas le réinitialiser** — la seule porte de secours est Telegram ou Google. En cas de fuite de mot de passe, il n'existe aucun moyen de reprise en main du compte.

**Correctif** : brancher `django.contrib.auth.views` (`LogoutView`, `PasswordChangeView`, `PasswordResetView`…) dans `ai_user/urls.py`, configurer `EMAIL_*` (les settings existent déjà mais pointent sur `localhost:25`), et ajouter les gabarits.

---

### 🟠 P1 — Importants

#### 2.4 Aucune CI, aucun linter, historique Git inexploitable

- Pas de `.github/workflows` : les **290 tests Django** et les 13 suites JS/Playwright ne sont jamais exécutés automatiquement. Une régression peut être poussée en production sans signal.
- Pas de `ruff` / `black` / `pre-commit` : le style est hétérogène (voir 2.11).
- **1 seul commit** pour ~21 000 lignes (`codex:update 4:bara style et studio 2`). Aucune revue possible, aucune bisection, perte de la chronologie des décisions.

**Correctif** : workflow GitHub Actions (install → `ruff check` → `ruff format --check` → `python manage.py test` → `node --test tests/js`), branchement `main` protégé, et passage à une historique incrémental (au minimum des commits thématiques par fonctionnalité).

#### 2.5 Dépendances incohérentes

| Constat | Détail |
|---|---|
| `httpx` manquant | importé directement dans `bara/mcp_client.py:6` mais **absent** de `requirements.txt` (il n'arrive que transitivement via `mcp`). Une mise à jour de `mcp` peut casser l'import. |
| `requires-python` faux | `pyproject.toml` annonce `>=3.10`, or `Django==6.1.1` exige **≥ 3.12**. `pip install -r requirements.txt` échoue sur Python 3.11 (vérifié). |
| Pas de `runtime.txt` | Render choisit sa version Python par défaut → l'environnement n'est pas reproductible. |
| Pas de dépendances de dev | ruff, pytest, playwright ne sont déclarés nulle part (uniquement `package.json`). |
| Dépendances non vérifiées | `requirements.txt` est figé mais sans hashes (`pip install --require-hashes` impossible). |

**Correctif** : ajouter `httpx==…`, passer `requires-python = ">=3.12"`, ajouter `runtime.txt` (`python-3.12.x`), créer `requirements-dev.txt`, et documenter la version Python minimale dans le README.

#### 2.6 `node_modules` versionné dans Git

195 fichiers suivis par Git, ~19 Mo, dont des bundles de 3,5 Mo (`node_modules/playwright-core/lib/coreBundle.js`).

**Correctif**

```sh
echo "node_modules/" >> .gitignore
git rm -r --cached node_modules && git commit -m "chore: retirer node_modules du suivi Git"
```

#### 2.7 Worker et serveur web couplés dans le même processus

`bara/management/commands/run_services.py` lance `agent_worker` et `gunicorn` comme processus enfants et **sort en erreur dès que l'un meurt** (`CommandError`). Parallèlement, `agent_worker` est une boucle séquentielle :

```python
task = claim_task();        if task: execute_task(task)
studio_job = claim_job();   if studio_job: execute_job(studio_job)
time.sleep(2)
```

Conséquences :
- Un job Studio peut bloquer le worker jusqu'à `STUDIO_MEDIA_TIMEOUT_SECONDS = 600 s` → les tâches agent et les autres jobs attendent derrière.
- Un plantage du worker (OOM sur un rendu vidéo, par exemple) fait redémarrer **le serveur web**.
- Aucune concurrence, aucune priorité, aucun retry, pas de file dédiée.
- Le polling `sleep(2)` génère une requête DB toutes les 2 s à vide.

**Correctif** : sur Render, déclarer un service **Background Worker** séparé (même dépôt, même volume) ; à défaut, lancer N workers threads/processus avec une file par type de job, et rendre `run_services` tolérant (redémarrer l'enfant au lieu de sortir). Ajouter un backoff sur le polling.

#### 2.8 `FERNET_KEY` : pas de valeur par défaut documentée, rotation impossible

`ai_2ai/settings.py:14` — `env("FERNET_KEY")` sans défaut : l'application **ne démarre pas** sans cette clé, mais aucun `.env.example` n'existe et le README ne la mentionne pas (seul `RENDER.md` en parle).

Surtout, **la rotation est destructrice** : changer la clé rend toutes les clés fournisseur illisibles. `sync_provider_keys` (`api/management/commands/sync_provider_keys.py:37`) avale l'exception et réécrit silencieusement — donc en cas de mauvaise manipulation, on perd les clés sans message clair.

**Correctif** : ajouter `.env.example` (et le `.gitignore` est déjà bon), utiliser `MultiFernet` pour permettre une rotation progressive, journaliser un avertissement au démarrage si des clés sont illisibles.

#### 2.9 Aucune observabilité

- Pas de bloc `LOGGING` dans `settings.py` → configuration Django par défaut uniquement.
- Pas de Sentry / OpenTelemetry / agrégation de logs.
- `render.yaml` sans `healthCheckPath` → Render ne peut pas détecter un service à moitié mort.
- Pas de métriques sur la consommation de crédits, les échecs fournisseur, la latence.

**Correctif** : configurer `LOGGING` (JSON en prod, rotation), ajouter un `/healthz` (DB + cache), brancher Sentry, et poser 3 ou 4 métriques métier (taux d'échec fournisseur, crédits consommés/h, durée des jobs, taille des files).

#### 2.10 Authentification : pas de limitation, e-mail non unique et non vérifié

- **Aucune limitation de débit sur `/login/`, `/signup/`, `/auth/`** → force brute et énumération de comptes librement possibles. Aucun verrouillage de compte.
- `SignupForm` (`ai_user/forms.py`) rend l'e-mail obligatoire mais **pas unique** : deux comptes peuvent partager le même e-mail. La connexion par e-mail (`ai_user/views.py:130`) ne fonctionne alors plus pour les deux (`len(matches) == 1`), sans message explicite.
- Aucune vérification d'e-mail → un utilisateur peut s'inscrire avec l'adresse d'un tiers.
- Pas d'authentification à deux facteurs, et `/admin/` n'est pas protégé au-delà du mot de passe.

**Correctif** : `email` unique (et normalisé en minuscules), vérification par e-mail, limitation par IP + par compte (`django-axes` ou le `check_user_rate_limit` existant), message d'erreur générique (« identifiant ou mot de passe incorrect »), et idéalement 2FA sur `/admin/`.

---

### 🟡 P2 — Qualité, performance, robustesse

#### 2.11 Style et maintenabilité

- **Tabulations** dans `chat/views.py` (1 229 lignes indentées par des tabulations), `chat/tests.py`, `chat/routing.py`, `ai_user/views.py`, `ai_user/tests.py`, `chat/admin.py`… alors que tout le reste du projet utilise 4 espaces. Aucun formateur configuré.
- **Fichiers et fonctions surdimensionnés** : `chat/views.py` 1 428 lignes dont une méthode `SendMessageView.post()` d'environ 400 lignes mêlant validation, routage IA, construction du prompt, facturation et streaming. `static/app/js/script.js` 2 764 lignes. `chat/tests.py` 1 881 lignes.
- Fichier mort : `static/app/js/chat.js` est **vide** (0 octet).
- Versionnage de cache incohérent sur les assets : `?v=frontend-1`, `?v=temporary-actions-2`, `?v=recovery-1`, `?v=citations-1`… alors que `ManifestStaticFilesStorage` ajoute déjà une empreinte en production (les deux mécanismes se doublonnent).
- Duplication : `urls.py` expose les mêmes vues sous `api/conversations/…` **et** `chat/api/conversations/…` sans justification visible.

**Correctif** : `ruff` + `ruff format` (avec une PR de formatage unique pour figer l'historique), découpage de `SendMessageView.post()` en helpers (`_resolve_model`, `_build_system_prompt`, `_reserve_credits`), suppression de `chat.js`, harmonisation des `?v=`.

#### 2.12 Le routeur IA est coûteux à chaque message « Auto »

`chat/routing.py:59` — `_observation_scope` exécute jusqu'à **3 `COUNT(*)`** sur `RoutingObservation` (qui croît sans limite) puis charge jusqu'à `MAX_SCORING_OBSERVATIONS = 500` lignes. `recommend_model` (`:89`) fait ensuite, **en Python**, un filtrage `O(candidats × observations)` :

```python
per_model = {key: [row for row in observations if (row.provider_slug, row.model_id) == key] …}
```

C'est exécuté sur le chemin critique de chaque message en mode `Auto 2AI`, avant même l'appel fournisseur. À mesure que la table grossit, la latence augmente **pour tous les utilisateurs**.

**Correctif** : remplacer le chargement par une agrégation SQL (`values('provider_slug','model_id').annotate(avg_duration=Avg(...), n=Count(...))`), filtrer sur une fenêtre temporelle (`created_at__gte=now()-90d`), mettre le catalogue de modèles et les classements OpenRouter en cache (60 s), et purger/archiver `RoutingObservation` au-delà d'une certaine volumétrie.

#### 2.13 Limitation de débit dépendante du cache mémoire

`chat/services.py:303` s'appuie sur `cache.add`/`cache.incr`. Or `CACHES` n'est défini **que si `REDIS_URL` est renseigné** (`settings.py`) — et sur Render cette variable est en `sync: false`, donc probablement vide. Sans Redis, Django retombe sur `LocMemCache` :

- la limite est **par processus** (avec un seul worker Gunicorn ici, ça passe ; dès qu'on passe à plusieurs workers ou plusieurs instances, elle est multipliée) ;
- elle est remise à zéro à chaque déploiement ;
- `cache.add` suivi de `cache.incr` n'est pas atomique si la clé expire entre les deux (bénin, mais la fenêtre est franchissable).

**Correctif** : rendre Redis obligatoire en production (ou utiliser un backend de rate limiting dédié), journaliser un avertissement au démarrage si `REDIS_URL` est absent, et documenter la dégradation.

#### 2.14 Capacité Gunicorn très faible face au streaming

`run_services` lance `--worker-class gthread --threads 4` avec **un seul worker** : au maximum 4 requêtes simultanées. Or chaque réponse de chat est un flux SSE qui **occupe son thread pendant toute la génération** (jusqu'à `UPSTREAM_TIMEOUT = 120 s` côté API, plus côté chat).Quatre conversations un peu longues saturent le service. Le `--timeout 120` de Gunicorn est par ailleurs susceptible de tuer les streams longs.

**Correctif** :workers asynchrones (`uvicorn`/`gevent`) ou montée à `--workers N --threads M` cohérente avec la RAM du plan Render ; sortir les appels fournisseur du thread de requête si possible ; aligner `--timeout` sur les timeouts applicatifs.

#### 2.15 SSRF : fenêtre de DNS rebinding

`bara/tools/definitions.py:438` (`_validate_url`) et `bara/mcp_client.py:18` (`validate_mcp_endpoint`) résolvent le nom d'hôte, vérifient `is_global`, **puis** laissent `requests`/`httpx` résoudre à nouveau au moment de la connexion. Un attaquant contrôlant un DNS à TTL court peut faire valider une IP publique puis faire résoudre vers `169.254.169.254` ou `127.0.0.1`.

C'est un durcissement, pas une faille ouverte : l'impact reste limité (pas d'injection de credentials, redirections re-validées). À corriger en épinglant l'IP validée (transport/adapter personnalisé) ou en passant par un proxy sortant autorisant une liste blanche.

#### 2.16 Les erreurs d'outils fuitent vers le modèle

`bara/tools/executor.py:26`

```python
except Exception as error:
    return {"error": str(error)}
```

Le message d'exception brut est renvoyé **dans le contexte du LLM**, donc potentiellement affiché à l'utilisateur : chemins absolus du serveur, messages d'erreur SQL, extraits de configuration.

**Correctif** : `logger.exception(...)` côté serveur + message générique renvoyé au modèle.

#### 2.17 Divers

- `BARA_USER_STORAGE_LIMIT_MB = 50` et `BARA_USER_FILE_LIMIT = 300` : quota par utilisateur, mais `check_quota` n'est appelé que sur l'upload ; la génération de fichiers par les outils (`write_file`, exports Studio) peut le dépasser. À vérifier.
- `SECURE_REFERRER_POLICY` non défini ; `ALLOWED_HOSTS` et `CSRF_TRUSTED_ORIGINS` figés sur `*.onrender.com` (cassera à l'ajout d'un domaine propre).
- PostgreSQL Render en **`plan: free`** pour une application qui stocke des conversations, des portefeuilles de crédits et des fichiers : pas de sauvegarde garantie, et suppression après inactivité/expiration selon l'offre. À passer sur un plan payant avec sauvegardes automatiques avant toute mise en production réelle.
- `MEDIA_GENERATION_CREDITS` / `EUR_PER_USD` / `PAYDUNYA_XOF_PER_USD` sont des constantes de facturation figées dans les settings : tout écart de change ou de tarif fournisseur se traduit par une marge qui fond (ou un surcoût client). À revoir périodiquement ou à sourcer dynamiquement.

---

## 3. Plan d'amélioration priorisé

| Ordre | Action | Effort | État |
|---|---|---|---|
| **1** | Sortir les fichiers Bara du stockage public (2.1) | M | ✅ stockage privé + `/media/` réservé à `DEBUG` + commande de migration + tests de non-régression |
| **2** | Self-héberger `marked`/`DOMPurify`/`highlight.js` + poser une CSP (2.2) | S | ✅ bibliothèques vendues dans `static/app/vendor/` avec `integrity`, middleware CSP (dérogations documentées), favicons Google supprimés |
| **3** | Ajouter logout / changement / reset de mot de passe (2.3) | S | ✅ vues Django + gabarits + entrée « Compte et sécurité » + 8 tests |
| **4** | Limitation de débit sur l'auth + e-mail unique (2.10) | S | ✅ plafond d'échecs par identifiant/IP, plafond d'inscriptions, e-mail unique normalisé + 7 tests |
| **5** | CI GitHub Actions + ruff (2.4, 2.11) | S | ✅ workflow `ci.yml` (lint, check, migrations, tests, JS) + configuration ruff |
| **6** | Corriger les dépendances (2.5) | S | ✅ `httpx` et `soupsieve` épinglés, `requires-python >= 3.12`, `runtime.txt`, `requirements-dev.txt` |
| **7** | Retirer `node_modules` de Git (2.6) | S | ✅ 195 fichiers retirés du suivi + `.gitignore` |
| **8** | `.env.example` + rotation `MultiFernet` (2.8) | S | ✅ `.env.example` commenté, `FERNET_PREVIOUS_KEYS`, message d'erreur explicite, procédure documentée dans `RENDER.md` |
| **9** | Observabilité : `LOGGING`, `/healthz`, Sentry (2.9) | M | ✅ `LOGGING` configuré, `/healthz`, `healthCheckPath` Render, vérifications `--deploy` (Redis, e-mail, `/media/`). *Sentry reste à brancher.* |
| **10** | Séparer le worker du serveur web (2.7) | M | ✅ superviseur qui redémarre les enfants, worker à deux files parallèles (`--queue`), temporisation croissante. *Render ne peut attacher un disque qu'à un seul service : la séparation complète demanderait un stockage objet.* |
| **11** | Optimiser le routeur IA (2.12) | M | ✅ agrégation SQL (`Avg`/`Count`/`Coalesce`), 2 requêtes bornées au lieu de 3 `COUNT(*)` + 500 lignes, test `assertNumQueries` |
| **12** | Redis obligatoire en prod / rate limiting partagé (2.13) | S | ⚠️ signalé par `check --deploy` (avertissement) ; nécessite une décision d'infrastructure |
| **13** | Revoir la config Gunicorn pour le streaming (2.14) | S | ✅ 16 threads par défaut (`GUNICORN_THREADS`), `--graceful-timeout`, journaux sur stdout |
| **14** | Durcir SSRF et les messages d'erreur d'outils (2.15, 2.16) | M | ⚠️ erreurs d'outils journalisées et résumées ✅ ; épinglage IP contre le DNS rebinding reste à faire |
| **15** | Découper `chat/views.py` et `script.js` (2.11) | L | ❌ laissé de côté : chantier à part, sans gain fonctionnel immédiat |
| **16** | Passer la base Render sur un plan avec sauvegardes (2.17) | S | ⚠️ décision d'exploitation, hors code |

### Corrections de bugs découvertes en chemin

- **`soupsieve` non épinglé** : la branche 3.x, installée automatiquement comme
  dépendance transitive de `beautifulsoup4`, est incompatible et faisait échouer
  16 tests. Épinglé à `2.10`.
- **Test obsolète** : `bara.tests.WorkspaceMCPTests` vérifiait encore le texte
  « Piston » alors que le panneau décrit désormais le conteneur isolé. Corrigé.
- **Fuite de vie privée** : chaque citation affichée appelait
  `google.com/s2/favicons`, révélant à Google les domaines consultés. Supprimé.

---

## 4. Conclusion

Le projet est **fonctionnellement riche et globalement bien pensé** : la
facturation à la réservation, le sandboxing Docker, la validation HMAC des
webhooks et le contrôle d'accès systématique témoignent d'un vrai soin. La dette
porte moins sur l'architecture que sur **l'industrialisation** : pas de CI, pas
de linter, dépendances incohérentes, `node_modules` versionné, aucune
observabilité, et un couplage web/worker fragile.

Les trois sujets critiques ont été traités : l'exposition publique des fichiers
Bara (2.1), l'absence de CSP couplée au chargement CDN sans SRI (2.2) et
l'impossibilité de se déconnecter ou de récupérer son compte (2.3).

Restent trois décisions qui ne relèvent pas du code : provisionner Redis,
passer la base Render sur un plan avec sauvegardes, et brancher un agrégateur
de journaux (Sentry ou équivalent).
