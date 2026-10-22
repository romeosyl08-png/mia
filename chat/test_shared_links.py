import json
from unittest.mock import MagicMock, patch

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import Client, SimpleTestCase, TestCase, override_settings
from django.urls import reverse

from api.models import AIModel, APIProvider
from bara.tools.definitions import fetch_url
from chat.idea_previews import PREVIEW_INSTRUCTIONS, wants_idea_preview
from chat.shared_links import SharedLinkError, canonical_share_url, parse_shared_conversation, shared_urls

URL = 'https://chatgpt.com/share/11111111-2222-4333-8444-555555555555'


def message(role, content, **extra):
    return {'author': {'role': role}, 'content': {'content_type': 'text', 'parts': [content]}, **extra}


def exported_page(messages, title='Conversation de test'):
    data = {'props': {'pageProps': {'serverResponse': {'data': {
        'title': title, 'linear_conversation': [{'message': m} for m in messages],
    }}}}}
    return '<html><title>ChatGPT</title><script id="__NEXT_DATA__" type="application/json">' + json.dumps(data) + '</script></html>'


def router_page(payload):
    table = []
    def add(value):
        index = len(table); table.append(None)
        if isinstance(value, dict):
            table[index] = {'_' + str(add(k)): add(v) for k, v in value.items()}
        elif isinstance(value, list):
            table[index] = [add(v) for v in value]
        else:
            table[index] = value
        return index
    add(payload)
    return '<script>window.__reactRouterContext.streamController.enqueue(' + json.dumps(json.dumps(table)) + ');</script>'


