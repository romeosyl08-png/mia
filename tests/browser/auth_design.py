"""Preview the authentication design and exercise real forms on an isolated DB."""
import os
from pathlib import Path
import re
import sys
import tempfile
import uuid
from urllib.parse import parse_qs, urlparse

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
os.environ['DJANGO_SETTINGS_MODULE'] = 'evaluations.test_settings'
import django
django.setup()

from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.staticfiles.testing import StaticLiveServerTestCase
from django.test import override_settings
from django.test.runner import DiscoverRunner
from playwright.sync_api import expect, sync_playwright

settings.DATABASES['default'].setdefault('TEST', {})['NAME'] = str(
    Path(tempfile.gettempdir()) / f'2ai-auth-design-{uuid.uuid4().hex}.sqlite3')
settings.DATABASES['default']['OPTIONS'] = {'timeout': 15, 'init_command': 'PRAGMA journal_mode=WAL;'}


@override_settings(
    DEBUG=True, SEARCH_API_KEY='', PUBLIC_BASE_URL='',
    GOOGLE_CLIENT_ID='auth-design-fixture', GOOGLE_CLIENT_SECRET='auth-design-fixture',
    TELEGRAM_BOT_TOKEN='auth-design-fixture', TELEGRAM_BOT_USERNAME='Fixture2AIBot',
)
class AuthDesignBrowserTests(StaticLiveServerTestCase):
    def test_desktop_mobile_themes_passwords_errors_signup_and_login(self):
        password = 'Auth!Dusk-River-726'
        get_user_model().objects.create_user('auth-existing', email='existing@example.test', password=password)
        errors = []
        url = self.live_server_url + '/auth/?next=/bara/'
        with sync_playwright() as pw:
            browser = pw.chromium.launch(executable_path='/usr/bin/google-chrome', headless=True,
                                         args=['--no-sandbox'])
            try:
                context = browser.new_context(viewport={'width': 1440, 'height': 1000})
                context.route('https://telegram.org/js/**', lambda route: route.fulfill(
                    status=200, content_type='application/javascript', body=''))
                page = context.new_page()
                page.on('pageerror', lambda error: errors.append(str(error)))
                page.goto(url)
                expect(page.locator('.auth-story')).to_be_visible()
                expect(page.locator('#panelLogin')).to_be_visible()
                expect(page.locator('#panelSignup')).to_be_hidden()
                expect(page.locator('.errorlist')).to_have_count(0)
                page.screenshot(path='/tmp/2ai-auth-login-desktop.png', full_page=True, animations='disabled')
                self.assertLessEqual(page.evaluate('document.documentElement.scrollWidth'), 1440)
                page.get_by_role('link', name='Mot de passe oublié ?').click()
                expect(page).to_have_url(re.compile('/password/reset/$'))
                page.goto(url)
                page.locator('#id_login_username').fill('existing@example.test')
                page.locator('#id_login_password').fill('incorrect')
                page.locator('#panelLogin .submit-btn').click()
                expect(page.locator('#panelLogin [role=alert]')).to_contain_text('Identifiants incorrects')
                expect(page.locator('#id_login_username')).to_have_value('existing@example.test')
                expect(page.locator('#tabLogin')).to_have_attribute('role', 'tab')
                page.locator('#tabLogin').focus()
                page.keyboard.press('ArrowRight')
                expect(page.locator('#panelSignup')).to_be_visible()
                page.locator('#id_signup_username').fill('auth-new-design')
                page.locator('#id_signup_email').fill('new-design@example.test')
                page.locator('#id_signup_password1').fill('x')
                page.locator('#id_signup_password2').fill('x')
                page.locator('#panelSignup .submit-btn').click()
                expect(page.locator('#panelSignup [role=alert]')).to_be_visible()
                expect(page.locator('#panelSignup')).to_be_visible()
                expect(page.locator('#tabSignup')).to_have_attribute('aria-selected', 'true')
                expect(page.locator('#id_signup_username')).to_have_value('auth-new-design')
                page.goto(url)
                page.locator('#tabSignup').click()
                page.screenshot(path='/tmp/2ai-auth-signup-desktop.png', full_page=True, animations='disabled')
                page.locator('[data-password-target=id_signup_password1]').click()
                expect(page.locator('#id_signup_password1')).to_have_attribute('type', 'text')
                page.locator('[data-password-target=id_signup_password1]').click()
                expect(page.locator('#id_signup_password1')).to_have_attribute('type', 'password')
                page.locator('#panelSignup .auth-other-options summary').click()
                page.locator('#waToggleSignup').click()
                expect(page.locator('#waPanelSignup')).to_contain_text('indisponible')
                expect(page.locator('#telegram-login-wrap-signup .telegram-fallback')).to_be_visible(timeout=6500)
                self.assertEqual(parse_qs(urlparse(page.locator('#panelSignup a.google').get_attribute('href')).query)['next'], ['/bara/'])
                page.evaluate("localStorage.setItem('ai2ai-appearance', JSON.stringify({theme:'dark', accent:'#6C6FCB'}))")
                page.goto(url)
                expect(page.locator('html')).to_have_attribute('data-theme', 'dark')
                page.screenshot(path='/tmp/2ai-auth-login-dark.png', full_page=True, animations='disabled')
                for width in (768, 390, 320):
                    page.set_viewport_size({'width': width, 'height': 844})
                    expect(page.locator('.auth-story')).to_be_hidden()
                    self.assertLessEqual(page.evaluate('document.documentElement.scrollWidth'), width + 1)
                    page.locator('#tabSignup').click()
                    page.locator('#panelSignup .submit-btn').scroll_into_view_if_needed()
                    expect(page.locator('#panelSignup .submit-btn')).to_be_in_viewport()
                    self.assertLessEqual(page.evaluate('document.documentElement.scrollWidth'), width + 1)
                    page.locator('#tabLogin').click()
                page.set_viewport_size({'width': 390, 'height': 844})
                page.screenshot(path='/tmp/2ai-auth-login-mobile-dark.png', full_page=True, animations='disabled')
                page.locator('#tabSignup').click()
                page.screenshot(path='/tmp/2ai-auth-signup-mobile-dark.png', full_page=True, animations='disabled')
                page.evaluate("localStorage.setItem('ai2ai-appearance', JSON.stringify({theme:'light', accent:'#3F8E8A'}))")
                page.goto(url)
                expect(page.locator('html')).to_have_attribute('data-theme', 'light')
                page.locator('#tabSignup').click()
                page.screenshot(path='/tmp/2ai-auth-signup-mobile.png', full_page=True, animations='disabled')
                page.locator('#id_signup_username').fill('auth-new-design')
                page.locator('#id_signup_email').fill('new-design@example.test')
                page.locator('#id_signup_password1').fill(password)
                page.locator('#id_signup_password2').fill(password)
                page.locator('#panelSignup .submit-btn').click()
                expect(page).to_have_url(self.live_server_url + '/bara/')
                context.clear_cookies()
                page.goto(url)
                page.locator('#id_login_username').fill('new-design@example.test')
                page.locator('#id_login_password').fill(password)
                page.locator('#panelLogin .submit-btn').click()
                expect(page).to_have_url(self.live_server_url + '/bara/')
                self.assertEqual(errors, [])
            finally:
                browser.close()
        self.assertTrue(get_user_model().objects.filter(username='auth-new-design', email='new-design@example.test').exists())


if __name__ == '__main__':
    raise SystemExit(bool(DiscoverRunner(verbosity=1, interactive=False).run_tests(['__main__.AuthDesignBrowserTests'])))
