"""Prompting for local, declarative widgets; no generated HTML or scripts execute."""
import re
import unicodedata


def wants_idea_preview(content):
    text = ''.join(c for c in unicodedata.normalize('NFKD', content.casefold()) if not unicodedata.combining(c))
    if re.search(r'\b(?:sans|pas d[e’\x27 ]*|aucun)\s*(?:apercu|prototype|maquette)', text):
        return False
    return bool(re.search(r'\b(?:apercu|prototype|maquette|simulateur|demo interactive|simulation interactive)\b', text))


PREVIEW_INSTRUCTIONS = '''
Pour cette demande, accompagne ta réponse d'un aperçu interactif local si cela aide à tester l'idée.
Ajoute au maximum un bloc Markdown ```2ai-preview contenant un objet JSON valide. Aucun HTML, JavaScript,
URL, appel réseau ou code à exécuter : l'interface construit les contrôles à partir de données déclaratives.
Ces contrôles sont une démonstration : ils n'appellent aucune IA, n'enregistrent aucun vote et ne réalisent
aucun achat. Les données d'exemple et les hypothèses doivent être annoncées comme telles.
Choisis l'un des trois formats suivants et adapte librement les textes à l'idée de l'utilisateur :
1. Comparaison (de 2 à 8 modèles/options) :
{"kind":"comparison","title":"Comparaison IA","description":"Exemple de fonctionnement", "models":[{"name":"Option 1","text":"Exemple de résultat 1"},{"name":"Option 2","text":"Exemple de résultat 2"}]}
2. Prototype navigable (de 1 à 8 écrans ; chaque cible doit être l'id d'un écran existant) :
{"kind":"prototype","title":"Mon idée","screens":[{"id":"accueil","title":"Accueil","body":"Présentation", "actions":[{"label":"Essayer","target":"resultat"}]},{"id":"resultat","title":"Résultat","body":"Exemple de résultat", "actions":[{"label":"Retour","target":"accueil"}]}]}
3. Calculateur avec curseurs (1 à 6 entrées ; résultat = offset + somme(valeur × coefficient)) :
{"kind":"calculator","title":"Budget estimé","description":"Hypothèses de démonstration", "inputs":[{"label":"Quantité","min":0,"max":100,"step":1,"value":10,"coefficient":5}],"offset":0,"result_label":"Total estimé","unit":"€"}
Titre : 100 caractères maximum. Description : 500. Texte d'une réponse/écran : 2000. JSON total : 16000 caractères.
Préfère des textes courts pour terminer le JSON dans le plafond de sortie. Ne présente jamais l'aperçu comme
une action réelle. Après le bloc, explique seulement les hypothèses utiles.
'''
