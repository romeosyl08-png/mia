# Comparaisons Side-by-Side et évaluations

Implémentation intégrée au projet existant. Aucun déploiement, aucune modification de `.env`, aucun appel payant pendant les tests. Les quatre feature flags sont `False` par défaut ; la liste des fournisseurs autorisés est vide.

## Audit et réutilisation

| Composant existant | Réutilisation |
| --- | --- |
| `api.models.AIModel`, `APIProvider`, `APIKey` | Catalogue, activation, modalités, prix, clés chiffrées et contrôle des connexions |
| `chat.services.call_llm_provider` | Adaptateur existant OpenRouter, compatible OpenAI, Anthropic et fournisseurs locaux ; timeout HTTP de 120 secondes |
| `chat.services.estimate_cost` | Estimation commune, renforcée pour la réservation des comparaisons |
| `chat.billing.credits_for_usage` | Même calcul que `bara.services.actual_cost_credits`, qui en est un alias |
| `chat.billing.settle_reservation` | Ajustement et remboursement par le portefeuille existant |
| `ai_user.CreditAccount`, `CreditTransaction` | Unique solde et unique registre comptable ; aucun portefeuille parallèle |
| `chat.routing.eligible_models`, `recommend_model` | Compatibilité, clés disponibles, routeur Auto conservé comme repli |
| `check_user_rate_limit` | Limiteur existant, complété par des contrôles en base sous verrou |
| `Conversation`, `Message`, `Persona`, contexte et mémoire | Identifiés pendant l’audit ; aucune conversation ni mémoire importée dans une comparaison |
| `base.html`, `base.css`, `theme.js`, `appearance.js` | Thèmes, couleurs personnalisées, composants et authentification existants |
| Tests Django de chat, API, Bara, Studio et interface | Exécutés avec une base isolée, fournisseurs simulés |

Le seul ajout au portefeuille est le suivi des bonus consommés lors d’un débit ou d’un remboursement. Il ne modifie ni le montant, ni le motif, ni le nombre de transactions. Ce suivi est nécessaire pour mesurer les bonus utilisés sur toutes les surfaces, notamment chat, Bara et API.

## Fonctionnement et garanties

1. `POST /comparisons/api/sessions/` valide côté serveur la question, le consentement, les deux modèles distincts et le budget. `request_key` doit être un UUID. Une même clé avec le même contenu restitue la session existante ; un contenu différent provoque un conflit.
2. Le verrou de budget global, puis celui du portefeuille, protègent la création atomique de la session, de ses deux emplacements A/B et du débit de réservation. Les contraintes limitent les emplacements à A et B et interdisent les doublons. La création et la suppression isolées de réponses ne sont pas exposées.
3. `comparison_worker` revendique une session une seule fois, revérifie les fournisseurs, puis effectue les deux appels simultanément, hors transaction SQL. Les threads réseau n’utilisent pas l’ORM. Les résultats sont enregistrés séparément dès leur arrivée ; l’interface les consulte par polling.
4. Le règlement utilise les tarifs figés lors de la réservation. Les réservations inutilisées sont remboursées une seule fois. Le débit final de chaque réponse ne dépasse jamais sa réservation ; un dépassement est absorbé par 2AI et visible dans `billing_shortfall`.
5. Un appel échoué n’est jamais une réponse réussie. Une comparaison avec une seule réponse réussie porte le statut `failed` et ne reçoit aucun vote. Seule la réponse réussie est facturée.
6. Une tâche abandonnée est clôturée après 600 secondes par le worker, par la commande de purge ou à la consultation de son état. Les réservations encore inutilisées sont remboursées. Les résultats tardifs sont ignorés. Aucun appel interrompu n’est rejoué automatiquement.

Les plafonds quotidiens portent sur les **réservations tentées**, même après remboursement : une erreur répétée ne rend pas disponible un nouveau budget d’appels fournisseur. Les plafonds utilisateur et global sont persistants et sérialisés. Les limites par minute utilisent aussi les sessions/votes enregistrés en base, en complément du cache.

