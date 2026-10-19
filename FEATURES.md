# Chat, Barrassons et Bara : fonctionnalités et activation

Ces fonctionnalités sont implémentées dans le dépôt. Leur disponibilité en production dépend des migrations, du worker et des services configurés. Aucun compte externe n’est connecté automatiquement.

## 1. Tâches persistantes et exécution isolée

Dans Bara, **Tâches** permet de lancer un objectif avec un nombre d’étapes et un budget de crédits. La tâche continue après fermeture de l’onglet. Pause et annulation prennent effet à la prochaine limite entre étapes ; un appel externe déjà commencé peut finir. Une confirmation traitée relance automatiquement la tâche. Après une panne du worker, la tâche passe en pause ; les actions au résultat incertain restent bloquées dans cette tâche pour éviter un doublon.

Installation locale :

```sh
python manage.py migrate
python manage.py runserver
# Dans un second terminal, avec les mêmes réglages et le même stockage :
python manage.py agent_worker
```

En production, `python manage.py run_services` supervise Gunicorn et le worker sur le même volume. PostgreSQL est recommandé pour les accès concurrents. Les fichiers sont conservés dans le stockage Bara ; les messages, budgets, confirmations et étapes sont en base. Sauvegarde les deux.

Pour activer le code et l’OCR, sur un hôte disposant de Docker :

```sh
docker build -t ai2ai-runtime:1 runtime
```

Configure `BARA_CODE_EXECUTION_ENABLED=true`, `BARA_SANDBOX_IMAGE=ai2ai-runtime:1` et éventuellement `BARA_SANDBOX_RUNTIME` (chemin de l’exécutable Docker). Utilise un daemon local, de préférence rootless, avec l’image déjà construite : aucun téléchargement d’image n’est déclenché par une tâche. Les commandes voient une copie en lecture seule des fichiers du workspace et un `/tmp` temporaire. Limites : réseau désactivé, 256 Mo, un CPU, 64 processus, 120 secondes et 1 Mo de sortie. Les fichiers créés par les commandes ne sont pas importés automatiquement ; les outils de fichiers et de documents enregistrent les livrables dans Bara.

Le runtime ne monte ni clés, ni dépôt du serveur, ni socket Docker. Son indisponibilité produit une erreur, sans exécuter le code sur l’hôte. Le service Python Render standard conserve ces outils désactivés.

### Bara Code et vérification des tâches

Le formulaire **Tâches** propose **Choix automatique**, **Bara Code**, **Bara Docs** et **Bara général**. La sélection automatique reconnaît localement les demandes de programmation ou de documents, sans appel IA supplémentaire ; choisis explicitement le harnais en cas d’ambiguïté. Le choix, la version du contrat, le parcours et les preuves sont conservés dans `AgentTask.state`, avec un événement `harness_selected`. Aucun agent supplémentaire, worker ou changement de schéma n’est nécessaire. Le worker existant garde la facturation, les confirmations, les limites et les reprises. Les tâches programmées et les tâches déjà en cours sans contrat adoptent un harnais lors de leur prochaine exécution ; un contrat déjà choisi reste conservé. Seules les traces backend de cette tâche servent de preuves, jamais son historique de conversation.

Bara Code limite les outils aux fichiers, à l’exécution isolée, à la recherche web et à la lecture GitHub déjà autorisées. Les réglages serveur et les outils désactivés dans l’espace restent prioritaires. Le harnais général conserve le catalogue habituel ; les harnais Recherche et Données spécialisés restent à développer.

Une réponse finale du modèle ne suffit plus à terminer une tâche : les opérations directes reconnues doivent disposer de preuves d’exécution. Code exige en plus une modification enregistrée pour une demande de changement et un contrôle réussi en conteneur sur les fichiers actuels. Une commande de contrôle peut être définie au lancement, par exemple `python -m unittest` ; elle doit alors être exécutée exactement par `run_command`. Sans commande imposée, les tests, compilations et contrôles usuels Python, Node et npm sont reconnus. Une commande arbitraire telle que `python -c 'print(1)'` ne compte pas comme contrôle automatique.

