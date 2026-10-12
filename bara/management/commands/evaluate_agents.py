import json
from pathlib import Path
from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError
from api.models import AIModel
from bara.evaluations import SCENARIOS, run_scenario
from bara.models import EvaluationRun


class Command(BaseCommand):
    help = 'Évalue des tâches réelles. --live utilise le modèle et les crédits du compte indiqué.'

    def add_arguments(self, parser):
        parser.add_argument('--live', action='store_true')
        parser.add_argument('--user')
        parser.add_argument('--model')
        parser.add_argument('--credit-limit', type=int, default=1000)
        parser.add_argument('--report')

    def handle(self, *args, **options):
        if not options['live']:
            self.stdout.write(json.dumps({'scenarios':SCENARIOS, 'live':False, 'message':'Aucun appel ni score simulé. Utilise --live pour mesurer les résultats.'}, ensure_ascii=False, indent=2))
            return
        user = get_user_model().objects.filter(username=options['user']).first()
        models = AIModel.objects.filter(model_id=options['model'], is_active=True, provider__is_active=True)
        if not user or models.count() != 1 or not 1 <= options['credit_limit'] <= 100000:
            raise CommandError('Utilisateur, modèle unique et budget valide requis.')
        model = models.first()
        rows = [run_scenario(user, model, scenario, options['credit_limit']) for scenario in SCENARIOS]
        report = {'model':model.model_id, 'results':rows, 'credits':sum(r['credits'] for r in rows),
                  'passed':sum(r['passed'] for r in rows), 'total':len(rows)}
        run = EvaluationRun.objects.create(creator=user, model=model, report=report)
        report['evaluation_id'] = run.pk
        rendered = json.dumps(report, ensure_ascii=False, indent=2)
        if options['report']:
            Path(options['report']).write_text(rendered, encoding='utf-8')
        self.stdout.write(rendered)
