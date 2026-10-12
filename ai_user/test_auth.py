import hashlib
import hmac
import time
from urllib.parse import parse_qs, urlparse

from bs4 import BeautifulSoup
from django.contrib.auth.models import User
from django.test import TestCase, override_settings
from unittest.mock import patch

from ai_user.models import TelegramIdentity

TEST_STATIC_STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
}


class SignupValidationTests(TestCase):
    @override_settings(
        SECURE_SSL_REDIRECT=False,
        STORAGES=TEST_STATIC_STORAGES,
        GOOGLE_CLIENT_ID='google-client-test',
        GOOGLE_CLIENT_SECRET='google-secret-test',
        GOOGLE_REDIRECT_URI='https://mia-vkl1.onrender.com/accounts/google/login/callback/',
        PUBLIC_BASE_URL='https://mia-vkl1.onrender.com',
        TELEGRAM_BOT_TOKEN='telegram-token-test',
        TELEGRAM_BOT_USERNAME='Test2AIBot',
    )
    def test_social_login_urls_use_the_configured_public_origin(self):
        page = self.client.get('/auth/?next=/bara/')
        self.assertEqual(
            page.context['telegram_auth_url'],
            'https://mia-vkl1.onrender.com/telegram/auth/?next=%2Fbara%2F',
        )

        google = self.client.get('/auth/google/?next=/bara/')
        query = parse_qs(urlparse(google['Location']).query)
        self.assertEqual(
            query['redirect_uri'],
            ['https://mia-vkl1.onrender.com/accounts/google/login/callback/'],
        )

    @override_settings(
        SECURE_SSL_REDIRECT=False,
        STORAGES=TEST_STATIC_STORAGES,
        GOOGLE_CLIENT_ID='google-client-test',
        GOOGLE_CLIENT_SECRET='google-secret-test',
        GOOGLE_REDIRECT_URI='',
        PUBLIC_BASE_URL='',
    )
    def test_local_google_redirect_matches_the_authorized_localhost_uri(self):
        response = self.client.get('/auth/google/', HTTP_HOST='localhost:8000')

        query = parse_qs(urlparse(response['Location']).query)
        self.assertEqual(
            query['redirect_uri'],
            ['http://localhost:8000/accounts/google/login/callback/'],
        )

    @override_settings(
        SECURE_SSL_REDIRECT=False,
        STORAGES=TEST_STATIC_STORAGES,
        TELEGRAM_BOT_TOKEN='telegram-token-test',
    )
    def test_valid_telegram_payload_creates_a_session(self):
        payload = {
            'id': '123456789',
            'auth_date': str(int(time.time())),
            'first_name': 'Compte test',
            'username': 'telegram_test_user',
        }
        data = '\n'.join(f'{key}={payload[key]}' for key in sorted(payload))
        secret = hashlib.sha256(b'telegram-token-test').digest()
        payload['hash'] = hmac.new(secret, data.encode(), hashlib.sha256).hexdigest()
        payload['next'] = '/bara/'

        response = self.client.get('/telegram/auth/', payload)

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response['Location'], '/bara/')
        identity = TelegramIdentity.objects.get(telegram_id=123456789)
        self.assertEqual(int(self.client.session['_auth_user_id']), identity.user_id)

    @override_settings(SECURE_SSL_REDIRECT=False, STORAGES=TEST_STATIC_STORAGES)
    def test_auth_page_has_password_visibility_and_whatsapp_unavailable_message(self):
        response = self.client.get('/auth/')

        self.assertContains(response, 'data-password-target="id_login_password"')
        self.assertContains(response, 'data-password-target="id_signup_password1"')
        self.assertContains(response, 'data-password-target="id_signup_password2"')
        self.assertContains(response, 'La connexion WhatsApp est indisponible pour le moment.', count=4)

    def test_login_accepts_email_case_insensitively(self):
        user = User.objects.create_user('email-login', email='qa@example.test', password='P7!cobalt-forest-V4')
        response = self.client.post('/login/', {'username': ' QA@EXAMPLE.TEST ', 'password': 'P7!cobalt-forest-V4'})
        self.assertEqual(response.status_code, 302)
        self.assertEqual(int(self.client.session['_auth_user_id']), user.pk)

    def test_ambiguous_email_and_inactive_accounts_cannot_log_in(self):
        for name in ['email-first', 'email-second']:
            User.objects.create_user(name, email='duplicate@example.test', password='P7!cobalt-forest-V4')
        response = self.client.post('/login/', {'username': 'duplicate@example.test', 'password': 'P7!cobalt-forest-V4'})
        self.assertContains(response, 'Identifiants incorrects')
        self.assertNotIn('_auth_user_id', self.client.session)
        User.objects.create_user('inactive-email', email='inactive@example.test', password='P7!cobalt-forest-V4', is_active=False)
        response = self.client.post('/login/', {'username': 'inactive@example.test', 'password': 'P7!cobalt-forest-V4'})
        self.assertContains(response, 'Identifiants incorrects')
        self.assertNotIn('_auth_user_id', self.client.session)

    def test_login_errors_only_appear_after_an_unsuccessful_submission(self):
        response = self.client.get('/login/')
        self.assertNotContains(response, 'Identifiants incorrects')
        response = self.client.post('/login/', {'username': 'unknown', 'password': 'wrong'})
        self.assertContains(response, 'Identifiants incorrects')

    def signup(self, **values):
        payload = {'username': 'new-account', 'email': 'new@example.test', 'password1': 'P7!cobalt-forest-V4', 'password2': 'P7!cobalt-forest-V4'}
        payload.update(values)
        return self.client.post('/signup/', payload)

    def test_weak_password_is_rejected_and_signup_errors_stay_visible(self):
        response = self.signup(password1='x', password2='x')
        self.assertEqual(response.status_code, 200)
        self.assertFalse(User.objects.filter(username='new-account').exists())
        self.assertTrue(response.context['signup_form'].errors['password2'])
        soup = BeautifulSoup(response.content, 'html.parser')
        self.assertEqual(soup.select_one('#tabSignup').get('class'), ['active'])
        self.assertNotIn('display:none', soup.select_one('#panelSignup').get('style', ''))
        self.assertEqual(soup.select_one('#id_signup_username')['value'], 'new-account')

    def test_invalid_email_is_rejected(self):
        response = self.signup(email='not-an-email')
        self.assertEqual(response.status_code, 200)
        self.assertIn('email', response.context['signup_form'].errors)
        self.assertFalse(User.objects.filter(username='new-account').exists())

    def test_valid_signup_creates_account_and_honors_safe_next_url(self):
        response = self.signup(next='/bara/')
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response['Location'], '/bara/')
        user = User.objects.get(username='new-account')
        self.assertTrue(user.check_password('P7!cobalt-forest-V4'))
        self.assertEqual(int(self.client.session['_auth_user_id']), user.pk)
        self.assertIsNotNone(user.credit_account)

    def test_duplicate_username_is_rejected(self):
        User.objects.create_user('new-account')
        response = self.signup()
        self.assertEqual(response.status_code, 200)
        self.assertIn('username', response.context['signup_form'].errors)
        self.assertEqual(User.objects.filter(username='new-account').count(), 1)

    @patch('chat.persona_context.ensure_big_persona', side_effect=RuntimeError('Persona setup failed'))
    def test_failed_account_setup_does_not_leave_a_partial_user(self, ensure_big_persona):
        from chat.models import Persona
        from ai_user.models import CreditAccount
        with self.assertRaises(RuntimeError):
            self.signup()
        self.assertFalse(User.objects.filter(username='new-account').exists())
        self.assertFalse(CreditAccount.objects.exists())
        self.assertFalse(Persona.objects.exists())
