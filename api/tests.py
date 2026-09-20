import json
from unittest.mock import MagicMock, patch

from django.test import TestCase, override_settings
from django.core.cache import cache

from api.models import AIModel, APIKey, APIProvider


class OpenAICompatibleAPITests(TestCase):
	def setUp(self):
		cache.clear()
		self.provider = APIProvider.objects.create(
			name='OpenAI compatible',
			base_url='https://provider.example.test',
		)
		self.model = AIModel.objects.create(
			provider=self.provider,
			name='Test model',
			model_id='test-model',
		)
		APIKey.objects.create(provider=self.provider, name='test', key='provider-secret')

	@override_settings(DEBUG=True, API_COMPATIBLE_KEY='client-secret')
	def test_api_home_is_available(self):
		response = self.client.get('/api/')

		self.assertEqual(response.status_code, 200)
		self.assertContains(response, 'POST /v1/chat/completions')

	@override_settings(DEBUG=True, API_COMPATIBLE_KEY='client-secret')
	def test_models_requires_bearer_key(self):
		response = self.client.get('/v1/models')
		self.assertEqual(response.status_code, 401)

		response = self.client.get('/v1/models', HTTP_AUTHORIZATION='Bearer client-secret')

		self.assertEqual(response.status_code, 200)
		self.assertEqual(response.json()['data'][0]['id'], 'test-model')

	@override_settings(DEBUG=True, API_COMPATIBLE_KEY='')
	def test_api_requires_a_configured_key_even_in_debug(self):
		response = self.client.get('/v1/models')

		self.assertEqual(response.status_code, 401)

	@override_settings(DEBUG=True, API_COMPATIBLE_KEY='client-secret')
	@patch('api.views.urlopen')
	def test_chat_completion_returns_openai_shape(self, urlopen):
		provider_response = MagicMock()
		provider_response.__enter__.return_value.read.return_value = json.dumps({
			'id': 'provider-response',
			'object': 'chat.completion',
			'choices': [{
				'index': 0,
				'message': {'role': 'assistant', 'content': 'Bonjour.'},
				'finish_reason': 'stop',
			}],
		}).encode()
		urlopen.return_value = provider_response

		response = self.client.post(
			'/v1/chat/completions',
			data=json.dumps({
				'model': 'test-model',
				'messages': [{'role': 'user', 'content': 'Bonjour'}],
			}),
			content_type='application/json',
			HTTP_AUTHORIZATION='Bearer client-secret',
		)

		self.assertEqual(response.status_code, 200)
		self.assertEqual(response.json()['choices'][0]['message']['content'], 'Bonjour.')
		self.assertEqual(urlopen.call_args.args[0].full_url, 'https://provider.example.test/v1/chat/completions')

	@override_settings(
		DEBUG=True,
		API_COMPATIBLE_KEY='client-secret',
		API_RATE_LIMIT_REQUESTS=1,
		API_RATE_LIMIT_WINDOW_SECONDS=60,
	)
	def test_models_rate_limit_is_enforced(self):
		response = self.client.get('/v1/models', HTTP_AUTHORIZATION='Bearer client-secret')
		self.assertEqual(response.status_code, 200)

		response = self.client.get('/v1/models', HTTP_AUTHORIZATION='Bearer client-secret')
		self.assertEqual(response.status_code, 429)
		self.assertEqual(response.json()['error']['code'], 'rate_limit_exceeded')

# Create your tests here.
