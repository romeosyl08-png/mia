from django.contrib.auth.models import User
from django.test import TestCase
from unittest.mock import MagicMock, patch
import json

from .models import AIModel, APIProvider, Conversation, Message, Persona


class ChatHomeTests(TestCase):
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
		user.credit_account.refresh_from_db()
		self.assertEqual(response.json()['remaining_balance'], user.credit_account.balance)
		self.assertLess(user.credit_account.balance, 1000)
		self.assertNotContains(response, 'server-secret')
		self.assertIsNotNone(response.json()['conversation_id'])
		self.assertEqual(response.json()['title'], 'Bonjour')
		self.assertEqual(Message.objects.filter(role='user').count(), 1)
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
		self.assertContains(response, 'id="rechargeBtn"')
		self.assertNotContains(response, 'id="rechargeBtn" type="button" disabled')
		self.assertContains(response, 'isAuthenticated: "true" === "true"')

	def test_login_returns_to_requested_chat_url(self):
		User.objects.create_user(username='return-user', password='test-password')

		response = self.client.post(
			'/login/?next=/',
			{'username': 'return-user', 'password': 'test-password', 'next': '/'},
		)

		self.assertRedirects(response, '/')