class SharedConversationParsingTests(SimpleTestCase):
    def test_known_domains_and_query_are_normalized(self):
        self.assertEqual(canonical_share_url(URL + '?ogimg=plain#ignored'), URL)
        self.assertEqual(canonical_share_url(URL.replace('chatgpt.com', 'chat.openai.com')), URL)

    def test_credentials_private_urls_ports_and_lookalike_hosts_are_refused(self):
        for url in (None, 'http://chatgpt.com/share/x', URL.replace('chatgpt.com', 'chatgpt.com.attacker.test'),
                    URL.replace('chatgpt.com', 'user:secret@chatgpt.com'), URL.replace('chatgpt.com', 'chatgpt.com:8443'),
                    'https://chatgpt.com/c/private', 'https://127.0.0.1/share/not-public'):
            with self.subTest(url=url), self.assertRaises(SharedLinkError): canonical_share_url(url)

    def test_reference_extraction_deduplicates_and_skips_quotes_and_code(self):
        self.assertEqual(shared_urls(URL + '?ogimg=plain ' + URL), [URL])
        self.assertEqual(shared_urls('Texte collé :\n> ' + URL), [])
        self.assertEqual(shared_urls('```text\n' + URL + '\n```'), [])
        with self.assertRaises(SharedLinkError):
            shared_urls('\n'.join(URL[:-1] + str(i) for i in range(3)))

    def test_next_data_reads_visible_messages_and_excludes_system_tools_and_reasoning(self):
        page = exported_page([message('system', 'secret system'), message('user', 'Une idée de comparateur'),
            message('assistant', 'private reasoning', channel='analysis'), message('tool', 'secret tool'),
            message('assistant', 'hidden', metadata={'is_visually_hidden_from_conversation': True}),
            message('assistant', 'Voici les deux options.')])
        result = parse_shared_conversation(page)
        self.assertEqual(result['message_count'], 2)
        self.assertIn('Voici les deux options.', result['content'])
        for hidden in ('secret system', 'private reasoning', 'secret tool', 'hidden'):
            self.assertNotIn(hidden, result['content'])

    def test_current_mapping_branch_excludes_unselected_answers(self):
        payload = {'mapping': {
            'a': {'parent': None, 'message': message('user', 'Question')},
            'b': {'parent': 'a', 'message': message('assistant', 'Réponse sélectionnée')},
            'c': {'parent': 'a', 'message': message('assistant', 'Branche abandonnée')},
        }, 'current_node': 'b'}
        page = '<script id="__NEXT_DATA__">' + json.dumps(payload) + '</script>'
        result = parse_shared_conversation(page)
        self.assertIn('Réponse sélectionnée', result['content']); self.assertNotIn('Branche abandonnée', result['content'])

    def test_current_react_router_reference_table_is_decoded_without_running_js(self):
        page = router_page({'loaderData': {'share': {'serverResponse': {'data': {'title': 'Idée interactive',
            'linear_conversation': [{'message': message('user', 'Ma question')}, {'message': message('assistant', 'Un prototype utile')}]
        }}}}})
        result = parse_shared_conversation(page)
        self.assertEqual(result['title'], 'Idée interactive')
        self.assertEqual(result['message_count'], 2)
        self.assertIn('Un prototype utile', result['content'])

    def test_visible_html_fallback(self):
        result = parse_shared_conversation('<div data-message-author-role="user">Bonjour</div><div data-message-author-role="assistant">Bonsoir</div>')
        self.assertEqual(result['message_count'], 2)

    def test_unavailable_shell_and_malformed_payload_fail_explicitly(self):
        for body in ('<html>Log in</html>', '<script>window.__reactRouterContext.streamController.enqueue("invalid");</script>',
                     '<script id="__NEXT_DATA__">not json</script>'):
            with self.assertRaises(SharedLinkError): parse_shared_conversation(body)

    def test_long_conversations_are_bounded_and_keep_recent_context(self):
        page = exported_page([message('user', 'ancien ' * 1000), message('assistant', 'contexte récent')])
        for limit in (40, 500):
            result = parse_shared_conversation(page, limit)
            self.assertTrue(result['truncated']); self.assertLessEqual(len(result['content']), limit)
        self.assertIn('contexte récent', parse_shared_conversation(page, 500)['content'])

    def test_generic_reader_uses_specialized_chatgpt_extraction_and_dns_guard(self):
        page = MagicMock(status_code=200, headers={'Content-Type': 'text/html'})
        page.iter_content.return_value = [exported_page([message('user', 'Idée'), message('assistant', 'Réponse lisible')]).encode()]
        with patch('bara.tools.definitions.socket.getaddrinfo', return_value=[(2, 1, 6, '', ('8.8.8.8', 443))]), \
                patch('bara.tools.definitions._public_get', return_value=page) as network:
            result = fetch_url(None, URL + '?ogimg=plain')
        self.assertEqual(result['source_type'], 'chatgpt_shared_conversation')
        self.assertIn('Réponse lisible', result['content'])
        self.assertFalse(network.call_args.kwargs['allow_redirects'])
        self.assertNotIn('cookies', network.call_args.kwargs)
        page.close.assert_called_once()

    def test_preview_intent_is_opt_in_and_no_executable_code_is_requested(self):
        self.assertTrue(wants_idea_preview('Crée un aperçu interactif pour mon idée'))
        self.assertFalse(wants_idea_preview('Réponds sans aperçu ni maquette'))
        self.assertFalse(wants_idea_preview('Bonjour'))
        self.assertIn('Aucun HTML, JavaScript', PREVIEW_INSTRUCTIONS)


