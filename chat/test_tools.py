import json
from tempfile import TemporaryDirectory
from unittest.mock import MagicMock, patch

from django.contrib.auth.models import User
from django.core.cache import cache
from django.core.files.base import ContentFile
from django.test import TestCase, override_settings

from api.models import AIModel, APIProvider
from bara.agent_tools import _tools_schema
from bara.models import ToolExecution, WorkFile, Workspace, WorkspaceMCPServer
from chat.file_tools import file_tool_schemas
from chat.models import Conversation, Message, RoutingObservation
from chat.sources import SourceCollector
from chat.test_regressions import tool_stream


def tool_call(name, arguments, call_id='tool-1'):
    return {'id': call_id, 'type': 'function', 'function': {'name': name, 'arguments': json.dumps(arguments)}}


@override_settings(SEARCH_API_KEY='dummy-search-key', BARA_CODE_EXECUTION_ENABLED=False)
class ChatWorkspaceToolTests(TestCase):
    def setUp(self):
        cache.clear()
        self.user = User.objects.create_user('chat-tool-user', password='test-password')
        self.user.credit_account.credit(100_000)
        self.client.force_login(self.user)
        provider = APIProvider.objects.create(name='OpenRouter tool fixture', slug='openrouter', base_url='https://example.test/api')
        provider.keys.create(name='dummy', key='not-a-real-key')
        self.model = AIModel.objects.create(provider=provider, name='Tool model', model_id='tools', input_price_per_million=1, output_price_per_million=1)
        self.workspace = Workspace.objects.create(user=self.user, title='Tools workspace', instructions='Compare les données avant de modifier les fichiers.')

    def send(self, responses, workspace=False, streamed=False, **extra):
        payload = {'content': 'Vérifie les informations et cite les sources', 'model_id': self.model.model_id}
        if workspace:
            payload['workspace_id'] = self.workspace.pk
        payload.update(extra)
        target = 'chat.file_tools.call_llm_with_tools_stream' if streamed else 'chat.file_tools.call_llm_with_tools'
        values = [tool_stream(message) for message in responses] if streamed else [(message, 100, 100) for message in responses]
        with patch(target, side_effect=values) as provider:
            response = self.client.post('/chat/api/send/', data=json.dumps(payload), content_type='application/json', HTTP_ACCEPT='text/event-stream' if streamed else 'application/json')
            if streamed:
                events = [json.loads(line[6:]) for line in b''.join(response.streaming_content).decode().splitlines() if line.startswith('data: ')]
                result = events[-1]
            else:
                result = response.json()
                events = []
        return response, result, events, provider

    def test_chat_can_search_and_read_results_without_selecting_a_workspace(self):
        search_response = MagicMock()
        search_response.json.return_value = {'organic': [
            {'title': 'Définition', 'snippet': 'La vitesse est une distance par unité de temps.', 'link': 'https://example.test/vitesse'},
            {'title': 'Unités', 'snippet': 'Unité SI : mètre par seconde.', 'link': 'https://example.test/unites'},
        ]}
        page = MagicMock()
        page.status_code = 200
        page.headers = {'Content-Type': 'text/html'}
        page.iter_content.return_value = [b'<html><title>Definition</title><p>Distance divided by time</p></html>']
        address = [(2, 1, 6, '', ('8.8.8.8', 443))]
        with patch('bara.tools.definitions.http_requests.post', return_value=search_response) as search, patch('bara.tools.definitions.http_requests.get', return_value=page) as fetch, patch('bara.tools.definitions.socket.getaddrinfo', return_value=address):
            response, result, _, provider = self.send([
                {'tool_calls': [tool_call('web_search', {'query': 'définition vitesse'})]},
                {'tool_calls': [tool_call('fetch_url', {'url': 'https://example.test/vitesse'})]},
                {'content': 'La vitesse est une distance par unité de temps. [[sources:1,2]]'},
            ])
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(result['sources']), 2)
        self.assertEqual(search.call_args.kwargs['json']['q'], 'définition vitesse')
        fetch.assert_called_once()
        self.assertIn('"citation_id": 1', provider.call_args_list[1].args[3][-1]['content'])
        self.assertEqual(Message.objects.get(pk=result['assistant_message_id']).sources, result['sources'])
        self.assertEqual(RoutingObservation.objects.get(assistant_message_id=result['assistant_message_id']).tools_used, ['fetch_url', 'web_search'])
        self.assertIsNone(Conversation.objects.get(pk=result['conversation_id']).workspace_id)

    def test_streaming_search_emits_progress_sources_and_cited_answer(self):
        search_response = MagicMock()
        search_response.json.return_value = {'organic': [{'title': 'Actualités', 'link': 'https://example.test/news', 'snippet': 'Information vérifiée'}]}
        with patch('bara.tools.definitions.http_requests.post', return_value=search_response):
            response, result, events, _ = self.send([
                {'tool_calls': [tool_call('web_search', {'query': 'actualité'})]},
                {'content': 'Information vérifiée. [[sources:1]]'},
            ], streamed=True)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(result['type'], 'done')
        self.assertIn('tool_status', [event['type'] for event in events])
        self.assertIn('sources', [event['type'] for event in events])
        self.assertEqual(result['sources'][0]['link'], 'https://example.test/news')

    def test_selected_workspace_uses_the_same_catalog_and_instructions_as_bara(self):
        self.assertEqual(file_tool_schemas(self.workspace), _tools_schema(self.workspace)[0])
        names = {schema['function']['name'] for schema in file_tool_schemas(self.workspace)}
        self.assertTrue({'web_search', 'fetch_url', 'compare_files', 'read_file_lines', 'search_code', 'delete_file', 'visualize_chart'}.issubset(names))
        self.assertNotIn('run_command', names)
        response, result, _, provider = self.send([{'content': 'Ready'}], workspace=True)
        self.assertEqual(response.status_code, 200)
        self.assertIn(self.workspace.instructions, provider.call_args.args[4])
        self.assertEqual(Conversation.objects.get(pk=result['conversation_id']).workspace_id, self.workspace.pk)

    def test_deletion_is_pending_until_user_confirms_in_chat(self):
        with TemporaryDirectory() as root, override_settings(MEDIA_ROOT=root):
            work_file = WorkFile(workspace=self.workspace, original_name='notes.txt', extracted_text='Keep this', size_bytes=9)
            work_file.file.save('notes.txt', ContentFile(b'Keep this'), save=True)
            response, result, _, _ = self.send([
                {'tool_calls': [tool_call('delete_file', {'file_id': work_file.pk})]},
                {'content': 'La suppression attend ta confirmation.'},
            ], workspace=True)
            self.assertEqual(response.status_code, 200)
            action_event = result['tool_events'][0]
            self.assertEqual(action_event['type'], 'confirmation')
            self.assertEqual(ToolExecution.objects.get(pk=action_event['action_id']).status, 'pending')
            self.assertTrue(WorkFile.objects.filter(pk=work_file.pk).exists())
            details = self.client.get(f'/chat/api/conversations/{result["conversation_id"]}/').json()
            self.assertEqual(details['messages'][-1]['tool_events'][0]['status'], 'pending')
            confirm = self.client.post(f'/bara/{self.workspace.pk}/actions/{action_event["action_id"]}/confirm/')
            self.assertEqual(confirm.status_code, 200)
            self.assertFalse(WorkFile.objects.filter(pk=work_file.pk).exists())
            details = self.client.get(f'/chat/api/conversations/{result["conversation_id"]}/').json()
            self.assertEqual(details['messages'][-1]['tool_events'][0]['status'], 'success')

    def test_mcp_tool_is_prepared_with_confirmation_and_never_called_implicitly(self):
        server = WorkspaceMCPServer.objects.create(workspace=self.workspace, name='Remote fixture', endpoint='https://example.test/mcp', available_tools=[{'name': 'inspect', 'inputSchema': {'type': 'object'}}])
        with patch('bara.views.call_mcp_tool') as remote:
            response, result, _, _ = self.send([
                {'tool_calls': [tool_call(f'mcp_{server.pk}_0', {'query': 'inspect'})]},
                {'content': 'Confirme l’appel au service connecté.'},
            ], workspace=True)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(ToolExecution.objects.get(pk=result['tool_events'][0]['action_id']).tool_name, 'mcp_call')
        remote.assert_not_called()

    def test_chart_tool_returns_a_persisted_visualization(self):
        chart = {'title': 'Mesures', 'chart_type': 'bar', 'labels': ['A', 'B'], 'datasets': [{'label': 'Vitesse', 'values': [3, 5]}]}
        response, result, events, _ = self.send([
            {'tool_calls': [tool_call('visualize_chart', chart)]},
            {'content': 'Voici le graphique des mesures.'},
        ], workspace=True, streamed=True)
        self.assertEqual(response.status_code, 200)
        self.assertIn('visualization', [event['type'] for event in events])
        self.assertEqual(result['tool_events'][0]['chart']['title'], 'Mesures')
        self.assertEqual(Message.objects.get(pk=result['assistant_message_id']).tool_events, result['tool_events'])

    def test_server_disabled_execution_and_other_users_workspaces_stay_inaccessible(self):
        with patch('bara.agent_tools.execute_tool') as execute:
            response, _, _, _ = self.send([
                {'tool_calls': [tool_call('run_command', {'command': 'forbidden'})]}, {'content': 'Tool blocked'},
            ], workspace=True)
        self.assertEqual(response.status_code, 200)
        execute.assert_not_called()
        other = User.objects.create_user('other-tools-user')
        self.client.force_login(other)
        response = self.client.post('/chat/api/send/', data=json.dumps({'content': 'Inspect', 'model_id': self.model.model_id, 'workspace_id': self.workspace.pk}), content_type='application/json')
        self.assertEqual(response.status_code, 404)


class CitationSourceTests(TestCase):
    def test_source_numbers_are_deduplicated_and_unsafe_links_are_excluded(self):
        sources = []
        collector = SourceCollector(sources)
        result = collector.collect('web_search', {'results': [{'link': 'https://example.test/page', 'title': 'Page'}, {'link': 'javascript:alert(1)', 'title': 'Unsafe'}]})
        self.assertEqual(result['results'][0]['citation_id'], 1)
        self.assertEqual(len(result['results']), 1)
        page = collector.collect('fetch_url', {'url': 'https://example.test/page', 'title': 'Page', 'content': 'Body'})
        self.assertEqual(page['citation_id'], 1)
        self.assertEqual(len(sources), 1)

    @override_settings(SEARCH_API_KEY='dummy-search-key')
    def test_malformed_search_response_is_reported_without_inventing_results(self):
        from bara.tools.definitions import web_search
        response = MagicMock()
        response.json.return_value = {'organic': 'invalid'}
        with patch('bara.tools.definitions.http_requests.post', return_value=response):
            result = web_search(None, 'Vérification')
        self.assertIn('error', result)
        self.assertNotIn('results', result)
