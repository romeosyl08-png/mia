import json
from unittest.mock import MagicMock, patch

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TestCase, override_settings
from django.urls import reverse

from api.models import AIModel, APIProvider
from bara.models import WorkConversation, Workspace
from bara.services import call_llm_with_tools, call_llm_with_tools_stream
from chat.models import Conversation, Message, Persona


@override_settings(SEARCH_API_KEY='', BARA_CODE_EXECUTION_ENABLED=False)
class CapabilityPreflightTests(TestCase):
    def setUp(self):
        cache.clear()
        self.user = get_user_model().objects.create_user('capability-user')
        self.user.credit_account.credit(100_000)
        self.client.force_login(self.user)
        provider = APIProvider.objects.create(name='Capabilities', slug='openrouter', base_url='https://example.test/api')
        provider.keys.create(name='test', key='test-key')
        self.model = AIModel.objects.create(
            provider=provider, name='Text only', model_id='text-only', input_modalities=['text'],
            supported_parameters=['temperature'], input_price_per_million=1, output_price_per_million=1,
        )
        self.workspace = Workspace.objects.create(user=self.user, active_model=self.model)

    def send(self, content, **extra):
        payload = {'content': content, 'model_id': self.model.model_id, **extra}
        return self.client.post('/chat/api/send/', json.dumps(payload), content_type='application/json', HTTP_ACCEPT='text/event-stream')

    def assert_blocked_without_spending(self, content, **extra):
        cache.clear()
        account = self.user.credit_account
        transactions = account.transactions.count()
        balance = account.balance
        with patch('chat.views.call_llm_provider') as plain, patch('chat.views.stream_llm_provider') as stream, patch('chat.views._run_file_tools') as tools, patch('chat.views.stream_chat_tools') as stream_tools, patch('chat.views.bara_tool_definitions.web_search') as search:
            response = self.send(content, **extra)
        self.assertEqual(response.status_code, 400, response.content)
        self.assertEqual(response.json()['error']['type'], 'capability_unavailable')
        self.assertEqual(response.json()['charged_credits'], 0)
        self.assertIn('aucun jeton débité', response.json()['error']['message'])
        for call in (plain, stream, tools, stream_tools, search):
            call.assert_not_called()
        account.refresh_from_db()
        self.assertEqual(account.balance, balance)
        self.assertEqual(account.transactions.count(), transactions)
        return response

    def test_direct_media_requests_are_stopped_in_text_modes(self):
        for content in ('Génère une image de chat', 'Peux-tu me créer une vidéo ?', 'Compose une chanson'):
            for mode in ('chat', 'deep'):
                with self.subTest(content=content, mode=mode):
                    self.assert_blocked_without_spending(content, mode=mode, temporary=True)
        self.assertFalse(Conversation.objects.exists())
        self.assertFalse(Message.objects.exists())

    def test_missing_web_and_execution_capabilities_are_stopped(self):
        self.assert_blocked_without_spending('Cherche sur internet le prix actuel')
        self.assert_blocked_without_spending('Prix actuel', mode='web')
        self.assert_blocked_without_spending('Exécute ce script', workspace_id=self.workspace.pk)
        self.assert_blocked_without_spending('Lis mon fichier rapport.pdf')

    def test_tool_incompatibility_and_images_are_stopped_before_reading_attachments(self):
        self.assert_blocked_without_spending('Modifie le fichier rapport.txt', workspace_id=self.workspace.pk)
        self.assert_blocked_without_spending('Décris cette image', image_file_ids=[42])


    def test_disabled_file_tool_is_reported(self):
        self.model.supported_parameters = ['tools']
        self.model.save(update_fields=['supported_parameters'])
        self.workspace.disabled_tools = ['rename_file']
        self.workspace.save(update_fields=['disabled_tools'])
        response = self.assert_blocked_without_spending('Renomme le fichier rapport.txt', workspace_id=self.workspace.pk)
        self.assertEqual(response.json()['required_capability'], 'file_tool')

    def test_blocked_edit_preserves_conversation_messages_and_settings(self):
        persona = Persona.objects.create(user=self.user, name='Original')
        other = Persona.objects.create(user=self.user, name='Other')
        conversation = Conversation.objects.create(user=self.user, persona=persona)
        message = Message.objects.create(conversation=conversation, role='user', content='Original request')
        Message.objects.create(conversation=conversation, role='assistant', content='Original reply')
        self.assert_blocked_without_spending('Crée une image', conversation_id=conversation.pk, edit_message_id=message.pk, persona_id=other.pk)
        conversation.refresh_from_db()
        self.assertEqual(conversation.persona_id, persona.pk)
        self.assertEqual(list(conversation.messages.values_list('content', flat=True)), ['Original request', 'Original reply'])

    def test_text_tasks_still_work_when_optional_web_tools_are_not_supported(self):
        for content in ('Explique comment générer une image', 'Écris un prompt pour une vidéo', 'Modifie le fichier suivant :\nprint("hello")', 'Bonjour'):
            with self.subTest(content=content), override_settings(SEARCH_API_KEY='dummy'):
                cache.clear()
                with patch('chat.views.call_llm_provider', return_value=('Réponse texte', 10, 10)) as provider, patch('chat.views._run_file_tools') as tools:
                    response = self.client.post('/chat/api/send/', json.dumps({'content': content, 'model_id': self.model.model_id}), content_type='application/json')
                self.assertEqual(response.status_code, 200, response.content)
                provider.assert_called_once()
                tools.assert_not_called()

    def test_auto_routes_to_a_model_with_tools_and_never_uses_an_incompatible_fallback(self):
        self.assert_blocked_without_spending('Crée un fichier bilan.txt', model_id='auto', workspace_id=self.workspace.pk)
        capable = AIModel.objects.create(provider=self.model.provider, name='Tools', model_id='tools', supported_parameters=['tools'])
        with patch('chat.views._run_file_tools', return_value=('Réponse', 10, 10)) as tools:
            response = self.client.post('/chat/api/send/', json.dumps({'content': 'Crée un fichier bilan.txt', 'model_id': 'auto', 'workspace_id': self.workspace.pk}), content_type='application/json')
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(tools.call_args.args[3], capable.model_id)

    def test_bara_blocks_a_manual_model_without_tools_before_any_charge(self):
        conversation = WorkConversation.objects.create(workspace=self.workspace)
        before = self.user.credit_account.transactions.count()
        with patch('bara.views.call_llm_with_tools_stream') as provider:
            response = self.client.post(reverse('bara:message', args=[self.workspace.pk, conversation.pk]), {'content': 'Modifie le fichier rapport.txt'})
        self.assertEqual(response.status_code, 400)
        provider.assert_not_called()
        self.assertEqual(self.user.credit_account.transactions.count(), before)
        self.assertFalse(conversation.messages.exists())

    def test_chat_exposes_capabilities_without_provider_credentials(self):
        response = self.client.get('/')
        config = response.context['chat_config']
        self.assertEqual(config['modelCapabilities']['text-only'], {'name': 'Text only', 'tools': False, 'images': False})
        self.assertNotIn('test-key', response.content.decode())

    def test_auto_incompatible_images_are_stopped_before_web_search(self):
        with override_settings(SEARCH_API_KEY='dummy'):
            self.assert_blocked_without_spending('Bonjour', model_id='auto', mode='web', image_file_ids=[42])

    @override_settings(SEARCH_API_KEY='dummy')
    def test_web_mode_can_supply_sources_to_a_text_model_without_tools(self):
        sources = {'results': [{'title': 'Source', 'link': 'https://example.test/page', 'snippet': 'Résultat récent'}]}
        with patch('chat.views.bara_tool_definitions.web_search', return_value=sources), patch('chat.views.call_llm_provider', return_value=('Réponse [1]', 10, 10)) as provider, patch('chat.views._run_file_tools') as tools:
            response = self.client.post('/chat/api/send/', json.dumps({'content': 'Question actuelle', 'mode': 'web', 'model_id': self.model.model_id}), content_type='application/json')
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(len(response.json()['sources']), 1)
        self.assertIn('La recherche Web a déjà été effectuée', provider.call_args.args[4])
        tools.assert_not_called()

    def test_openrouter_tools_cannot_be_silently_ignored_by_the_upstream(self):
        tool = {'type': 'function', 'function': {'name': 'read_file', 'parameters': {'type': 'object'}}}
        for streamed in (False, True):
            with self.subTest(streamed=streamed):
                response = MagicMock()
                response.read.return_value = json.dumps({'choices': [{'message': {'content': 'Réponse'}}]}).encode()
                response.__iter__.return_value = iter([
                    b'data: {"choices":[{"delta":{"content":"Reponse"},"finish_reason":"stop"}]}\n',
                    b'data: [DONE]\n',
                ])
                context = MagicMock()
                context.__enter__.return_value = response
                with patch('bara.services.urlopen', return_value=response if streamed else context) as upstream:
                    call = call_llm_with_tools_stream if streamed else call_llm_with_tools
                    result = call(self.model.provider, 'fixture', self.model.model_id, [{'role': 'user', 'content': 'Bonjour'}], tools=[tool])
                    if streamed:
                        list(result)
                payload = json.loads(upstream.call_args.args[0].data)
                self.assertEqual(payload['provider'], {'require_parameters': True})
                self.assertEqual(payload['tools'], [tool])