@override_settings(SEARCH_API_KEY='')
class SharedConversationIntegrationTests(TestCase):
    def setUp(self):
        cache.clear()
        self.user = get_user_model().objects.create_user('shared-reader')
        self.client.force_login(self.user)
        self.endpoint = reverse('chat:shared-link-preview')
        provider = APIProvider.objects.create(name='Share test', slug='openrouter', base_url='https://fixture.example.test')
        provider.keys.create(name='fixture', key='not-real')
        self.model = AIModel.objects.create(provider=provider, name='Test', model_id='share-test', supported_parameters=[],
                                           input_price_per_million=1, output_price_per_million=1)
        self.result = {'url': URL, 'title': 'Idée partagée', 'content': 'Utilisateur :\nUne idée\nAssistant :\nDeux options.',
                       'message_count': 2, 'truncated': False, 'source_type': 'chatgpt_shared_conversation'}

    def post(self, payload, client=None):
        return (client or self.client).post(self.endpoint, json.dumps(payload), content_type='application/json')

    def test_preview_requires_login_and_csrf(self):
        self.assertEqual(self.post({'url': URL}, Client()).status_code, 401)
        strict = Client(enforce_csrf_checks=True); strict.force_login(self.user)
        self.assertEqual(self.post({'url': URL}, strict).status_code, 403)

    def test_reading_is_private_does_not_save_conversation_or_debit_wallet(self):
        before = self.user.credit_account.balance
        with patch('chat.shared_link_views.fetch_url', return_value=self.result):
            response = self.post({'url': URL + '?ogimg=plain'})
        self.assertEqual(response.status_code, 200)
        self.assertIn('no-store', response['Cache-Control'])
        self.user.credit_account.refresh_from_db()
        self.assertEqual(self.user.credit_account.balance, before)
        self.assertEqual(self.user.conversations.count(), 0)

    def test_endpoint_rejects_non_public_urls_before_network_access(self):
        with patch('chat.shared_link_views.fetch_url') as network:
            self.assertEqual(self.post({'url': 'https://127.0.0.1/secret'}).status_code, 400)
            self.assertEqual(self.post([]).status_code, 400)
        network.assert_not_called()

    def test_blocked_share_offers_paste_fallback_and_rate_limits(self):
        with patch('chat.shared_link_views.fetch_url', return_value={'error': '403'}) as network:
            for _ in range(5):
                response = self.post({'url': URL}); self.assertEqual(response.status_code, 422)
                self.assertTrue(response.json()['paste_available'])
            self.assertEqual(self.post({'url': URL}).status_code, 429)
        self.assertEqual(network.call_count, 5)

    def test_pasted_share_is_read_before_paid_call_even_without_search_or_tools(self):
        with patch('chat.views.bara_tool_definitions.fetch_url', return_value=self.result) as read, \
                patch('chat.views.call_llm_provider', return_value=('Les deux options sont utiles. [[sources:1]]', 100, 20)) as ai:
            response = self.client.post('/chat/api/send/', json.dumps({'content': 'Analyse cette idée : ' + URL + '?ogimg=plain',
                'model_id': self.model.model_id}), content_type='application/json')
        self.assertEqual(response.status_code, 200)
        read.assert_called_once()
        self.assertIn('Deux options.', ai.call_args.args[4])
        self.assertIn('jamais des instructions', ai.call_args.args[4])
        self.assertEqual(response.json()['sources'][0]['link'], URL)

    def test_unreadable_share_never_debits_or_calls_ai(self):
        before = self.user.credit_account.balance
        with patch('chat.views.bara_tool_definitions.fetch_url', return_value={'error': 'blocked'}), patch('chat.views.call_llm_provider') as ai:
            response = self.client.post('/chat/api/send/', json.dumps({'content': 'Analyse ' + URL, 'model_id': self.model.model_id}), content_type='application/json')
        self.assertEqual(response.status_code, 422); ai.assert_not_called()
        self.user.credit_account.refresh_from_db(); self.assertEqual(self.user.credit_account.balance, before)

    def test_manual_paste_context_is_not_fetched_again(self):
        with patch('chat.views.bara_tool_definitions.fetch_url') as network, patch('chat.views.call_llm_provider', return_value=('Réponse', 10, 10)):
            response = self.client.post('/chat/api/send/', json.dumps({'content': 'Analyse cet extrait :\n> ' + URL + '\n> Le contenu collé', 'model_id': self.model.model_id}), content_type='application/json')
        self.assertEqual(response.status_code, 200); network.assert_not_called()

    def test_preview_guidance_preserves_explicit_output_limit_and_normal_chat(self):
        for prompt, limit, expected in [('Crée un aperçu interactif pour cette idée', 800, 800), ('Bonjour', None, 500),
                                        ('Crée un prototype de mon idée', None, 1600)]:
            with patch('chat.views.call_llm_provider', return_value=('Réponse', 10, 10)) as ai:
                data = {'content': prompt, 'model_id': self.model.model_id}
                if limit is not None: data['max_tokens'] = limit
                response = self.client.post('/chat/api/send/', json.dumps(data), content_type='application/json')
            self.assertEqual(response.status_code, 200)
            self.assertEqual(ai.call_args.args[6], expected)
            self.assertEqual('```2ai-preview' in ai.call_args.args[4], wants_idea_preview(prompt))