### Consommation incertaine et coûts

La réservation prend le maximum entre l’estimation commune et un calcul à partir des octets UTF-8 de la question, de 512 tokens de protocole et du plafond de sortie. Cette enveloppe est prudente ; elle ne garantit pas la tokenisation d’un fournisseur arbitraire.

La consommation n’est exploitable que si les nombres de tokens d’entrée **et** de sortie sont des entiers positifs plausibles. Sinon, une réponse textuelle valide est conservée avec `usage_reliable=False`, facturée **zéro jeton**, et sa réservation est remboursée. Son coût fournisseur reste une estimation prudente. Une réponse vide ou une exception est un échec remboursé. Les échecs ayant pu atteindre le fournisseur sont également comptabilisés avec une estimation du coût possible.

`provider_cost` utilise les tarifs du catalogue et la consommation lorsqu’elle est disponible, sinon l’enveloppe estimée. L’adaptateur existant ne renvoie pas de facture fournisseur en USD ; cette colonne n’est donc pas une réconciliation de facture. Les surcharges, caches, variations tarifaires et consommations perdues à l’arrêt d’un processus nécessitent une réconciliation opérateur.

### Mode Surprise et confidentialité

Avant le vote, les réponses JSON ne contiennent ni identifiant/nom de modèle, ni fournisseur, ni coût, ni tokens, ni probabilités de sélection. Les erreurs fournisseur ne sont jamais renvoyées brutes. Les identités connues présentes dans le texte généré sont masquées à l’affichage. Le catalogue manuel est retiré du DOM en mode Surprise. Après le vote, le serveur révèle les modèles, coûts et consommations.

Un modèle peut toutefois s’identifier indirectement par une marque, un alias non enregistré ou son style. Un aveuglement sémantique absolu de texte libre n’est pas garanti. Les entrées et sorties des autres utilisateurs restent protégées par une vérification de propriété sur chaque route.

### Classement communautaire façon Arena

La page authentifiée `/comparisons/leaderboard/` affiche un classement par catégorie à partir des seuls agrégats `routing` : votes du mode Surprise avec consentement, sans signal de suspicion ni dossier de récompense. Elle ne lit et ne publie jamais les questions, les réponses ou les identifiants des votants. Pour limiter les inférences et les classements fragiles, chaque modèle doit avoir au moins 30 votes et 10 évaluateurs distincts, appartenir à la plus grande composante de comparaisons suffisamment soutenue, et disposer d’un calcul actualisé depuis moins de 48 heures. L’indice Bradley–Terry est comparatif et régularisé ; ce n’est ni un pourcentage de réussite ni une garantie. Sans assez de données fraîches, la page explique le seuil au lieu de montrer un classement. Aucun appel fournisseur ou migration n’est ajouté par cette page ; elle reste derrière `COMPARISON_ENABLED`, désactivé par défaut.

Le navigateur affiche les réponses avec `textContent`, sans HTML généré exécutable. Les POST/DELETE utilisent la protection CSRF Django. Les pages et JSON privés sont servis avec `no-store`. Les clés API restent côté serveur. Un brouillon de reprise est conservé dans `sessionStorage`, par compte et par onglet, pendant au plus 24 heures ; aucune génération ne démarre sur un GET ou une actualisation.

## Sélection automatique et observation du routeur

La sélection filtre les modèles/fournisseurs actifs, les clés disponibles, les modalités texte, les listes d’autorisation et d’exclusion. Elle construit des paires distinctes compatibles avec le budget. Les grands catalogues sont limités par une sélection équilibrée de modèles économiques par éditeur.

Le moteur combine exploitation softmax, qualité historique régularisée, coût, exploration et diversité des éditeurs, y compris derrière OpenRouter. Une part d’exploration d’au moins 5 % reste obligatoire. L’univers des candidats, les paires possibles, leurs probabilités, la probabilité de la paire retenue et celle de son ordre sont enregistrés **côté serveur**. Les probabilités sont conditionnelles au catalogue et au budget au moment de la sélection ; elles ne rendent pas les modèles exclus statistiquement représentés.

