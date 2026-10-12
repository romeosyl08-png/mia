# Audit Playwright de 2AI — 12 octobre 2026

Application testée : http://127.0.0.1:8000 avec Chromium piloté par Playwright. Ordinateur : 1440 × 1000 ; mobile : viewport 390 × 844. Les interactions ont utilisé des comptes, personas, conversations, espaces et fichiers de test.

## Résultat final

- 53 scénarios Playwright réussis ; 0 échec.
- 0 erreur JavaScript et 0 requête réseau échouée relevées lors du passage final.
- 50 tests Django ciblés réussis ; six suites JavaScript réussies.
- Vérification Django, cohérence des migrations et syntaxe JavaScript : réussies.
- Aucun paiement ni aucune suppression de données de l’application. Les données de test sont conservées.

## Problèmes constatés et corrections

| Problème | Correction | Vérification |
| --- | --- | --- |
| L’inscription valide renvoyait HTTP 500 : colonnes des personas absentes de la base. | Application de `chat.0024_persona_sharing_big_persona`, après autorisation explicite. | Création du compte de test, personas chargés, connexion et navigation fonctionnelles. |
| Une erreur pendant la création des personas pouvait laisser un compte partiellement créé. | Création du compte et de ses données initiales dans une transaction atomique. | Test d’échec de configuration : aucun utilisateur, portefeuille ou persona partiel enregistré. |
| Le formulaire annonçait la connexion par e-mail mais authentifiait uniquement par nom utilisateur. | Recherche d’un compte unique par e-mail, sans distinction de casse ; maintien des contrôles du mot de passe et des comptes inactifs. | Connexion par e-mail et nom utilisateur dans Playwright ; tests des e-mails ambigus et des comptes inactifs. |
| La recherche de modèle ne donnait aucune indication lorsqu’aucune IA ne correspondait. | Ajout d’un message sans résultat, synchronisé avec le filtre. | Recherche vide, recherche sans résultat, catégories et sélection d’un modèle. |
| Échap ne fermait pas le menu « + » du chat. | Fermeture du menu et retour du focus au bouton. | Ouverture puis fermeture au clavier dans Playwright. |
| La documentation API dépassait horizontalement sur mobile : 395 px pour un viewport de 390 px. | Retour à la ligne des chaînes longues. | Largeur finale de la page : 390 px ; aucun débordement horizontal. |
| Le panneau des outils Bara décrivait encore Piston et une exécution non isolée sur l’hôte. | Texte aligné sur le runtime actuel : conteneur isolé sans réseau, fichiers en lecture seule. | Lecture des fonctions `run_python` et `run_command` et du panneau Bara. |

Le bouton ninja, la sélection explicite d’une mémoire et l’enregistrement de la conversation dans un persona ont été vérifiés sur l’application réelle, y compris après rechargement. Les corrections antérieures du bouton et du cache restent en place.

## Périmètre et limites

Les requêtes de génération du chat et de Bara ont reçu des réponses déterministes simulées dans Playwright. La sélection des modèles, les données transmises, le streaming, l’affichage, les contrôles Équipe IA et la sauvegarde explicite des conversations ont été testés. La création des comptes, les réglages des personas, les mémoires, la sauvegarde temporaire et les opérations de fichiers Bara ont utilisé le vrai serveur Django.

L’exécution réelle chez les fournisseurs IA, les connexions OAuth externes, l’envoi d’OTP et les transactions Stripe/PayDunya n’ont pas été déclenchés. La recharge a été testée pour l’affichage du solde, les devises, les montants, les estimations et les validations de champs. Les endpoints de paiement et de suppression sont bloqués par le script de test.

Le mobile est une émulation de viewport dans Chromium ; ce rapport ne constitue pas un test sur un téléphone physique ou dans Safari.

Compte principal de test : `qa_playwright_1791814139789`. Un premier essai d’inscription avant migration a laissé un compte de test partiel ; il a été conservé conformément à l’interdiction de suppression. Les espaces Bara et personas créés pendant les passages ont également été conservés.

## Captures et preuves

