# ai_2ai
# mia

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

Dans le chat, **Choisir une IA** et **Équipe IA** peuvent classer les modèles OpenRouter par usage texte général ou par tâche (programmation, jeu de rôle, marketing, sciences, traduction, etc.). `sync_openrouter_models` synchronise ces classements hebdomadaires avec la clé OpenRouter active. Les catégories de tâches sont des estimations basées sur un échantillon et indiquent la popularité, pas la qualité ni un benchmark. Les modèles sans rang restent triés par prix de sortie décroissant. La fenêtre cite OpenRouter et la date de référence de chaque classement; les données sont réutilisées sous CC BY 4.0. Sans clé ou si l’API des classements échoue, le catalogue tarifé reste disponible et l’ordre de secours est le prix. L’IA locale apparaît séparément dans le sélecteur et ne peut pas rejoindre l’équipe cloud.

L’option **IA locale** se connecte directement, depuis le navigateur, à Ollama ou à une API locale compatible OpenAI (`/v1/models` et `/v1/chat/completions`). **Analyser mon appareil** recherche les modèles sur les adresses habituelles d’Ollama, LM Studio, Jan, LocalAI/llama.cpp et GPT4All ; une adresse personnalisée peut aussi être saisie. La recherche est lancée par l’utilisateur, ne passe pas par le serveur 2AI, et ne détecte que les serveurs locaux démarrés et accessibles au navigateur. Avec Ollama, autorise l’origine du site dans `OLLAMA_ORIGINS`; le navigateur peut aussi bloquer une connexion locale selon sa configuration CORS ou HTTPS.

Les conversations créées avec une IA locale sont enregistrées dans le stockage du navigateur par défaut, sans être envoyées à l’historique du compte. Le bouton **Copier dans mon compte** synchronise explicitement les nouveaux échanges vers le serveur. Cette copie est distincte de l’original local.

## Recharges et paiements

Stripe accepte les paiements par carte en USD. La page publique `/tarifs/` liste les modèles cloud actifs et les crédits par million de tokens en entrée et en sortie, selon la même formule que la facturation. Stripe nécessite également `STRIPE_SECRET_KEY`.

Pour activer PayDunya (carte et moyens de paiement locaux proposés sur son checkout), crée une AppDunya et configure ses clés, sans les exposer au navigateur :

```dotenv
PAYDUNYA_MASTER_KEY=
PAYDUNYA_PRIVATE_KEY=
PAYDUNYA_TOKEN=
PAYDUNYA_STORE_NAME=2AI
PAYDUNYA_XOF_PER_USD=600
PAYDUNYA_MINIMUM_XOF=200
```

La création de facture utilise automatiquement l'endpoint sandbox lorsque `PAYDUNYA_PRIVATE_KEY` commence par `test_private_`, et l'endpoint live pour les clés live. `PAYDUNYA_CHECKOUT_URL` permet de remplacer explicitement ce choix. L'IPN doit pouvoir joindre publiquement `/recharge/paydunya-webhook/`; il est enregistré automatiquement dans chaque facture. Cette notification est validée avec le hash SHA-512 de `PAYDUNYA_MASTER_KEY`, puis son token et son montant sont comparés à la recharge créée avant de créditer le compte.

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

L’équipe garde « Comparer et synthétiser » comme stratégie par défaut. Une option distincte « Planifier, réaliser, vérifier » orchestre trois étapes avec deux ou trois modèles sélectionnés; le plan et le brouillon restent internes et seule la réponse vérifiée est affichée. Le mode est également transmis lors du streaming.

L’API compatible OpenAI accepte aussi `model="auto"` et expose cette option dans `/v1/models` lorsqu'un fournisseur éligible est configuré. Les appels standards de l'API enregistrent toujours une recommandation shadow sans remplacer le modèle explicitement sélectionné. Le streaming avec outils n'est pas pris en charge par l'API.

Chaque appel d'une équipe est facturé selon les tarifs du modèle qui l'a exécuté, y compris la synthèse ou la vérification finale. Les réservations sont ajustées avec une règle commune au chat, à Bara et à l'API ; les échecs réseau avant réponse sont remboursés. Un éventuel complément impossible à débiter est signalé dans le chat et Bara.

L'historique transmis aux modèles est limité aux échanges récents avec `CHAT_HISTORY_MAX_MESSAGES=48` et `CHAT_HISTORY_MAX_CHARS=48000`. Cette limite concerne le contexte fournisseur, pas l'historique sauvegardé. Les conversations reprennent leur place dans les échanges récents après chaque nouvelle réponse.

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

```sh
python manage.py test chat api ai_user bara barrassons
node --test tests/js/chat_transport.test.cjs
node --test tests/js/message_sources.test.cjs
python manage.py makemigrations --check --dry-run
```

Les tests couvrent les tarifs distincts d'une équipe, les remboursements lors d'une coupure réseau, les outils de fichiers en streaming, la validation à l'inscription et le sandbox des prévisualisations HTML.

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

## Tâches, mémoire, documents et collaboration

Les six chantiers et leurs réglages sont décrits dans [FEATURES.md](FEATURES.md). Les nouvelles migrations doivent être appliquées avant de lancer cette version. Render démarre désormais le serveur Web et le worker sur le même volume avec `python manage.py run_services`.
