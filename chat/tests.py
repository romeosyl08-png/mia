from django.contrib.auth.models import User
from django.core.cache import cache
from django.test import TestCase
from unittest.mock import MagicMock, patch
from http.client import RemoteDisconnected
from bs4 import BeautifulSoup
from types import SimpleNamespace
import json
from pathlib import Path
from urllib.error import HTTPError

from django.conf import settings
from api.models import AIModel, APIProvider
from bara.models import Workspace
from chat.services import ProviderError, stream_llm_provider
from chat.media_generation import _download_output, generate_media
from .models import ChatPreferences, Conversation, GeneratedMedia, Message, Persona


class ChatHomeTests(TestCase):
	def setUp(self):
		cache.clear()

	def test_messages_and_composer_stay_inside_main_chat(self):
		user = User.objects.create_user(username='chat-layout-user', password='test-password')
		self.client.force_login(user)

		response = self.client.get('/')
		soup = BeautifulSoup(response.content, 'html.parser')
		main = soup.select_one('main.main')

		self.assertEqual(response.status_code, 200)
		self.assertIsNotNone(main.select_one('#messages'))
		self.assertIsNotNone(main.select_one('#composerForm'))
		self.assertIsNotNone(main.select_one('#composerPlusMenu'))
		self.assertIsNotNone(main.select_one('#plusFilesBtn'))
		self.assertIsNotNone(main.select_one('#plusImageBtn'))
		self.assertIsNotNone(main.select_one('#voiceBtn'))
		self.assertIsNotNone(main.select_one('#memorySuggestBtn'))
		sidebar = soup.select_one('aside.sidebar')
		sidebar_html = str(sidebar)
		self.assertIsNotNone(sidebar.select_one('#openImageGalleryBtn'))
		self.assertIsNotNone(sidebar.select_one('#conversationSearch'))
		self.assertIsNotNone(sidebar.select_one('#archiveFilterBtn'))
		self.assertIsNotNone(sidebar.select_one('#folderFilter'))
		self.assertEqual(main.select_one('#workspaceOpen')['href'], '/bara/')
		self.assertIn('id="personaTopSelect"', sidebar_html)
		self.assertIn('id="newPersonaBtn"', sidebar_html)
		self.assertIn('id="renamePersonaBtn"', sidebar_html)
		self.assertIn('id="deletePersonaBtn"', sidebar_html)
		self.assertIn('id="personaRenameForm"', sidebar_html)
		self.assertIn('id="openImageGalleryBtn"', sidebar_html)
		self.assertIn('id="openVideoGalleryBtn"', sidebar_html)
		self.assertContains(response, 'data-gallery-kind="image"')
		self.assertContains(response, 'data-gallery-kind="video"')
		self.assertLess(sidebar_html.index('personaTopSelect'), sidebar_html.index('conversationsSectionTitle'))
		self.assertGreater(sidebar_html.index('quick-nav'), sidebar_html.index('convoList'))

	def test_media_controls_enable_for_a_configured_provider(self):
		user = User.objects.create_user(username='configured-media-user', password='test-password')
		provider = APIProvider.objects.create(name='Configured fal', slug='fal', base_url='https://queue.fal.run')
		provider.keys.create(name='fal-key', key='server-secret')
		self.client.force_login(user)

		response = self.client.get('/')
		soup = BeautifulSoup(response.content, 'html.parser')
		chat_config = json.loads(soup.select_one('#chat-config').string)

		self.assertEqual(response.status_code, 200)
		self.assertIsNotNone(soup.select_one('#videoModeBtn[data-mode="video"]:not([disabled])'))
		self.assertIsNotNone(soup.select_one('#audioModeBtn[data-mode="audio"]:not([disabled])'))
		self.assertIn({'slug': 'fal', 'name': 'Configured fal', 'modalities': ['image', 'video', 'audio']}, chat_config['mediaProviders'])

	def test_about_page_is_public_and_explains_site_capabilities(self):
		response = self.client.get('/nous/')

		self.assertEqual(response.status_code, 200)
		self.assertContains(response, 'À propos de 2AI')
		self.assertContains(response, 'Recherche web')
		self.assertContains(response, 'génération d’images, de vidéos ou d’audio')
		self.assertContains(response, 'dépendent des services activés')
		self.assertContains(response, '<title>À propos de 2AI — Le Comptoir</title>')
		self.assertContains(response, 'href="/juridique/"')
		self.assertContains(response, 'href="/auth/?next=/recharge/"')

		user = User.objects.create_user(username='about-purchase-user', password='test-password')
		self.client.force_login(user)
		authenticated_response = self.client.get('/nous/')
		self.assertContains(authenticated_response, 'href="/recharge/"')

	def test_legal_page_is_public_and_discloses_known_processing(self):
		response = self.client.get('/juridique/')

		self.assertEqual(response.status_code, 200)
		self.assertContains(response, 'Informations juridiques')
		self.assertContains(response, 'À compléter avant mise en production')
		self.assertContains(response, 'Stripe')
		self.assertContains(response, 'CinetPay')
		self.assertContains(response, 'mode temporaire')

	def test_pricing_page_has_been_removed(self):
		self.assertEqual(self.client.get('/prix/').status_code, 404)

	def test_settings_page_exposes_persona_appearance_and_generation_controls(self):
		user = User.objects.create_user(username='settings-page-user', password='test-password')
		self.client.force_login(user)

		response = self.client.get('/customize/')

		self.assertEqual(response.status_code, 200)
		self.assertContains(response, 'Paramètres')
		self.assertContains(response, 'defaultModelSelect')
		self.assertContains(response, 'defaultTopP')
		self.assertContains(response, 'defaultMaxTokens')
		self.assertContains(response, 'personaSystemPrompt')
		self.assertContains(response, 'personaMemory')
		self.assertContains(response, 'Apparence du persona')
		self.assertContains(response, 'botNameInput')

	def test_chat_preferences_are_saved_and_scoped_to_the_user(self):
		user = User.objects.create_user(username='settings-owner', password='test-password')
		other_user = User.objects.create_user(username='settings-other', password='test-password')
		provider = APIProvider.objects.create(name='Settings provider', base_url='https://provider.example.test')
		model = AIModel.objects.create(provider=provider, name='Settings model', model_id='settings-model')
		self.client.force_login(user)

		response = self.client.patch(
			'/chat/api/preferences/',
			data=json.dumps({'default_model_id': model.model_id, 'top_p': 0.35, 'max_tokens': 1200}),
			content_type='application/json',
		)

		self.assertEqual(response.status_code, 200)
		self.assertEqual(response.json(), {
			'default_model_id': 'settings-model',
			'top_p': 0.35,
			'max_tokens': 1200,
		})
		self.client.force_login(other_user)
		other_preferences = self.client.get('/chat/api/preferences/')
		self.assertEqual(other_preferences.status_code, 200)
		self.assertEqual(other_preferences.json()['default_model_id'], '')
		self.assertEqual(other_preferences.json()['top_p'], 1.0)
		self.assertEqual(other_preferences.json()['max_tokens'], 500)
		self.assertEqual(ChatPreferences.objects.filter(user=user).count(), 1)

	def test_chat_workspace_list_is_scoped_to_the_signed_in_user(self):
		user = User.objects.create_user(username='workspace-list-owner', password='test-password')
		other_user = User.objects.create_user(username='workspace-list-other', password='test-password')
		workspace = Workspace.objects.create(user=user, title='Mon espace')
		Workspace.objects.create(user=other_user, title='Espace privé')
		self.client.force_login(user)

		response = self.client.get('/chat/api/workspaces/')

		self.assertEqual(response.status_code, 200)
		self.assertEqual(response.json(), [{'id': workspace.id, 'title': 'Mon espace'}])

	def test_conversation_list_supports_search_folders_and_archive_filter(self):
		user = User.objects.create_user(username='conversation-search-user', password='test-password')
		work_chat = Conversation.objects.create(user=user, title='Notes', folder='Travail')
		Message.objects.create(conversation=work_chat, role='user', content='needle inside message')
		archived_chat = Conversation.objects.create(user=user, title='Ancien projet', folder='Travail', is_archived=True)
		self.client.force_login(user)

		response = self.client.get('/chat/api/conversations/', {'q': 'needle'})
		self.assertEqual([item['id'] for item in response.json()], [work_chat.id])
		self.assertEqual(response.json()[0]['folder'], 'Travail')

		response = self.client.get('/chat/api/conversations/', {'folder': 'Travail'})
		self.assertEqual([item['id'] for item in response.json()], [work_chat.id])

		response = self.client.get('/chat/api/conversations/', {'archived': '1'})
		self.assertEqual([item['id'] for item in response.json()], [archived_chat.id])
		self.assertEqual(self.client.get('/chat/api/folders/').json(), ['Travail'])

	def test_conversation_can_be_archived_and_moved_to_folder(self):
		user = User.objects.create_user(username='conversation-organize-user', password='test-password')
		conversation = Conversation.objects.create(user=user, title='À classer')
		self.client.force_login(user)

		response = self.client.patch(
			f'/chat/api/conversations/{conversation.id}/',
			data=json.dumps({'folder': 'Projets', 'is_archived': True}),
			content_type='application/json',
		)

		self.assertEqual(response.status_code, 200)
		self.assertEqual(response.json()['folder'], 'Projets')
		self.assertIs(response.json()['is_archived'], True)

	def test_legacy_dev_prod_route_uses_bara_workspace_home(self):
		user = User.objects.create_user(username='legacy-dap-user', password='test-password')
		self.client.force_login(user)

		response = self.client.get('/dap/')

		self.assertRedirects(response, '/bara/', fetch_redirect_response=False)

	def test_new_users_receive_default_personas(self):
		user = User.objects.create_user(username='persona-user', password='test-password')

		self.assertEqual(user.personas.count(), 3)
		personas = list(user.personas.order_by('id'))
		self.assertEqual(personas[0].name, 'Assistant général')
		self.assertEqual(personas[0].appearance_avatar, '🐘')
		self.assertEqual(personas[1].appearance_avatar, '📚')
		self.assertEqual(personas[1].appearance_accent, '#3F8E8A')
		self.assertEqual(personas[2].appearance_avatar, '⌘')
		self.assertEqual(personas[2].appearance_theme, 'dark')

	def test_persona_crud_is_scoped_to_authenticated_user(self):
		user = User.objects.create_user(username='persona-owner', password='test-password')
		other_user = User.objects.create_user(username='persona-other', password='test-password')
		persona = user.personas.first()
		other_persona = other_user.personas.first()
		self.client.force_login(user)

		response = self.client.get('/chat/api/personas/')
		self.assertEqual(response.status_code, 200)
		self.assertEqual(len(response.json()), 3)

		response = self.client.post(
			'/chat/api/personas/',
			data=json.dumps({'name': 'Persona test', 'temp': 0.2}),
			content_type='application/json',
		)
		self.assertEqual(response.status_code, 201)
		created_id = response.json()['id']

		response = self.client.patch(
			f'/chat/api/personas/{created_id}/',
			data=json.dumps({
				'name': 'Persona renommé',
				'prompt': 'Nouveau prompt',
				'memory': 'Réponses courtes et métriques.',
				'temp': 0.3,
				'appearance_name': 'Mia',
				'appearance_avatar': '✨',
				'appearance_accent': '#3F8E8A',
				'appearance_theme': 'dark',
			}),
			content_type='application/json',
		)
		self.assertEqual(response.status_code, 200)
		self.assertEqual(response.json()['name'], 'Persona renommé')
		self.assertEqual(response.json()['prompt'], 'Nouveau prompt')
		self.assertEqual(response.json()['memory'], 'Réponses courtes et métriques.')
		self.assertEqual(response.json()['appearance_name'], 'Mia')
		self.assertEqual(response.json()['appearance_avatar'], '✨')
		self.assertEqual(response.json()['appearance_accent'], '#3F8E8A')
		self.assertEqual(response.json()['appearance_theme'], 'dark')
		response = self.client.patch(
			f'/chat/api/personas/{created_id}/',
			data=json.dumps({'name': '   '}),
			content_type='application/json',
		)
		self.assertEqual(response.status_code, 400)

		response = self.client.patch(
			f'/chat/api/personas/{other_persona.id}/',
			data=json.dumps({'name': 'Interdit'}),
			content_type='application/json',
		)
		self.assertEqual(response.status_code, 404)

		response = self.client.delete(f'/chat/api/personas/{created_id}/')
		self.assertEqual(response.status_code, 204)
		self.assertFalse(Persona.objects.filter(id=created_id).exists())

	@patch('chat.views.call_llm_provider', return_value=('Réponse.', 10, 2))
	def test_persona_memory_is_injected_only_for_the_matching_persona(self, call_llm_provider):
		user = User.objects.create_user(username='persona-memory-user', password='test-password')
		first_persona, second_persona = list(user.personas.order_by('id')[:2])
		first_persona.memory = 'Préfère les réponses très concises.'
		first_persona.save(update_fields=['memory'])
		user.credit_account.balance = 1000
		user.credit_account.save(update_fields=['balance'])
		provider = APIProvider.objects.create(name='Persona memory provider', base_url='https://provider.example.test')
		AIModel.objects.create(provider=provider, name='Persona memory model', model_id='persona-memory-model')
		provider.keys.create(name='server-key', key='server-secret')
		self.client.force_login(user)

		for persona in (first_persona, second_persona):
			response = self.client.post('/chat/api/send/', data=json.dumps({
				'model_id': 'persona-memory-model',
				'persona_id': persona.id,
				'content': 'Comment répondre ?',
			}), content_type='application/json')
			self.assertEqual(response.status_code, 200)
			prompt = call_llm_provider.call_args.args[4]
			if persona == first_persona:
				self.assertIn('Préfère les réponses très concises.', prompt)
			else:
				self.assertNotIn('Préfère les réponses très concises.', prompt)

	@patch('chat.views.call_llm_provider', return_value=('• Réponses concises.\n• Utiliser le système métrique.', 25, 18))
	def test_memory_suggestion_is_not_saved_until_confirmation(self, call_llm_provider):
		user = User.objects.create_user(username='memory-suggestion-user', password='test-password')
		persona = user.personas.first()
		persona.memory = 'Conserver cette préférence existante.'
		persona.save(update_fields=['memory'])
		provider = APIProvider.objects.create(name='Memory suggestion provider', base_url='https://provider.example.test')
		AIModel.objects.create(provider=provider, name='Memory suggestion model', model_id='memory-suggestion-model')
		provider.keys.create(name='server-key', key='server-secret')
		conversation = Conversation.objects.create(user=user, persona=persona, title='Conversation source')
		Message.objects.create(conversation=conversation, role='user', content='Réponds brièvement et en mètres.')
		Message.objects.create(conversation=conversation, role='assistant', content='D’accord.')
		self.client.force_login(user)

		response = self.client.post(
			f'/chat/api/personas/{persona.id}/suggest-memory/',
			data=json.dumps({'conversation_id': conversation.id, 'model_id': 'memory-suggestion-model'}),
			content_type='application/json',
		)

		self.assertEqual(response.status_code, 200)
		self.assertEqual(response.json()['suggestion'], '• Réponses concises.\n• Utiliser le système métrique.')
		self.assertEqual(call_llm_provider.call_args.args[3], [
			{'role': 'user', 'content': 'Réponds brièvement et en mètres.'},
			{'role': 'assistant', 'content': 'D’accord.'},
		])
		self.assertIn('Conserver cette préférence existante.', call_llm_provider.call_args.args[4])
		persona.refresh_from_db()
		self.assertEqual(persona.memory, 'Conserver cette préférence existante.')
		user.credit_account.refresh_from_db()
		self.assertLess(user.credit_account.balance, 20000)

	@patch('chat.views.call_llm_provider', return_value=('Préférence proposée.', 10, 5))
	def test_memory_suggestion_cannot_read_another_users_conversation(self, call_llm_provider):
		user = User.objects.create_user(username='memory-owner', password='test-password')
		other_user = User.objects.create_user(username='memory-other', password='test-password')
		persona = user.personas.first()
		other_persona = other_user.personas.first()
		conversation = Conversation.objects.create(user=other_user, persona=other_persona)
		Message.objects.create(conversation=conversation, role='user', content='Secret conversation')
		self.client.force_login(user)

		response = self.client.post(
			f'/chat/api/personas/{persona.id}/suggest-memory/',
			data=json.dumps({'conversation_id': conversation.id}),
			content_type='application/json',
		)

		self.assertEqual(response.status_code, 404)
		call_llm_provider.assert_not_called()

	def test_conversation_persona_can_be_changed(self):
		user = User.objects.create_user(username='conversation-persona-user', password='test-password')
		first_persona, second_persona = list(user.personas.all()[:2])
		conversation = Conversation.objects.create(user=user, persona=first_persona)
		self.client.force_login(user)

		response = self.client.patch(
			f'/chat/api/conversations/{conversation.id}/',
			data=json.dumps({'persona_id': second_persona.id}),
			content_type='application/json',
		)

		self.assertEqual(response.status_code, 200)
		self.assertEqual(response.json()['persona_id'], second_persona.id)
		conversation.refresh_from_db()
		self.assertEqual(conversation.persona_id, second_persona.id)

	def test_conversation_workspace_can_be_changed(self):
		user = User.objects.create_user(username='conversation-workspace-user', password='test-password')
		workspace = Workspace.objects.create(user=user, title='Fichiers choisis')
		conversation = Conversation.objects.create(user=user, title='Chat lié')
		self.client.force_login(user)

		response = self.client.patch(
			f'/chat/api/conversations/{conversation.id}/',
			data=json.dumps({'workspace_id': workspace.id}),
			content_type='application/json',
		)

		self.assertEqual(response.status_code, 200)
		self.assertEqual(response.json()['workspace_id'], workspace.id)
		conversation.refresh_from_db()
		self.assertEqual(conversation.workspace_id, workspace.id)


	@patch('chat.services.urlopen')
	def test_send_message_debits_server_credits(self, urlopen):
		user = User.objects.create_user(username='backend-chat-user', password='test-password')
		user.credit_account.balance = 1000
		user.credit_account.save(update_fields=['balance'])
		provider = APIProvider.objects.create(
			name='Backend provider',
			base_url='https://provider.example.test',
		)
		AIModel.objects.create(provider=provider, name='Backend model', model_id='backend-model')
		provider.keys.create(name='server-key', key='server-secret')
		ChatPreferences.objects.create(
			user=user,
			default_model_id='backend-model',
			top_p=0.4,
			max_tokens=900,
		)
		provider_response = MagicMock()
		provider_response.__enter__.return_value.read.return_value = json.dumps({
			'id': 'provider-response',
			'object': 'chat.completion',
			'choices': [{'index': 0, 'message': {'role': 'assistant', 'content': 'Bonjour.'}, 'finish_reason': 'stop'}],
		}).encode()
		urlopen.return_value = provider_response
		self.client.force_login(user)

		response = self.client.post('/chat/api/send/', data=json.dumps({
			'content': 'Bonjour',
		}), content_type='application/json')

		self.assertEqual(response.status_code, 200)
		self.assertEqual(response.json()['reply'], 'Bonjour.')
		provider_request = urlopen.call_args.args[0]
		request_payload = json.loads(provider_request.data)
		self.assertNotIn('balance', request_payload)
		self.assertEqual(request_payload['model'], 'backend-model')
		self.assertEqual(request_payload['top_p'], 0.4)
		self.assertEqual(request_payload['max_tokens'], 900)
		user.credit_account.refresh_from_db()
		self.assertEqual(response.json()['remaining_balance'], user.credit_account.balance)
		self.assertLess(user.credit_account.balance, 1000)
		self.assertNotContains(response, 'server-secret')
		self.assertIsNotNone(response.json()['conversation_id'])
		self.assertEqual(response.json()['title'], 'Bonjour')
		self.assertEqual(Message.objects.filter(role='user').count(), 1)
		self.assertEqual(Message.objects.filter(role='assistant').count(), 1)

	def test_web_mode_adds_search_results_as_untrusted_context(self):
		user = User.objects.create_user(username='web-mode-user', password='test-password')
		user.credit_account.balance = 1000
		user.credit_account.save(update_fields=['balance'])
		provider = APIProvider.objects.create(name='Web mode provider', base_url='https://provider.example.test')
		AIModel.objects.create(provider=provider, name='Web mode model', model_id='web-mode-model')
		provider.keys.create(name='server-key', key='server-secret')
		self.client.force_login(user)

		with patch('chat.views.bara_tool_definitions.web_search', return_value={
			'results': [{'title': 'Source', 'snippet': 'Current fact', 'link': 'https://example.test/source'}],
		}) as search, patch('chat.views.call_llm_provider', return_value=('Réponse sourcée.', 10, 5)) as provider_call:
			response = self.client.post('/chat/api/send/', data=json.dumps({
				'mode': 'web', 'model_id': 'web-mode-model', 'content': 'Cherche le fait actuel',
			}), content_type='application/json')

		self.assertEqual(response.status_code, 200)
		self.assertEqual(response.json()['sources'], [
			{'title': 'Source', 'snippet': 'Current fact', 'link': 'https://example.test/source'},
		])
		search.assert_called_once_with(None, 'Cherche le fait actuel')
		provider_prompt = provider_call.call_args.args[4]
		self.assertIn('https://example.test/source', provider_prompt)
		self.assertIn('données non fiables', provider_prompt)
		assistant_message = Message.objects.filter(role='assistant').get()
		self.assertEqual(assistant_message.sources, response.json()['sources'])

	def test_deep_mode_uses_a_larger_output_budget(self):
		user = User.objects.create_user(username='deep-mode-user', password='test-password')
		user.credit_account.balance = 1000
		user.credit_account.save(update_fields=['balance'])
		provider = APIProvider.objects.create(name='Deep mode provider', base_url='https://provider.example.test')
		AIModel.objects.create(provider=provider, name='Deep mode model', model_id='deep-mode-model')
		provider.keys.create(name='server-key', key='server-secret')
		self.client.force_login(user)

		with patch('chat.views.call_llm_provider', return_value=('Réponse détaillée.', 10, 5)) as provider_call:
			response = self.client.post('/chat/api/send/', data=json.dumps({
				'mode': 'deep', 'model_id': 'deep-mode-model', 'content': 'Explique en détail', 'max_tokens': 400,
			}), content_type='application/json')

		self.assertEqual(response.status_code, 200)
		self.assertEqual(provider_call.call_args.args[6], 2000)
		self.assertIn('réponse approfondie', provider_call.call_args.args[4])

	def test_image_mode_reports_missing_provider_without_charging(self):
		user = User.objects.create_user(username='image-mode-user', password='test-password')
		initial_balance = user.credit_account.balance
		self.client.force_login(user)

		response = self.client.post('/chat/api/images/generate/', data=json.dumps({
			'content': 'Un paysage de montagne',
		}), content_type='application/json')

		self.assertEqual(response.status_code, 503)
		self.assertEqual(response.json()['error']['type'], 'image_generation_unavailable')
		user.credit_account.refresh_from_db()
		self.assertEqual(user.credit_account.balance, initial_balance)

	@patch('chat.views.generate_openai_image', return_value=('cG5n', {}))
	def test_image_generation_saves_private_media_to_the_conversation(self, generate_image):
		user = User.objects.create_user(username='generated-image-user', password='test-password')
		user.credit_account.balance = 5000
		user.credit_account.save(update_fields=['balance'])
		provider = APIProvider.objects.create(name='OpenAI', slug='openai', base_url='https://api.openai.com')
		provider.keys.create(name='image-key', key='server-secret')
		persona = user.personas.first()
		self.client.force_login(user)

		response = self.client.post('/chat/api/images/generate/', data=json.dumps({
			'content': 'Un paysage de montagne',
			'persona_id': persona.id,
		}), content_type='application/json')
		self.assertEqual(response.status_code, 200)
		self.assertTrue(response.json()['media_url'].startswith('/chat/api/media/'))
		conversation = Conversation.objects.get(id=response.json()['conversation_id'], user=user)
		self.assertEqual(conversation.persona_id, persona.id)
		self.assertEqual(list(conversation.messages.values_list('content', flat=True)), [
			'Un paysage de montagne', 'Image générée.',
		])
		media = GeneratedMedia.objects.get(conversation=conversation, owner=user)
		self.addCleanup(media.image.delete, save=False)
		self.assertEqual(media.message_id, response.json()['assistant_message_id'])
		self.assertFalse(Path(media.image.path).is_relative_to(Path(settings.MEDIA_ROOT)))
		with self.assertRaises(ValueError):
			media.image.url
		media_response = self.client.get(response.json()['media_url'])
		self.assertEqual(media_response.status_code, 200)
		self.assertEqual(media_response['Content-Type'], 'image/png')
		self.assertEqual(b''.join(media_response.streaming_content), b'png')
		gallery_response = self.client.get('/chat/api/media/gallery/')
		self.assertEqual(gallery_response.status_code, 200)
		self.assertEqual(gallery_response.json()[0]['id'], media.id)
		self.assertEqual(gallery_response.json()[0]['conversation_title'], conversation.title)
		video = GeneratedMedia.objects.create(
			owner=user,
			conversation=conversation,
			prompt='Plan vidéo',
			kind='video',
		)
		video_gallery = self.client.get('/chat/api/media/gallery/?kind=video')
		self.assertEqual([item['id'] for item in video_gallery.json()], [video.id])
		conversation_response = self.client.get(f'/chat/api/conversations/{conversation.id}/')
		self.assertEqual(conversation_response.json()['messages'][1]['media_url'], response.json()['media_url'])
		gallery_response = self.client.get('/chat/api/media/gallery/')
		self.assertEqual(gallery_response.status_code, 200)
		self.assertEqual(len(gallery_response.json()), 1)
		self.assertEqual(gallery_response.json()[0]['prompt'], 'Un paysage de montagne')

		other_user = User.objects.create_user(username='other-image-user', password='test-password')
		self.client.force_login(other_user)
		self.assertEqual(self.client.get(response.json()['media_url']).status_code, 404)
		self.assertEqual(self.client.get('/chat/api/media/gallery/').json(), [])
		self.assertEqual(self.client.get('/chat/api/media/gallery/').json(), [])
		generate_image.assert_called_once()

	def test_media_adapters_normalize_image_video_and_audio_outputs(self):
		media_urls = {
			'image': 'https://v3.fal.media/files/image.png',
			'video': 'https://cdn2.apiframe.ai/videos/video.mp4',
			'audio': 'https://replicate.delivery/audio.mp3',
		}
		content_types = {'image': 'image/png', 'video': 'video/mp4', 'audio': 'audio/mpeg'}
		for provider_slug in ('fal', 'replicate', 'apiframe'):
			for media_type, output_url in media_urls.items():
				with self.subTest(provider=provider_slug, media=media_type):
					if provider_slug == 'fal':
						result = {'images': [{'url': output_url}]} if media_type == 'image' else {'video': {'url': output_url}} if media_type == 'video' else {'audio_file': {'url': output_url}}
						responses = [{'request_id': 'fal-job'}, {'status': 'COMPLETED'}, result]
					elif provider_slug == 'replicate':
						responses = [{'status': 'succeeded', 'output': [output_url]}]
					else:
						result = {'images': [output_url]} if media_type == 'image' else {'videoUrl': output_url} if media_type == 'video' else {'tracks': [{'audioUrl': output_url}]}
						responses = [{'jobId': 'apiframe-job'}, {'status': 'COMPLETED', 'result': result}]
					with patch('chat.media_generation._json_request', side_effect=responses), patch(
						'chat.media_generation._download_output',
						return_value=(b'media', content_types[media_type]),
					) as download:
						media, content_type = generate_media(
							SimpleNamespace(slug=provider_slug), 'test-key', media_type, 'A test prompt', f'model/{provider_slug}',
						)
					self.assertEqual(media, b'media')
					self.assertEqual(content_type, content_types[media_type])
					download.assert_called_once_with(output_url, provider_slug, media_type, 'test-key')

	def test_media_download_rejects_lookalike_provider_hosts(self):
		with self.assertRaises(ProviderError):
			_download_output('https://evilreplicate.delivery/file.png', 'replicate', 'image', 'server-secret')

	@patch('chat.views.generate_media', side_effect=ProviderError('Le fournisseur est indisponible.'))
	def test_failed_video_generation_refunds_reserved_credits(self, generate_video):
		user = User.objects.create_user(username='failed-video-user', password='test-password')
		user.credit_account.balance = 50000
		user.credit_account.save(update_fields=['balance'])
		provider = APIProvider.objects.create(name='fal video', slug='fal', base_url='https://queue.fal.run')
		provider.keys.create(name='video-key', key='server-secret')
		self.client.force_login(user)

		response = self.client.post('/chat/api/media/generate/', data=json.dumps({
			'content': 'Une scène de test',
			'kind': 'video',
			'provider_slug': 'fal',
		}), content_type='application/json')

		self.assertEqual(response.status_code, 502)
		self.assertEqual(response.json()['error']['type'], 'media_generation_failed')
		user.credit_account.refresh_from_db()
		self.assertEqual(user.credit_account.balance, 50000)
		generate_video.assert_called_once()

	@patch('chat.views.generate_media', return_value=(b'audio bytes', 'audio/mpeg'))
	def test_audio_generation_saves_private_media_and_debits_credits(self, generate_audio):
		user = User.objects.create_user(username='generated-audio-user', password='test-password')
		user.credit_account.balance = 5000
		user.credit_account.save(update_fields=['balance'])
		provider = APIProvider.objects.create(name='Apiframe media', slug='apiframe', base_url='https://api.apiframe.ai')
		provider.keys.create(name='audio-key', key='server-secret')
		self.client.force_login(user)

		response = self.client.post('/chat/api/media/generate/', data=json.dumps({
			'content': 'Un court thème musical',
			'kind': 'audio',
			'provider_slug': 'apiframe',
		}), content_type='application/json')

		self.assertEqual(response.status_code, 200)
		self.assertEqual(response.json()['media_kind'], 'audio')
		self.assertEqual(response.json()['remaining_balance'], 3000)
		media = GeneratedMedia.objects.get(owner=user, kind='audio')
		self.addCleanup(media.image.delete, save=False)
		self.assertEqual(media.content_type, 'audio/mpeg')
		media_response = self.client.get(response.json()['media_url'])
		self.assertEqual(media_response['Content-Type'], 'audio/mpeg')
		self.assertEqual(b''.join(media_response.streaming_content), b'audio bytes')
		self.assertEqual(self.client.get('/chat/api/media/gallery/?kind=audio').json()[0]['id'], media.id)
		generate_audio.assert_called_once()

	@patch('chat.views.generate_openai_image', return_value=('cG5n', {}))
	def test_temporary_image_is_returned_inline_without_database_records(self, generate_image):
		user = User.objects.create_user(username='temporary-image-user', password='test-password')
		user.credit_account.balance = 5000
		user.credit_account.save(update_fields=['balance'])
		provider = APIProvider.objects.create(name='OpenAI', slug='openai', base_url='https://api.openai.com')
		provider.keys.create(name='image-key', key='server-secret')
		self.client.force_login(user)

		response = self.client.post('/chat/api/images/generate/', data=json.dumps({
			'content': 'Un paysage temporaire',
			'temporary': True,
		}), content_type='application/json')

		self.assertEqual(response.status_code, 200)
		self.assertTrue(response.json()['media_url'].startswith('data:image/png;base64,'))
		self.assertFalse(Conversation.objects.filter(user=user).exists())
		self.assertFalse(GeneratedMedia.objects.filter(owner=user).exists())
		generate_image.assert_called_once()

	@patch('chat.views.call_llm_provider', return_value=('Réponse locale.', 12, 8))
	def test_send_message_with_existing_conversation(self, call_llm_provider):
		user = User.objects.create_user(username='existing-conversation-user', password='test-password')
		user.credit_account.balance = 1000
		user.credit_account.save(update_fields=['balance'])
		persona = user.personas.first()
		provider = APIProvider.objects.create(name='Existing conversation provider', base_url='https://provider.example.test')
		AIModel.objects.create(provider=provider, name='Existing conversation model', model_id='existing-conversation-model')
		provider.keys.create(name='server-key', key='server-secret')
		conversation = Conversation.objects.create(user=user, persona=persona, title='Conversation existante')
		Message.objects.create(conversation=conversation, role='user', content='Question précédente')
		self.client.force_login(user)

		response = self.client.post('/chat/api/send/', data=json.dumps({
			'conversation_id': conversation.id,
			'model_id': 'existing-conversation-model',
			'persona_id': persona.id,
			'content': 'Nouvelle question',
		}), content_type='application/json')

		self.assertEqual(response.status_code, 200)
		self.assertEqual(response.json()['reply'], 'Réponse locale.')
		self.assertEqual(conversation.messages.count(), 3)
		call_llm_provider.assert_called_once()

	@patch('chat.views.stream_llm_provider')
	def test_editing_a_sent_prompt_replaces_its_following_history(self, stream_provider):
		user = User.objects.create_user(username='edit-prompt-user', password='test-password')
		user.credit_account.balance = 1000
		user.credit_account.save(update_fields=['balance'])
		persona = user.personas.first()
		provider = APIProvider.objects.create(name='Edit prompt provider', base_url='https://provider.example.test')
		AIModel.objects.create(provider=provider, name='Edit prompt model', model_id='edit-prompt-model')
		provider.keys.create(name='server-key', key='server-secret')
		conversation = Conversation.objects.create(user=user, persona=persona, title='Ancien titre')
		prompt = Message.objects.create(conversation=conversation, role='user', content='Ancien prompt')
		Message.objects.create(conversation=conversation, role='assistant', content='Ancienne réponse')
		Message.objects.create(conversation=conversation, role='user', content='Question suivante')
		Message.objects.create(conversation=conversation, role='assistant', content='Réponse suivante')
		stream_provider.return_value = iter([
			{'type': 'delta', 'content': 'Réponse'},
			{'type': 'usage', 'input_tokens': 4, 'output_tokens': 2},
		])
		self.client.force_login(user)

		response = self.client.post(
			'/chat/api/send/',
			data=json.dumps({
				'conversation_id': conversation.id,
				'edit_message_id': prompt.id,
				'model_id': 'edit-prompt-model',
				'persona_id': persona.id,
				'content': 'Prompt modifié',
			}),
			content_type='application/json',
			HTTP_ACCEPT='text/event-stream',
		)
		events = [
			json.loads(line.removeprefix('data: '))
			for line in b''.join(response.streaming_content).decode().splitlines()
			if line.startswith('data: ')
		]

		self.assertEqual(events[-1]['type'], 'done')
		self.assertEqual(stream_provider.call_args.args[3], [{'role': 'user', 'content': 'Prompt modifié'}])
		conversation.refresh_from_db()
		self.assertEqual(conversation.title, 'Prompt modifié')
		self.assertEqual(list(conversation.messages.values_list('content', flat=True)), ['Prompt modifié', 'Réponse'])
		self.assertNotEqual(events[-1]['user_message_id'], prompt.id)

	@patch('chat.views.stream_llm_provider')
	def test_failed_prompt_edit_preserves_old_history_and_refunds(self, stream_provider):
		user = User.objects.create_user(username='edit-prompt-failure-user', password='test-password')
		user.credit_account.balance = 1000
		user.credit_account.save(update_fields=['balance'])
		provider = APIProvider.objects.create(name='Edit failure provider', base_url='https://provider.example.test')
		AIModel.objects.create(provider=provider, name='Edit failure model', model_id='edit-failure-model')
		provider.keys.create(name='server-key', key='server-secret')
		conversation = Conversation.objects.create(user=user, title='Original')
		prompt = Message.objects.create(conversation=conversation, role='user', content='Prompt original')
		Message.objects.create(conversation=conversation, role='assistant', content='Réponse originale')

		def failing_stream(*args, **kwargs):
			yield {'type': 'delta', 'content': 'Partiel'}
			raise ProviderError('Fournisseur indisponible.')

		stream_provider.side_effect = failing_stream
		self.client.force_login(user)
		response = self.client.post(
			'/chat/api/send/',
			data=json.dumps({
				'conversation_id': conversation.id,
				'edit_message_id': prompt.id,
				'model_id': 'edit-failure-model',
				'content': 'Prompt nouveau',
			}),
			content_type='application/json',
			HTTP_ACCEPT='text/event-stream',
		)
		body = b''.join(response.streaming_content).decode()

		self.assertIn('"type": "error"', body)
		self.assertEqual(list(conversation.messages.values_list('content', flat=True)), ['Prompt original', 'Réponse originale'])
		user.credit_account.refresh_from_db()
		self.assertEqual(user.credit_account.balance, 1000)

	@patch('chat.views.stream_llm_provider')
	def test_send_message_streams_deltas_and_persists_the_completed_turn(self, stream_provider):
		user = User.objects.create_user(username='streaming-chat-user', password='test-password')
		user.credit_account.balance = 1000
		user.credit_account.save(update_fields=['balance'])
		persona = user.personas.first()
		provider = APIProvider.objects.create(name='Streaming provider', base_url='https://provider.example.test')
		AIModel.objects.create(provider=provider, name='Streaming model', model_id='streaming-model')
		provider.keys.create(name='server-key', key='server-secret')
		stream_provider.return_value = iter([
			{'type': 'delta', 'content': 'Bon'},
			{'type': 'delta', 'content': 'jour.'},
			{'type': 'usage', 'input_tokens': 4, 'output_tokens': 2},
		])
		self.client.force_login(user)

		response = self.client.post(
			'/chat/api/send/',
			data=json.dumps({'model_id': 'streaming-model', 'persona_id': persona.id, 'content': 'Salut'}),
			content_type='application/json',
			HTTP_ACCEPT='text/event-stream',
		)
		events = [
			json.loads(line.removeprefix('data: '))
			for line in b''.join(response.streaming_content).decode().splitlines()
			if line.startswith('data: ')
		]

		self.assertEqual(response.status_code, 200)
		self.assertEqual(response['Content-Type'], 'text/event-stream')
		self.assertEqual([event['content'] for event in events if event['type'] == 'delta'], ['Bon', 'jour.'])
		self.assertEqual(events[-1]['type'], 'done')
		conversation = Conversation.objects.get(id=events[-1]['conversation_id'])
		self.assertEqual(conversation.persona_id, persona.id)
		self.assertEqual(list(conversation.messages.values_list('content', flat=True)), ['Salut', 'Bonjour.'])
		user.credit_account.refresh_from_db()
		self.assertLess(user.credit_account.balance, 1000)

	@patch('chat.views.call_llm_provider', return_value=('Réponse temporaire.', 8, 3))
	def test_temporary_sync_turn_keeps_history_in_memory_and_does_not_persist(self, call_llm_provider):
		user = User.objects.create_user(username='temporary-sync-user', password='test-password')
		user.credit_account.balance = 1000
		user.credit_account.save(update_fields=['balance'])
		provider = APIProvider.objects.create(name='Temporary sync provider', base_url='https://provider.example.test')
		AIModel.objects.create(provider=provider, name='Temporary sync model', model_id='temporary-sync-model')
		provider.keys.create(name='server-key', key='server-secret')
		self.client.force_login(user)

		response = self.client.post('/chat/api/send/', data=json.dumps({
			'temporary': True,
			'history': [{'role': 'user', 'content': 'Question précédente'}],
			'model_id': 'temporary-sync-model',
			'content': 'Question actuelle',
			'temperature': 9,
			'top_p': -0.1,
			'max_tokens': 99999,
		}), content_type='application/json')

		self.assertEqual(response.status_code, 200)
		self.assertIsNone(response.json()['conversation_id'])
		self.assertFalse(Conversation.objects.filter(user=user).exists())
		self.assertFalse(Message.objects.exists())
		call_args = call_llm_provider.call_args.args
		self.assertEqual(call_args[3], [
			{'role': 'user', 'content': 'Question précédente'},
			{'role': 'user', 'content': 'Question actuelle'},
		])
		self.assertEqual(call_args[5:8], (2, 8192, 0))

	@patch('chat.views.stream_llm_provider')
	def test_temporary_stream_turn_does_not_persist(self, stream_provider):
		user = User.objects.create_user(username='temporary-stream-user', password='test-password')
		user.credit_account.balance = 1000
		user.credit_account.save(update_fields=['balance'])
		provider = APIProvider.objects.create(name='Temporary stream provider', base_url='https://provider.example.test')
		AIModel.objects.create(provider=provider, name='Temporary stream model', model_id='temporary-stream-model')
		provider.keys.create(name='server-key', key='server-secret')
		stream_provider.return_value = iter([
			{'type': 'delta', 'content': 'Réponse'},
			{'type': 'usage', 'input_tokens': 5, 'output_tokens': 2},
		])
		self.client.force_login(user)

		response = self.client.post(
			'/chat/api/send/',
			data=json.dumps({
				'temporary': True,
				'history': [{'role': 'assistant', 'content': 'Réponse précédente'}],
				'model_id': 'temporary-stream-model',
				'content': 'Continue',
				'temperature': 0.4,
				'top_p': 0.65,
				'max_tokens': 120,
			}),
			content_type='application/json',
			HTTP_ACCEPT='text/event-stream',
		)
		events = [
			json.loads(line.removeprefix('data: '))
			for line in b''.join(response.streaming_content).decode().splitlines()
				if line.startswith('data: ')
		]

		self.assertEqual(response.status_code, 200)
		self.assertEqual(events[-1]['type'], 'done')
		self.assertIsNone(events[-1]['conversation_id'])
		self.assertFalse(Conversation.objects.filter(user=user).exists())
		self.assertFalse(Message.objects.exists())
		call_args = stream_provider.call_args.args
		self.assertEqual(call_args[3], [
			{'role': 'assistant', 'content': 'Réponse précédente'},
			{'role': 'user', 'content': 'Continue'},
		])
		self.assertEqual(call_args[5:8], (0.4, 120, 0.65))

	@patch('chat.views.stream_llm_provider')
	def test_streaming_provider_error_refunds_reserved_credits(self, stream_provider):
		user = User.objects.create_user(username='streaming-error-user', password='test-password')
		user.credit_account.balance = 1000
		user.credit_account.save(update_fields=['balance'])
		provider = APIProvider.objects.create(name='Streaming error provider', base_url='https://provider.example.test')
		AIModel.objects.create(provider=provider, name='Streaming error model', model_id='stream-error-model')
		provider.keys.create(name='server-key', key='server-secret')

		def failing_stream(*args, **kwargs):
			yield {'type': 'delta', 'content': 'Partiel'}
			raise ProviderError('Le fournisseur est temporairement indisponible.')

		stream_provider.side_effect = failing_stream
		self.client.force_login(user)
		response = self.client.post(
			'/chat/api/send/',
			data=json.dumps({'model_id': 'stream-error-model', 'content': 'Salut'}),
			content_type='application/json',
			HTTP_ACCEPT='text/event-stream',
		)
		body = b''.join(response.streaming_content).decode()

		self.assertIn('"type": "delta"', body)
		self.assertIn('"type": "error"', body)
		user.credit_account.refresh_from_db()
		self.assertEqual(user.credit_account.balance, 1000)
		self.assertFalse(Conversation.objects.filter(user=user).exists())

	@patch('chat.services.urlopen')
	def test_stream_llm_provider_parses_openai_sse(self, urlopen):
		provider = APIProvider(name='SSE provider', slug='sse-provider', base_url='https://provider.example.test')
		response = MagicMock()
		response.__enter__.return_value.__iter__.return_value = [
			b'data: {"choices":[{"delta":{"content":"Bonjour"}}]}\n',
			b'data: {"choices":[{"delta":{"content":"."}}]}\n',
			b'data: [DONE]\n',
		]
		urlopen.return_value = response

		events = list(stream_llm_provider(provider, 'test-key', 'test-model', [{'role': 'user', 'content': 'Salut'}]))

		self.assertEqual(events, [
			{'type': 'delta', 'content': 'Bonjour'},
			{'type': 'delta', 'content': '.'},
			{'type': 'usage', 'input_tokens': 0, 'output_tokens': 0},
		])
		request_payload = json.loads(urlopen.call_args.args[0].data)
		self.assertTrue(request_payload['stream'])

	@patch('chat.services.stream_llm_provider')
	def test_send_message_streams_team_response(self, stream_provider):
		user = User.objects.create_user(username='streaming-team-user', password='test-password')
		user.credit_account.balance = 1000
		user.credit_account.save(update_fields=['balance'])
		provider = APIProvider.objects.create(name='Streaming team provider', base_url='https://provider.example.test')
		AIModel.objects.create(provider=provider, name='Streaming team model', model_id='streaming-team-model')
		provider.keys.create(name='server-key', key='server-secret')
		stream_provider.return_value = iter([
			{'type': 'delta', 'content': 'Réponse'},
			{'type': 'usage', 'input_tokens': 3, 'output_tokens': 2},
		])
		self.client.force_login(user)

		response = self.client.post(
			'/chat/api/send/',
			data=json.dumps({
				'model_id': 'streaming-team-model',
				'team_mode': True,
				'team_model_ids': ['streaming-team-model'],
				'content': 'Salut',
			}),
			content_type='application/json',
			HTTP_ACCEPT='text/event-stream',
		)
		body = b''.join(response.streaming_content).decode()

		self.assertIn('"content": "Réponse"', body)
		self.assertIn('"type": "done"', body)
		stream_provider.assert_called_once()

	@patch('chat.services.urlopen')
	def test_stream_llm_provider_parses_anthropic_sse(self, urlopen):
		provider = APIProvider(name='Anthropic SSE', slug='anthropic', base_url='https://provider.example.test')
		response = MagicMock()
		response.__enter__.return_value.__iter__.return_value = [
			b'data: {"type":"message_start","message":{"usage":{"input_tokens":7}}}\n',
			b'data: {"type":"content_block_delta","delta":{"type":"text_delta","text":"Salut"}}\n',
			b'data: {"type":"message_delta","usage":{"output_tokens":1}}\n',
		]
		urlopen.return_value = response

		events = list(stream_llm_provider(provider, 'test-key', 'test-model', [{'role': 'user', 'content': 'Bonjour'}]))

		self.assertEqual(events, [
			{'type': 'delta', 'content': 'Salut'},
			{'type': 'usage', 'input_tokens': 7, 'output_tokens': 1},
		])

	@patch('chat.services.urlopen')
	def test_send_message_handles_provider_rate_limit(self, urlopen):
		user = User.objects.create_user(username='rate-limited-user', password='test-password')
		user.credit_account.balance = 1000
		user.credit_account.save(update_fields=['balance'])
		provider = APIProvider.objects.create(
			name='Rate limited provider',
			base_url='https://provider.example.test',
		)
		AIModel.objects.create(provider=provider, name='Rate limited model', model_id='rate-limited-model')
		provider.keys.create(name='server-key', key='server-secret')
		urlopen.side_effect = HTTPError(
			'https://provider.example.test/v1/chat/completions',
			429,
			'Too Many Requests',
			{},
			None,
		)
		self.client.force_login(user)

		response = self.client.post('/chat/api/send/', data=json.dumps({
			'model_id': 'rate-limited-model',
			'content': 'Bonjour',
		}), content_type='application/json')

		self.assertEqual(response.status_code, 502)
		self.assertEqual(response.json()['error']['message'], 'Trop de requêtes, réessaie dans un instant.')

	@patch('chat.services.urlopen')
	def test_send_message_handles_upstream_connection_reset(self, urlopen):
		user = User.objects.create_user(username='disconnected-provider-user', password='test-password')
		user.credit_account.balance = 1000
		user.credit_account.save(update_fields=['balance'])
		provider = APIProvider.objects.create(name='Disconnected provider', base_url='https://provider.example.test')
		AIModel.objects.create(provider=provider, name='Disconnected model', model_id='disconnected-model')
		provider.keys.create(name='server-key', key='server-secret')
		urlopen.side_effect = RemoteDisconnected('connection closed before response')
		self.client.force_login(user)

		response = self.client.post('/chat/api/send/', data=json.dumps({
			'model_id': 'disconnected-model',
			'content': 'Bonjour',
		}), content_type='application/json')

		self.assertEqual(response.status_code, 502)
		self.assertEqual(response.json()['error']['type'], 'provider_error')
		self.assertEqual(response.json()['error']['message'], 'Le fournisseur est temporairement indisponible.')
		user.credit_account.refresh_from_db()
		self.assertEqual(user.credit_account.balance, 1000)

	@patch('chat.views.call_llm_provider', side_effect=RuntimeError('unexpected provider failure'))
	def test_send_message_handles_unexpected_provider_exception(self, call_llm_provider):
		user = User.objects.create_user(username='unexpected-provider-user', password='test-password')
		user.credit_account.balance = 1000
		user.credit_account.save(update_fields=['balance'])
		provider = APIProvider.objects.create(name='Unexpected provider', base_url='https://provider.example.test')
		AIModel.objects.create(provider=provider, name='Unexpected model', model_id='unexpected-model')
		provider.keys.create(name='server-key', key='server-secret')
		self.client.force_login(user)

		response = self.client.post('/chat/api/send/', data=json.dumps({
			'model_id': 'unexpected-model',
			'content': 'Bonjour',
		}), content_type='application/json')

		self.assertEqual(response.status_code, 502)
		self.assertEqual(response.json()['error']['type'], 'provider_error')
		user.credit_account.refresh_from_db()
		self.assertEqual(user.credit_account.balance, 1000)

	@patch('chat.views.estimate_cost', return_value=(1, 1))
	@patch('chat.services.urlopen')
	def test_send_message_keeps_reservation_when_usage_exceeds_it(self, urlopen, estimate_cost):
		user = User.objects.create_user(username='reserved-chat-user', password='test-password')
		user.credit_account.balance = 1
		user.credit_account.save(update_fields=['balance'])
		provider = APIProvider.objects.create(name='Reserved provider', base_url='https://provider.example.test')
		AIModel.objects.create(provider=provider, name='Reserved model', model_id='reserved-model')
		provider.keys.create(name='server-key', key='server-secret')
		provider_response = MagicMock()
		provider_response.__enter__.return_value.read.return_value = json.dumps({
			'choices': [{'message': {'content': 'Bonjour.'}}],
			'usage': {'prompt_tokens': 10, 'completion_tokens': 10},
		}).encode()
		urlopen.return_value = provider_response
		self.client.force_login(user)

		response = self.client.post('/chat/api/send/', data=json.dumps({
			'model_id': 'reserved-model',
			'content': 'Bonjour',
		}), content_type='application/json')

		self.assertEqual(response.status_code, 200)
		self.assertEqual(response.json()['remaining_balance'], 0)
		self.assertEqual(Message.objects.filter(role='assistant').count(), 1)

	def test_conversation_routes_are_scoped_to_authenticated_user(self):
		user = User.objects.create_user(username='conversation-user', password='test-password')
		conversation = Conversation.objects.create(user=user, title='Test conversation')
		Message.objects.create(conversation=conversation, role='user', content='Bonjour', prompt_tokens=2)
		self.client.force_login(user)

		response = self.client.get('/chat/api/conversations/')
		self.assertEqual(response.status_code, 200)
		self.assertEqual(response.json()[0]['id'], conversation.id)

		response = self.client.get(f'/chat/api/conversations/{conversation.id}/')
		self.assertEqual(response.status_code, 200)
		self.assertEqual(response.json()['messages'][0]['content'], 'Bonjour')

		response = self.client.delete(f'/chat/api/conversations/{conversation.id}/delete/')
		self.assertEqual(response.status_code, 204)
		self.assertFalse(Conversation.objects.filter(id=conversation.id).exists())

	def test_send_message_rejects_insufficient_credits_before_provider_call(self):
		user = User.objects.create_user(username='poor-chat-user', password='test-password')
		user.credit_account.balance = 0
		user.credit_account.save(update_fields=['balance'])
		provider = APIProvider.objects.create(name='Poor provider', base_url='https://provider.example.test')
		AIModel.objects.create(provider=provider, name='Poor model', model_id='poor-model')
		provider.keys.create(name='server-key', key='secret-not-in-response')
		self.client.force_login(user)

		response = self.client.post('/chat/api/send/', data=json.dumps({
			'model_id': 'poor-model',
			'content': 'Bonjour',
		}), content_type='application/json')

		self.assertEqual(response.status_code, 402)
		self.assertEqual(response.json()['error']['type'], 'insufficient_credits')
		user.credit_account.refresh_from_db()
		self.assertEqual(user.credit_account.balance, 0)

	def test_anonymous_send_message_is_redirected(self):
		response = self.client.post('/chat/api/send/', data='{}', content_type='application/json')
		self.assertRedirects(response, '/auth/?next=/chat/api/send/')

	def test_chat_home_renders(self):
		user = User.objects.create_user(username='chat-user', password='test-password')
		self.client.force_login(user)
		response = self.client.get('/')

		self.assertEqual(response.status_code, 200)
		self.assertContains(response, 'Le Comptoir')

	def test_anonymous_user_is_sent_to_authentication(self):
		response = self.client.get('/')

		self.assertRedirects(response, '/auth/?next=/')

	def test_active_models_are_available_for_selection(self):
		user = User.objects.create_user(username='model-user', password='test-password')
		self.client.force_login(user)
		provider = APIProvider.objects.create(name='Anthropic')
		AIModel.objects.create(
			provider=provider,
			name='Claude Sonnet 4',
			model_id='claude-sonnet-4-6',
		)

		response = self.client.get('/')

		self.assertContains(response, 'Claude Sonnet 4')
		self.assertContains(response, 'value="claude-sonnet-4-6"')

	def test_anonymous_user_has_zero_credits_and_cannot_recharge(self):
		response = self.client.get('/auth/?next=/')

		self.assertEqual(response.status_code, 200)
		self.assertContains(response, 'Créer un compte')

	def test_authenticated_user_sees_account_credits_and_can_recharge(self):
		user = User.objects.create_user(username='credit-user', password='test-password')
		user.credit_account.balance = 25
		user.credit_account.save(update_fields=['balance'])
		self.client.force_login(user)

		response = self.client.get('/')

		self.assertEqual(response.context['initial_credits'], 25)
		self.assertTrue(response.context['is_authenticated'])
		self.assertContains(response, 'id="creditPill"')
		self.assertNotContains(response, 'id="creditPill" type="button" disabled')
		self.assertContains(response, '"isAuthenticated": true')

	def test_login_returns_to_requested_chat_url(self):
		User.objects.create_user(username='return-user', password='test-password')

		response = self.client.post(
			'/login/?next=/',
			{'username': 'return-user', 'password': 'test-password', 'next': '/'},
		)

		self.assertRedirects(response, '/')
