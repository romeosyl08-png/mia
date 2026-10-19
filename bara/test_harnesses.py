import json
import tempfile
import uuid
from unittest.mock import patch

from django.test import TestCase, override_settings
from django.core.files.storage import FileSystemStorage

from chat.models import RoutingObservation
from .harnesses import configure_harness, select_harness
from .harnesses.code import check_command
from .models import AgentTask, ToolExecution, WorkFile, WorkMessage
from .services import store_file_content
from .tasks import claim_task, execute_task
from . import test_tasks


def tool(name, arguments, call_id=None):
    return {'id': call_id or uuid.uuid4().hex, 'type': 'function',
            'function': {'name': name, 'arguments': json.dumps(arguments)}}


@override_settings(BARA_CODE_EXECUTION_ENABLED=True)
class HarnessTaskTests(TestCase):
    def setUp(self):
        root = self.enterContext(tempfile.TemporaryDirectory(prefix='bara-harness-test-'))
        self.enterContext(patch.object(WorkFile._meta.get_field('file'), 'storage', FileSystemStorage(location=root)))
        test_tasks.DurableTaskTests.setUp(self)

    def task(self, objective='Corrige numbers.py', **kwargs):
        return AgentTask.objects.create(workspace=self.workspace, creator=self.user, model=self.model,
                                        objective=objective, **kwargs)

    def turn(self, task, response):
        with patch('bara.tasks.call_llm_with_tools', return_value=(response, 10, 10)) as provider:
            claimed = claim_task(task.pk)
            self.assertIsNotNone(claimed)
            execute_task(claimed)
        task.refresh_from_db()
        return provider

    def write(self, task, content='def is_even(n):\n    return n % 2 == 0\n'):
        self.turn(task, {'tool_calls': [tool('write_file', {'filename': 'numbers.py', 'content': content})]})

    def successful_check(self, task, command=None, following=None):
        command = command or ['python', '-m', 'unittest']
        self.turn(task, {'tool_calls': [tool('run_command', {'command': command})]})
        # File tools, workspace materialization and the shared dispatcher run for
        # real; only the unavailable container process is replaced in this test.
        with patch('bara.tools.definitions.run_sandbox_command', return_value={
            'exit_code': 0, 'isolated': True, 'stdout': 'OK', 'stderr': ''
        }) as runtime:
            self.turn(task, following or {'content': 'Correction vérifiée.'})
        return runtime

    def test_final_claim_without_actions_never_completes_and_retries_are_bounded(self):
        from .models import WorkConversation
        conversation = WorkConversation.objects.create(workspace=self.workspace)
        task = self.task(conversation=conversation)
        for _ in range(3):
            self.turn(task, {'content': 'Tout est corrigé, tous les tests passent.'})
        self.assertEqual(task.status, 'paused')
        self.assertEqual(task.steps, 3)
        self.assertEqual(task.state['harness']['correction_attempts'], 2)
        self.assertFalse(task.state['harness']['verification']['checks']['code_changes'])
        self.assertNotIn('tous les tests passent', task.result)
        self.assertFalse(WorkMessage.objects.filter(conversation=conversation, role='assistant').exists())

    def test_code_tools_are_filtered_and_unauthorized_calls_are_denied(self):
        task = self.task()
        provider = self.turn(task, {'tool_calls': [tool('create_document', {}, 'not-code')]})
        names = {schema['function']['name'] for schema in provider.call_args.kwargs['tools']}
        self.assertIn('write_file', names)
        self.assertNotIn('create_document', names)
        with patch('bara.tasks.dispatch_tool') as dispatch:
            self.turn(task, {'content': 'Impossible de créer ce document.'})
        dispatch.assert_not_called()
        self.assertEqual(task.status, 'queued')

    def test_successful_check_after_write_completes_with_persisted_evidence(self):
        task = self.task()
        self.write(task)
        runtime = self.successful_check(task)
        self.assertEqual(task.status, 'completed')
        self.assertTrue(task.state['harness']['verification']['checks']['current_code_check'])
        self.assertTrue(task.state['harness']['verification']['checks']['file_edit'])
        self.assertEqual(task.state['harness']['verification']['command'], ['python', '-m', 'unittest'])
        self.assertEqual(runtime.call_count, 1)
        self.assertEqual(task.events.filter(kind='harness_selected').count(), 1)
        self.assertEqual(RoutingObservation.objects.get(pk=task.events.filter(kind='response').last().data['observation_id']).task_category, 'coding')

    def test_write_after_successful_check_requires_another_check(self):
        task = self.task()
        self.write(task)
        self.successful_check(task, following={'tool_calls': [tool('write_file', {
            'filename': 'numbers.py', 'content': 'def is_even(n):\n    return False\n'
        }, 'second-write')]})
        self.turn(task, {'content': 'Terminé.'})
        self.assertEqual(task.status, 'queued')
        self.assertFalse(task.state['harness']['verification']['checks']['current_code_check'])

    def test_edit_outside_task_invalidates_a_previous_check(self):
        task = self.task()
        self.write(task)
        self.successful_check(task, following={'tool_calls': [tool('list_files', {}, 'inspect')]})
        file = WorkFile.objects.get(workspace=self.workspace, original_name='numbers.py')
        store_file_content(file, 'def is_even(n):\n    return False\n')
        self.turn(task, {'content': 'Terminé.'})
        self.assertEqual(task.status, 'queued')
        self.assertFalse(task.state['harness']['verification']['checks']['current_code_check'])

    def test_edit_during_check_cannot_validate_a_different_snapshot(self):
        task = self.task()
        self.write(task)
        self.turn(task, {'tool_calls': [tool('run_command', {'command': ['pytest']})]})

        def concurrent_edit(**kwargs):
            file = self.workspace.files.get(original_name='numbers.py')
            store_file_content(file, 'def is_even(n):\n    return False\n')
            return {'isolated': True, 'exit_code': 0}

        with patch('bara.tools.definitions.run_sandbox_command', side_effect=concurrent_edit):
            self.turn(task, {'content': 'Terminé.'})
        self.assertEqual(task.status, 'queued')
        self.assertFalse(task.state['harness']['verification']['checks']['current_code_check'])

    def test_failed_check_can_be_corrected_without_losing_task_budget(self):
        task = self.task()
        self.write(task)
        self.turn(task, {'tool_calls': [tool('run_command', {'command': ['python', '-m', 'unittest']})]})
        with patch('bara.tools.definitions.run_sandbox_command', return_value={
            'isolated': True, 'exit_code': 1, 'stderr': 'AssertionError'
        }):
            self.turn(task, {'content': 'Terminé malgré les erreurs.'})
        self.assertEqual(task.status, 'queued')
        self.assertIn('Contrôle réussi', task.result)
        spent = task.spent_credits
        self.successful_check(task)
        self.assertEqual(task.status, 'completed')
        self.assertGreater(task.spent_credits, spent)

    def test_arbitrary_successful_command_is_not_a_code_check(self):
        task = self.task()
        self.write(task)
        self.successful_check(task, ['python', '-c', 'print("all tests pass")'])
        self.assertEqual(task.status, 'queued')
        self.assertFalse(task.state['harness']['verification']['checks']['current_code_check'])

    def test_execution_without_isolation_is_not_evidence(self):
        task = self.task()
        self.write(task)
        self.turn(task, {'tool_calls': [tool('run_command', {'command': ['pytest']})]})
        with patch('bara.tools.definitions.run_sandbox_command', return_value={'exit_code': 0}):
            self.turn(task, {'content': 'Terminé.'})
        self.assertEqual(task.status, 'queued')
        self.assertFalse(task.state['harness']['verification']['checks']['current_code_check'])

    @override_settings(BARA_CODE_EXECUTION_ENABLED=False)
    def test_disabled_runtime_pauses_after_edits_without_spending_on_retries(self):
        task = self.task()
        self.write(task)
        self.turn(task, {'content': 'Modification enregistrée.'})
        self.assertEqual(task.status, 'paused')
        self.assertEqual(task.steps, 2)
        self.assertIn('Activer le runtime', task.error)

    def test_workspace_cannot_enable_a_disabled_command_for_code_harness(self):
        self.workspace.disabled_tools = ['run_command']
        self.workspace.save()
        task = self.task()
        self.write(task)
        provider = self.turn(task, {'content': 'Terminé.'})
        self.assertNotIn('run_command', {s['function']['name'] for s in provider.call_args.kwargs['tools']})
        self.assertEqual(task.status, 'paused')

    def test_explicit_command_is_pinned_across_turns_and_other_checks_do_not_satisfy_it(self):
        task = self.task(state={'harness': configure_harness('Corrige numbers.py', 'code', 'python test_numbers.py')})
        self.write(task)
        self.successful_check(task, ['pytest'])
        self.assertEqual(task.status, 'queued')
        self.successful_check(task, ['python', 'test_numbers.py'])
        self.assertEqual(task.status, 'completed')
        self.assertEqual(task.state['harness']['verification_command'], ['python', 'test_numbers.py'])

    def test_confirmation_counts_only_after_success_and_rejection_stops_retries(self):
        file = WorkFile(workspace=self.workspace, original_name='notes.txt')
        store_file_content(file, 'notes')
        task = self.task('Supprime notes.txt')
        self.turn(task, {'tool_calls': [tool('delete_file', {'file_id': file.pk})]})
        with patch('bara.tasks.call_llm_with_tools') as provider:
            execute_task(claim_task(task.pk))
        provider.assert_not_called()
        task.refresh_from_db()
        self.assertEqual(task.status, 'waiting')
        self.assertNotIn('file_delete', task.state['harness']['completed'])
        action = ToolExecution.objects.get(workspace=self.workspace, status='pending')
        self.client.force_login(self.user)
        response = self.client.post(f'/bara/{self.workspace.pk}/actions/{action.pk}/confirm/')
        self.assertEqual(response.status_code, 200)
        self.turn(task, {'content': 'Suppression effectuée.'})
        self.assertEqual(task.status, 'completed')
        self.assertIn('file_delete', task.state['harness']['completed'])

        file = WorkFile(workspace=self.workspace, original_name='other.txt')
        store_file_content(file, 'keep')
        refused = self.task('Supprime other.txt')
        self.turn(refused, {'tool_calls': [tool('delete_file', {'file_id': file.pk})]})
        execute_task(claim_task(refused.pk))
        action = ToolExecution.objects.get(workspace=self.workspace, status='pending')
        self.client.post(f'/bara/{self.workspace.pk}/actions/{action.pk}/reject/')
        provider = self.turn(refused, {'content': 'Suppression effectuée.'})
        provider.assert_not_called()
        self.assertEqual(refused.status, 'paused')
        self.assertTrue(WorkFile.objects.filter(pk=file.pk).exists())

    def test_create_task_validates_config_and_exposes_verification_without_internal_context(self):
        self.client.force_login(self.user)
        url = f'/bara/{self.workspace.pk}/tasks/create/'
        response = self.client.post(url, {'objective': 'Corrige numbers.py', 'harness': 'code',
                                          'verification_command': 'python -m unittest'})
        self.assertEqual(response.status_code, 302)
        task = self.workspace.agent_tasks.get()
        self.assertEqual(task.state['harness']['verification_command'], ['python', '-m', 'unittest'])
        self.write(task)
        self.successful_check(task)
        detail = self.client.get(f'/bara/{self.workspace.pk}/tasks/{task.pk}/').json()
        self.assertEqual(detail['harness']['id'], 'code')
        self.assertEqual(detail['harness']['verification']['status'], 'passed')
        self.assertNotIn('state', detail)
        for config in ({'harness': 'unknown'}, {'harness': 'code', 'verification_command': 'sh -c tests'},
                       {'harness': 'code', 'verification_command': 'python "unterminated'},
                       {'harness': 'general', 'verification_command': 'pytest'}):
            with self.subTest(config=config):
                self.assertEqual(self.client.post(url, {'objective': 'Corrige numbers.py', **config}).status_code, 400)

    def test_auto_routing_uses_general_category_for_non_code_work(self):
        self.workspace.auto_route = True
        self.workspace.save()
        self.client.force_login(self.user)
        with patch('bara.task_views.recommend_model', return_value={
            'model_id': self.model.model_id, 'provider_slug': self.model.provider.slug
        }) as recommend:
            response = self.client.post(f'/bara/{self.workspace.pk}/tasks/create/', {'objective': 'Bonjour'})
        self.assertEqual(response.status_code, 302)
        self.assertEqual(recommend.call_args.args[2], 'general')

    def test_code_explanation_does_not_require_modification_or_execution(self):
        task = self.task('Explique ce code Python :\n```python\nprint("fix this bug")\n```')
        self.turn(task, {'content': 'Une fonction regroupe des instructions.'})
        self.assertEqual(task.status, 'completed')

    def test_uncertain_write_is_not_retried_by_verifier(self):
        call = tool('write_file', {'filename': 'numbers.py', 'content': 'pass'})
        task = self.task(state={'messages': [], 'calls': [call], 'inflight': call['id']})
        with patch('bara.tasks.dispatch_tool') as dispatch:
            provider = self.turn(task, {'content': 'Terminé.'})
        dispatch.assert_not_called()
        provider.assert_not_called()
        self.assertEqual(task.status, 'paused')
        self.assertFalse(task.state['harness']['verification']['retryable'])


class HarnessSelectionTests(TestCase):
    def test_auto_selection_and_check_detection_are_conservative(self):
        self.assertEqual(select_harness('Ajoute des notifications à mon application Django').id, 'code')
        self.assertEqual(select_harness('Crée bilan.xlsx').id, 'documents')
        self.assertEqual(select_harness('Résume ce texte :\n```python\nprint(1)').id, 'general')
        for command in (['python', 'test_numbers.py'], ['npm', 'run', 'build'], ['node', '--test']):
            self.assertTrue(check_command(command))
        for command in (['pytest', '--version'], ['python', '-m', 'pytest', '--collect-only'],
                        ['python', '-c', 'print(1)'], ['git', 'status']):
            self.assertFalse(check_command(command))
