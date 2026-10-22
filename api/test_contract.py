"""Contrat de l'API publique /v1 : une requête refusée ne coûte rien et n'atteint jamais le fournisseur."""
import json
from unittest.mock import patch

from django.contrib.auth.models import User
from django.core.cache import cache
from django.test import TestCase, override_settings

from ai_user.models import UserAPIKey
from api.models import AIModel, APIKey, APIProvider
from api.tests import UPSTREAM_OK, fake_response
from api.views import MAX_BODY_BYTES, MAX_MESSAGES, MAX_OUTPUT_TOKENS

USER = {'role': 'user', 'content': 'Salut'}
TOOL = {'type': 'function', 'function': {'name': 'read_file', 'parameters': {'type': 'object', 'properties': {}}}}


class ApiContractTests(TestCase):
    def setUp(self):
        cache.clear()
        self.user = User.objects.create_user('contract', password='x')
        self.account = self.user.credit_account
        self.account.credit(100_000, reason='adjustment', note='test')
        provider = APIProvider.objects.create(name='Fake', slug='fakeprovider', base_url='https://fake.example')
        key = APIKey(provider=provider, name='k')
        key.set_key('secret')
        key.save()
        AIModel.objects.create(provider=provider, name='Fake', model_id='fake-model',
                               input_price_per_million=1, output_price_per_million=2)
        self.token, prefix, key_hash = UserAPIKey.generate()
        self.api_key = UserAPIKey.objects.create(user=self.user, name='t', key_prefix=prefix, key_hash=key_hash)

    def post(self, body, token=None, raw=None):
        return self.client.post('/v1/chat/completions', data=raw if raw is not None else json.dumps(body),
                                content_type='application/json', HTTP_AUTHORIZATION=f'Bearer {token or self.token}')

    def balance(self):
        self.account.refresh_from_db()
        return self.account.balance

    def assert_refused_for_free(self, response, status, error_type=None):
        self.assertEqual(response.status_code, status, response.content[:300])
        if error_type:
            self.assertEqual(response.json()['error']['type'], error_type)
        self.assertEqual(self.balance(), self.start)
        self.assertEqual(self.account.transactions.count(), self.transactions)

    def test_invalid_requests_are_refused_without_charge_or_provider_call(self):
        many = [USER] * (MAX_MESSAGES + 1)
        cases = {
            'corps trop gros': (None, 'x' * (MAX_BODY_BYTES + 1)),
            'JSON illisible': (None, '{pas du json'),
            'JSON non objet': (None, '[1, 2]'),
            'sans messages': ({'model': 'fake-model'}, None),
            'trop de messages': ({'model': 'fake-model', 'messages': many}, None),
            'rôle inconnu': ({'model': 'fake-model', 'messages': [{'role': 'admin', 'content': 'x'}]}, None),
            'aucun message utilisateur': ({'model': 'fake-model', 'messages': [{'role': 'system', 'content': 'x'}]}, None),
            'image': ({'model': 'fake-model', 'messages': [{'role': 'user', 'content': [
                {'type': 'image_url', 'image_url': {'url': 'https://x'}}]}]}, None),
            'outil sans identifiant': ({'model': 'fake-model', 'messages': [USER, {'role': 'tool', 'content': 'r'}]}, None),
            'arguments non JSON': ({'model': 'fake-model', 'messages': [USER, {'role': 'assistant', 'tool_calls': [
                {'id': '1', 'function': {'name': 'f', 'arguments': '{'}}]}]}, None),
            'arguments non objet': ({'model': 'fake-model', 'messages': [USER, {'role': 'assistant', 'tool_calls': [
                {'id': '1', 'function': {'name': 'f', 'arguments': '[]'}}]}]}, None),
            'max_tokens texte': ({'model': 'fake-model', 'messages': [USER], 'max_tokens': 'beaucoup'}, None),
            'température texte': ({'model': 'fake-model', 'messages': [USER], 'temperature': 'chaude'}, None),
            'outil mal nommé': ({'model': 'fake-model', 'messages': [USER], 'tools': [
                {'type': 'function', 'function': {'name': 'rm -rf', 'parameters': {'type': 'object'}}}]}, None),
            'outil non fonction': ({'model': 'fake-model', 'messages': [USER], 'tools': [{'type': 'retrieval'}]}, None),
            'trop d’outils': ({'model': 'fake-model', 'messages': [USER], 'tools': [TOOL] * 17}, None),
            'outils en flux': ({'model': 'fake-model', 'messages': [USER], 'tools': [TOOL], 'stream': True}, None),
        }
        self.start, self.transactions = self.balance(), self.account.transactions.count()
        with patch('api.views.urlopen') as upstream:
            for label, (body, raw) in cases.items():
                with self.subTest(label):
                    self.assert_refused_for_free(self.post(body, raw=raw), 400, 'invalid_request_error')
        upstream.assert_not_called()

    def test_unknown_model_is_404_without_charge(self):
        self.start, self.transactions = self.balance(), self.account.transactions.count()
        with patch('api.views.urlopen') as upstream:
            self.assert_refused_for_free(self.post({'model': 'absent', 'messages': [USER]}), 404, 'model_not_found')
        upstream.assert_not_called()

    def test_revoked_key_and_inactive_account_are_rejected(self):
        self.start, self.transactions = self.balance(), self.account.transactions.count()
        self.api_key.is_active = False
        self.api_key.save()
        self.assert_refused_for_free(self.post({'model': 'fake-model', 'messages': [USER]}), 401, 'invalid_api_key')
        self.api_key.is_active = True
        self.api_key.save()
        self.user.is_active = False
        self.user.save()
        self.assert_refused_for_free(self.post({'model': 'fake-model', 'messages': [USER]}), 401, 'invalid_api_key')

    def test_missing_or_malformed_authorization_is_rejected(self):
        for header in ('', 'Basic abc', 'Bearer '):
            with self.subTest(header=header):
                response = self.client.post('/v1/chat/completions', data='{}', content_type='application/json',
                                            HTTP_AUTHORIZATION=header)
                self.assertEqual(response.status_code, 401)

    def test_only_post_is_accepted(self):
        response = self.client.get('/v1/chat/completions', HTTP_AUTHORIZATION=f'Bearer {self.token}')
        self.assertEqual(response.status_code, 405)

    @override_settings(API_RATE_LIMIT_REQUESTS=2, API_RATE_LIMIT_WINDOW_SECONDS=60)
    def test_rate_limit_is_per_key_and_says_when_to_retry(self):
        with patch('api.views.urlopen', return_value=fake_response(UPSTREAM_OK)):
            statuses = [self.post({'model': 'fake-model', 'messages': [USER]}).status_code for _ in range(3)]
        self.assertEqual(statuses, [200, 200, 429])
        self.start, self.transactions = self.balance(), self.account.transactions.count()
        limited = self.post({'model': 'fake-model', 'messages': [USER]})
        self.assert_refused_for_free(limited, 429, 'rate_limit_exceeded')
        self.assertTrue(int(limited['Retry-After']) > 0)
        other, prefix, key_hash = UserAPIKey.generate()
        UserAPIKey.objects.create(user=self.user, name='autre', key_prefix=prefix, key_hash=key_hash)
        with patch('api.views.urlopen', return_value=fake_response(UPSTREAM_OK)):
            self.assertEqual(self.post({'model': 'fake-model', 'messages': [USER]}, token=other).status_code, 200)

    def test_parameters_are_clamped_before_reaching_the_provider(self):
        with patch('api.views.urlopen', return_value=fake_response(UPSTREAM_OK)) as upstream:
            response = self.post({'model': 'fake-model', 'messages': [USER], 'max_tokens': 10 ** 9,
                                  'temperature': 9, 'top_p': -1, 'stop': ['a', 'b', 'c', 'd', 'e']})
        self.assertEqual(response.status_code, 200)
        sent = json.loads(upstream.call_args.args[0].data)
        self.assertEqual((sent['max_tokens'], sent['temperature'], sent['top_p'], len(sent['stop'])),
                         (MAX_OUTPUT_TOKENS, 2.0, 0.0, 4))

    def test_successful_call_reports_usage_and_writes_one_net_charge(self):
        start = self.balance()
        with patch('api.views.urlopen', return_value=fake_response(UPSTREAM_OK)):
            body = self.post({'model': 'fake-model', 'messages': [USER]}).json()
        charged = body['usage']['credits_charged']
        self.assertEqual(start - self.balance(), charged)
        self.assertEqual(body['usage']['remaining_balance'], self.balance())
        self.assertEqual((body['usage']['prompt_tokens'], body['usage']['completion_tokens']), (12, 5))
        # Réserve débitée puis surplus rendu : le total des mouvements égale le coût réel.
        self.assertEqual(sum(self.account.transactions.exclude(note='test').values_list('amount', flat=True)), -charged)
