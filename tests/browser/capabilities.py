"""Capability UX against an isolated database and offline model fixtures."""
import json
import os
from pathlib import Path
import sys
import tempfile
import uuid
from unittest.mock import patch

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

from api.models import AIModel, APIProvider
from bara.models import Workspace
from chat.models import ChatPreferences, Conversation
from chat.test_regressions import tool_stream
from chat.test_tools import tool_call

settings.DATABASES['default'].setdefault('TEST', {})['NAME'] = str(
    Path(tempfile.gettempdir()) / f'2ai-capability-{uuid.uuid4().hex}.sqlite3')
settings.DATABASES['default']['OPTIONS'] = {'timeout': 15, 'init_command': 'PRAGMA journal_mode=WAL;'}


@override_settings(SEARCH_API_KEY='', DEBUG=True)
class CapabilityBrowserTests(StaticLiveServerTestCase):
    def test_text_workspace_block_recovery_and_verified_download(self):
        user = get_user_model().objects.create_user('capability-browser')
        user.credit_account.credit(100_000)
        provider = APIProvider.objects.create(name='Fixture', slug='openrouter', base_url='https://example.test/api')
        provider.keys.create(name='fixture', key='offline-fixture')
        text = AIModel.objects.create(provider=provider, name='Texte seul', model_id='text', supported_parameters=[])
        AIModel.objects.create(provider=provider, name='Avec outils', model_id='tools', supported_parameters=['tools'])
        ChatPreferences.objects.update_or_create(user=user, defaults={'default_model_id': text.model_id})
        workspace = Workspace.objects.create(user=user, active_model=text)
        conversation = Conversation.objects.create(user=user, workspace=workspace, persona=user.personas.first())
        self.client.force_login(user)

        def plain(*args, **kwargs):
            yield {'type': 'delta', 'content': 'Bonjour depuis ton espace.'}
            yield {'type': 'usage', 'input_tokens': 20, 'output_tokens': 10}

        def tools(*args, **kwargs):
            messages = args[3]
            if any(message.get('role') == 'tool' for message in messages):
                yield from tool_stream({'content': 'Le fichier est prêt.'})
            else:
                yield from tool_stream({'tool_calls': [tool_call('write_file', {'filename': 'bilan.txt', 'content': 'Résultat du test'})]})

        errors, sends = [], []
        with tempfile.TemporaryDirectory(prefix='2ai-capability-media-') as root, override_settings(MEDIA_ROOT=root), \
                patch('chat.views.stream_llm_provider', side_effect=plain) as plain_call, \
                patch('chat.file_tools.call_llm_with_tools_stream', side_effect=tools) as tool_call_mock, \
                sync_playwright() as pw:
            browser = pw.chromium.launch(executable_path='/usr/bin/google-chrome', headless=True, args=['--no-sandbox'])
            try:
                context = browser.new_context(viewport={'width': 1280, 'height': 900}, service_workers='block')
                context.add_cookies([{'name': settings.SESSION_COOKIE_NAME,
                    'value': self.client.cookies[settings.SESSION_COOKIE_NAME].value, 'url': self.live_server_url}])
                page = context.new_page()
                page.on('pageerror', lambda error: errors.append(str(error)))
                page.on('request', lambda request: sends.append(request.url) if '/chat/api/send/' in request.url else None)
                page.goto(f'{self.live_server_url}/?conversation={conversation.pk}')
                page.locator('#composerInput').fill('Bonjour')
                page.locator('#sendBtn').click()
                expect(page.locator('#messages .msg.bot')).to_contain_text('Bonjour depuis ton espace.')
                expect(page.locator('#messages .msg.bot')).to_contain_text('contexte textuel')
                expect(page.locator('.msg.bot.streaming')).to_have_count(0)
                self.assertEqual(plain_call.call_count, 1)
                page.locator('#composerInput').fill('Crée un fichier bilan.txt')
                page.locator('#sendBtn').click()
                expect(page.locator('#creditNotice')).to_contain_text('ne prend pas en charge les outils')
                expect(page.locator('#composerInput')).to_have_value('Crée un fichier bilan.txt')
                self.assertEqual(len(sends), 1)
                page.get_by_role('button', name='Choisir Auto 2AI', exact=True).click()
                expect(page.locator('#creditNotice')).to_contain_text('Auto 2AI sélectionné')
                page.locator('#sendBtn').click()
                expect(page.locator('#messages .msg.bot').last).to_contain_text('Le fichier est prêt.')
                expect(page.locator('.msg.bot.streaming')).to_have_count(0)
                download = page.locator('#messages .msg.bot').last.get_by_role('link', name='Télécharger', exact=True)
                expect(download).to_be_visible()
                response = context.request.get(self.live_server_url + download.get_attribute('href'))
                self.assertEqual(response.body(), b'R\xc3\xa9sultat du test')
                self.assertEqual(tool_call_mock.call_count, 2)
                page.reload()
                expect(page.locator('#messages').get_by_role('link', name='Télécharger', exact=True)).to_be_visible()
                page.screenshot(path='/tmp/2ai-capabilities-desktop.png', full_page=True)
                self.assertEqual(errors, [])
            finally:
                browser.close()


if __name__ == '__main__':
    raise SystemExit(bool(DiscoverRunner(verbosity=1, interactive=False).run_tests(['__main__.CapabilityBrowserTests'])))
