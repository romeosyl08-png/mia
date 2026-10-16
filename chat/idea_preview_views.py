from django.contrib.auth.decorators import login_required
from django.shortcuts import render
from django.views.decorators.http import require_GET


@login_required
@require_GET
def examples(request):
    samples = [
        {'kind': 'comparison', 'title': 'Comparaison IA — aperçu du fonctionnement',
         'description': 'Choisis deux options, puis essaie le mode Surprise. Toutes les réponses sont des exemples fixes.',
         'models': [
             {'name': 'GPT (exemple)', 'text': 'Une première approche : un plan concis en trois étapes.'},
             {'name': 'Claude (exemple)', 'text': 'Une deuxième approche : une explication avec un exemple détaillé.'},
             {'name': 'Mistral (exemple)', 'text': 'Une autre approche : une réponse courte et pratique.'},
         ]},
        {'kind': 'prototype', 'title': 'Une idée, plusieurs écrans',
         'description': 'Navigue dans une maquette avant de développer la fonctionnalité.',
         'screens': [
             {'id': 'accueil', 'title': 'Découvrir', 'body': 'Présente ton idée ici. Le bouton ouvre un deuxième écran.',
              'actions': [{'label': 'Voir un exemple', 'target': 'exemple'}]},
             {'id': 'exemple', 'title': 'Essayer', 'body': 'Voici le résultat simulé de l’idée. Aucune action réelle n’a été exécutée.',
              'actions': [{'label': 'Revenir à l’accueil', 'target': 'accueil'}]},
         ]},
        {'kind': 'calculator', 'title': 'Tester une hypothèse de budget',
         'description': 'Tarif fictif : 5 € par unité. Ce montant est une hypothèse, pas le tarif de 2AI.',
         'inputs': [{'label': 'Quantité', 'min': 0, 'max': 100, 'step': 1, 'value': 10, 'coefficient': 5}],
         'offset': 0, 'result_label': 'Budget simulé', 'unit': '€'},
    ]
    response = render(request, 'idea_preview_examples.html', {'preview_examples': samples})
    response['Cache-Control'] = 'private, no-store'
    return response
