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

Stripe accepte les paiements par carte en USD. Pour activer Mobile Money via CinetPay, configure `CINETPAY_API_KEY`, `CINETPAY_SITE_ID` et `CINETPAY_XOF_PER_USD` dans l'environnement :

```dotenv
CINETPAY_API_KEY=
CINETPAY_SITE_ID=
CINETPAY_XOF_PER_USD=600
```

Le taux par défaut affiché est `600 FCFA = 1 USD = 10 000 jetons`; tu peux le remplacer avec `CINETPAY_XOF_PER_USD`. Le montant Mobile Money est saisi en FCFA et les jetons sont calculés avec ce taux. Le minimum de paiement correspond à l'équivalent configuré de 1 USD. La page publique `/tarifs/` liste les modèles cloud actifs et les crédits par million de tokens en entrée et en sortie, selon la même formule que la facturation. Stripe nécessite également `STRIPE_SECRET_KEY`.

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

L’API compatible OpenAI continue d’exiger un identifiant de modèle explicite : `model="auto"` n’est pas encore activé. L’Auto actif concerne le chat et les workspaces Bara; aucune orchestration automatique n’est ajoutée à l’API.

### Sécurité des outils Bara

Les outils `run_python` et `run_command` sont désactivés par défaut. `run_python` transmet le code au service tiers Piston; `run_command` lance un sous-processus sur l’hôte applicatif et ne constitue pas un bac à sable de sécurité, même si les fichiers du workspace sont copiés dans un répertoire temporaire. N'active ces outils qu'avec `BARA_CODE_EXECUTION_ENABLED=true` sur une instance isolée et sans données sensibles. La sélection individuelle dans un workspace ne peut pas contourner ce réglage serveur.

Bara ajuste le débit après la réponse selon les tokens d’entrée/sortie réellement rapportés et les tarifs configurés pour le modèle. Si le coût dépasse la réserve prélevée et que le solde ne suffit plus, l’interface indique explicitement les jetons qui n’ont pas pu être débités.

Les outils de lecture `read_file_lines` (jusqu’à 500 lignes) et `compare_files` (diff en lecture seule) complètent `read_file` pour inspecter de gros fichiers ou comparer deux versions sans modifier le workspace.

Le panneau `AUTOMATISATIONS` propose Gmail, Outlook et Blender, ainsi que des serveurs MCP Streamable HTTP personnalisés. Gmail et Outlook utilisent OAuth avec PKCE et nécessitent `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET`, `MICROSOFT_CLIENT_ID` et `MICROSOFT_CLIENT_SECRET` ; `MICROSOFT_TENANT_ID` vaut `common` par défaut. Enregistre comme URI de redirection OAuth les adresses fixes `https://<domaine>/bara/plugins/email/google_mail/callback/` et `https://<domaine>/bara/plugins/email/microsoft_mail/callback/`.

Gmail demande `gmail.readonly` et `gmail.compose` ; Outlook demande `Mail.ReadWrite` et `Mail.Send` (la permission Microsoft `Mail.ReadWrite` est plus large que les opérations exposées). Bara ne propose que la recherche/lecture, la création de brouillons et l'envoi après confirmation explicite. Le contenu des e-mails est non fiable et n'est pas conservé dans le journal des outils.

Pour Blender, connecte le module complémentaire téléchargeable dans le panneau. Le module interroge Bara par HTTPS sortant avec un jeton secret, qui n'est affiché qu'à sa création ; aucun code Python arbitraire n'est exécuté. La lecture de scène est en lecture seule et toute modification de scène demande une confirmation dans Bara. HTTP n'est autorisé que pour les adresses loopback en développement.

Le panneau `VISUALISATIONS` ouvre un site directement dans le navigateur, dans un iframe sandboxé, ou prépare une demande de graphique. Le site n'est pas récupéré par le serveur ; certains sites refusent les iframes ou leur authentification ne fonctionne pas dans le cadre isolé, et doivent alors être ouverts dans un nouvel onglet. L'outil `visualize_chart` affiche des graphiques SVG avec infobulles à partir des données fournies dans la conversation ou les fichiers.

Dans le chat, sélectionne un espace Bara dans le contrôle **Fichiers** avant d'ajouter un document. Les fichiers sont conservés dans cet espace, limités à 10 Mo chacun, et leur texte extrait peut servir de contexte aux échanges. Les formats texte courants, PDF, DOCX, XLSX et PPTX sont pris en charge ; les images et les documents scannés sans couche texte ne sont pas interprétés comme contenu visuel.

2AI est installable comme PWA sur HTTPS (et sur `localhost` en développement). Le service worker met uniquement en cache les ressources publiques sous `/static/` ; les pages, conversations et réponses privées ne sont pas disponibles hors ligne. Sur iOS, utilise Safari puis **Partager → Sur l’écran d’accueil**.

Les serveurs MCP activés peuvent agir sans confirmation individuelle : limite leurs permissions et ne connecte que des services de confiance.

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
