import hashlib
import hmac
import random
import time
from urllib.parse import parse_qs, urlparse

from bs4 import BeautifulSoup
from django.contrib.auth.models import User
from django.core import mail
from django.core.cache import cache
from django.test import TestCase, override_settings
from django.urls import reverse
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

    def setUp(self):
        # Le plafond d'inscription vit dans le cache, partagé entre les tests.
        cache.clear()

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


@override_settings(
    SECURE_SSL_REDIRECT=False,
    STORAGES=TEST_STATIC_STORAGES,
    EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend",
)
class AccountRecoveryTests(TestCase):
    """Déconnexion, changement et réinitialisation du mot de passe."""

    def setUp(self):
        self.user = User.objects.create_user(
            "account-owner", email="owner@example.test", password="P7!cobalt-forest-V4"
        )

    def test_logout_requires_post_and_closes_the_session(self):
        self.client.force_login(self.user)
        # Depuis Django 5, une déconnexion en GET n'est plus acceptée.
        self.assertEqual(self.client.get("/logout/").status_code, 405)
        self.assertIn("_auth_user_id", self.client.session)

        response = self.client.post("/logout/")
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response["Location"], reverse("auth-page"))
        self.assertNotIn("_auth_user_id", self.client.session)

    def test_password_change_updates_the_credentials(self):
        self.client.force_login(self.user)
        self.assertEqual(self.client.get("/password/change/").status_code, 200)
        response = self.client.post(
            "/password/change/",
            {
                "old_password": "P7!cobalt-forest-V4",
                "new_password1": "N3!w-quartz-harbour-X9",
                "new_password2": "N3!w-quartz-harbour-X9",
            },
        )
        self.assertEqual(response.status_code, 302)
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password("N3!w-quartz-harbour-X9"))

    def test_password_change_rejects_a_wrong_current_password(self):
        self.client.force_login(self.user)
        response = self.client.post(
            "/password/change/",
            {
                "old_password": "wrong-password",
                "new_password1": "N3!w-quartz-harbour-X9",
                "new_password2": "N3!w-quartz-harbour-X9",
            },
        )
        self.assertEqual(response.status_code, 200)
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password("P7!cobalt-forest-V4"))

    def test_password_reset_emails_a_link_that_sets_a_new_password(self):
        response = self.client.post("/password/reset/", {"email": "owner@example.test"})
        self.assertEqual(response.status_code, 302)
        self.assertEqual(len(mail.outbox), 1)

        body = mail.outbox[0].body
        link = next(line.strip() for line in body.splitlines() if "/password/reset/" in line)
        path = urlparse(link).path
        confirm = self.client.get(path, follow=True)
        self.assertEqual(confirm.status_code, 200)
        self.assertTrue(confirm.context["validlink"])
        # Django remplace le jeton de l'URL par "set-password" pour éviter
        # qu'il fuite dans un en-tête Referer : la saisie se fait sur cette URL.
        final_path = confirm.redirect_chain[-1][0] if confirm.redirect_chain else path

        response = self.client.post(
            final_path,
            {"new_password1": "R3set!granite-bay-Z2", "new_password2": "R3set!granite-bay-Z2"},
        )
        self.assertEqual(response.status_code, 302)
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password("R3set!granite-bay-Z2"))

    def test_password_reset_does_not_disclose_existing_accounts(self):
        response = self.client.post("/password/reset/", {"email": "nobody@example.test"}, follow=True)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(mail.outbox), 0)

    def test_password_reset_survives_an_unconfigured_mail_server(self):
        with patch(
            "django.contrib.auth.forms.PasswordResetForm.save",
            side_effect=OSError("Connexion refusée"),
        ):
            response = self.client.post("/password/reset/", {"email": "owner@example.test"})
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "le serveur de messagerie n’est pas")

    def test_google_or_telegram_accounts_are_offered_to_set_a_password(self):
        social_user = User.objects.create_user("social-account", email="social@example.test")
        social_user.set_unusable_password()
        social_user.save()
        self.client.force_login(social_user)
        page = self.client.get("/customize/")
        self.assertContains(page, "Définir un mot de passe")


@override_settings(
    SECURE_SSL_REDIRECT=False,
    STORAGES=TEST_STATIC_STORAGES,
    AUTH_MAX_FAILURES=3,
    TRUST_X_FORWARDED_FOR=False,
)
class BruteForceProtectionTests(TestCase):
    def setUp(self):
        cache.clear()
        self.password = "P7!cobalt-forest-V4"
        self.user = User.objects.create_user("brute-target", password=self.password)

    def login(self, username, password, **extra):
        return self.client.post("/login/", {"username": username, "password": password}, **extra)

    def test_repeated_failures_are_blocked_then_released(self):
        for _ in range(3):
            self.assertEqual(self.login("brute-target", "wrong-password").status_code, 200)

        blocked = self.login("brute-target", self.password)
        self.assertEqual(blocked.status_code, 429)
        self.assertContains(blocked, "Trop de tentatives", status_code=429)

        cache.clear()
        response = self.login("brute-target", self.password)
        self.assertEqual(response.status_code, 302)

    def test_the_lockout_is_scoped_to_the_identifier(self):
        other = User.objects.create_user("brute-other", password=self.password)
        for _ in range(3):
            self.login("brute-target", "wrong-password")
        response = self.login("brute-other", self.password)
        self.assertEqual(response.status_code, 302)
        self.assertEqual(int(self.client.session["_auth_user_id"]), other.pk)

    def test_a_spoofed_forwarded_for_header_does_not_reset_the_counter(self):
        # Sans TRUST_X_FORWARDED_FOR, l'en-tête est ignoré : changer d'IP
        # déclarée ne remet pas le compteur à zéro.
        for _ in range(3):
            self.login(
                "brute-target",
                "wrong-password",
                HTTP_X_FORWARDED_FOR=f"10.0.0.{random.randint(1, 250)}",
            )
        self.assertEqual(
            self.login("brute-target", self.password, HTTP_X_FORWARDED_FOR="10.0.0.99").status_code,
            429,
        )


@override_settings(SECURE_SSL_REDIRECT=False, STORAGES=TEST_STATIC_STORAGES, SIGNUP_MAX_PER_WINDOW=2)
class SignupAbuseTests(TestCase):
    def setUp(self):
        cache.clear()

    def signup(self, username, email):
        return self.client.post(
            "/signup/",
            {
                "username": username,
                "email": email,
                "password1": "P7!cobalt-forest-V4",
                "password2": "P7!cobalt-forest-V4",
            },
        )

    def test_signup_is_capped_per_address(self):
        self.assertEqual(self.signup("creator-1", "one@example.test").status_code, 302)
        self.assertEqual(self.signup("creator-2", "two@example.test").status_code, 302)
        blocked = self.signup("creator-3", "three@example.test")
        self.assertEqual(blocked.status_code, 429)
        self.assertFalse(User.objects.filter(username="creator-3").exists())

    def test_email_must_be_unique_regardless_of_case(self):
        User.objects.create_user("existing", email="taken@example.test")
        response = self.signup("fresh-account", "TAKEN@example.test")
        self.assertEqual(response.status_code, 200)
        self.assertFalse(User.objects.filter(username="fresh-account").exists())
        self.assertContains(response, "déjà utilisée")

    def test_email_is_stored_normalized(self):
        self.assertEqual(self.signup("normalize-me", "MiXeD@Example.TEST").status_code, 302)
        self.assertEqual(User.objects.get(username="normalize-me").email, "mixed@example.test")
