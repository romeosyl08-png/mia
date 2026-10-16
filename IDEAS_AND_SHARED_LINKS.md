# Aperçus interactifs et liens partagés dans 2AI

Les réponses du chat peuvent maintenant contenir des aperçus interactifs. Le lecteur de pages existant sait également extraire le texte des conversations ChatGPT partagées, y compris quand ce texte est chargé dans les données React de la page.

## Utilisation

- Dans le chat, ouvrir **+ → Aperçu interactif d’une idée**, compléter la demande puis envoyer. On peut aussi écrire directement « Crée un aperçu interactif de cette idée ».
- **+ → Essayer les aperçus sans jetons** ouvre `/chat/ideas/`, avec trois exemples utilisables immédiatement.
- Coller un lien public `https://chatgpt.com/share/…` dans une demande : le serveur lit la conversation avant l’appel IA et l’ajoute comme source citée.
- **+ → Lire un lien ChatGPT** permet de lire le lien, relire/modifier l’extrait et l’ajouter au brouillon sans envoyer le message. Si la lecture échoue, coller directement le texte dans ce panneau.

Les interactions dans un aperçu et la lecture seule d’un lien n’appellent aucun modèle et ne débitent aucun jeton. L’envoi d’une demande et la génération de sa réponse utilisent la facturation habituelle de 2AI, y compris les tokens du texte partagé transmis. Aucun aperçu ne vote dans le système réel de comparaisons, ne sélectionne un modèle réel ni n’attribue de récompense.

## Formats des aperçus

Le modèle reçoit une courte instruction de format lorsqu’un aperçu/prototype/une maquette est demandé. Le résultat est un bloc Markdown `2ai-preview` contenant des données JSON. Le rendu existant reconnaît ce bloc et le remplace par un composant du thème courant :

- `comparison` : deux à huit options, deux sélecteurs distincts, modes Manuel/Surprise et préférence simulée ;
- `prototype` : un à huit écrans, navigation et boutons vers les écrans existants ;
- `calculator` : un à six curseurs et un calcul linéaire explicite à partir de coefficients.

Les titres, textes, valeurs et tailles sont bornés. Aucun HTML ou JavaScript généré n’est exécuté ; aucun iframe ni assouplissement de CSP n’est ajouté. Les chaînes sont affichées avec `textContent`, les calculs n’utilisent pas `eval`, et les cibles des prototypes restent dans leurs écrans locaux. Un JSON invalide reste lisible comme code ordinaire.

Les aperçus sont conservés dans le contenu du message, avec les permissions et la suppression habituelles des conversations : aucune table supplémentaire. Ils sont reconstruits lors d’une réouverture de l’historique. L’état des contrôles est local ; il est préservé pendant les rafraîchissements du rendu sur la page, puis réinitialisé à son rechargement. La génération reste limitée au plafond de sortie choisi ; un JSON tronqué ne devient pas interactif. L’API utilise un plafond par défaut de 1600 tokens pour une demande d’aperçu sans limite explicite, tandis que toute limite explicitement fournie est respectée.

## Lecture des liens ChatGPT

`chat/shared_links.py` prend en charge les messages visibles en HTML, les anciennes pages `__NEXT_DATA__` et les tables de références React Router/turbo-stream. Le parseur n’exécute aucun script téléchargé. Il sélectionne la branche courante lorsqu’elle est indiquée et exclut les messages système, outils, analyses internes et messages masqués.

Le téléchargement réutilise `bara.tools.definitions.fetch_url` : vérification DNS et adresses publiques, ports limités, redirections vérifiées, timeout et taille bornée. Les identifiants dans les URL sont refusés. Le panneau n’accepte que les hôtes ChatGPT prévus et un chemin de partage UUID en HTTPS. Les paramètres décoratifs comme `?ogimg=plain` sont retirés ; aucune session ou clé de compte ChatGPT n’est demandée ni transmise.

Un extrait contient au maximum 20 000 caractères. Pour les longues conversations, le lecteur conserve la fin et indique explicitement la troncature. Les contenus restent des citations externes non fiables, séparées de la demande actuelle. Le modèle reçoit leur contenu avant l’estimation et la réservation du coût de son appel. Une erreur de lecture bloque cet appel sans débit et propose le collage manuel.

Le panneau utilise une route POST authentifiée, protégée par CSRF, limitée à cinq lectures par minute et servie avec `private, no-store`. Il ne crée aucune conversation et ne modifie aucune mémoire. Les extraits ajoutés manuellement sont placés dans un bloc cité ; les liens qu’ils contiennent ne sont pas relus automatiquement. La lecture d’un espace Bara respecte son outil `fetch_url` désactivé.

Les formats externes peuvent changer et les partages privés, supprimés ou bloqués peuvent être illisibles. Le collage manuel reste disponible. Le lecteur extrait les messages textuels ; il ne récupère pas automatiquement les fichiers, images privées ou applications interactives hébergées séparément. Les règles de partage restent celles de [ChatGPT](https://learn.chatgpt.com/docs/use-chatgpt).

## Vérifications

Le lien fourni dans la demande a été testé avec le vrai lecteur serveur : succès, 18 messages reconnus et troncature explicite de la longue conversation. Son contenu et son identifiant ne sont pas utilisés dans les fixtures versionnées ; les tests utilisent des données synthétiques.

```sh
python manage.py test --noinput --settings=evaluations.test_settings
node --test tests/js/*.cjs
python tests/browser/ideas.py
ruff check --exclude node_modules,.venv .
python manage.py check --settings=evaluations.test_settings
python manage.py makemigrations --check --dry-run --settings=evaluations.test_settings
```

Les tests automatisés simulent les fournisseurs IA et les téléchargements. La vérification ponctuelle du lien réel effectue seulement une lecture HTTP, sans appel IA. Le test navigateur couvre l’aperçu dans une réponse réellement enregistrée dans une base temporaire, les contrôles locaux sans nouveau débit, la reprise de l’historique, le panneau de lecture éditable, les trois types d’aperçus et le thème sombre mobile.

Fichiers ajoutés : `chat/idea_previews.py`, `chat/idea_preview_views.py`, `chat/shared_links.py`, `chat/shared_link_views.py`, `chat/test_shared_links.py`, `chat/templates/idea_preview_examples.html`, les scripts `idea_previews.js`, `idea_preview_examples.js`, `shared_links.js`, la feuille `idea_previews.css`, `tests/js/idea_previews.test.cjs`, `tests/browser/ideas.py` et cette documentation.

Fichiers modifiés pour cet ajout : `chat/views.py`, `chat/urls.py`, `chat/templates/chat.html`, `static/app/js/script.js`, `bara/tools/definitions.py`, `chat/templates/includes/markdown_scripts.html` et `README.md`. Le dernier gabarit corrige un commentaire multiligne préexistant qui s’affichait comme texte sous le chat. Aucun secret, paramètre de production ou modèle de données n’est modifié. Aucune migration supplémentaire n’est nécessaire. Les quatre flags du système de comparaison précédent conservent leurs valeurs désactivées.
