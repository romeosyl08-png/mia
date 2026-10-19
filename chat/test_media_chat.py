"""Chat generation routing, persistence and billing with offline providers."""
import json
from tempfile import TemporaryDirectory
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.core.files.base import ContentFile
from django.core.files.storage import FileSystemStorage
from django.test import TestCase, override_settings

from api.models import AIModel, APIProvider
from bara.models import WorkFile, Workspace
from .models import Conversation, GeneratedMedia, Message
from .services import ProviderError


@override_settings(SEARCH_API_KEY='', OPENROUTER_IMAGE_MODEL='test/image', OPENROUTER_VIDEO_MODEL='test/video',
                   MEDIA_GENERATION_MODELS={'apiframe': {'audio': 'test/audio'}})
class MediaChatTests(TestCase):
    def setUp(self):
        cache.clear()
        folder = TemporaryDirectory(prefix='2ai-chat-media-')
        self.addCleanup(folder.cleanup)
        for model, field in ((GeneratedMedia, 'image'), (WorkFile, 'file')):
            patcher = patch.object(model._meta.get_field(field), 'storage', FileSystemStorage(location=folder.name))
            patcher.start()
            self.addCleanup(patcher.stop)
        self.user = get_user_model().objects.create_user('media-chat')
        self.user.credit_account.credit(100_000)
        self.client.force_login(self.user)
        self.provider = APIProvider.objects.create(name='OpenRouter', slug='openrouter')
        self.provider.keys.create(name='fixture', key='offline-key')
        self.model = AIModel.objects.create(provider=self.provider, name='Text', model_id='text', supported_parameters=[])
        audio = APIProvider.objects.create(name='Audio', slug='apiframe')
        audio.keys.create(name='fixture', key='offline-audio')

    def send(self, content, **extra):
        return self.client.post('/chat/api/send/', json.dumps({'content': content, 'model_id': 'text', **extra}),
                                content_type='application/json', HTTP_ACCEPT='text/event-stream')

    def balance(self):
        self.user.credit_account.refresh_from_db()
        return self.user.credit_account.balance

    def test_natural_language_generates_each_kind_with_a_text_model_and_persists_results(self):
        for kind, prompt in [('image', 'Crée une image de forêt'), ('video', 'Peux-tu me créer une vidéo de forêt ?'),
                             ('audio', 'Compose une chanson sur la forêt')]:
            with self.subTest(kind=kind), patch('chat.views.generate_openrouter_media', return_value=(b'media', f'{kind}/mp4', None)) as cloud, \
                    patch('chat.views.generate_media', return_value=(b'audio', 'audio/mpeg')) as audio, \
                    patch('chat.views.call_llm_provider') as text:
                before = self.balance()
                response = self.send(prompt, persona_id=self.user.personas.first().pk, team_mode=True)
                self.assertEqual(response.status_code, 200, response.content)
                result = response.json()
                media = GeneratedMedia.objects.get(pk=result['media_id'])
                self.assertEqual(media.kind, kind)
                self.assertEqual(media.owner, self.user)
                self.assertEqual(media.conversation_id, result['conversation_id'])
                self.assertEqual(media.message_id, result['assistant_message_id'])
                self.assertEqual(media.conversation.messages.count(), 2)
                self.assertEqual(result['prompt_tokens'], 0)
                self.assertLess(self.balance(), before)
                (audio if kind == 'audio' else cloud).assert_called_once()
                text.assert_not_called()
                history = self.client.get(f'/chat/api/conversations/{media.conversation_id}/').json()
                self.assertEqual(history['messages'][-1]['media_id'], media.pk)
                self.assertEqual(history['messages'][-1]['media_kind'], kind)
                self.assertIn(media.pk, [row['id'] for row in self.client.get('/creation/api/library/').json()['items']])

    def test_explicit_modes_work_without_a_text_model(self):
        self.model.delete()
        for kind in ('image', 'video', 'audio'):
            with self.subTest(kind=kind), patch('chat.views.generate_openrouter_media', return_value=(b'media', 'image/png', None)), \
                    patch('chat.views.generate_media', return_value=(b'audio', 'audio/mpeg')):
                response = self.send('Une forêt au coucher du soleil', mode=kind)
                self.assertEqual(response.status_code, 200, response.content)
                self.assertEqual(response.json()['media_kind'], kind)

    def test_failed_or_unconfigured_generation_keeps_credits_and_history(self):
        conversation = Conversation.objects.create(user=self.user, title='Original')
        message = Message.objects.create(conversation=conversation, role='user', content='Original')
        Message.objects.create(conversation=conversation, role='assistant', content='Réponse')
        before = self.balance()
        with patch('chat.views.generate_openrouter_media', side_effect=ProviderError('Fournisseur indisponible')):
            response = self.send('Crée une image', conversation_id=conversation.pk, edit_message_id=message.pk)
        self.assertEqual(response.status_code, 502)
        self.assertEqual(self.balance(), before)
        self.assertEqual(list(conversation.messages.values_list('content', flat=True)), ['Original', 'Réponse'])
        with override_settings(OPENROUTER_VIDEO_MODEL=''), patch('chat.views.generate_openrouter_media') as provider:
            response = self.send('Crée une vidéo')
        self.assertEqual(response.status_code, 503)
        provider.assert_not_called()
        self.assertEqual(self.balance(), before)
        self.assertFalse(GeneratedMedia.objects.exists())

    def test_successful_edit_replaces_only_the_edited_suffix(self):
        conversation = Conversation.objects.create(user=self.user, title='Original', summary='Ancien résumé', summary_through_id=1)
        first = Message.objects.create(conversation=conversation, role='user', content='Bonjour')
        Message.objects.create(conversation=conversation, role='assistant', content='Salut')
        edit = Message.objects.create(conversation=conversation, role='user', content='Original')
        Message.objects.create(conversation=conversation, role='assistant', content='Ancienne réponse')
        with patch('chat.views.generate_openrouter_media', return_value=(b'png', 'image/png', None)):
            response = self.send('Crée une image de forêt', conversation_id=conversation.pk, edit_message_id=edit.pk)
        self.assertEqual(response.status_code, 200, response.content)
        conversation.refresh_from_db()
        self.assertEqual(conversation.messages.count(), 4)
        self.assertTrue(conversation.messages.filter(pk=first.pk).exists())
        self.assertFalse(conversation.messages.filter(pk=edit.pk).exists())
        self.assertEqual(conversation.summary, '')
        self.assertEqual(conversation.summary_through_id, 0)

    def test_reference_images_respect_workspace_access_and_reach_the_generator(self):
        workspace = Workspace.objects.create(user=self.user)
        file = WorkFile.objects.create(workspace=workspace, original_name='image.png', content_type='image/png', size_bytes=3)
        file.file.save('image.png', ContentFile(b'png'))
        with patch('chat.views.generate_openrouter_media', return_value=(b'png', 'image/png', None)) as provider:
            response = self.send('Anime cette forêt', mode='video', workspace_id=workspace.pk, image_file_ids=[file.pk])
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(provider.call_args.kwargs['input_references'], ['data:image/png;base64,cG5n'])
        self.assertEqual(Message.objects.get(pk=response.json()['user_message_id']).image_file_ids, [file.pk])
        before = self.balance()
        other_workspace = Workspace.objects.create(user=self.user)
        with patch('chat.views.generate_openrouter_media') as provider:
            response = self.send('Crée une image', workspace_id=other_workspace.pk, image_file_ids=[file.pk])
        self.assertEqual(response.status_code, 400)
        provider.assert_not_called()
        self.assertEqual(self.balance(), before)

    def test_foreign_conversations_and_edit_targets_are_rejected_before_generation(self):
        other = get_user_model().objects.create_user('other-media-chat')
        foreign = Conversation.objects.create(user=other)
        foreign_message = Message.objects.create(conversation=foreign, role='user', content='Privé')
        own = Conversation.objects.create(user=self.user)
        before = self.balance()
        for extra in ({'conversation_id': foreign.pk}, {'conversation_id': own.pk, 'edit_message_id': foreign_message.pk}):
            with patch('chat.views.generate_openrouter_media') as provider:
                response = self.send('Crée une image', **extra)
            self.assertEqual(response.status_code, 404)
            provider.assert_not_called()
            self.assertEqual(self.balance(), before)

    def test_temporary_images_stay_ephemeral_and_other_media_are_blocked_before_charging(self):
        with patch('chat.views.generate_openrouter_media', return_value=(b'png', 'image/png', None)):
            response = self.send('Crée une image', temporary=True)
        self.assertEqual(response.status_code, 200, response.content)
        self.assertTrue(response.json()['media_url'].startswith('data:image/png;base64,'))
        before = self.balance()
        for content in ('Crée une vidéo', 'Compose une chanson'):
            response = self.send(content, temporary=True)
            self.assertEqual(response.status_code, 400, response.content)
        self.assertEqual(self.balance(), before)
        self.assertFalse(Conversation.objects.exists())
        self.assertFalse(GeneratedMedia.objects.exists())