L’empreinte des octets et des noms de tous les fichiers est comparée avant et après le contrôle, puis avant livraison. Une modification ultérieure, même depuis un autre onglet, impose une nouvelle vérification. Les contrôles manquants sont transmis au modèle pour au plus deux reprises automatiques, toujours dans les plafonds d’étapes et de jetons. Une action incertaine ou refusée, un runtime indisponible, ou une vérification qui reste incomplète met la tâche en pause. Aucun résultat final n’est ajouté à la conversation avant réussite des contrôles. Le détail de la tâche expose le parcours et le rapport technique, sans son contexte interne.

Ces contrôles attestent les opérations reconnues et l’exécution d’une commande sur une version des fichiers. Ils ne garantissent ni la pertinence ou la couverture des tests, ni la qualité fonctionnelle du résultat, ni l’analyse de toutes les formulations.

### Bara Docs

**Bara Docs** lit, crée et révise les documents PDF, Word, Excel et PowerPoint. La sélection automatique privilégie un livrable documentaire, par exemple une présentation sur Python, et conserve Code pour une demande de script qui manipule des PDF. Une tâche garde un seul harnais ; l’enchaînement automatique de plusieurs harnais reste à développer. Les appels Docs sont classés dans la catégorie `documents` du routeur.

L’outil `read_document` relit les fichiers enregistrés, avec les tableaux Word dans l’ordre des paragraphes, les cellules et formules Excel, le texte des diapositives et celui des pages PDF. TXT, Markdown et CSV sont également lisibles comme sources. Les résultats sont paginés par 12 000 caractères ; `next_offset` permet de continuer. Pour une analyse ou une révision reconnue, toutes les plages de texte des sources désignées doivent être lues avant livraison. Les noms ambigus ou absents mettent la tâche en pause. Un fichier créé pendant la tâche ne peut pas remplacer une source manquante. L’OCR existant reste soumis au runtime isolé ; un résultat tronqué ou limité à une partie des pages ne constitue pas une lecture complète.

Les livrables passent par `create_document`, avec versions conservées en cas de révision. Les contenus placés dans des champs que le format ignorerait sont refusés avant écriture. À la livraison, le harnais rouvre chaque fichier et compare son contenu structuré à la spécification transmise au générateur : texte et tableaux Word, cellules et formules Excel, titres et textes des diapositives, texte PDF. Il contrôle aussi les noms et formats demandés. Les empreintes des octets et de la spécification sont conservées dans l’état de la tâche ; une modification extérieure invalide la livraison et n’autorise pas un écrasement automatique. Les sources relues sont aussi contrôlées avant livraison, sauf celles révisées par la tâche, qui passent alors les contrôles de sortie.

La lecture et la génération documentaires ordinaires ne nécessitent pas Docker et n’exposent pas de terminal. Les limites de lecture sont 10 Mo, 100 pages PDF ou diapositives, 50 feuilles, 100 000 cellules et 200 000 caractères de texte. Les budgets, confirmations, permissions et deux reprises de correction maximum restent ceux du moteur commun. Aucune migration supplémentaire n’est nécessaire.

Le rapport et l’interface distinguent ces contrôles du rendu visuel, qui reste à relire dans le fichier final. Une vérification explicite de la mise en page met la tâche en pause avec cette limite. Les images, annotations et objets intégrés ne sont pas analysés ; les formules Excel sont conservées sans recalcul. La concordance avec la spécification ne garantit pas que le modèle a correctement interprété toute la demande ni que le contenu métier est exact.

## 2. Évaluations de tâches

Les scénarios couvrent recherche sourcée, correction d’une fonction avec tests et tableur avec une formule vérifiable. Le rapport conserve résultat, contrôles, coût et durée. Les liens de citations sont vérifiés automatiquement ; leur soutien réel aux affirmations demande une revue humaine. Une exécution de tests non disponible n’est jamais annoncée comme réussie.

```sh
# Affiche les scénarios sans appeler de modèle :
python manage.py evaluate_agents
# Mesure réelle, avec les crédits du compte indiqué et un plafond par scénario :
python manage.py evaluate_agents --live --user moncompte --model identifiant-du-modele --credit-limit 1000 --report evaluation.json
```

