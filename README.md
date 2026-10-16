# ai_2ai
# mia

Le système Side-by-Side est décrit dans [EVALUATIONS.md](EVALUATIONS.md) : architecture, migrations, tests, paramètres et activation progressive. Ses quatre fonctionnalités sont désactivées par défaut.

Le chat propose aussi des [aperçus interactifs et un lecteur de liens ChatGPT](IDEAS_AND_SHARED_LINKS.md), accessibles dans le menu **+**. Des exemples gratuits se trouvent à `/chat/ideas/`.

L’espace **Création** (`/creation/`) regroupe la génération d’images, de vidéos et de sons, avec les fournisseurs et jetons du compte. Il fonctionne sans projet ni conversation. La **Médiathèque** (`/creation/library/`) rassemble les créations, les anciens médias du chat et les médias des projets Studio, avec recherche, filtres et pagination. Le montage reste dans le Studio (`/studio/`).

Le chat conserve les pièces jointes et leur analyse. **Créer à partir de ce message** préremplit une demande dans Création ; aucune génération n’est lancée automatiquement. Depuis la médiathèque, **Utiliser dans une conversation** copie le fichier dans un nouvel espace privé et ouvre le chat : une image est jointe au prochain message, les vidéos et sons sont disponibles comme fichiers de l’espace. **Utiliser dans le Studio** ajoute le média à un montage nouveau ou existant. Ces transferts ne déclenchent aucun appel IA.

Applique `python manage.py migrate` pour la migration `chat.0026_generatedmedia_independent_conversation` ; le démarrage Render l’applique automatiquement au prochain déploiement. Les médias existants restent disponibles aux mêmes adresses et sont conservés après suppression de leur conversation. Les liens d’origine deviennent facultatifs. La médiathèque, les fichiers et les transferts vérifient le propriétaire du compte.

Vérifications de Création sans appels fournisseur payants : `python manage.py test creation chat.tests chat.test_frontend studio --noinput --settings=evaluations.test_settings`, `node --test tests/js/*.test.cjs` et, avec Chrome et Playwright, `python tests/browser/creation.py`.

## Démarrage

Le projet exige **Python 3.12 ou plus** (Django 6.1) et Node 22 pour les tests
JavaScript.

```sh
python -m venv .venv && source .venv/bin/activate
pip install -r requirements-dev.txt
cp .env.example .env      # puis renseigne au moins FERNET_KEY et SECRET_KEY
python manage.py migrate
python manage.py seed_providers && python manage.py sync_provider_keys
python manage.py runserver
# Dans un second terminal, pour les tâches Bara et Studio :
python manage.py agent_worker
```

`FERNET_KEY` est **obligatoire** : elle chiffre les clés fournisseur en base et
l'application refuse de démarrer sans elle. Génère-la avec :