- [Conversation temporaire sur ordinateur](/tmp/2ai-playwright-audit/temporary-1440.png)
- [Conversation temporaire sur mobile](/tmp/2ai-playwright-audit/temporary-390.png)
- [Connexion sur mobile](/tmp/2ai-playwright-audit/auth-mobile.png)
- [Recharge sur mobile](/tmp/2ai-playwright-audit/recharge-390.png)
- [API sur mobile après correction](/tmp/2ai-playwright-audit/api-mobile-after.png)
- [Bara sur mobile](/tmp/2ai-playwright-audit/bara-mobile-after.png)
- [Résultats structurés du passage final](/tmp/2ai-playwright-audit/report.json)

## Scénarios du passage final

| Scénario | Résultat |
| --- | --- |
| Connexion — refus de mot de passe incorrect | Réussi |
| Connexion — adresse e-mail | Réussi |
| Connexion — nom utilisateur | Réussi |
| Chat — boutons, modèles et catégories | Réussi |
| Personas — création et renommage | Réussi |
| Personas — mémoire et réglages enregistrés | Réussi |
| Mémoire du compte — sauvegarde des préférences de test | Réussi |
| Conversation temporaire — ninja, mémoire, message et sauvegarde | Réussi |
| Équipe IA — choix des modèles et stratégie | Réussi |
| Chat standard — réponse et galeries | Réussi |
| Bara — création d’un espace de test | Réussi |
| Bara — instructions et document de test | Réussi |
| Bara — réponse en flux simulée | Réussi |
| Bara — modification et historique du document de test | Réussi |
| Navigation 1440 — / | Réussi |
| Navigation 1440 — /tarifs/ | Réussi |
| Navigation 1440 — /recharge/ | Réussi |
| Navigation 1440 — /customize/ | Réussi |
| Navigation 1440 — /memory/ | Réussi |
| Navigation 1440 — /bara/ | Réussi |
| Navigation 1440 — /api/ | Réussi |
| Navigation 1440 — /api-keys/ | Réussi |
| Navigation 1440 — /nous/ | Réussi |
| Navigation 1440 — /juridique/ | Réussi |
| Navigation 1440 — /bara/8/c/8/ | Réussi |
| Navigation 1440 — /bara/8/tasks/ | Réussi |
| Navigation 1440 — /bara/8/journal/ | Réussi |
| Navigation 1440 — /bara/8/connections/ | Réussi |
| Navigation 1440 — /bara/8/sharing/ | Réussi |
| Navigation 1440 — /bara/8/schedules/ | Réussi |
| Recharge 1440 — devises, montants et validations sans paiement | Réussi |
| Chat 1440 — menus et conversation temporaire | Réussi |
| Tarifs 1440 — filtre des modèles | Réussi |
| Navigation 390 — / | Réussi |
| Navigation 390 — /tarifs/ | Réussi |
| Navigation 390 — /recharge/ | Réussi |
| Navigation 390 — /customize/ | Réussi |
| Navigation 390 — /memory/ | Réussi |
| Navigation 390 — /bara/ | Réussi |
| Navigation 390 — /api/ | Réussi |
| Navigation 390 — /api-keys/ | Réussi |
| Navigation 390 — /nous/ | Réussi |
| Navigation 390 — /juridique/ | Réussi |
| Navigation 390 — /bara/8/c/8/ | Réussi |
| Navigation 390 — /bara/8/tasks/ | Réussi |
| Navigation 390 — /bara/8/journal/ | Réussi |
| Navigation 390 — /bara/8/connections/ | Réussi |
| Navigation 390 — /bara/8/sharing/ | Réussi |
| Navigation 390 — /bara/8/schedules/ | Réussi |
| Recharge 390 — devises, montants et validations sans paiement | Réussi |
| Chat 390 — menus et conversation temporaire | Réussi |
| Tarifs 390 — filtre des modèles | Réussi |
| Connexion et inscription — affichage mobile et champs requis | Réussi |

## Reproduire

Depuis le dépôt, avec le serveur Django sur le port 8000 et Playwright installé :

```sh
node tests/browser/audit.cjs
node tests/browser/audit.cjs screenshots
node --test tests/js/*.test.cjs
```

Le script réutilise son compte de test stocké sous `/tmp/2ai-playwright-audit/`, ou en crée un lors d’un premier lancement. Il ne supprime pas les données de test. Le fichier contenant les identifiants de test est privé et n’est pas inclus dans ce rapport. Les tests Django ont utilisé une base temporaire distincte de la base de l’application.
