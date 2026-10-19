import json
from decimal import Decimal

from bs4 import BeautifulSoup
from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings

from api.models import AIModel, APIProvider
from chat.models import ChatPreferences


@override_settings(PAYDUNYA_XOF_PER_USD=Decimal('600'), EUR_PER_USD=Decimal('0.92'), DEFAULT_CURRENCY='XOF', STORAGES={
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
            self.assertIn('2\u00a0250 FCFA', row.get_text())
            self.assertNotIn('2.5000', row.get_text())
        self.assertEqual(page.select_one('.model-picker-item')['data-output-price'], '2.5000')
        self.assertIn('270 FCFA', page.select_one('.model-picker-price').get_text())

    def test_chat_team_and_tariffs_use_the_saved_currency_and_include_the_margin_once(self):
        transactions = self.user.credit_account.transactions.count()
        for currency, input_price, output_price in (
            ('XOF', '270 FCFA', '2\u00a0250 FCFA'),
            ('EUR', '0,414 €', '3,45 €'),
            ('USD', '0,45 $', '3,75 $'),
        ):
            with self.subTest(currency=currency):
                session = self.client.session
                session['display_currency'] = currency
                session.save()
                page = BeautifulSoup(self.client.get('/', HTTP_CF_IPCOUNTRY='CI').content, 'html.parser')
                for selector in ('.model-picker-price', '.team-model-copy'):
                    self.assertIn(input_price, page.select_one(selector).get_text())
                    self.assertIn(output_price, page.select_one(selector).get_text())
                tariffs = BeautifulSoup(self.client.get('/tarifs/').content, 'html.parser')
                cells = tariffs.select('tbody td')
                self.assertIn(input_price, cells[0].get_text())
                self.assertIn(output_price, cells[1].get_text())
                self.assertIn('4 500 jetons', cells[0].get_text())
                self.assertIn('37 500 jetons', cells[1].get_text())
        self.assertEqual(self.user.credit_account.transactions.count(), transactions)
        self.model.refresh_from_db()
        self.assertEqual(self.model.output_price_per_million, Decimal('2.5'))

    def test_small_prices_are_not_rounded_to_zero_in_any_currency(self):
        self.model.input_price_per_million = self.model.output_price_per_million = Decimal('0.0001')
        self.model.save(update_fields=['input_price_per_million', 'output_price_per_million'])
        for currency, expected in (('XOF', '0,09 FCFA'), ('EUR', '0,000138 €'), ('USD', '0,00015 $')):
            with self.subTest(currency=currency):
                session = self.client.session
                session['display_currency'] = currency
                session.save()
                page = BeautifulSoup(self.client.get('/').content, 'html.parser')
                self.assertIn(expected, page.select_one('.model-picker-price').get_text())

    @override_settings(PAYDUNYA_XOF_PER_USD=Decimal('700'))
    def test_display_uses_the_configured_conversion_rate(self):
        page = BeautifulSoup(self.client.get('/').content, 'html.parser')
        self.assertIn('315 FCFA', page.select_one('.model-picker-price').get_text())
        self.assertIn('2\u00a0625 FCFA', page.select_one('.model-picker-price').get_text())

    def test_country_detection_applies_to_chat_before_visiting_recharge(self):
        page = BeautifulSoup(self.client.get('/', HTTP_CF_IPCOUNTRY='FR').content, 'html.parser')
        self.assertIn('3,45 €', page.select_one('.model-picker-price').get_text())
        self.assertEqual(self.client.session['display_currency'], 'EUR')
