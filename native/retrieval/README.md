# Recherche documentaire Bara : index lexical et calcul C

Les mots-clés de chaque passage sont normalisés une fois pendant l'indexation,
puis conservés dans le JSON existant. Un index ancien est enrichi à sa prochaine
lecture. Cette mise à jour préserve les vecteurs, le modèle, les pages et les
positions ; elle ne redemande aucun embedding au fournisseur. Une modification
du texte invalide les anciens mots-clés et vecteurs. Aucun changement de schéma
ni migration Django n'est nécessaire. La première lecture d'un ancien index
effectue donc une écriture locale ; les lectures suivantes réutilisent l'index.

Le module `bara._retrieval_native` remplace uniquement le calcul de similarité
cosinus entre deux vecteurs numériques. Il parcourt les coordonnées en C sans
copie intermédiaire des listes. Le GIL reste détenu pendant ce calcul pour garder
les listes Python stables. Les appels réseau, l'ORM, l'authentification et les
autorisations restent dans les fonctions Django existantes. Les vecteurs d'un
ancien modèle d'embedding sont ignorés lorsqu'un autre modèle est configuré.

L'absence du module active la même formule en Python. Aucun compilateur n'est
lancé au démarrage du serveur ou pendant une requête. Le module ne concerne pas
la génération de réponses par les modèles IA. Le moteur Markdown Rust/Wasm
existant reste chargé de l'analyse des longues réponses dans le navigateur.

## Construction

Depuis la racine du dépôt, avec le même interpréteur que celui du serveur :

```sh
python scripts/build_retrieval_native.py
```

Le script nécessite CPython, Linux/macOS, un compilateur C et les en-têtes de
développement Python (`Python.h`). Il utilise la configuration de compilation
de cet interpréteur, valide le résultat dans un processus séparé et remplace
l'artefact seulement après réussite. `CC` permet de choisir le compilateur.
Le binaire dépend de la plateforme et de la version Python : il est ignoré par
Git et doit être reconstruit dans chaque environnement.

Render utilise `--optional` : un compilateur ou des en-têtes absents conservent
le chemin Python et produisent un message dans les logs du build. Un ancien
binaire généré est alors supprimé pour éviter son chargement par erreur.
L'intégration continue exige une compilation réussie et teste les deux chemins.

## Vérification et mesures

```sh
python scripts/build_retrieval_native.py
python manage.py test bara.test_retrieval bara.test_token_optimization chat.test_memory --noinput --settings=evaluations.test_settings
python tests/bench/retrieval.py
```

Validation locale : suite Django complète, 512 tests dont 507 réussis et 5 tests
PostgreSQL ignorés sur SQLite ; suite ciblée sans binaire, 18 tests dont 16
réussis et 2 tests réservés au C ignorés. L'absence de compilateur a également
été simulée pour vérifier le build facultatif. Ruff, les vérifications Django
et la cohérence des migrations passent.

Le benchmark est déterministe et hors ligne : 40 documents, 800 passages,
vecteurs de 1 536 coordonnées, médiane de cinq mesures après échauffement.
Il vérifie aussi les mêmes six passages les mieux classés et compare les scores
à une tolérance de `1e-12`. Les écarts d'arrondi entre les calculs C et Python
peuvent modifier l'ordre de scores presque identiques. Les égalités exactes
conservent le tri par fichier puis position.

Mesures locales du 16 octobre 2026 (CPython 3.14, à reproduire sur le serveur) :

| Recherche | Code initial | Index lexical + Python | Index lexical + C |
| --- | ---: | ---: | ---: |
| Par mots-clés | 107,87 ms | 3,62 ms | 3,09 ms |
| Sémantique | 245,37 ms | 150,53 ms | 12,62 ms |

Le C n'est pas utilisé dans une recherche purement lexicale : les petites
différences entre ses deux mesures indexées sont du bruit de mesure.
Ces chiffres isolent le parcours et le classement des passages déjà chargés.
Ils excluent l'indexation initiale, les lectures SQL, la désérialisation JSON,
le fournisseur d'embeddings et la génération IA ; ils ne prédisent pas le
temps total d'une requête de production. La désérialisation de gros index peut
ainsi devenir le coût dominant après cette optimisation.

## Branches GitHub examinées

Comparaison effectuée le 16 octobre 2026 avec `main` au commit `9d129c6` :

- [`arena/89454cbe-mia`](https://github.com/romeosyl08-png/mia/tree/arena/89454cbe-mia)
  contient le renforcement de sécurité déjà repris dans `main` par `c1bc57f`.
  Les arbres de ces deux commits sont identiques. Réintroduire l'ancien arbre
  complet ferait perdre les fonctionnalités ajoutées ensuite dans `main`.
- [`arena/e8f3f7c8-mia`](https://github.com/romeosyl08-png/mia/tree/arena/e8f3f7c8-mia)
  est en avance de sept commits sur cette version de `main`. Les changements
  portent notamment sur le classement communautaire, l'apparence des personas,
  le rendu des réponses en streaming, le Studio et l'extension VS Code.
  Le code de recherche documentaire y est identique à celui de `main` avant
  cette optimisation. Le planificateur du rendu Markdown déjà présent dans
  cette branche traite un autre coût, côté navigateur.

Cette modification est faite sur la copie locale de `main`. Les branches
distantes ont été lues ; aucune fusion, publication ou activation de l'Arène
n'a été effectuée. Les changements de compilation dans la CI et Render devront
être conservés lors d'une future fusion avec la branche Arena récente.
