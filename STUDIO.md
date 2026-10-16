# Studio multimédia 2AI

Le studio est accessible à `/studio/`, depuis le chat et depuis un espace Bara.
Il conserve des projets privés, des sources, des références, des masques, des pistes
et des versions de scène. La restauration crée une nouvelle version ; aucun endpoint
ne supprime les projets, les médias ou les versions.

L’interface de montage utilise des panneaux sombres et compacts : médias et exports
à gauche, moniteur central, onglets **Outils IA**, **Clips** et **Projet** à droite,
et timeline en bas. Sélectionne un média pour le lire dans le moniteur **Source**,
ou un clip de la timeline pour ouvrir ses réglages. La timeline affiche les durées
des plans, les décalages audio, la position de lecture et un zoom réglable.
Le moniteur **Montage** permet de parcourir et lire les plans avec les pistes audio
du projet. Le son intégré aux vidéos reste exclu, comme lors de l’export. Sur mobile,
les panneaux se placent sous le moniteur et la timeline.

## Fonctionnement

- Un projet peut utiliser les instructions et la mémoire d’un persona appartenant au
  même compte et être associé à un espace Bara appartenant au même compte.
- Le directeur IA produit une proposition JSON validée. Une demande de masque manquant
  peut produire une explication sans opération. La préparation du plan consomme les
  jetons affichés ; les opérations nécessitent ensuite une confirmation distincte.
- Les générations d’images et de vidéos utilisent les modèles OpenRouter existants.
  Les images avec références et les éditions utilisent `STUDIO_IMAGE_EDIT_MODEL`.
  Le fournisseur audio vient de `MEDIA_GENERATION_MODELS`, comme dans le chat.
  Les capacités de voix et de références dépendent du modèle configuré.
- L’image source et le masque sont transmis à l’éditeur d’image. Le compositing local
  conserve exactement les pixels décodés en dehors du masque. Le masque doit avoir
  les mêmes dimensions que l’image. Les originaux restent dans la médiathèque.
- La recoloration par masque et les exports FFmpeg sont locaux et gratuits en jetons.
  Les plans visuels sont assemblés dans l’ordre. Les pistes audio restent distinctes
  dans la scène, avec leur type, leur volume et leur décalage. Les exports mélangent
  ces pistes ; le son intégré à une source vidéo n’est pas repris automatiquement.
- Un résultat de tâche ne remplace pas un montage enregistré pendant son exécution.
  Il reste accessible comme branche dans l’historique.

## Installation et worker

```sh
pip install -r requirements.txt
python manage.py migrate studio
python manage.py studio_worker
```

FFmpeg et FFprobe doivent être disponibles sur le **serveur Django**, par exemple via
le paquet système `ffmpeg`. Pillow est une dépendance explicite. Le montage ne passe
pas par les commandes libres de Bara ni par un service distant.

Le worker Bara `agent_worker`, lancé aussi par `run_services`, traite désormais les
tâches du studio. En développement, démarrer `studio_worker` dans un terminal séparé
du serveur Web. `--once` vide les tâches en attente et quitte. Pour traiter un seul
job de test, utiliser `--job-id ID`. Redémarrer les workers existants après déploiement.

Configuration supplémentaire :

```dotenv
STUDIO_IMAGE_EDIT_MODEL=google/gemini-3.1-flash-image
STUDIO_MEDIA_TIMEOUT_SECONDS=600
```

Les fichiers sont stockés dans `PRIVATE_MEDIA_ROOT`. Leur lecture, leur téléchargement
et les requêtes HTTP Range nécessitent une session du propriétaire.

## Limites de cette première version

Montages limités à trois minutes, 24 plans et 24 pistes audio, huit références et
huit opérations par plan. Imports limités à 100 Mo, dix minutes et 16 millions de
pixels. Les images sont normalisées en PNG, sans métadonnées EXIF ; une image animée
est importée comme image fixe. Les formats vidéo MP4/WebM et audio WAV/MP3/M4A/OGG/FLAC
doivent contenir de vraies pistes lisibles. Les lecteurs peuvent dépendre des codecs
pris en charge par le navigateur.

Les objets ne sont pas détectés automatiquement. Le suivi de masques dans une vidéo,
la modification cohérente d’un personnage sur plusieurs images et la séparation
automatique voix/musique restent à développer. Le projet fournit la base pour ces
extensions sans promettre leur exécution dans cette version.

Une tâche réserve son estimation avant exécution. La facture respecte le plafond
confirmé et rembourse les jetons inutilisés. Une tâche en attente peut être annulée.
Une tâche interrompue ne relance jamais automatiquement un appel potentiellement
payant. Les appels dont la réponse a été perdue peuvent néanmoins avoir coûté au
fournisseur ; leur coût n’est pas inventé dans le portefeuille de l’utilisateur.

## Validation

Tests Django : `python manage.py test studio`. Les tests créent une base séparée,
simulent les fournisseurs payants et vérifient aussi les vrais traitements FFmpeg.
Les tests couvrent les propriétaires, les versions, les conflits, les réservations,
les remboursements, le plafond, les masques et les références transmises au fournisseur.

`node tests/browser/studio.cjs` utilise Playwright sur `http://127.0.0.1:8000` avec
le compte `qa_playwright_…` créé par l’audit précédent. Les identifiants sont lus dans
`/tmp/2ai-playwright-audit/test-account.json`, jamais imprimés. Seuls des médias
synthétiques et des opérations gratuites sont exécutés. Le directeur est simulé
dans le navigateur. Le rapport et les captures sont dans `/tmp/2ai-studio-audit/`.
Les projets de test sont conservés et aucune recharge n’est déclenchée.

Validation du 12 octobre 2026 : 116 tests Django, six suites JavaScript et
13 parcours Playwright réussis. Le dernier passage ne relève aucune erreur
JavaScript ni requête en échec. Les vrais exports vidéo avec audio et WAV ont
été produits et lus ; les fournisseurs de génération payants ont été simulés.
Les vues ont été vérifiées à 390, 768 et 1440 pixels.

Les essais sur la base PostgreSQL ont permis de corriger le verrouillage d’un
projet avec persona facultatif (`select_for_update(of=('self',))`). Le sélecteur
d’historique sélectionne désormais la version actuelle après un enregistrement,
une restauration ou le chargement d’un résultat.

[Rapport Playwright](/tmp/2ai-studio-audit/report.json) ·
[Capture ordinateur](/tmp/2ai-studio-audit/studio-1440.png) ·
[Capture mobile](/tmp/2ai-studio-audit/studio-390.png)
