import base64
import json
from tempfile import TemporaryDirectory
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TestCase, override_settings
from django.urls import reverse

from api.models import AIModel, APIProvider
from bara.models import Workspace
from .models import Conversation, GeneratedMedia, Message, RoutingObservation, UserMemory


@override_settings(SEARCH_API_KEY='')
class TemporaryConversationTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = get_user_model().objects.create_user('temporary-owner')
        cls.other = get_user_model().objects.create_user('temporary-other')
        cls.active = cls.user.personas.filter(is_big_persona=False).first()
        cls.active.prompt = 'STYLE_ACTIF'
        cls.active.memory = 'MEMOIRE_ACTIVE'
        cls.active.share_with_personas = True
        cls.active.save()
        cls.source = cls.user.personas.filter(is_big_persona=False).exclude(pk=cls.active.pk).first()
        cls.source.memory = 'MEMOIRE_SOURCE'
        cls.source.share_with_personas = True
        cls.source.save()
        cls.foreign = cls.other.personas.first()
        UserMemory.objects.create(user=cls.user, content='MEMOIRE_GLOBALE', enabled=True)
        provider = APIProvider.objects.create(name='Temporary fixture', slug='openrouter', base_url='https://example.test')
        provider.keys.create(name='fixture', key='fixture-secret')
        cls.model = AIModel.objects.create(provider=provider, name='Fixture', model_id='temporary-fixture')
        cls.workspace = Workspace.objects.create(user=cls.user, title='Documents', active_model=cls.model)

    def setUp(self):
        cache.clear()
        self.user.credit_account.balance = 100_000
        self.user.credit_account.save(update_fields=['balance'])
        self.client.force_login(self.user)

    def send(self, **options):
        payload = {'content': 'Bonjour', 'model_id': self.model.model_id, 'persona_id': self.active.pk, 'temporary': True}
        payload.update(options)
        return self.client.post(reverse('chat:chat-send-message'), json.dumps(payload), content_type='application/json')

    def save(self, **options):
        payload = {'persona_id': self.source.pk, 'messages': [
            {'role': 'user', 'content': 'Une question complète', 'prompt_tokens': 12},
            {'role': 'assistant', 'content': 'Une réponse complète', 'completion_tokens': 8},
        ]}
        payload.update(options)
        return self.client.post(reverse('chat:chat-save-temporary-conversation'), json.dumps(payload), content_type='application/json')

    @patch('chat.views.call_llm_provider', return_value=('Réponse', 4, 2))
    def test_no_saved_memory_is_used_by_default_and_nothing_is_persisted(self, provider):
        response = self.send()
        self.assertEqual(response.status_code, 200)
        prompt = provider.call_args.args[4]
        self.assertIn('STYLE_ACTIF', prompt)
        for memory in ('MEMOIRE_ACTIVE', 'MEMOIRE_SOURCE', 'MEMOIRE_GLOBALE'):
            self.assertNotIn(memory, prompt)
        self.assertFalse(Conversation.objects.exists())
        self.assertFalse(Message.objects.exists())
        self.assertFalse(RoutingObservation.objects.exists())

    @patch('chat.views.call_llm_provider', return_value=('Réponse', 4, 2))
    def test_only_selected_persona_memory_is_used_and_reloaded_for_each_turn(self, provider):
        self.assertEqual(self.send(temporary_memory_persona_id=self.source.pk).status_code, 200)
        prompt = provider.call_args.args[4]
        self.assertIn('STYLE_ACTIF', prompt)
        self.assertIn('MEMOIRE_SOURCE', prompt)
        self.assertNotIn('MEMOIRE_ACTIVE', prompt)
        self.assertNotIn('MEMOIRE_GLOBALE', prompt)
        self.source.memory = 'MEMOIRE_MODIFIEE'
        self.source.save(update_fields=['memory'])
        self.assertEqual(self.send(temporary_memory_persona_id=self.source.pk).status_code, 200)
        self.assertIn('MEMOIRE_MODIFIEE', provider.call_args.args[4])
        self.assertNotIn('MEMOIRE_SOURCE', provider.call_args.args[4])
        self.assertFalse(Conversation.objects.exists())

    @patch('chat.views.call_llm_provider', return_value=('Réponse', 4, 2))
    def test_regular_conversations_keep_their_existing_memories(self, provider):
        self.assertEqual(self.send(temporary=False).status_code, 200)
        prompt = provider.call_args.args[4]
        for memory in ('MEMOIRE_ACTIVE', 'MEMOIRE_SOURCE', 'MEMOIRE_GLOBALE'):
            self.assertIn(memory, prompt)

    @patch('chat.views.call_llm_provider')
    def test_foreign_and_invalid_memory_personas_are_rejected_before_provider_call(self, provider):
        for value, status in ((self.foreign.pk, 404), (True, 400), ({'id': 1}, 400), (-1, 400)):
            with self.subTest(value=value):
                self.assertEqual(self.send(temporary_memory_persona_id=value).status_code, status)
        provider.assert_not_called()

    @patch('chat.views.stream_llm_provider', return_value=iter([
        {'type': 'delta', 'content': 'Réponse'}, {'type': 'usage', 'input_tokens': 4, 'output_tokens': 2},
    ]))
    def test_streaming_uses_selected_memory_without_saving(self, provider):
        response = self.client.post(reverse('chat:chat-send-message'), json.dumps({
            'content': 'Bonjour', 'model_id': self.model.model_id, 'persona_id': self.active.pk,
            'temporary': True, 'temporary_memory_persona_id': self.source.pk,
        }), content_type='application/json', HTTP_ACCEPT='text/event-stream')
        events = [json.loads(line[6:]) for line in b''.join(response.streaming_content).decode().splitlines() if line.startswith('data: ')]
        self.assertEqual(events[-1]['type'], 'done')
        self.assertIsNone(events[-1]['conversation_id'])
        self.assertIn('MEMOIRE_SOURCE', provider.call_args.args[4])
        self.assertFalse(Message.objects.exists())

    def test_local_context_obeys_same_memory_selection_and_access_rules(self):
        url = reverse('chat:persona-context', args=[self.active.pk])
        self.assertEqual(self.client.get(url, {'temporary': '1'}).json()['system_prompt'], 'STYLE_ACTIF')
        prompt = self.client.get(url, {'temporary': '1', 'memory_persona_id': self.source.pk}).json()['system_prompt']
        self.assertIn('MEMOIRE_SOURCE', prompt)
        self.assertNotIn('MEMOIRE_ACTIVE', prompt)
        self.assertNotIn('MEMOIRE_GLOBALE', prompt)
        self.assertEqual(self.client.get(url, {'temporary': '1', 'memory_persona_id': self.foreign.pk}).status_code, 404)
        self.assertEqual(self.client.get(url, {'temporary': '1', 'memory_persona_id': 'invalid'}).status_code, 400)
        self.assertIn('MEMOIRE_GLOBALE', self.client.get(url).json()['system_prompt'])

    def test_explicit_save_preserves_all_text_sources_tools_and_tokens_in_chosen_persona(self):
        source = {'link': 'https://example.test/article', 'title': 'Article', 'snippet': 'Extrait'}
        chart = {'type': 'visualization', 'chart': {'type': 'bar', 'title': 'Résultats', 'data': [1, 2]}}
        messages = [
            {'role': 'user', 'content': 'Question longue ' * 500, 'prompt_tokens': 120},
            {'role': 'assistant', 'content': 'Réponse [[sources:1]]', 'completion_tokens': 20,
             'sources': [source], 'tool_events': [chart], 'interrupted': True},
        ]
        balance = self.user.credit_account.balance
        response = self.save(title='Titre choisi', workspace_id=self.workspace.pk, messages=messages)
        self.assertEqual(response.status_code, 201)
        conversation = Conversation.objects.get(pk=response.json()['conversation_id'])
        self.assertEqual(conversation.persona_id, self.source.pk)
        self.assertEqual(conversation.workspace_id, self.workspace.pk)
        self.assertEqual(conversation.title, 'Titre choisi')
        rows = list(conversation.messages.all())
        self.assertEqual(rows[0].content, messages[0]['content'])
        self.assertEqual(rows[0].prompt_tokens, 120)
        self.assertEqual(rows[1].completion_tokens, 20)
        self.assertEqual(rows[1].sources, [source])
        self.assertEqual(rows[1].tool_events, [chart])
        self.assertTrue(rows[1].interrupted)
        self.assertEqual([item['id'] for item in response.json()['messages']], [row.pk for row in rows])
        self.source.refresh_from_db()
        self.assertEqual(self.source.memory, 'MEMOIRE_SOURCE')
        self.user.credit_account.refresh_from_db()
        self.assertEqual(self.user.credit_account.balance, balance)
        history = self.client.get(reverse('chat:chat-conversation-list'), {'persona_id': self.source.pk}).json()
        self.assertEqual(history[0]['id'], conversation.pk)

    def test_save_rejects_foreign_persona_and_workspace_without_creating_records(self):
        self.assertEqual(self.save(persona_id=self.foreign.pk).status_code, 404)
        foreign_workspace = Workspace.objects.create(user=self.other, title='Privé', active_model=self.model)
        self.assertEqual(self.save(workspace_id=foreign_workspace.pk).status_code, 404)
        self.assertFalse(Conversation.objects.exists())

    def test_save_requires_login_persona_and_valid_complete_messages(self):
        for options in ({'persona_id': None}, {'persona_id': True}, {'messages': []},
                        {'messages': [{'role': 'system', 'content': 'Texte'}]},
                        {'messages': [{'role': 'user', 'content': ''}]},
                        {'messages': [{'role': 'user', 'content': 'Question', 'image_file_ids': [1]}]},
                        {'messages': [{'role': 'user', 'content': 'Question'}, {'role': 'assistant', 'content': 'Réponse',
                                      'tool_events': [{'type': 'confirmation', 'action_id': 1}]}]}):
            with self.subTest(options=options):
                self.assertEqual(self.save(**options).status_code, 400)
        self.assertFalse(Conversation.objects.exists())
        self.client.logout()
        self.assertEqual(self.save().status_code, 302)

    def test_save_cannot_append_to_an_existing_conversation(self):
        existing = Conversation.objects.create(user=self.user, persona=self.active, title='Original')
        self.assertEqual(self.save(conversation_id=existing.pk).status_code, 400)
        self.assertEqual(Conversation.objects.count(), 1)
        self.assertFalse(Message.objects.exists())

    def test_save_generated_image_creates_private_media_and_reopens_it(self):
        image = base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jv6kAAAAASUVORK5CYII=')
        with TemporaryDirectory() as directory, override_settings(PRIVATE_MEDIA_ROOT=directory):
            response = self.save(messages=[
                {'role': 'user', 'content': 'Une image'},
                {'role': 'assistant', 'content': 'Image générée.', 'media_kind': 'image',
                 'media_url': 'data:image/png;base64,' + base64.b64encode(image).decode()},
            ])
            self.assertEqual(response.status_code, 201)
            media = GeneratedMedia.objects.get()
            self.assertEqual(media.owner_id, self.user.pk)
            with media.image.open('rb') as file:
                self.assertEqual(file.read(), image)
            url = response.json()['messages'][1]['media_url']
            self.assertEqual(url, reverse('chat:generated-media', args=[media.pk]))
            detail = self.client.get(reverse('chat:chat-conversation-detail', args=[media.conversation_id])).json()
            self.assertEqual(detail['messages'][1]['media_url'], url)

    def test_failed_save_rolls_back_all_messages(self):
        with patch('chat.temporary_views.Message.objects.create', side_effect=RuntimeError('Storage failure')):
            self.assertEqual(self.save().status_code, 500)
        self.assertFalse(Conversation.objects.exists())
        self.assertFalse(Message.objects.exists())
