import json
from datetime import timedelta
from tempfile import TemporaryDirectory
from unittest.mock import patch

from django.contrib.auth.models import User
from django.core.cache import cache
from django.test import TestCase, override_settings
from django.utils import timezone

from api.models import AIModel, APIProvider
from bara.models import WorkFile, Workspace
from chat.models import Conversation, Message, RoutingObservation


def tool_stream(message, prompt_tokens=100, completion_tokens=100):
    if message.get('content'):
        yield {'type': 'delta', 'content': message['content']}
    for index, call in enumerate(message.get('tool_calls') or []):
        yield {'type': 'tool_call_delta', 'index': index, 'id': call['id'], 'name': call['function']['name'], 'arguments_delta': call['function']['arguments']}
    yield {'type': 'done', 'usage': {'prompt_tokens': prompt_tokens, 'completion_tokens': completion_tokens}}


@override_settings(SEARCH_API_KEY='')
class ChatRegressionTests(TestCase):
    def setUp(self):
        cache.clear()
        self.user = User.objects.create_user('regression-user', password='test-password')
        self.user.credit_account.credit(100_000)
        self.client.force_login(self.user)
        self.provider = APIProvider.objects.create(name='Regression provider', slug='openrouter', base_url='https://example.test/api')
        self.provider.keys.create(name='dummy', key='not-a-real-key')
        self.cheap = AIModel.objects.create(provider=self.provider, name='A cheap', model_id='cheap', input_price_per_million=1, output_price_per_million=1)
        self.costly = AIModel.objects.create(provider=self.provider, name='Z costly', model_id='costly', input_price_per_million=100, output_price_per_million=100)

    def send(self, payload=None, streamed=False):
        data = {'content': 'Regression request', 'model_id': self.cheap.model_id}
        data.update(payload or {})
        response = self.client.post('/chat/api/send/', data=json.dumps(data), content_type='application/json', HTTP_ACCEPT='text/event-stream' if streamed else 'application/json')
        if not streamed or response.status_code != 200:
            return response, response.json()
        events = [json.loads(line[6:]) for line in b''.join(response.streaming_content).decode().splitlines() if line.startswith('data: ')]
        return response, events[-1]

    def balance(self):
        self.user.credit_account.refresh_from_db()
        return self.user.credit_account.balance

    def test_each_team_call_uses_its_own_tariff_for_both_strategies_and_streaming(self):
        for strategy in ('consensus', 'plan_execute_verify'):
            for streamed in (False, True):
                with self.subTest(strategy=strategy, streamed=streamed):
                    cache.clear()
                    before = self.balance()
                    with patch('chat.services.call_llm_provider', return_value=('Team reply', 100, 100)), patch('chat.services.stream_llm_provider', return_value=iter([
                        {'type': 'delta', 'content': 'Verified reply'},
                        {'type': 'usage', 'input_tokens': 100, 'output_tokens': 100},
                    ])):
                        response, result = self.send({'team_mode': True, 'team_model_ids': ['cheap', 'costly'], 'team_strategy': strategy}, streamed=streamed)
                    self.assertEqual(response.status_code, 200)
                    expected_cost = 606 if strategy == 'plan_execute_verify' else 306
                    expected_tokens = 400 if strategy == 'plan_execute_verify' else 300
                    self.assertEqual(before - self.balance(), expected_cost)
                    self.assertEqual(result['charged_credits'], expected_cost)
                    self.assertEqual(result['prompt_tokens'], expected_tokens)
                    self.assertEqual(result['completion_tokens'], expected_tokens)

    def test_team_keeps_selected_model_order(self):
        with patch('chat.services.call_llm_provider', return_value=('Team reply', 100, 100)) as call:
            response, result = self.send({'team_mode': True, 'team_model_ids': ['costly', 'cheap']})
        self.assertEqual(response.status_code, 200)
        self.assertEqual([invocation.args[2] for invocation in call.call_args_list], ['costly', 'cheap', 'costly'])
        self.assertEqual(result['charged_credits'], 603)

    def test_streamed_file_tools_write_file_and_keep_workspace(self):
        workspace = Workspace.objects.create(user=self.user, title='Files')
        tool_call = {'id': 'write-1', 'type': 'function', 'function': {'name': 'write_file', 'arguments': json.dumps({'filename': 'audit.txt', 'content': 'Created by tool'})}}
        with TemporaryDirectory() as media_root, override_settings(MEDIA_ROOT=media_root), patch('chat.file_tools.call_llm_with_tools_stream', side_effect=[
            tool_stream({'content': None, 'tool_calls': [tool_call]}),
            tool_stream({'content': 'File created'}),
        ]) as provider_call, patch('chat.views.stream_llm_provider') as plain_stream:
            response, result = self.send({'workspace_id': workspace.pk}, streamed=True)
            self.assertEqual(response.status_code, 200)
            self.assertEqual(result['type'], 'done')
            conversation = Conversation.objects.get(pk=result['conversation_id'])
            self.assertEqual(conversation.workspace_id, workspace.pk)
            work_file = WorkFile.objects.get(workspace=workspace, original_name='audit.txt')
            self.assertEqual(work_file.extracted_text, 'Created by tool')
            self.assertEqual(provider_call.call_count, 2)
            plain_stream.assert_not_called()
            self.assertEqual(RoutingObservation.objects.get(assistant_message_id=result['assistant_message_id']).tools_used, ['write_file'])

    def test_chat_file_tools_respect_disabled_workspace_tools(self):
        workspace = Workspace.objects.create(user=self.user, title='Disabled', disabled_tools=['write_file'])
        tool_call = {'id': 'write-1', 'type': 'function', 'function': {'name': 'write_file', 'arguments': '{"filename":"denied.txt","content":"denied"}'}}
        with patch('chat.file_tools.call_llm_with_tools_stream', side_effect=[
            tool_stream({'content': None, 'tool_calls': [tool_call]}, 10, 10),
            tool_stream({'content': 'Tool denied'}, 10, 10),
        ]) as call:
            response, _ = self.send({'workspace_id': workspace.pk}, streamed=True)
        self.assertEqual(response.status_code, 200)
        self.assertNotIn('write_file', [schema['function']['name'] for schema in call.call_args_list[0].kwargs['tools']])
        self.assertIn('error', json.loads(call.call_args_list[1].args[3][-1]['content']))
        self.assertFalse(workspace.files.exists())

    def test_non_text_content_is_rejected_before_provider_call(self):
        with patch('chat.views.call_llm_provider') as call:
            for content in (123, None, {}, []):
                with self.subTest(content=content):
                    response, _ = self.send({'content': content})
                    self.assertEqual(response.status_code, 400)
            call.assert_not_called()

    def test_new_message_refreshes_conversation_recency(self):
        conversation = Conversation.objects.create(user=self.user)
        old = timezone.now() - timedelta(days=1)
        Conversation.objects.filter(pk=conversation.pk).update(updated_at=old)
        with patch('chat.views.call_llm_provider', return_value=('Reply', 100, 100)):
            response, _ = self.send({'conversation_id': conversation.pk})
        self.assertEqual(response.status_code, 200)
        conversation.refresh_from_db()
        self.assertGreater(conversation.updated_at, old)

    def test_a_reply_does_not_overwrite_a_title_changed_during_generation(self):
        conversation = Conversation.objects.create(user=self.user, title='Original')
        def provider_reply(*args, **kwargs):
            Conversation.objects.filter(pk=conversation.pk).update(title='Renamed during generation')
            return 'Reply', 100, 100
        with patch('chat.views.call_llm_provider', side_effect=provider_reply):
            response, result = self.send({'conversation_id': conversation.pk})
        self.assertEqual(response.status_code, 200)
        conversation.refresh_from_db()
        self.assertEqual(conversation.title, 'Renamed during generation')
        self.assertEqual(result['title'], conversation.title)

    @override_settings(CHAT_HISTORY_MAX_MESSAGES=4, CHAT_HISTORY_MAX_CHARS=35)
    def test_provider_history_is_bounded_and_saved_history_remains_complete(self):
        conversation = Conversation.objects.create(user=self.user)
        for index in range(70):
            Message.objects.create(conversation=conversation, role='user' if index % 2 == 0 else 'assistant', content=f'Historical message {index}')
        with patch('chat.views.call_llm_provider', return_value=('Reply', 100, 100)) as call:
            response, _ = self.send({'conversation_id': conversation.pk})
        self.assertEqual(response.status_code, 200)
        history = call.call_args.args[3][:-1]
        self.assertLessEqual(len(history), 4)
        self.assertLessEqual(sum(len(message['content']) for message in history), 35)
        self.assertEqual(conversation.messages.count(), 72)

    def test_failed_turn_storage_refunds_once_and_rolls_back_messages(self):
        for streamed in (False, True):
            with self.subTest(streamed=streamed):
                before = self.balance()
                with patch('chat.views.call_llm_provider', return_value=('Reply', 100, 100)), patch('chat.views.stream_llm_provider', return_value=iter([
                    {'type': 'delta', 'content': 'Reply'}, {'type': 'usage', 'input_tokens': 100, 'output_tokens': 100},
                ])), patch('chat.turns.Message.objects.create', side_effect=RuntimeError('synthetic storage failure')):
                    response, result = self.send(streamed=streamed)
                self.assertEqual(response.status_code, 200 if streamed else 500)
                if streamed:
                    self.assertEqual(result['type'], 'error')
                self.assertEqual(self.balance(), before)
                self.assertFalse(Conversation.objects.filter(user=self.user).exists())

    def test_html_preview_is_sandboxed_even_when_opened_directly(self):
        workspace = Workspace.objects.create(user=self.user, title='Preview')
        work_file = WorkFile.objects.create(workspace=workspace, original_name='preview.HTML', extracted_text='<script>/* sample */</script>')
        response = self.client.get(f'/bara/{workspace.pk}/files/{work_file.pk}/preview/')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response['Content-Security-Policy'], 'sandbox allow-scripts')
        self.assertEqual(response['Cache-Control'], 'private, no-store')
        self.assertIn('text/html', response['Content-Type'])
        other = User.objects.create_user('other-preview-user')
        self.client.force_login(other)
        self.assertEqual(self.client.get(f'/bara/{workspace.pk}/files/{work_file.pk}/preview/').status_code, 404)
