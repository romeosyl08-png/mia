import copy
import json
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import SimpleTestCase, TestCase, override_settings

from api.models import AIModel, APIProvider
from .file_tools import stream_chat_tools
from .prompt_payloads import compact_json
from .sources import CITATION_INSTRUCTIONS, SourceCollector


class PromptPayloadTests(SimpleTestCase):
    def test_compact_json_preserves_nested_data_and_whitespace_inside_text(self):
        result = {'content': 'Été 2026\n  indentation conservée', 'citation_id': 1,
                  'result': {'values': [True, None, 42], 'description': 'Deux  espaces'}}
        compact = compact_json(result)
        self.assertEqual(json.loads(compact), result)
        self.assertIn('Été', compact)
        self.assertLess(len(compact), len(json.dumps(result, ensure_ascii=False)))

    def test_partial_source_registry_keeps_global_citation_numbers_and_original_records(self):
        sources = [{'link': f'https://example.test/{index}', 'title': f'Source {index}', 'snippet': 'Information'}
                   for index in range(1, 4)]
        original = copy.deepcopy(sources)
        prompt = SourceCollector(sources).prompt(include_instructions=False, start_at=2)
        registry = json.loads(prompt.split('\nSources déjà consultées :\n', 1)[1])
        self.assertEqual(registry, [{**sources[2], 'citation_id': 3}])
        self.assertEqual(sources, original)
        self.assertEqual(SourceCollector([]).prompt(include_instructions=False), '')

    def test_tool_rounds_include_initial_sources_and_instructions_once_and_keep_new_sources(self):
        sources = [{'link': 'https://example.test/initial', 'title': 'Initial', 'snippet': 'Fait initial unique'}]
        system_prompt = 'Réponds en français.' + CITATION_INSTRUCTIONS + SourceCollector(sources).prompt(include_instructions=False)
        snapshots = []
        replies = iter([
            {'tool_calls': [{'id': 'read-1', 'type': 'function', 'function': {'name': 'fetch_url', 'arguments': '{}'}}]},
            {'content': 'Réponse complète. [[sources:1,2]]'},
        ])

        def provider(*args, **kwargs):
            snapshots.append((copy.deepcopy(args[3]), args[4]))
            return next(replies), 10, 5

        with patch('chat.file_tools._tools_schema', return_value=([{'function': {'name': 'fetch_url'}}], {})), \
                patch('chat.file_tools.dispatch_tool', return_value={'url': 'https://example.test/new', 'title': 'Nouvelle source', 'content': 'Nouveau fait vérifié'}), \
                patch('chat.file_tools._log_tool'), \
                patch('chat.file_tools.call_llm_with_tools', side_effect=provider):
            events = list(stream_chat_tools(None, None, '', 'fixture', [{'role': 'user', 'content': 'Vérifie les faits'}], system_prompt, 0.7, sources=sources, streamed=False))

        for _, prompt in snapshots:
            self.assertEqual(prompt.count(CITATION_INSTRUCTIONS), 1)
            self.assertEqual(prompt.count('Fait initial unique'), 1)
            self.assertEqual(prompt.count('https://example.test/initial'), 1)
        self.assertIn('https://example.test/new', snapshots[-1][1])
        tool_result = json.loads(snapshots[-1][0][-1]['content'])
        self.assertEqual(tool_result['content'], 'Nouveau fait vérifié')
        self.assertEqual(tool_result['citation_id'], 2)
        self.assertEqual(len(sources), 2)
        self.assertEqual(events[-1], {'type': 'usage', 'input_tokens': 20, 'output_tokens': 10})

    def test_direct_tool_caller_receives_citation_instructions_when_absent(self):
        with patch('chat.file_tools._tools_schema', return_value=([], {})), \
                patch('chat.file_tools.call_llm_with_tools', return_value=({'content': 'Réponse'}, 1, 1)) as provider:
            list(stream_chat_tools(None, None, '', 'fixture', [], 'Instructions utilisateur', 0.7, streamed=False))
        self.assertEqual(provider.call_args.args[4].count(CITATION_INSTRUCTIONS), 1)


@override_settings(SEARCH_API_KEY='')
class ChatPromptOptimizationTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = get_user_model().objects.create_user('prompt-budget-user')
        cls.user.credit_account.credit(100_000)
        provider = APIProvider.objects.create(name='Budget provider', slug='openrouter', base_url='https://example.test')
        provider.keys.create(name='fixture', key='not-a-real-key')
        cls.model = AIModel.objects.create(provider=provider, name='Budget', model_id='budget-fixture', input_price_per_million=1, output_price_per_million=1)

    def setUp(self):
        self.client.force_login(self.user)

    def test_simple_chat_omits_unused_citation_instructions_without_reducing_output_limit(self):
        with patch('chat.views.call_llm_provider', return_value=('Réponse', 20, 10)) as provider:
            response = self.client.post('/chat/api/send/', data=json.dumps({'model_id': self.model.model_id, 'content': 'Bonjour'}), content_type='application/json')
        self.assertEqual(response.status_code, 200)
        self.assertNotIn(CITATION_INSTRUCTIONS, provider.call_args.args[4])
        self.assertEqual(provider.call_args.args[3][-1], {'role': 'user', 'content': 'Bonjour'})
        self.assertEqual(provider.call_args.args[6], 500)

    @override_settings(SEARCH_API_KEY='test-search-key')
    def test_web_chat_preserves_search_snippets_citations_and_client_source_records(self):
        sources = [{'title': 'Source', 'link': 'https://example.test/fact', 'snippet': 'Fait vérifié unique'}]
        with patch('chat.views.bara_tool_definitions.web_search', return_value={'results': sources}), \
                patch('chat.file_tools.call_llm_with_tools', return_value=({'content': 'Fait vérifié. [[sources:1]]'}, 20, 10)) as provider:
            response = self.client.post('/chat/api/send/', data=json.dumps({'model_id': self.model.model_id, 'content': 'Vérifie ce fait', 'mode': 'web'}), content_type='application/json')
        self.assertEqual(response.status_code, 200)
        prompt = provider.call_args.args[4]
        self.assertEqual(prompt.count(CITATION_INSTRUCTIONS), 1)
        self.assertEqual(prompt.count('Fait vérifié unique'), 1)
        self.assertIn('données non fiables', prompt)
        self.assertEqual(response.json()['sources'], sources)