Les espaces d’évaluation et leurs résultats sont conservés pour inspection. Le rapport est également enregistré dans `EvaluationRun`. Les résultats automatiquement vérifiables alimentent les retours de qualité du routeur. Les appels de tâches enregistrent leurs coûts et délais dans les observations de routage. Auto peut essayer un autre modèle compatible après une erreur fournisseur, dans le budget restant. Une réserve dont l’usage amont est inconnu reste facturée et apparaît dans le journal.

## 3. Barrassons et développement

```sh
barrassons --resume --test-command "python -m pytest"
barrassons --worktree --test-command "npm test"
```

Les écritures affichent un diff et demandent confirmation. `/diff` affiche le diff Git ; `/undo` annule la dernière édition si le fichier n’a pas été modifié depuis ; `/verify` lance la commande configurée avec confirmation ; `/review` demande une revue au modèle. Avec `--test-command`, une réponse finale après modification déclenche la vérification et renvoie les erreurs au modèle pour correction, avec deux vérifications automatiques maximum et confirmation avant chaque commande. Une commande de tests déjà réussie après la dernière modification évite une nouvelle exécution. Le prompt demande également de lire les instructions du projet. Les sessions et sauvegardes sont locales, avec fichiers de mode 0600. Elles peuvent contenir du code et des résultats d’outils ; elles n’incluent pas la clé API de configuration. Les appels inachevés sont signalés lors d’une reprise sans réexécution automatique.

`--worktree` crée une branche indépendante à partir d’un dépôt propre. Le terminal affiche son chemin ; tu peux y consulter le diff et préparer une PR avec tes outils Git habituels. Les commandes shell locales restent soumises à confirmation et s’exécutent avec les droits de ton terminal.

L’extension dans `integrations/vscode/` propose les commandes **Barrassons : ouvrir dans ce projet** et **reprendre la session** dans le terminal intégré. Lance-la avec Extension Development Host dans VS Code ; le binaire `barrassons` doit être installé et accessible dans PATH. Elle n’est pas publiée sur le Marketplace.

## 4. Mémoire et contexte

Les pages **Mémoire → Lier la mémoire entre les assistants** proposent des liens séparés pour Chat, Bara et Barrassons. La mémoire personnelle reste commune à Chat et Bara par défaut ; le lien Barrassons est désactivé par défaut. La mémoire d’un projet peut être utilisée dans le chat lorsque ce projet est sélectionné, et dans Barrassons après activation par son propriétaire. Le commutateur d’activation de chaque mémoire reste prioritaire.

Dans Barrassons, `/memory` affiche les sources accessibles ; `/memory chat`, `/memory bara ID`, `/memory both ID` et `/memory off` configurent le lien pour le dossier et la connexion courants. Les options `--memory chat` ou `--memory both --memory-workspace ID` font le même choix au démarrage. `/remember texte` ajoute un souvenir après aperçu et confirmation, au projet lié ou, à défaut, à la mémoire personnelle. L’API `/v1/memory` limite la modification d’un projet à son propriétaire et refuse une écriture si la mémoire a changé depuis l’aperçu. Les appels `/v1/chat/completions` n’incluent une mémoire que si le champ `memory` est fourni ; le serveur relit les autorisations et les liens à chaque appel, avant la facturation. Les souvenirs ne sont pas recopiés dans les sessions locales. Les historiques et les conversations temporaires restent séparés. Applique les migrations `chat.0027` et `bara.0019` avant d’utiliser ces options.

**Mémoire** dans le chat modifie les préférences du compte. **Mémoire** dans Bara modifie celles du projet. Elles sont désactivables et effaçables, et aucun fait n’est ajouté automatiquement à ces préférences. La mémoire personnelle du propriétaire n’est pas transmise aux membres d’un projet partagé.

Les longues conversations conservent une mémoire extraite de leurs anciens messages, avec leur auteur, et retrouvent des passages originaux selon la demande actuelle. La place des anciens échanges est réservée dans le budget d'historique, même quand la limite est atteinte en caractères avant celle des messages. Le mécanisme est local, sans appel IA supplémentaire, et reste incomplet : le rappel utilise les mots de la demande, sans garantie pour les paraphrases. Le chat, Bara et les tâches Bara peuvent aussi retrouver des extraits des autres conversations du même persona ou espace autorisé, dans un budget séparé de 6 000 caractères (`CHAT_CONTEXT_RELATED_MAX_CHARS`, `0` pour désactiver). Désactiver la mémoire du projet coupe ce rappel pour l'espace. Les conversations privées du chat restent privées, y compris dans un projet partagé ; les conversations Bara suivent les accès à l'espace. Les conversations temporaires sont exclues du rappel entre conversations. La compression des échanges locaux reste dans le navigateur. L'historique sauvegardé et les préférences explicites sont conservés. Les recherches documentaires indiquent fichier, page PDF lorsqu’elle est disponible et position du passage.