`rebuild_evaluation_stats` calcule des cohortes distinctes manuel/automatique et rémunéré/non rémunéré, ainsi qu’une cohorte `routing` composée uniquement de votes Surprise consentis, non suspects et sans dossier de récompense. Les notes facultatives décrivent la réponse préférée ; elles ne sont pas attribuées automatiquement aux deux modèles.

Le classement Bradley–Terry est régularisé par un a priori L2. L’influence de chaque utilisateur est plafonnée, la pondération par propension est limitée à cinq, et les statistiques conservent les victoires, égalités, deux réponses mauvaises, coûts, latences, effectifs, utilisateurs distincts et intervalles indicatifs. L’effectif de confiance est plafonné par le nombre d’utilisateurs pour tenir compte de leur dépendance. Ces statistiques restent observationnelles et sensibles au recrutement et au choix des questions.

Le branchement d’Auto IA se limite au **chat Auto simple**, sans workspace ni équipe. Bara, équipes et API compatible OpenAI gardent leur routage existant. Le flag de routage étant désactivé, les statistiques servent uniquement à l’observation. Une fois activé, il faut au moins deux candidats documentés, 30 observations, 10 utilisateurs distincts et un effectif pondéré d’au moins 10 par modèle, une composante de comparaisons connectée au choix du routeur existant, et un calcul âgé de moins de 48 heures. Sinon, le choix existant est conservé. La qualité domine les faibles pénalités de coût/latence ; la marge commerciale ne figure jamais dans les données de qualité.

## Récompenses

Les votes A, B, égalité et deux mauvaises suivent les mêmes critères. Les scores et une justification sont des prérequis de revue, sans présumer de leur qualité. Comptes récents/inactifs, justifications répétées, votes extrêmement rapides et séquences répétitives sont signalés ou exclus des bonus. Ces signaux prudents ne constituent pas une preuve de fraude.

Un bonus commence en `pending`. Un administrateur disposant de la permission de modification ouvre **Examiner** dans la liste des bonus. Cette vue privée lui présente la question, les deux réponses, les notes, la justification et les signaux. Il doit contrôler les faits et la cohérence puis inscrire une motivation individuelle avant **Valider la qualité**. Le valideur doit être distinct de l’auteur. L’action **Créditer les bonus validés** applique ensuite les plafonds et crédite le portefeuille existant, une seule fois, sous verrou.

Les réponses privées ne figurent pas dans les tableaux de bord ; seule la revue autorisée des bonus les présente. Le système ne publie aucun contenu privé et n’ouvre aucun accès public supplémentaire. Une justification longue n’est jamais validée automatiquement.

Le budget de financement est `EvaluationBudget.reward_credits_distributed`, séparé des réservations de comparaisons. Il ne diminue pas lorsqu’un compte ou une évaluation est supprimé. Les plafonds quotidien/mensuel sont des **nombres de bonus par utilisateur** ; le budget global est un nombre de **jetons**, cumulatif. Zéro budget signifie zéro versement. Les bonus ne permettent aucun retrait monétaire.

Le suivi de consommation est fongible : les bonus sont consommés en premier, les ajustements restaurent d’abord les bonus consommés, les recharges ordinaires ne les restaurent pas. Il suit les réservations puis leurs ajustements. Le tableau de bord distingue le budget historique des bonus distribués des bonus consommés sur les comptes encore présents ; son équivalent USD est indicatif, hors marge, et non un coût fournisseur réconcilié.

## Conservation et suppression

Le consentement à l’analyse est facultatif et désactivé par défaut. Aucune conversation d’un autre mode n’est exploitée. Le bouton **Supprimer le contenu** efface question, réponses et justification, retire le consentement, rejette les bonus encore non crédités et invalide les statistiques de la catégorie atomiquement. Un vote n’est plus possible après suppression.

