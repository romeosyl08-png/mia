from bs4 import BeautifulSoup
from django.contrib.auth.models import User
from django.test import TestCase


class SignupValidationTests(TestCase):
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