Pour les embeddings, configure `EMBEDDING_MODEL`, `EMBEDDING_API_KEY`, `EMBEDDING_API_URL` (par défaut OpenRouter). Active la recherche par le sens dans le projet puis lance :

```sh
python manage.py index_documents ID_ESPACE --semantic
```

Cette indexation envoie les passages au fournisseur configuré et utilise son compte. Les requêtes sont également vectorisées et mises en cache brièvement. Le coût des embeddings relève de ce fournisseur et n’est pas débité du portefeuille 2AI. Les changements de document invalident l’index ; réindexe après une modification pour retrouver la recherche sémantique. Les mots-clés restent utilisables sans fournisseur ou après une erreur d’embeddings.

## 5. Livrables bureautiques

L’outil partagé `create_document` génère et révise Word, Excel, PowerPoint et PDF à partir de titre, texte, tableaux et diapositives. **Ouvrir** dans les fichiers permet d’examiner, modifier et télécharger le document. Chaque révision conserve le fichier précédent et sa spécification pour restauration.

Les formules Excel courantes sont conservées et recalculées à l’ouverture dans Excel ou LibreOffice ; aucun résultat calculé n’est inventé. Les formules externes sont refusées. L’éditeur de tableur utilise des colonnes séparées par tabulations. Le PDF actuel prend en charge les caractères latins ; Word convient aux autres écritures. Les formats et la mise en page sont simples : ce n’est pas une suite bureautique complète.

## 6. Usage quotidien et collaboration

- Images : ajoute une image dans l’espace puis envoie un message avec un modèle acceptant les images. Quatre images maximum, 5 Mo chacune. Les capacités sont synchronisées depuis OpenRouter ; resynchronise le catalogue après les migrations.
- OCR : `extract_document_text` traite une image ou jusqu’à dix pages d’un PDF dans le runtime isolé. Les textes extraits deviennent recherchables ; qualité et troncature restent à vérifier.
- Voix : **Conversation vocale** enchaîne reconnaissance du navigateur, réponse du chat et lecture vocale. Chaque message utilise les crédits habituels. Ce mode demande un navigateur compatible et une autorisation microphone ; il ne fournit pas un flux audio Realtime du modèle.
- Historique : la recherche existante couvre titres et contenu des messages. Les tâches longues reprennent par leur état conservé. En chat enregistré, une réponse partielle reçue avant une coupure est conservée et propose **Continuer** ; cette continuation lance un nouvel échange facturé normalement. Les échanges temporaires et les tentatives d’édition échouées ne créent pas de copie de récupération. La régénération reste disponible.
- Programmation : **Programmer** crée une exécution future ou récurrente (60 minutes minimum), en UTC et avec budget par exécution. Les échéances manquées ne déclenchent pas une rafale de rattrapage.
- Connexions : Drive et Calendar utilisent OAuth Google avec autorisations de lecture séparées ; GitHub utilise un jeton chiffré avec accès limité aux dépôts choisis. Les outils exposés sont en lecture seule. Les écritures externes existantes (envoi e-mail, Blender, MCP) gardent leurs confirmations.
- Partage : le propriétaire ajoute un compte existant avec rôle Lecture ou Modification. Lecture donne accès aux conversations/fichiers ; Modification permet les écritures et les tâches avec les crédits du membre. La configuration des connexions, la politique d’outils et la publication restent réservées au propriétaire. Les membres autorisés à modifier peuvent utiliser les connexions du projet. Les requêtes d’écriture sont journalisées et la révocation empêche les nouvelles étapes des tâches du membre.

Les URI Google supplémentaires sont `/bara/plugins/email/google_drive/callback/` et `/bara/plugins/email/google_calendar/callback/`. Active les API Drive et Calendar dans la console Google et configure les URI publiques exactes.
