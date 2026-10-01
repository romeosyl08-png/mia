from django.contrib.auth.models import User
from django.core.cache import cache
from django.test import TestCase
from unittest.mock import MagicMock, patch
from http.client import RemoteDisconnected
from bs4 import BeautifulSoup
import json
from urllib.error import HTTPError

from api.models import AIModel, APIProvider
from chat.services import ProviderError, stream_llm_provider
from .models import Conversation, Message, Persona


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

	def test_new_users_receive_default_personas(self):
		user = User.objects.create_user(username='persona-user', password='test-password')

		self.assertEqual(user.personas.count(), 3)
		self.assertEqual(user.personas.first().name, 'Assistant général')

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
			data=json.dumps({'prompt': 'Nouveau prompt', 'temp': 0.3}),
			content_type='application/json',
		)
		self.assertEqual(response.status_code, 200)
		self.assertEqual(response.json()['prompt'], 'Nouveau prompt')

		response = self.client.patch(
			f'/chat/api/personas/{other_persona.id}/',
			data=json.dumps({'name': 'Interdit'}),
			content_type='application/json',
		)
		self.assertEqual(response.status_code, 404)

		response = self.client.delete(f'/chat/api/personas/{created_id}/')
		self.assertEqual(response.status_code, 204)
		self.assertFalse(Persona.objects.filter(id=created_id).exists())

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
		provider_response = MagicMock()
		provider_response.__enter__.return_value.read.return_value = json.dumps({
			'id': 'provider-response',
			'object': 'chat.completion',
			'choices': [{'index': 0, 'message': {'role': 'assistant', 'content': 'Bonjour.'}, 'finish_reason': 'stop'}],
		}).encode()
		urlopen.return_value = provider_response
		self.client.force_login(user)

		response = self.client.post('/chat/api/send/', data=json.dumps({
			'model_id': 'backend-model',
			'content': 'Bonjour',
		}), content_type='application/json')

		self.assertEqual(response.status_code, 200)
		self.assertEqual(response.json()['reply'], 'Bonjour.')
		provider_request = urlopen.call_args.args[0]
		self.assertNotIn('balance', json.loads(provider_request.data))
		user.credit_account.refresh_from_db()
		self.assertEqual(response.json()['remaining_balance'], user.credit_account.balance)
		self.assertLess(user.credit_account.balance, 1000)
		self.assertNotContains(response, 'server-secret')
		self.assertIsNotNone(response.json()['conversation_id'])
		self.assertEqual(response.json()['title'], 'Bonjour')
		self.assertEqual(Message.objects.filter(role='user').count(), 1)
		self.assertEqual(Message.objects.filter(role='assistant').count(), 1)

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
