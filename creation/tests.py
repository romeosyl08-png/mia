import json
import tempfile
from decimal import Decimal
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

from bs4 import BeautifulSoup
from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.core.files.base import ContentFile
from django.core.files.storage import FileSystemStorage
from django.test import TestCase, override_settings
from PIL import Image

from api.models import APIProvider
from bara.models import WorkFile, Workspace
from chat.models import Conversation, GeneratedMedia, Message
from chat.services import ProviderError
from studio import media
from studio.models import Asset, Project
from studio.schema import empty_scene, new_version


@override_settings(SEARCH_API_KEY='', MEDIA_GENERATION_MODELS={'apiframe': {'audio': 'music-model'}})
class CreationTests(TestCase):
    def setUp(self):
        cache.clear()
        folder = tempfile.TemporaryDirectory(prefix='2ai-creation-tests-')
        self.addCleanup(folder.cleanup)
        storage = FileSystemStorage(location=folder.name)
        for model, field in [(GeneratedMedia, 'image'), (Asset, 'file'), (WorkFile, 'file')]:
            patcher = patch.object(model._meta.get_field(field), 'storage', storage)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.user = get_user_model().objects.create_user('creator')
        self.other = get_user_model().objects.create_user('other-creator')
        self.provider = APIProvider.objects.create(name='OpenRouter', slug='openrouter')
        self.provider.keys.create(name='test', key='fixture-never-used')
        self.client.force_login(self.user)
        self.png = media.png(Image.new('RGB', (32, 24), '#6c6fcb'))

    def post(self, path, payload):
        return self.client.post('/creation/api/' + path + '/', json.dumps(payload), content_type='application/json')

    def generated(self, **kwargs):
        item = GeneratedMedia(owner=self.user, kind='image', content_type='image/png', prompt='Lumière à Abidjan', **kwargs)
        item.image.save('test.png', ContentFile(self.png), save=True)
        return item

    def project(self, owner=None):
        project = Project.objects.create(owner=owner or self.user, title='Mon montage')
        new_version(project, empty_scene(), 'Original')
        return project

    def balance(self):
        self.user.credit_account.refresh_from_db()
        return self.user.credit_account.balance

    def test_creation_and_library_require_login_and_do_not_load_chat_runtime(self):
        for path in ['/creation/', '/creation/library/']:
            response = self.client.get(path)
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response['Cache-Control'], 'private, no-store')
            self.assertNotContains(response, '/app/js/script.js')
            self.assertNotContains(response, 'marked.min.js')
            soup = BeautifulSoup(response.content, 'html.parser')
            self.assertIsNotNone(soup.select_one('a[href="/studio/"]'))
            self.assertIsNotNone(soup.select_one('#creation-config'))
        self.client.logout()
        for path in ['/creation/', '/creation/library/', '/creation/api/library/']:
            self.assertEqual(self.client.get(path).status_code, 302)
        self.assertEqual(self.post('generate', {'content': 'test'}).status_code, 302)

    def test_message_prefill_keeps_origin_and_checks_ownership(self):
        conversation = Conversation.objects.create(user=self.user, title='Idée')
        message = Message.objects.create(conversation=conversation, role='assistant', content='Une illustration de forêt')
        response = self.client.get(f'/creation/?message={message.pk}')
        config = response.context['creation_config']
        self.assertEqual(config['prompt'], message.content)
        self.assertEqual(config['conversationId'], conversation.pk)
        self.client.force_login(self.other)
        self.assertEqual(self.client.get(f'/creation/?message={message.pk}').status_code, 404)
        self.assertEqual(self.client.get('/creation/?message=invalid').status_code, 404)

    def test_generating_image_needs_no_project_conversation_or_messages(self):
        before = self.balance()
        with patch('chat.views.generate_openrouter_media', return_value=(self.png, 'image/png', Decimal('0.04'))) as provider:
            response = self.post('generate', {'content': 'Un jardin', 'kind': 'image',
                'generation_options': {'image_size': '1024x1536'}})
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(response['Cache-Control'], 'private, no-store')
        item = GeneratedMedia.objects.get(pk=response.json()['media_id'])
        self.assertEqual(item.owner, self.user)
        self.assertIsNone(item.conversation_id)
        self.assertIsNone(item.message_id)
        self.assertFalse(Conversation.objects.exists())
        self.assertFalse(Message.objects.exists())
        self.assertFalse(Project.objects.exists())
        self.assertEqual(self.balance(), before - 600)
        self.assertEqual(provider.call_args.args[4], {'image_size': '1024x1536'})

    def test_generating_video_and_audio_use_existing_adapters_without_chats(self):
        audio = APIProvider.objects.create(name='Audio', slug='apiframe')
        audio.keys.create(name='test', key='fixture-never-used')
        for kind, options, path, output in [
            ('video', {'duration_seconds': 4, 'aspect_ratio': '9:16', 'resolution': '480p', 'generate_audio': False},
                'chat.views.generate_openrouter_media', (b'video-fixture', 'video/mp4', Decimal('0.01'))),
            ('audio', {'style': 'jazz', 'instrumental': True}, 'chat.views.generate_media', (b'audio-fixture', 'audio/mpeg')),
        ]:
            with self.subTest(kind=kind), patch(path, return_value=output) as provider:
                response = self.post('generate', {'kind': kind, 'content': 'Création de test', 'generation_options': options})
                self.assertEqual(response.status_code, 200, response.content)
                self.assertEqual(GeneratedMedia.objects.get(pk=response.json()['media_id']).kind, kind)
                self.assertEqual(provider.call_args.args[-1], options)
        self.assertFalse(Conversation.objects.exists())

    def test_origin_link_does_not_append_messages_and_survives_conversation_deletion(self):
        conversation = Conversation.objects.create(user=self.user, title='Origine')
        origin = Message.objects.create(conversation=conversation, role='user', content='Mon idée')
        with patch('chat.views.generate_openrouter_media', return_value=(self.png, 'image/png', None)):
            response = self.post('generate', {'content': 'Une image', 'conversation_id': conversation.pk})
        self.assertEqual(response.status_code, 200)
        item = GeneratedMedia.objects.get(pk=response.json()['media_id'])
        self.assertEqual(item.conversation_id, conversation.pk)
        self.assertEqual(conversation.messages.count(), 1)
        item.message = origin
        item.save(update_fields=['message'])
        conversation.delete()
        item.refresh_from_db()
        self.assertIsNone(item.conversation_id)
        self.assertIsNone(item.message_id)
        self.assertEqual(self.client.get(response.json()['media_url']).status_code, 200)
        row = self.client.get('/creation/api/library/').json()['items'][0]
        self.assertIsNone(row['origin_url'])
        self.assertEqual(self.client.get('/chat/api/media/gallery/').json()[0]['conversation_title'], None)

    def test_provider_and_storage_failures_refund_without_partial_records(self):
        for failure in ['provider', 'storage']:
            cache.clear()
            before = self.balance()
            with self.subTest(failure=failure):
                with patch('chat.views.generate_openrouter_media', side_effect=ProviderError('Refus du fournisseur') if failure == 'provider' else None,
                        return_value=(self.png, 'image/png', None)):
                    if failure == 'storage':
                        with patch('django.db.models.fields.files.FieldFile.save', side_effect=OSError('Storage offline')):
                            response = self.post('generate', {'content': 'Un dessin'})
                    else:
                        response = self.post('generate', {'content': 'Un dessin'})
                self.assertEqual(response.status_code, 502 if failure == 'provider' else 500)
                self.assertEqual(self.balance(), before)
                self.assertFalse(GeneratedMedia.objects.exists())
                self.assertFalse(Conversation.objects.exists())

    def test_source_chat_deleted_during_generation_does_not_lose_the_result(self):
        conversation = Conversation.objects.create(user=self.user)
        def generate(*args):
            conversation.delete()
            return self.png, 'image/png', None
        with patch('chat.views.generate_openrouter_media', side_effect=generate):
            response = self.post('generate', {'content': 'Une image', 'conversation_id': conversation.pk})
        self.assertEqual(response.status_code, 200, response.content)
        self.assertIsNone(GeneratedMedia.objects.get(pk=response.json()['media_id']).conversation_id)

    def test_invalid_requests_do_not_call_paid_provider(self):
        foreign = Conversation.objects.create(user=self.other)
        before = self.balance()
        with patch('chat.views.generate_openrouter_media') as provider:
            for payload, expected in [({'content': ''}, 400), ({'content': 'Test', 'kind': 'bad'}, 400),
                    ({'content': 'Test', 'temporary': True}, 400),
                    ({'content': 'Test', 'conversation_id': foreign.pk}, 404),
                    ({'content': 'Test', 'generation_options': {'image_size': 'bad'}}, 400)]:
                cache.clear()
                self.assertEqual(self.post('generate', payload).status_code, expected)
            provider.assert_not_called()
        self.assertEqual(self.balance(), before)

    def test_library_combines_old_chat_media_new_creations_and_studio_assets_privately(self):
        conversation = Conversation.objects.create(user=self.user, title='Ancien chat')
        old = self.generated(conversation=conversation)
        new = self.generated()
        project = self.project()
        asset = media.store_asset(project, self.png, 'Référence Studio')
        media.store_asset(project, self.png, 'Masque', role='mask')
        GeneratedMedia.objects.create(owner=self.other, prompt='Secret', image='never-opened.png')
        rows = self.client.get('/creation/api/library/').json()['items']
        self.assertEqual({(row['source'], row['id']) for row in rows},
            {('generated', old.pk), ('generated', new.pk), ('studio', asset.pk)})
        self.assertEqual(next(row for row in rows if row['id'] == old.pk and row['source'] == 'generated')['conversation_title'], 'Ancien chat')
        self.assertEqual(self.client.get('/creation/api/library/?kind=video').json()['items'], [])
        self.assertEqual(len(self.client.get('/creation/api/library/?q=Référence').json()['items']), 1)
        self.assertEqual(self.client.get('/creation/api/library/?q=absent').json()['items'], [])
        for query in ['kind=bad', 'offset=-1', 'offset=invalid']:
            self.assertEqual(self.client.get('/creation/api/library/?' + query).status_code, 400)

    def test_library_pagination_has_no_duplicates_across_sources(self):
        GeneratedMedia.objects.bulk_create([GeneratedMedia(owner=self.user, prompt=f'Image {i}', image='test.png') for i in range(28)])
        project = self.project()
        Asset.objects.bulk_create([Asset(project=project, name=f'Studio {i}', kind='image', file='test.png') for i in range(6)])
        first = self.client.get('/creation/api/library/').json()
        second = self.client.get('/creation/api/library/?offset=' + str(first['next_offset'])).json()
        self.assertEqual(len(first['items']), 24)
        self.assertEqual(len(second['items']), 10)
        self.assertIsNone(second['next_offset'])
        self.assertEqual(len({(item['source'], item['id']) for item in first['items'] + second['items']}), 34)

    def test_private_download_and_ownership_checks(self):
        item = self.generated()
        path = f'/chat/api/media/{item.pk}/?download=1'
        response = self.client.get(path)
        self.assertEqual(response.status_code, 200)
        self.assertIn('attachment;', response['Content-Disposition'])
        self.assertEqual(b''.join(response.streaming_content), self.png)
        self.client.force_login(self.other)
        self.assertEqual(self.client.get(path).status_code, 404)
        for action in ['use-in-chat', 'use-in-studio']:
            self.assertEqual(self.post(action, {'id': item.pk, 'source': 'generated'}).status_code, 404)
        self.assertFalse(Workspace.objects.exists())

    def test_using_image_in_chat_copies_to_private_workspace_without_ai_charge(self):
        item = self.generated()
        before = self.balance()
        response = self.post('use-in-chat', {'id': item.pk, 'source': 'generated'})
        self.assertEqual(response.status_code, 200, response.content)
        params = parse_qs(urlsplit(response.json()['url']).query)
        conversation = Conversation.objects.get(pk=params['conversation'][0])
        file = WorkFile.objects.get(pk=params['creation_file'][0])
        self.assertEqual(file.workspace, conversation.workspace)
        self.assertEqual(file.workspace.user, self.user)
        self.assertFalse(conversation.messages.exists())
        self.assertEqual(self.balance(), before)
        response = self.client.get(response.json()['url'])
        self.assertEqual(response.context['chat_config']['initialCreationAttachment']['id'], file.pk)
        self.assertTrue(response.context['chat_config']['initialCreationAttachment']['isImage'])
        item.delete()
        self.assertEqual(file.file.read(), self.png)

    @override_settings(BARA_USER_FILE_LIMIT=0)
    def test_chat_import_obeys_workspace_storage_quota_without_partial_chat(self):
        item = self.generated()
        self.assertEqual(self.post('use-in-chat', {'source': 'generated', 'id': item.pk}).status_code, 400)
        self.assertFalse(Workspace.objects.exists())
        self.assertFalse(Conversation.objects.exists())

    def test_using_media_in_new_or_existing_studio_project_is_free_and_private(self):
        item = self.generated()
        before = self.balance()
        response = self.post('use-in-studio', {'id': item.pk, 'source': 'generated'})
        self.assertEqual(response.status_code, 200, response.content)
        project = Project.objects.get(owner=self.user)
        self.assertEqual(project.assets.count(), 1)
        self.assertIsNotNone(project.current_version_id)
        foreign = self.project(self.other)
        self.assertEqual(self.post('use-in-studio', {'id': item.pk, 'source': 'generated', 'project_id': foreign.pk}).status_code, 404)
        response = self.post('use-in-studio', {'id': item.pk, 'source': 'generated', 'project_id': project.pk})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(project.assets.count(), 2)
        self.assertEqual(self.balance(), before)
        asset = project.assets.first()
        response = self.post('use-in-chat', {'source': 'studio', 'id': asset.pk})
        self.assertEqual(response.status_code, 200)

    def test_failed_studio_import_rolls_back_new_project(self):
        item = self.generated()
        with patch('creation.views.studio_media.store_asset', side_effect=ValueError('Média invalide')):
            response = self.post('use-in-studio', {'id': item.pk, 'source': 'generated'})
        self.assertEqual(response.status_code, 400)
        self.assertFalse(Project.objects.exists())
