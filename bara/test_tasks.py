import json
import tempfile
import sys
from datetime import timedelta
from unittest.mock import patch
from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.utils import timezone
from api.models import APIProvider, AIModel
from .models import AgentTask, Workspace, WorkFile, ToolExecution
from .tasks import claim_task, execute_task
from .tools.sandbox import run_sandbox_command
from .tools.sandbox import _run_bounded


class DurableTaskTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user('task-user')
        self.user.credit_account.credit(10000)
        p = APIProvider.objects.create(name='Task provider', slug='task', base_url='https://example.test')
        p.keys.create(name='key', key='test-key')
        self.model = AIModel.objects.create(provider=p, name='Task', model_id='task-model', input_price_per_million=1, output_price_per_million=2)
        self.workspace = Workspace.objects.create(user=self.user, active_model=self.model)

    def task(self, **kwargs):
        return AgentTask.objects.create(workspace=self.workspace, creator=self.user, model=self.model, objective='Créer un fichier', **kwargs)

    def test_checkpoint_executes_write_once_after_worker_restart(self):
        task = self.task()
        call = {'id':'write1', 'type':'function', 'function':{'name':'write_file', 'arguments':json.dumps({'filename':'durable.txt','content':'hello'})}}
        with patch('bara.tasks.call_llm_with_tools', return_value=({'tool_calls':[call]}, 10, 10)):
            execute_task(claim_task())
        task.refresh_from_db()
        self.assertEqual(task.status, 'queued')
        self.assertIsNotNone(claim_task())
        self.assertIsNone(claim_task())
        # Release the deliberately claimed lease to simulate a stopped worker.
        task.refresh_from_db()
        task.lease_until = timezone.now() - timedelta(seconds=1)
        task.save()
        self.assertIsNone(claim_task())
        task.refresh_from_db()
        self.assertEqual(task.status, 'paused')
        self.client.force_login(self.user)
        self.assertEqual(self.client.post(f'/bara/{self.workspace.pk}/tasks/{task.pk}/resume/').status_code, 200)
        with patch('bara.tasks.call_llm_with_tools', return_value=({'content':'Terminé'}, 10, 10)):
            execute_task(claim_task())
        self.assertEqual(WorkFile.objects.filter(workspace=self.workspace, original_name='durable.txt').count(), 1)
        task.refresh_from_db()
        self.assertEqual(task.status, 'completed')

    def test_inflight_effect_is_never_replayed(self):
        call = {'id':'uncertain', 'function':{'name':'write_file','arguments':'{}'}}
        self.task(state={'messages':[], 'calls':[call], 'inflight':'uncertain'})
        with patch('bara.tasks.dispatch_tool') as dispatch, patch('bara.tasks.call_llm_with_tools', return_value=({'content':'Vérifier le journal'},1,1)):
            execute_task(claim_task())
        dispatch.assert_not_called()

    def test_uncertain_effect_stays_blocked_with_a_new_tool_call_id(self):
        call = {'id':'uncertain', 'type':'function','function':{'name':'write_file','arguments':'{}'}}
        task = self.task(state={'messages':[],'calls':[call],'inflight':'uncertain'})
        repeated = {**call,'id':'new-id'}
        with patch('bara.tasks.dispatch_tool') as dispatch, patch('bara.tasks.call_llm_with_tools',side_effect=[({'tool_calls':[repeated]},1,1),({'content':'Vérifie le journal'},1,1)]):
            execute_task(claim_task(task.pk))
            execute_task(claim_task(task.pk))
        dispatch.assert_not_called()

    def test_confirmation_resumes_task_with_actual_result(self):
        action = ToolExecution.objects.create(workspace=self.workspace,tool_name='delete_file',status='pending')
        task = self.task(status='waiting',state={'messages':[{'role':'tool','tool_call_id':'action','content':'pending'}],
            'pending':[{'id':action.pk,'call_id':'action'}],'calls':[]})
        self.assertIsNone(claim_task(task.pk))
        action.status='success';action.result={'deleted':True};action.save()
        with patch('bara.tasks.call_llm_with_tools',return_value=({'content':'Suppression effectuée'},1,1)) as provider:
            execute_task(claim_task(task.pk))
        self.assertIn('deleted',provider.call_args.args[3][0]['content'])

    def test_budget_pauses_before_provider_call_and_claim_is_exclusive(self):
        self.task(credit_limit=1)
        claimed = claim_task()
        self.assertIsNone(claim_task())
        with patch('bara.tasks.estimate_cost', return_value=(50,1)), patch('bara.tasks.call_llm_with_tools') as provider:
            execute_task(claimed)
        provider.assert_not_called()
        claimed.refresh_from_db()
        self.assertEqual(claimed.status, 'paused')

    def test_cancel_and_owner_permissions(self):
        task = self.task()
        other = get_user_model().objects.create_user('other-task-user')
        self.client.force_login(other)
        self.assertEqual(self.client.get(f'/bara/{self.workspace.pk}/tasks/{task.pk}/').status_code,404)
        self.client.force_login(self.user)
        self.assertEqual(self.client.post(f'/bara/{self.workspace.pk}/tasks/{task.pk}/cancel/').status_code,200)
        self.assertIsNone(claim_task())
        self.assertContains(self.client.get(f'/bara/{self.workspace.pk}/tasks/'), 'Tâches')

    @override_settings(BARA_CODE_EXECUTION_ENABLED=True)
    def test_container_denies_network_host_secrets_and_privileges(self):
        with tempfile.TemporaryDirectory() as root, patch('bara.tools.sandbox._run_bounded',return_value={'exit_code':0}) as run, patch('bara.tools.sandbox.subprocess.run'):
            run_sandbox_command(['python', '-c', 'print(1)'], cwd=root)
            argv = run.call_args.args[0]
            for restriction in ['--network=none','--read-only','--cap-drop=ALL','--security-opt=no-new-privileges']:
                self.assertIn(restriction, argv)
            self.assertFalse(any('docker.sock' in v or 'FERNET_KEY' in v for v in argv))

    @override_settings(BARA_CODE_EXECUTION_ENABLED=False)
    def test_disabled_execution_never_calls_a_process(self):
        with patch('bara.tools.sandbox.subprocess.run') as run:
            self.assertIn('error', run_sandbox_command(['python','-c','print(1)'],cwd='/tmp'))
        run.assert_not_called()

    def test_output_limit_stops_a_process_without_filling_host_storage(self):
        result = _run_bounded([sys.executable,'-c','import sys;sys.stdout.write("x"*2000000)'],5)
        self.assertTrue(result['truncated'])
        self.assertLessEqual(len(result['stdout']),12000)
        self.assertIn('error',result)
