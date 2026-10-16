import json

from bs4 import BeautifulSoup
from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings

from api.models import AIModel, APIProvider
from chat.models import ChatPreferences


@override_settings(STORAGES={
    'default': {'BACKEND': 'django.core.files.storage.FileSystemStorage'},
    'staticfiles': {'BACKEND': 'django.contrib.staticfiles.storage.StaticFilesStorage'},
})
class ModelFavoriteTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user('favorite-owner')
        self.other = get_user_model().objects.create_user('favorite-other')
        self.client.force_login(self.user)
        provider = APIProvider.objects.create(name='Favorite provider', slug='openrouter')
        self.model = AIModel.objects.create(
            provider=provider, name='Favorite model', model_id='favorite-model',
            input_price_per_million='0.3000', output_price_per_million='2.5000',
        )

    def update(self, **payload):
        return self.client.patch('/chat/api/preferences/', json.dumps(payload), content_type='application/json')

    def test_add_remove_and_reload_preserve_account_favorites_without_changing_model_or_balance(self):
        preferences = ChatPreferences.objects.create(user=self.user, default_model_id=self.model.model_id, max_tokens=800)
        transactions = self.user.credit_account.transactions.count()
        for favorite, expected in ((True, [self.model.model_id]), (True, [self.model.model_id]), (False, [])):
            response = self.update(favorite_model_id=self.model.model_id, favorite=favorite)
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json()['favorite_model_ids'], expected)
            self.assertEqual(self.client.get('/chat/api/preferences/').json()['favorite_model_ids'], expected)
            preferences.refresh_from_db()
            self.assertEqual(preferences.default_model_id, self.model.model_id)
            self.assertEqual(preferences.max_tokens, 800)
        self.assertEqual(self.user.credit_account.transactions.count(), transactions)

    def test_favorites_are_private_to_each_account(self):
        self.update(favorite_model_id=self.model.model_id, favorite=True)
        self.client.force_login(self.other)
        self.assertEqual(self.client.get('/chat/api/preferences/').json()['favorite_model_ids'], [])
        self.assertEqual(self.client.get('/').context['chat_config']['favoriteModelIds'], [])
        self.client.force_login(self.user)
        self.assertEqual(self.client.get('/').context['chat_config']['favoriteModelIds'], [self.model.model_id])

    def test_invalid_or_unavailable_favorites_are_rejected_without_altering_preferences(self):
        for payload in ({'favorite_model_id': []}, {'favorite_model_id': self.model.model_id, 'favorite': 'true'}, {'favorite_model_id': 'unknown', 'favorite': True}):
            self.assertEqual(self.update(**payload).status_code, 400)
        self.update(favorite_model_id=self.model.model_id, favorite=True)
        self.model.is_active = False
        self.model.save(update_fields=['is_active'])
        self.assertEqual(self.client.get('/').context['chat_config']['favoriteModelIds'], [])
        self.assertEqual(self.update(favorite_model_id=self.model.model_id, favorite=False).json()['favorite_model_ids'], [])

    def test_generation_preferences_do_not_reset_saved_favorites(self):
        self.update(favorite_model_id=self.model.model_id, favorite=True)
        response = self.update(max_tokens=1200)
        self.assertEqual(response.json()['favorite_model_ids'], [self.model.model_id])
        self.assertEqual(response.json()['max_tokens'], 1200)

    def test_stars_are_separate_buttons_and_prices_use_readable_decimal_amounts(self):
        page = BeautifulSoup(self.client.get('/').content, 'html.parser')
        for selector in ('.model-picker-row', '.team-model-row'):
            row = page.select_one(selector)
            self.assertEqual(len(row.select('button')), 2)
            star = row.select_one('[data-model-favorite]')
            self.assertEqual(star['aria-pressed'], 'false')
            self.assertEqual(star['data-model-favorite'], self.model.model_id)
            self.assertIsNone(star.find_parent('button'))
            self.assertIn('2,50 $', row.get_text())
            self.assertNotIn('2.5000', row.get_text())
        self.assertEqual(page.select_one('.model-picker-item')['data-output-price'], '2.5000')
        self.assertIn('0,30 $', page.select_one('.model-picker-price').get_text())
