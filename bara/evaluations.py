"""Task-level benchmarks with explicit verification and human-review limits."""
import io
import re
import time
from .models import AgentTask, Workspace, WorkFile
from .services import store_file_content
from .tasks import claim_task, execute_task

SCENARIOS = [
    {'id':'research', 'prompt':'Recherche la loi d’Ohm sur le Web. Lis au moins deux sources distinctes et explique la formule avec des citations.', 'files':{}},
    {'id':'coding', 'prompt':'Corrige is_even dans numbers.py. Exécute test_numbers.py et conserve les deux fichiers.',
     'files':{'numbers.py':'def is_even(n):\n    return n % 2 == 1\n',
              'test_numbers.py':'from numbers import is_even\nassert is_even(0)\nassert is_even(2)\nassert is_even(-4)\nassert not is_even(3)\nprint("AI2AI_CHECKS_PASSED")\n'}},
    {'id':'document', 'prompt':'Crée bilan.xlsx avec create_document, au format xlsx. Mets les colonnes Article et Montant, les lignes A=120 et B=80, puis Total avec la formule =SUM(B2:B3).', 'files':{}},
]


def verify(task, scenario):
    checks = {'completed': task.status == 'completed'}
    human_review = []
    if scenario == 'research':
        sources = task.state.get('sources', [])
        ids = {str(i + 1) for i in range(len(sources))}
        groups = re.findall(r'\[\[sources:([\d, ]+)\]\]', task.result)
        cited = {v.strip() for group in groups for v in group.split(',')}
        checks.update(sources_read=len(sources) >= 2, citations_resolve=bool(cited) and cited <= ids)
        human_review.append('Vérifier que les sources soutiennent réellement chaque affirmation.')
    elif scenario == 'coding':
        from .tools.definitions import run_command
        output = run_command(task.workspace, ['python', 'test_numbers.py'])
        checks['tests_pass'] = output.get('exit_code') == 0 and 'AI2AI_CHECKS_PASSED' in output.get('stdout','')
        if output.get('error'):
            human_review.append(output['error'])
    elif scenario == 'document':
        from openpyxl import load_workbook
        file = task.workspace.files.filter(original_name='bilan.xlsx').first()
        try:
            with file.file.open('rb') as f:
                sheet = load_workbook(io.BytesIO(f.read()), data_only=False).active
            checks['values_and_formula'] = sheet['B2'].value == 120 and sheet['B3'].value == 80 and sheet['B4'].value == '=SUM(B2:B3)'
        except (AttributeError, OSError, ValueError, KeyError):
            checks['values_and_formula'] = False
    return {'checks': checks, 'passed': all(checks.values()), 'human_review': human_review,
            'quality_fully_verified': all(checks.values()) and not human_review}


def run_scenario(user, model, scenario, credit_limit):
    workspace = Workspace.objects.create(user=user, title='Évaluation : ' + scenario['id'], active_model=model)
    for name, text in scenario['files'].items():
        file = WorkFile(workspace=workspace, original_name=name)
        store_file_content(file, text)
    task = AgentTask.objects.create(workspace=workspace, creator=user, model=model,
                                   objective=scenario['prompt'], credit_limit=credit_limit)
    start = time.monotonic()
    # The command owns this task only; it never drains another user's queue.
    for _ in range(task.max_steps + 2):
        task.refresh_from_db()
        if task.status != 'queued':
            break
        claimed = claim_task(task.pk)
        if not claimed:
            break
        execute_task(claimed)
    task.refresh_from_db()
    verdict = verify(task,scenario['id'])
    receipt = task.events.filter(kind='response').order_by('-id').first()
    if receipt and not verdict['human_review'] and task.status=='completed':
        from chat.models import RoutingFeedback
        RoutingFeedback.objects.update_or_create(observation_id=receipt.data['observation_id'],user=user,
            defaults={'rating':'positive' if verdict['passed'] else 'negative','correction_needed':not verdict['passed']})
    return {'scenario': scenario['id'], 'task_id': task.pk, 'status':task.status,
            'seconds':round(time.monotonic() - start, 3), 'credits':task.spent_credits,
            **verdict}