Exécuter quotidiennement `purge_evaluations` pour supprimer les contenus et votes après `EVALUATION_RETENTION_DAYS` (90 jours). Les dossiers de bonus crédités, transactions et informations minimales de session/réservation sont conservés pour l’audit et l’idempotence. Cette conservation évite qu’une ancienne requête UUID redevienne facturable après purge. La suppression d’un compte applique les cascades Django ; le compteur global de financement survit. Les sauvegardes, journaux d’infrastructure et politiques propres aux fournisseurs doivent suivre la politique de conservation de l’exploitant.

### Conditions des fournisseurs

Revue documentaire effectuée le 15 octobre 2026 : les [conditions OpenRouter](https://openrouter.ai/terms) renvoient aux conditions propres à chaque modèle et fournisseur ; la [politique de confidentialité OpenRouter](https://openrouter.ai/privacy) distingue les pratiques des fournisseurs et les réglages du compte. Cela ne constitue pas une autorisation générale de redistribution ou d’entraînement. Aucun fournisseur n’est autorisé par défaut dans cette nouvelle fonctionnalité.

Avant activation, enregistrer la revue des modèles autorisés, contrats applicables, conservation, entraînement fournisseur et réglages du compte, puis renseigner les listes d’autorisation. Un consentement dans 2AI ne modifie pas les conditions du fournisseur. L’implémentation réalise du routage statistique interne, sans entraînement de modèle fondation, export public ni redistribution de corpus. Toute exploitation commerciale des données hors de cet usage doit faire l’objet d’une revue spécifique préalable.

## Configuration

Les valeurs figurent dans `ai_2ai/settings.py` et les exemples dans `.env.example`.

| Paramètre | Défaut | Usage |
| --- | --- | --- |
| `COMPARISON_ENABLED` | `False` | Page, création et worker |
| `COMPARISON_AUTOMATIC_ENABLED` | `False` | Mode Surprise |
| `EVALUATION_REWARDS_ENABLED` | `False` | Dossiers et versement de bonus |
| `EVALUATION_ROUTING_ENABLED` | `False` | Influence des statistiques sur le chat Auto simple |
| `COMPARISON_PROVIDER_ALLOWLIST` | vide | Fournisseurs autorisés après revue |
| `COMPARISON_MODEL_ALLOWLIST` / `COMPARISON_MODEL_DENYLIST` | vides | Identifiants API des modèles inclus/exclus |
| `COMPARISON_MAX_PROMPT_CHARS` | 8000 | Taille de question |
| `COMPARISON_MAX_OUTPUT_TOKENS` | 1024 | Plafond par appel ; maximum autorisé 8192 |
| `COMPARISON_MAX_CREDITS` / `COMPARISON_AUTOMATIC_MAX_CREDITS` | 500 / 150 | Réservation maximale par comparaison |
| `COMPARISON_USER_DAILY_CREDITS` / `COMPARISON_GLOBAL_DAILY_CREDITS` | 2000 / 20000 | Plafonds de réservations quotidiennes UTC |
| `COMPARISON_RATE_LIMIT` | 3 | Créations par utilisateur/minute |
| `COMPARISON_STALE_SECONDS` | 600 | Récupération des tâches abandonnées ; minimum 300 |
| `COMPARISON_MAX_CANDIDATES` | 80 | Taille maximale de présélection |
| `COMPARISON_EXPLORATION_RATE` | 0.2 | Exploration ; minimum 0.05 |
| `EVALUATION_VOTE_RATE_LIMIT` | 10 | Votes par utilisateur/minute |
| `EVALUATION_MIN_READING_SECONDS` | 5 | Seuil de signalement des votes rapides |
| `EVALUATION_REWARD_MIN_ACCOUNT_DAYS` | 7 | Ancienneté minimale pour proposer un bonus |
| `EVALUATION_REWARD_CREDITS` | 10 | Montant par dossier créé |
| `EVALUATION_DAILY_REWARD_LIMIT` / `EVALUATION_MONTHLY_REWARD_LIMIT` | 3 / 30 | Nombre de versements par utilisateur |
| `EVALUATION_GLOBAL_REWARD_BUDGET` | 0 | Financement cumulatif maximal en jetons |
| `EVALUATION_RETENTION_DAYS` | 90 | Conservation des contenus |
| `EVALUATION_ANALYSIS_MAX_VOTES` | 5000 | Votes les plus récents par catégorie |
| `EVALUATION_ROUTING_MIN_USERS` / `EVALUATION_ROUTING_MIN_SAMPLES` | 10 / 30 | Seuils de routage par modèle |

## Migrations et activation progressive

Deux migrations additives, sans modification des tables existantes :

- `evaluations/0001_initial` : six tables, index et contraintes.
- `evaluations/0002_budget_lock` : ligne singleton de verrou/budget.

Elles ont été appliquées uniquement dans les bases de tests temporaires. Avant une activation opérateur, appliquer les migrations du projet **avant** de démarrer le nouveau code qui accède aux bonus depuis le portefeuille.

```sh
python manage.py migrate
python manage.py check
python manage.py comparison_worker
```

Faire fonctionner un worker supervisé et un déclenchement périodique de `comparison_worker --once` ou un contrôle de disponibilité indépendant. Un worker unique suffit au lancement. Utiliser PostgreSQL pour les verrous en production et Redis pour le limiteur partagé ; SQLite convient au développement avec un worker unique. Une panne totale des services exige la relance d’un worker ou de `purge_evaluations` pour rembourser les réservations abandonnées.

1. Activer seulement `COMPARISON_ENABLED` après revue des fournisseurs et configuration des budgets. Vérifier coûts, remboursements et erreurs sur un groupe pilote.
2. Activer `COMPARISON_AUTOMATIC_ENABLED` avec un petit catalogue économique autorisé. Vérifier le masquage et la diversité.
3. Programmer `rebuild_evaluation_stats` au moins quotidiennement et consulter `/admin/evaluations/comparisonsession/dashboard/`. Les votes existent dès la comparaison ; cette étape ouvre l’observation statistique sans effet sur Auto.
4. Financer un budget expérimental explicite, configurer les plafonds puis activer `EVALUATION_REWARDS_ENABLED`. Valider humainement chaque bonus avant crédit.
5. Après données suffisantes et vérification des cohortes, activer `EVALUATION_ROUTING_ENABLED` pour le chat Auto simple. Surveiller qualité, coûts et latence.

Repli : désactiver le flag concerné ; le routeur revient immédiatement au comportement existant. La désactivation générale empêche de nouvelles générations mais laisse consulter/supprimer l’historique et rembourser les tâches abandonnées. Garder le mécanisme de récupération actif jusqu’au règlement de toutes les réservations.

Planifier également :

```sh
python manage.py rebuild_evaluation_stats
python manage.py purge_evaluations
```

## Vérification

```sh
python manage.py test --noinput --settings=evaluations.test_settings
EVALUATION_TEST_POSTGRES_DSN=postgresql://utilisateur@localhost:port/base_temporaire \
  python manage.py test evaluations --noinput --settings=evaluations.test_settings
python manage.py makemigrations --check --dry-run --settings=evaluations.test_settings
python manage.py check --settings=evaluations.test_settings
ruff check --exclude node_modules,.venv .
node --check static/app/js/comparisons.js
node --test tests/js/*.cjs
python tests/browser/comparisons.py  # Playwright + Chrome locaux, facultatif
```

Les paramètres de tests utilisent une base isolée et ne modifient jamais la base configurée de l’application. Le test navigateur crée sa propre base SQLite temporaire avec WAL, ou utilise le DSN PostgreSQL temporaire fourni. Les appels IA sont simulés. Les cinq tests concurrents de `test_concurrency.py` sont ignorés sur SQLite et exécutés sur PostgreSQL.

Première suite étendue : un test chat préexistant échouait également sur une copie intacte de HEAD, car il désactivait la clé de recherche tout en attendant une recherche réussie. Sa méthode dispose maintenant d’une clé factice sous `override_settings`, avec recherche et fournisseur toujours simulés ; aucun comportement du chat n’a été modifié pour ce correctif.

Résultats du 15 octobre 2026 sur l’implémentation finale :

| Vérification | Résultat |
| --- | --- |
| Suite Django complète sur SQLite isolé | 467 tests : 462 réussis, 5 tests de verrous ignorés |
| Application `evaluations` sur PostgreSQL 16 temporaire | 55 tests réussis, dont les 5 tests concurrents |
| Navigateur réel Chrome/Playwright | Parcours manuel, reprise après actualisation, vote, Surprise masqué/révélé, mobile sombre, suppression : réussi |
| JavaScript existant | 9 tests réussis |
| Ruff 0.17.0, syntaxe du script, `git diff --check` | Réussis |
| Vérifications Django et cohérence des migrations | Réussies ; aucune migration manquante |
| Construction WhiteNoise avec manifeste dans `/tmp` | 190 fichiers copiés, 556 traitements réussis |

Le seul avertissement de configuration observé est le contrôle existant `ai_2ai.W001` : Redis absent dans les paramètres de tests. Les nouvelles limites persistantes en base restent testées. Aucun appel IA réel ni migration sur la base applicative n’a été effectué.

## Fichiers

Créés :

- `evaluations/__init__.py`, `apps.py`, `checks.py`, `models.py`
- `evaluations/selection.py`, `services.py`, `jobs.py`, `rewards.py`, `analytics.py`
- `evaluations/views.py`, `urls.py`, `admin.py`
- `evaluations/migrations/__init__.py`, `0001_initial.py`, `0002_budget_lock.py`
- `evaluations/management/__init__.py`, `management/commands/__init__.py`
- `evaluations/management/commands/comparison_worker.py`, `rebuild_evaluation_stats.py`, `purge_evaluations.py`
- `evaluations/templates/evaluations/index.html`
- `evaluations/templates/admin/evaluations/dashboard.html`
- `evaluations/templates/admin/evaluations/reward_review.html`
- `evaluations/tests.py`, `test_concurrency.py`, `test_settings.py`
- `static/app/js/comparisons.js`, `static/app/css/comparisons.css`
- `tests/browser/comparisons.py`, `EVALUATIONS.md`

Modifiés :

- `ai_2ai/settings.py` : application, flags, plafonds et paramètres.
- `ai_2ai/urls.py` : routes du module.
- `ai_user/models.py` : suivi de consommation/restauration des bonus dans les transactions existantes ; aucune modification de schéma.
- `chat/views.py` : navigation conditionnelle et branchement optionnel sur Auto simple.
- `chat/templates/chat.html` : lien de comparaison conditionnel, y compris dans la navigation mobile existante.
- `chat/test_token_optimization.py` : correction du paramétrage d’un test web préexistant.
- `.env.example`, `README.md` : configuration et documentation.

## Limites techniques restantes

- Les coûts fournisseur restent des estimations tarifaires ; rapprocher les factures et surveiller les réponses sans consommation exploitable.
- Les quotas locaux ne remplacent pas les plafonds du compte fournisseur. Un appel déjà envoyé ne peut pas être annulé à distance par le bouton de suppression ou la fermeture de la page.
- Une panne après envoi fournisseur mais avant enregistrement peut entraîner un coût absorbé par 2AI ; le système privilégie l’absence de double appel et de double débit.
- La file et les instantanés statistiques utilisent un verrou global de budget : approche volontairement simple et sûre pour le lancement, à profiler avant de fortes charges.
- Les récompenses exigent une revue humaine autorisée ; aucun jugement de qualité automatique ni versement automatique n’est fourni.
- Les intervalles statistiques sont indicatifs ; la propension plafonnée, le recrutement des utilisateurs et la présélection économique limitent les conclusions de classement.
- Les bonus consommés des comptes supprimés ne sont plus détaillés ; le financement global distribué reste conservé. Aucun retrait monétaire n’est ajouté.
- La revue contractuelle du catalogue effectif, la supervision des workers, Redis, les sauvegardes et l’activation progressive restent des opérations de l’exploitant.
