from unittest.mock import patch

from django.contrib.auth.models import User
from django.core.cache import cache
from django.test import TestCase

from ai_user.models import UserAPIKey
from api.models import AIModel, APIProvider
from chat.models import ChatPreferences


class BarrassonsAppearanceTests(TestCase):
    def setUp(self):
        cache.clear()
        self.user = User.objects.create_user('appearance-user', password='x')
        self.other = User.objects.create_user('appearance-other', password='x')
        self.token, prefix, digest = UserAPIKey.generate()
        self.key = UserAPIKey.objects.create(user=self.user, name='test', key_prefix=prefix, key_hash=digest)

    def get(self, path='/v1/appearance'):
        return self.client.get(path, HTTP_AUTHORIZATION='Bearer ' + self.token)

    def test_appearance_is_account_scoped_fresh_and_contains_only_visual_settings(self):
        own = self.user.personas.get(is_big_persona=True)
        own.appearance_theme, own.appearance_accent = 'dark', '#3F8E8A'
        own.memory, own.prompt = 'PRIVATE MEMORY', 'PRIVATE INSTRUCTIONS'
        own.save()
        foreign = self.other.personas.get(is_big_persona=True)
        foreign.appearance_accent = '#B4483A'
        foreign.save()
        before = self.user.credit_account.balance
        response = self.get()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {'theme': 'dark', 'accent': '#3F8E8A'})
        self.assertIn('no-store', response['Cache-Control'])
        own.appearance_theme = 'light'
        own.save(update_fields=['appearance_theme'])
        self.assertEqual(self.get().json()['theme'], 'light')
        self.user.credit_account.refresh_from_db()
        self.assertEqual(self.user.credit_account.balance, before)

    def test_key_is_required_and_revocation_takes_effect(self):
        self.client.force_login(self.user)
        self.assertEqual(self.client.get('/v1/appearance').status_code, 401)
        self.key.is_active = False
        self.key.save(update_fields=['is_active'])
        self.assertEqual(self.get().status_code, 401)

    @patch('api.views.eligible_models', return_value=[])
    def test_default_model_respects_preference_and_excludes_incompatible_tools(self, _eligible):
        provider = APIProvider.objects.create(name='Test', slug='appearance-test', base_url='https://example.test')
        compatible = AIModel.objects.create(provider=provider, name='Tools', model_id='tools', supported_parameters=['tools'])
        AIModel.objects.create(provider=provider, name='Text', model_id='text', supported_parameters=[])
        preferences, _ = ChatPreferences.objects.get_or_create(user=self.user)
        preferences.default_model_id = 'tools'
        preferences.save()
        response = self.get('/v1/models').json()
        self.assertEqual(response['default_model'], compatible.model_id)
        self.assertEqual({row['id']: row['supports_tools'] for row in response['data']}, {'tools': True, 'text': False})
        preferences.default_model_id = 'text'
        preferences.save()
        self.assertEqual(self.get('/v1/models').json()['default_model'], 'tools')