```sh
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

Vérifications avant de pousser :

```sh
ruff check --exclude node_modules,.venv .
python manage.py check
python manage.py makemigrations --check --dry-run
python manage.py test --noinput
node --test tests/js/*.cjs
```

Le chat et Bara utilisent désormais un [moteur Markdown Rust/WebAssembly](rust/markdown/README.md)
pour les réponses longues, avec chargement à la demande et repli JavaScript.
Le module compilé est fourni dans les fichiers statiques : Rust n'est requis que
pour le reconstruire avec `python scripts/build_markdown_wasm.py`.

## Modèles texte : OpenRouter et IA locale

Le chat cloud utilise OpenRouter. Ajoute la clé au fichier `.env` du projet, puis initialise le fournisseur, sa clé chiffrée et le catalogue des modèles :

```dotenv
OPENROUTER_API_KEY=ta_cle_openrouter
```

```sh
python manage.py seed_providers
python manage.py sync_provider_keys
python manage.py sync_openrouter_models
```

La synchronisation récupère les modèles texte tarifés et leurs prix. Les modèles dont OpenRouter n’expose pas le tarif sont ignorés, car leur coût ne peut pas être estimé de manière fiable. Les anciens modèles texte Groq, Together, Anthropic, OpenAI et Ollama sont désactivés par `seed_providers` sans supprimer leurs données. Les générations d’images et de vidéos utilisent également OpenRouter; l’audio conserve sa configuration dédiée.

Avant l’envoi, le chat vérifie sans appel IA les incompatibilités connues : images jointes, outils, recherche web, exécution de code et demandes directes de génération de médias ou d’accès aux fichiers. Le message indique le mode, le modèle ou l’espace à choisir ; la saisie et les pièces jointes sont conservées, sans débit de jetons. Le serveur répète la vérification avant la facturation, y compris dans Bara. `Auto 2AI` écarte les modèles incompatibles avec les outils ou les images requis et bloque si aucun candidat ne convient. Cette vérification utilise les capacités techniques connues et des règles prudentes pour les demandes directes ; elle ne prédit pas la qualité d’une réponse ni toutes les formulations possibles.

Après mise à jour, lance `python manage.py migrate`, puis `python manage.py sync_openrouter_models` pour renseigner `supported_parameters` depuis le [catalogue OpenRouter](https://openrouter.ai/docs/guides/overview/models). Une valeur absente reste inconnue et n’est pas traitée comme une incompatibilité. Les appels OpenRouter avec outils demandent aussi un [fournisseur acceptant les paramètres envoyés](https://openrouter.ai/docs/guides/routing/provider-selection#requiring-providers-to-support-all-parameters), pour éviter une réponse facturée qui ignore les outils.

Dans le chat, **Choisir une IA** et **Équipe IA** peuvent classer les modèles OpenRouter par usage texte général ou par tâche (programmation, jeu de rôle, marketing, sciences, traduction, etc.). `sync_openrouter_models` synchronise ces classements hebdomadaires avec la clé OpenRouter active. Les catégories de tâches sont des estimations basées sur un échantillon et indiquent la popularité, pas la qualité ni un benchmark. Les modèles sans rang restent triés par prix de sortie décroissant. La fenêtre cite OpenRouter et la date de référence de chaque classement; les données sont réutilisées sous CC BY 4.0. Sans clé ou si l’API des classements échoue, le catalogue tarifé reste disponible et l’ordre de secours est le prix. L’IA locale apparaît séparément dans le sélecteur et ne peut pas rejoindre l’équipe cloud.

L’option **IA locale** se connecte directement, depuis le navigateur, à Ollama ou à une API locale compatible OpenAI (`/v1/models` et `/v1/chat/completions`). **Analyser mon appareil** recherche les modèles sur les adresses habituelles d’Ollama, LM Studio, Jan, LocalAI/llama.cpp et GPT4All ; une adresse personnalisée peut aussi être saisie. La recherche est lancée par l’utilisateur, ne passe pas par le serveur 2AI, et ne détecte que les serveurs locaux démarrés et accessibles au navigateur. Avec Ollama, autorise l’origine du site dans `OLLAMA_ORIGINS`; le navigateur peut aussi bloquer une connexion locale selon sa configuration CORS ou HTTPS.

Les conversations créées avec une IA locale sont enregistrées dans le stockage du navigateur par défaut, sans être envoyées à l’historique du compte. Le bouton **Copier dans mon compte** synchronise explicitement les nouveaux échanges vers le serveur. Cette copie est distincte de l’original local.

Le bouton ninja ouvre une **conversation temporaire**, sans mémoire enregistrée par défaut. Le sélecteur **Mémoire de persona** permet d’utiliser la mémoire du persona choisi pour les prochains messages, avec un modèle cloud ou local. La sélection ne modifie pas cette mémoire. Pour conserver les échanges, choisis un persona dans **Enregistrer dans un persona**, puis clique sur **Enregistrer** : la conversation, ses sources, ses résultats d’outils et ses images générées rejoignent l’historique de ce persona. Elle devient alors une conversation enregistrée. Sans cette action, elle reste uniquement en mémoire dans l’onglet.

## Recharges et paiements

Les étoiles dans **Choisir une IA** et **Équipe IA** enregistrent les modèles favoris du compte, sans changer le modèle actif ni lancer de génération. Les favoris passent en tête des deux listes ; le filtre **Favoris** du sélecteur peut les afficher seuls. Les numéros de classement d’usage gardent leur signification. La migration `chat.0025_chatpreferences_favorite_model_ids` ajoute leur stockage ; les préférences et les étoiles restent distinctes entre les comptes. Les prix fournisseur utilisent une virgule décimale et conservent les petites fractions utiles : `2,50 $` signifie deux dollars cinquante par million de tokens. Les prix d’entrée et de sortie sont affichés séparément dans le sélecteur et la facturation 2AI conserve sa marge existante.

La monnaie est détectée au premier accès à partir du pays transmis par le proxy ou, à défaut, de l’adresse IP. La zone UEMOA utilise XOF avec PayDunya, la zone euro utilise EUR avec Stripe et les autres pays utilisent USD avec Stripe. L’utilisateur peut remplacer ce choix sur `/recharge/`; la préférence reste dans sa session et s’applique aussi aux pages `/tarifs/` et `/nous/`. Stripe nécessite `STRIPE_SECRET_KEY`.

Les taux de référence sont des paramètres de facturation 2AI, pas des taux de change en temps réel :

```dotenv
DEFAULT_CURRENCY=XOF
EUR_PER_USD=0.92
IP_CURRENCY_LOOKUP_ENABLED=True
IP_CURRENCY_LOOKUP_URL=https://ipapi.co/{ip}/country/
IP_CURRENCY_LOOKUP_TIMEOUT_SECONDS=1.5
IP_CURRENCY_CACHE_SECONDS=86400
```

Pour activer PayDunya (carte et moyens de paiement locaux proposés sur son checkout), crée une AppDunya et configure ses clés, sans les exposer au navigateur :

```dotenv
PAYDUNYA_MASTER_KEY=
PAYDUNYA_PRIVATE_KEY=
PAYDUNYA_TOKEN=
PAYDUNYA_STORE_NAME=2AI
PAYDUNYA_XOF_PER_USD=600
PAYDUNYA_MINIMUM_XOF=200
```

Sur Render, renseigne `PAYDUNYA_MASTER_KEY`, `PAYDUNYA_PRIVATE_KEY` et
`PAYDUNYA_TOKEN` dans **Environment** du service web, puis choisis **Save and
deploy**. Le fichier `.env` de ton ordinateur ne configure pas le service en
ligne. Les déclarations `sync: false` dans `render.yaml` demandent ces valeurs
à la création d'un Blueprint ; leur ajout à un Blueprint existant nécessite
de les renseigner directement dans le tableau de bord Render. Les trois
valeurs sont obligatoires pour activer le formulaire de paiement. Utilise
les identifiants de test pour le sandbox, ou les identifiants de production
de la même application PayDunya pour encaisser de vrais paiements.

La création de facture utilise automatiquement l'endpoint sandbox lorsque `PAYDUNYA_PRIVATE_KEY` commence par `test_private_`, et l'endpoint live pour les clés live. `PAYDUNYA_CHECKOUT_URL` permet de remplacer explicitement ce choix. L'IPN doit pouvoir joindre publiquement `/recharge/paydunya-webhook/`; il est enregistré automatiquement dans chaque facture. Cette notification est validée avec le hash SHA-512 de `PAYDUNYA_MASTER_KEY`, puis son token et son montant sont comparés à la recharge créée avant de créditer le compte.

Le retour après paiement vérifie aussi la facture avec l'API `checkout-invoice/confirm` de PayDunya, puis affiche les jetons ajoutés et le solde. Cette vérification fonctionne en sandbox et en production même si l'IPN n'a pas encore été reçu. La notification et le retour utilisent le même verrou en base pour créditer chaque facture une seule fois. Les URL de paiement utilisent `PUBLIC_BASE_URL` lorsqu'il est renseigné (ou `RENDER_EXTERNAL_URL` sur Render). Voir la [documentation HTTP/JSON PayDunya](https://developers.paydunya.com/doc/EN/http_json).

Pour régulariser les recharges existantes restées en attente, avec les clés PayDunya correspondant à ces factures :

```bash
python manage.py reconcile_paydunya
# Vérifier uniquement une recharge :
python manage.py reconcile_paydunya --purchase-id 123
```

La commande ne crédite que les factures confirmées, signées et dont le montant correspond à la recharge enregistrée. Elle peut être relancée sans doubler les jetons.

## PostgreSQL local

Le fichier `.env` à la racine du projet (`ai_2ai/.env`) peut sélectionner une base PostgreSQL locale :

```dotenv
DATABASE_URL=postgresql://postgres:mot_de_passe@127.0.0.1:5432/ai_2ai
```

Crée d'abord la base `ai_2ai` dans PostgreSQL, puis applique les migrations avec `python manage.py migrate`. Si `DATABASE_URL` est absent, le projet utilise SQLite (`db.sqlite3`).

## Génération de médias

Les images et vidéos utilisent la clé `OPENROUTER_API_KEY` configurée plus haut. Le modèle par défaut est `bytedance-seed/seedream-4.5` pour les images et `alibaba/wan-3.0` pour les vidéos; remplace-les si besoin avec `OPENROUTER_IMAGE_MODEL` et `OPENROUTER_VIDEO_MODEL`. Les modèles renvoyant `usage.cost` sont facturés au coût réel avec la marge configurée; une réserve est débitée avant l’appel et ajustée après la réponse.

La génération audio conserve les intégrations existantes. Configure une clé audio dans l'environnement, puis lance `python manage.py seed_providers` et `python manage.py sync_provider_keys` :

```dotenv
FAL_API_KEY=
REPLICATE_API_TOKEN=
APIFRAME_API_KEY=
```

Les modèles audio par défaut sont configurables avec `FAL_AUDIO_MODEL`, `REPLICATE_AUDIO_MODEL` et `APIFRAME_AUDIO_MODEL`. Le compositeur propose des dimensions image standard (carré, portrait ou paysage), et pour la vidéo un ratio, une résolution, une durée et l’activation du son. L’audio propose un style et une option instrumentale.

Les crédits internes se règlent avec `MEDIA_IMAGE_CREDITS` (630 pour 1 MP), `MEDIA_VIDEO_CREDITS` (16800 pour 8 secondes en 720p) et `MEDIA_AUDIO_CREDITS` (2000 par génération). Le débit image évolue avec le nombre de pixels et le débit vidéo avec la durée et la résolution sélectionnées. Ces montants servent de réserve; le coût réel OpenRouter ajuste le débit lorsqu’il est communiqué.

## AI Router — Phases 1 à 6

Les requêtes standard du chat, Bara et API enregistrent une recommandation shadow sans changer leur modèle sélectionné. Dans le chat, le choix explicite `Auto 2AI` (ou le modèle par défaut `Auto 2AI` dans les paramètres) active le routage. Dans Bara, chaque workspace peut être basculé de son modèle manuel vers `Auto 2AI`; le modèle sélectionné précédemment reste son fallback. Les modèles candidats doivent être actifs, avoir une clé serveur active (sauf fournisseur local) et respecter les contraintes outils/streaming de la surface.

Les observations enregistrent la surface, la catégorie, le modèle réellement exécuté, la recommandation, le résultat technique, la durée, les tokens, les crédits estimés/débités et les outils réellement utilisés; elles ne stockent ni prompt ni réponse. Les conversations temporaires ne créent pas d’observation. Le score combine fiabilité technique, coût et durée avec un seuil de cinq observations et un repli utilisateur/catégorie/global.

Dans le chat, les votes J’aime/Je n’aime pas sont désormais persistés par réponse; un vote négatif est marqué comme correction nécessaire. Après au moins cinq feedbacks dans la portée considérée, le score ajoute un signal de qualité lissé. Ce signal vient uniquement de ces votes explicites; une réussite fournisseur seule n’est pas considérée comme une réponse de qualité.

L’équipe garde « Comparer et synthétiser » comme stratégie par défaut. Le premier modèle disponible propose des résultats en notes JSON compactes ; les autres les vérifient indépendamment et transmettent uniquement leurs corrections, informations manquantes, incertitudes et références. Le coordinateur rédige une seule réponse finale à partir de la demande d’origine, des préférences et des notes. Si un membre échoue, les membres disponibles continuent ; si un seul modèle est configuré, il répond directement en un appel.

L’option « Planifier, réaliser, vérifier » utilise deux ou trois modèles sélectionnés : un plan compact, une proposition structurée, une vérification par corrections ciblées, puis une rédaction finale par le modèle chargé de la réalisation. Cela représente quatre appels, dont trois échanges internes, au lieu de trois réponses longues. Avec deux modèles, le planificateur effectue également la vérification ; avec trois, le troisième modèle vérifie. Seule la réponse finale est diffusée ou enregistrée, y compris en streaming.

Les budgets de sortie internes dépendent de la limite finale : plan jusqu’à 128 tokens, proposition jusqu’à 384 et corrections jusqu’à 160, toujours limités par le plafond demandé. Pour une réponse finale de 500 tokens, les budgets sont de 125/250/160 tokens. La limite finale choisie par l’utilisateur est conservée. Les notes sont validées et limitées côté serveur ; un JSON invalide devient une note bornée et signalée comme non structurée, sans appel de réparation supplémentaire. La réserve inclut tous les rôles, les notes relayées et les replis possibles ; le débit reste ajusté à l’usage réel de chaque modèle. Les échanges restent du texte tokenisé : les économies réelles de coût et la qualité doivent être mesurées sur des tâches représentatives, notamment parce que la vérification distincte ajoute un appel et que le contexte est relu à chaque étape.

L’API compatible OpenAI accepte aussi `model="auto"` et expose cette option dans `/v1/models` lorsqu'un fournisseur éligible est configuré. Les appels standards de l'API enregistrent toujours une recommandation shadow sans remplacer le modèle explicitement sélectionné. Le streaming avec outils n'est pas pris en charge par l'API.

Chaque appel d'une équipe est facturé selon les tarifs du modèle qui l'a exécuté, y compris la synthèse ou la vérification finale. Les réservations sont ajustées avec une règle commune au chat, à Bara et à l'API ; les échecs réseau avant réponse sont remboursés. Un éventuel complément impossible à débiter est signalé dans le chat et Bara.

L'historique transmis aux modèles est limité aux échanges récents avec `CHAT_HISTORY_MAX_MESSAGES=48` et `CHAT_HISTORY_MAX_CHARS=48000`. Cette limite concerne le contexte fournisseur, pas l'historique sauvegardé. Les conversations reprennent leur place dans les échanges récents après chaque nouvelle réponse.

Les résultats d’outils envoyés aux modèles utilisent du JSON compact sans modifier leurs données. Les instructions et sources de citation ne sont plus répétées dans un même prompt, et les passages documentaires qui se chevauchent sont fusionnés quand leur contenu correspond exactement dans le même fichier et la même page. L’historique sauvegardé, les préférences, les sources affichées et les limites de sortie sont conservés. Les mesures sur des exemples fictifs et les pistes supplémentaires de réduction du contexte sont détaillées dans `fiches.txt`, sections 19 et 20 ; les gains réels en tokens dépendent du modèle et des demandes.

Chaque compte dispose aussi de **Big Persona**, créé par la migration `chat.0024_persona_sharing_big_persona` pour les comptes existants, puis automatiquement à l’inscription. Dans **Paramètres → Persona actif**, active **Autoriser la liaison avec Big Persona et les autres personas autorisés** sur chaque persona que tu souhaites partager. Le partage est désactivé par défaut et concerne uniquement la mémoire et les réglages, entre personas du même compte ; les conversations restent séparées. Les personas classiques qui autorisent la liaison reçoivent également le contexte des autres personas autorisés.

Big Persona reçoit les sources autorisées, le profil du compte (nom, e-mail, date d’inscription et solde) et les réglages fonctionnels du site : génération, fonctionnalités, limites, tarification et modèles de médias. Ces informations sont sélectionnées explicitement côté serveur ; les mots de passe, clés API, secrets OAuth et paramètres de connexion à la base ne sont pas transmis. Le panneau **Big Persona** permet de consulter le profil et les réglages accessibles et d’ouvrir sa conversation. Le contexte est relu à chaque demande, pour les modèles hébergés comme locaux, sans recopier les mémoires sources dans celle de Big Persona. Une révocation s’applique aux prochaines demandes ; les messages déjà échangés ne sont pas effacés. Le contexte partagé utilise du JSON compact, mais sa taille augmente avec les sources autorisées.

Applique la migration avec `python manage.py migrate` avant de démarrer cette version. Les nouveaux endpoints authentifiés `/chat/api/big-persona/` et `/chat/api/personas/<id>/context/` renvoient uniquement le contexte du compte connecté avec `Cache-Control: no-store`. Big Persona est un persona intégré, unique par compte ; son type ne peut pas être forgé via l’API et il ne peut pas servir de source partagée ni être supprimé par cette API.

### Sécurité des outils Bara

Les outils `run_python`, `run_command` et `extract_document_text` sont désactivés par défaut. Ils utilisent uniquement un conteneur Docker sans réseau, avec système de fichiers en lecture seule, utilisateur non privilégié et limites de ressources. Aucun repli vers une exécution sur l’hôte n’est permis. Prépare l’image `runtime/Dockerfile` avant de configurer `BARA_CODE_EXECUTION_ENABLED=true`. La sélection d’outils d’un workspace ne peut pas contourner ce réglage serveur. Voir `FEATURES.md` pour l’installation du runtime.

Bara ajuste le débit après la réponse selon les tokens d’entrée/sortie réellement rapportés et les tarifs configurés pour le modèle. Si le coût dépasse la réserve prélevée et que le solde ne suffit plus, l’interface indique explicitement les jetons qui n’ont pas pu être débités.

Les outils de lecture `read_file_lines` (jusqu’à 500 lignes) et `compare_files` (diff en lecture seule) complètent `read_file` pour inspecter de gros fichiers ou comparer deux versions sans modifier le workspace.

Le panneau `AUTOMATISATIONS` propose Gmail, Outlook et Blender, ainsi que des serveurs MCP Streamable HTTP personnalisés. Gmail et Outlook utilisent OAuth avec PKCE et nécessitent `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET`, `MICROSOFT_CLIENT_ID` et `MICROSOFT_CLIENT_SECRET` ; `MICROSOFT_TENANT_ID` vaut `common` par défaut. Enregistre comme URI de redirection OAuth les adresses fixes `https://<domaine>/bara/plugins/email/google_mail/callback/` et `https://<domaine>/bara/plugins/email/microsoft_mail/callback/`.

Gmail demande `gmail.readonly` et `gmail.compose` ; Outlook demande `Mail.ReadWrite` et `Mail.Send` (la permission Microsoft `Mail.ReadWrite` est plus large que les opérations exposées). Bara ne propose que la recherche/lecture, la création de brouillons et l'envoi après confirmation explicite. Le contenu des e-mails est non fiable et n'est pas conservé dans le journal des outils.

Pour Blender, connecte le module complémentaire téléchargeable dans le panneau. Le module interroge Bara par HTTPS sortant avec un jeton secret, qui n'est affiché qu'à sa création ; aucun code Python arbitraire n'est exécuté. La lecture de scène est en lecture seule et toute modification de scène demande une confirmation dans Bara. HTTP n'est autorisé que pour les adresses loopback en développement.

Le panneau `VISUALISATIONS` ouvre un site directement dans le navigateur, dans un iframe sandboxé, ou prépare une demande de graphique. Le site n'est pas récupéré par le serveur ; certains sites refusent les iframes ou leur authentification ne fonctionne pas dans le cadre isolé, et doivent alors être ouverts dans un nouvel onglet. L'outil `visualize_chart` affiche des graphiques SVG avec infobulles à partir des données fournies dans la conversation ou les fichiers.

Dans le chat, sélectionne un espace Bara dans le contrôle **Espace** avant d'ajouter un document. Les fichiers sont conservés dans cet espace, limités à 10 Mo chacun, et leur texte extrait peut servir de contexte aux échanges. Les formats texte courants, PDF, DOCX, XLSX et PPTX sont pris en charge ; les images peuvent être jointes au prochain message avec un modèle acceptant les images (quatre images de 5 Mo maximum). Les références sont sauvegardées pour les échanges suivants. Les documents scannés peuvent être traités par l’outil OCR lorsque le runtime isolé est configuré.

Le chat utilise le même catalogue d'outils que Bara pour l'espace sélectionné : lecture et modification de fichiers, comparaison, recherche, graphiques et connexions actives. Les outils désactivés et le réglage serveur d'exécution de code restent respectés. Les suppressions, envois d'e-mails, modifications Blender et appels MCP affichent leurs confirmations dans la réponse du chat. Les graphiques et les confirmations sont conservés avec le message et restaurés à la réouverture de la conversation.

Configure `SEARCH_API_KEY` avec la même clé Serper que Bara pour activer la recherche Web. En chat simple avec un modèle compatible avec les outils, l'IA peut rechercher et lire les pages pertinentes de sa propre initiative, même sans espace sélectionné. Le bouton **Recherche web** lance une première recherche explicitement, puis le modèle peut la compléter avec les outils. Le mode Équipe conserve la première recherche et sa synthèse ; l'usage des outils de workspace concerne le mode à un seul modèle.

Les références sont des pastilles intégrées après les affirmations : nom du site, icône et `+1`, `+2`, etc. pour les citations regroupées. Un clic ouvre les titres, liens et extraits des références. Les numéros renvoient uniquement aux résultats effectivement obtenus par la recherche ou la lecture d'une page ; une source inventée ou une URL non HTTP(S) ne devient pas une pastille. Quand un modèle ne place aucune citation, une ligne compacte « Sources consultées » reste disponible sous la réponse.

La migration `chat.0020_message_tool_events` ajoute les données de graphiques et de confirmations aux messages. Applique-la avec `python manage.py migrate` avant d'utiliser cette version ; le démarrage Render applique automatiquement les migrations au prochain déploiement.

2AI est installable comme PWA sur HTTPS (et sur `localhost` en développement). Le service worker met uniquement en cache les ressources publiques sous `/static/` ; les pages, conversations et réponses privées ne sont pas disponibles hors ligne. Sur iOS, utilise Safari puis **Partager → Sur l’écran d’accueil**.

Chaque appel à un serveur MCP dans Bara demande une confirmation individuelle. Limite également les permissions des services connectés.

## Stockage et dépôt

`MEDIA_ROOT` et `PRIVATE_MEDIA_ROOT` permettent de placer les fichiers Bara et les médias générés sur un stockage persistant ; leurs valeurs locales par défaut restent `media/` et `private_media/`. Le Blueprint Render utilise un disque de 1 Go et un service web payant `starter`, avec les deux répertoires sous `/var/data`. Voir `RENDER.md` pour préparer une instance existante.

Les documents uploadés, médias privés, fichiers Python compilés et artefacts de packaging ne sont plus suivis par Git. Ils restent présents localement. L'exclusion du suivi courant ne supprime pas les fichiers des anciens commits.

Le calcul des crédits se trouve dans `chat/billing.py`, l'orchestration des équipes dans `chat/team.py`, la gestion du contexte dans `chat/context.py`, les outils du chat dans `chat/file_tools.py` et la sauvegarde d'un échange dans `chat/turns.py`. Le catalogue, l'exécution et le journal des outils sont partagés dans `bara/agent_tools.py`. Le lecteur de flux SSE du navigateur est isolé dans `static/app/js/chat_transport.js`, les références dans `message_sources.js`, les actions dans `chat_tools.js` et les graphiques dans `charts.js`.

## Vérifications

Le frontend utilise `base.css` pour les couleurs, la typographie système, les formulaires et les pages de contenu. Chaque template charge ensuite ses propres styles et scripts ; les bibliothèques Markdown et le runtime du chat sont réservés aux pages qui en ont besoin. Les scripts sont différés, les requêtes initiales du chat indépendantes partent en parallèle, et le thème sauvegardé est appliqué avant l’affichage.

La séparation des ressources réduit les fichiers CSS/JS locaux suivants, en Ko décimaux avant compression (HTML, images et ressources CDN exclus) :

| Page | Avant | Après |
| --- | ---: | ---: |
| Connexion | 206 | 28 |
| Tarifs | 206 | 26 |
| Recharge | 213 | 36 |
| À propos | 200 | 24 |
| Paramètres | 222 | 42 |

Ces tailles décrivent les ressources référencées par les templates, pas un temps de chargement en production. `chat.test_frontend` protège le budget des pages de contenu et vérifie les dépendances du chat, de Bara et des tâches. La compilation WhiteNoise conserve les noms de fichiers avec empreinte et la compression en production.

```sh
python manage.py test chat api ai_user bara barrassons
node --test tests/js/*.test.cjs
python manage.py makemigrations --check --dry-run
```

Les tests couvrent les tarifs distincts d'une équipe, les remboursements lors d'une coupure réseau, les outils de fichiers en streaming, la validation à l'inscription et le sandbox des prévisualisations HTML.

## Sécurité et confidentialité

- **Fichiers des espaces Bara** : les pièces jointes sont stockées hors de
  `MEDIA_ROOT` et ne sont plus servies par `/media/`, même en production. Le
  téléchargement passe par des vues qui vérifient l'appartenance à l'espace.
  Après mise à jour, lance `python manage.py migrate_files_to_private_storage`
  pour déplacer les fichiers déjà présents.
- **Aucune bibliothèque tierce à l'exécution** : `marked`, `DOMPurify` et
  `highlight.js` sont servis par l'application depuis `static/app/vendor/`, avec
  attribut `integrity`. Une `Content-Security-Policy` stricte est envoyée sur
  chaque page ; seules deux dérogations existent (l'IA locale sur
  `localhost`, et le widget de connexion Telegram).
- **Compte** : `Paramètres → Compte et sécurité` permet de se déconnecter, de
  changer son mot de passe et d'en demander la réinitialisation. Les tentatives
  de connexion sont plafonnées (`AUTH_MAX_FAILURES`) et l'adresse e-mail est
  unique, pour éviter les comptes doubles et l'usurpation.
- **Journalisation** : `LOGGING` est configuré, `/healthz` vérifie la base de
  données et peut servir de sonde à l'hébergeur. `python manage.py check
  --deploy` signale les réglages à corriger avant la production.

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
AI2AI deployment, use `barrassons login --api-base URL` or set
`BARRASSONS_API_BASE`. Run `barrassons login` to change credentials and `barrassons logout` to
remove the locally stored key.

Render (`https://mia-vkl1.onrender.com/v1`) is the default. Login uses the saved
server or this default without asking for an address. For local development,
start Django, then run `barrassons login --api-base http://127.0.0.1:8000/v1` with
a key created on that local instance. The option accepts a site URL and adds
`/v1` when needed. Key input is hidden and the connection is verified before
replacing the saved configuration. `barrassons login` returns to the shell
after successful login; launch `barrassons` to begin working.

The terminal has a compact welcome panel, colored diffs, an activity indicator,
and a status line showing the model and reported usage. The model picker supports
search and pagination, then remembers the choice. `/model` changes it during a
session, `/status` refreshes credits, and `/verbose` expands tool results. Use
Tab for command completion, arrow keys for prompt history, and a trailing
backslash followed by Enter for multiline input. `--model ID` selects a model
directly; `--no-color` or `NO_COLOR=1` disables color. Redirected output stays plain.

The selected model must support OpenAI-compatible function tools. API usage is
charged to the AI2AI account associated with the key. Anthropic models and streamed
tool calls are not currently supported by this CLI.

## Tâches, mémoire, documents et collaboration

Dans **Paramètres → Mémoire et import** (ou **Mémoire** dans le chat), importe les souvenirs de n’importe quel assistant avec la **consigne d’export universelle**. Colle sa réponse ou charge un fichier TXT, Markdown ou JSON en UTF-8 (256 Ko et 64 000 caractères maximum). Le JSON accepte une liste de textes, des entrées `content`/`text`, un objet `memories`/`memory` ou des préférences structurées. Relis et modifie l’aperçu, puis ajoute-le à la mémoire actuelle ou choisis explicitement de la remplacer. La limite finale est de 8 000 caractères ; les textes trop longs restent modifiables et ne sont pas tronqués à l’enregistrement. Une modification effectuée dans un autre onglet bloque la validation jusqu’à une nouvelle relecture.

Le même import est disponible dans la **mémoire d’un projet** (`/memory/project/<id>/`). Il conserve les réglages d’activation et de recherche du projet, reste limité au compte connecté et traite les fichiers localement sur le serveur, sans appel de modèle ni débit de crédits. Les exports d’historiques complets ne sont pas importés comme des conversations : utilise la consigne pour en extraire les souvenirs. Aucune migration supplémentaire n’est nécessaire.

Les six chantiers et leurs réglages sont décrits dans [FEATURES.md](FEATURES.md). Les nouvelles migrations doivent être appliquées avant de lancer cette version. Render démarre désormais le serveur Web et le worker sur le même volume avec `python manage.py run_services`.
