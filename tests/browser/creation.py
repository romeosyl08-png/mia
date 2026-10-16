"""Browser QA for Creation, using isolated data and mocked paid providers."""
import os
from pathlib import Path
import re
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
from django.core.files.storage import FileSystemStorage
from django.test import override_settings
from django.test.runner import DiscoverRunner
from PIL import Image
from playwright.sync_api import expect, sync_playwright

from api.models import AIModel, APIProvider
from bara.models import WorkFile
from chat.models import Conversation, GeneratedMedia, Message
from chat.services import ProviderError
from studio import media
from studio.models import Asset

if settings.DATABASES['default']['ENGINE'].endswith('sqlite3'):
    settings.DATABASES['default'].setdefault('TEST', {})['NAME'] = str(Path(tempfile.gettempdir()) / f'2ai-creation-{uuid.uuid4().hex}.sqlite3')
    settings.DATABASES['default']['OPTIONS'] = {'timeout': 15, 'init_command': 'PRAGMA journal_mode=WAL;'}


@override_settings(SEARCH_API_KEY='', DEBUG=True)
class CreationBrowserTests(StaticLiveServerTestCase):
    def test_creation_library_chat_studio_and_mobile(self):
        with tempfile.TemporaryDirectory(prefix='2ai-creation-browser-') as folder:
            for model, field in [(GeneratedMedia, 'image'), (WorkFile, 'file'), (Asset, 'file')]:
                patcher = patch.object(model._meta.get_field(field), 'storage', FileSystemStorage(location=folder))
                patcher.start()
                self.addCleanup(patcher.stop)
            user = get_user_model().objects.create_user('creation-browser')
            provider = APIProvider.objects.create(name='OpenRouter', slug='openrouter')
            provider.keys.create(name='fixture', key='never-call-real-api')
            AIModel.objects.create(provider=provider, name='Vision test', model_id='vision-test', input_modalities=['text', 'image'])
            conversation = Conversation.objects.create(user=user, persona=user.personas.first(), title='Idée archivée', is_archived=True)
            message = Message.objects.create(conversation=conversation, role='user', content='Une affiche pour un festival à Abidjan')
            self.client.force_login(user)
            png = media.png(Image.new('RGB', (640, 480), '#6c6fcb'))
            errors, calls = [], []
            def generate(*args):
                calls.append(args)
                return png, 'image/png', None
            with patch('chat.views.generate_openrouter_media', side_effect=generate) as upstream, sync_playwright() as pw:
                browser = pw.chromium.launch(executable_path='/usr/bin/google-chrome', headless=True, args=['--no-sandbox'])
                try:
                    context = browser.new_context(viewport={'width':1440, 'height':1000})
                    context.add_cookies([{'name': settings.SESSION_COOKIE_NAME,
                        'value': self.client.cookies[settings.SESSION_COOKIE_NAME].value, 'url': self.live_server_url}])
                    page = context.new_page()
                    page.on('pageerror', lambda error: errors.append(str(error)))
                    page.goto(f'{self.live_server_url}/?conversation={conversation.pk}')
                    expect(page.locator('#chatTitle')).to_have_text('Idée archivée')
                    expect(page.locator('#imageModeBtn, #videoModeBtn, #audioModeBtn, #imageGallery')).to_have_count(0)
                    expect(page.locator('#messages .msg.user')).to_have_count(1)
                    page.get_by_role('button', name='Créer à partir de ce message', exact=True).click()
                    expect(page).to_have_url(re.compile('/creation/\\?message='))
                    expect(page.locator('#creationPrompt')).to_have_value(message.content)
                    page.screenshot(path='/tmp/2ai-creation-desktop.png', full_page=True)
                    self.assertEqual(upstream.call_count, 0)
                    page.locator('[name=kind][value=video]').check()
                    expect(page.locator('[data-kind-options=video]')).to_be_visible()
                    expect(page.locator('[data-kind-options=image]')).to_be_hidden()
                    page.locator('[name=kind][value=audio]').check()
                    expect(page.locator('#creationSubmit')).to_be_disabled()
                    page.locator('[name=kind][value=image]').check()
                    page.locator('#creationImageSize').select_option('1024x1536')
                    page.locator('#creationSubmit').click()
                    expect(page.locator('#creationStatus')).to_have_text('Création enregistrée dans ta médiathèque.')
                    expect(page.locator('#creationPreview img')).to_be_visible()
                    self.assertEqual(upstream.call_count, 1)
                    self.assertEqual(calls[0][4], {'image_size':'1024x1536'})
                    page.get_by_role('link', name='Retrouver toutes mes créations').click()
                    expect(page.locator('.creation-media')).to_have_count(1)
                    expect(page.get_by_role('link', name='Conversation d’origine')).to_be_visible()
                    page.locator('#librarySearch').fill('inexistant')
                    page.get_by_role('button', name='Rechercher', exact=True).click()
                    expect(page.locator('#libraryEmptyTitle')).to_have_text('Aucun média ne correspond')
                    page.locator('#librarySearch').fill('Abidjan')
                    page.get_by_role('button', name='Rechercher', exact=True).click()
                    expect(page.locator('.creation-media')).to_have_count(1)
                    page.get_by_role('button', name='Réutiliser la description', exact=True).click()
                    expect(page.locator('#creationPrompt')).to_have_value(message.content)
                    self.assertEqual(upstream.call_count, 1)
                    # A provider rejection preserves the draft, updates balance and creates no fake result.
                    upstream.side_effect = ProviderError('Le format demandé a été refusé.')
                    page.locator('#creationSubmit').click()
                    expect(page.locator('#creationStatus')).to_have_text('Le format demandé a été refusé.')
                    expect(page.locator('#creationPrompt')).to_have_value(message.content)
                    expect(page.locator('#creationSubmit')).to_be_enabled()
                    expect(page.locator('#creationResultActions')).to_be_hidden()
                    page.goto(self.live_server_url + '/creation/library/')
                    expect(page.locator('.creation-media')).to_have_count(1)
                    page.get_by_role('button', name='Utiliser dans une conversation', exact=True).click()
                    expect(page.locator('#imageAttachmentBar img')).to_be_visible()
                    expect(page.locator('#composerInput')).to_have_value('Que peux-tu me dire de cette image ?')
                    expect(page.locator('#workspaceSummary')).to_contain_text('Média')
                    self.assertEqual(upstream.call_count, 2)
                    page.goto(self.live_server_url + '/creation/library/')
                    expect(page.locator('.creation-media')).to_have_count(1)
                    page.get_by_role('button', name='Utiliser dans le Studio', exact=True).click()
                    expect(page.locator('#creationStudioDialog')).to_be_visible()
                    page.locator('#creationStudioSubmit').click()
                    expect(page.locator('#studio')).to_be_visible()
                    expect(page.locator('#studio-library .studio-asset')).to_have_count(1)
                    page.goto(self.live_server_url + '/creation/library/')
                    expect(page.locator('.creation-media')).to_have_count(2)
                    page.set_viewport_size({'width':390, 'height':844})
                    page.evaluate("localStorage.setItem('ai2ai-appearance', JSON.stringify({theme:'dark', accent:'#3F8E8A'}))")
                    page.reload()
                    expect(page.locator('html')).to_have_attribute('data-theme', 'dark')
                    expect(page.locator('.creation-media')).to_have_count(2)
                    self.assertLessEqual(page.evaluate('document.documentElement.scrollWidth'), 391)
                    page.screenshot(path='/tmp/2ai-creation-library-mobile-dark.png', full_page=True)
                    page.goto(self.live_server_url + '/creation/')
                    expect(page.locator('#creationSubmit')).to_be_visible()
                    self.assertLessEqual(page.evaluate('document.documentElement.scrollWidth'), 391)
                    page.screenshot(path='/tmp/2ai-creation-mobile-dark.png', full_page=True)
                    page.set_viewport_size({'width':320, 'height':740})
                    self.assertLessEqual(page.evaluate('document.documentElement.scrollWidth'), 321)
                    expect(page.locator('[name=kind][value=video]')).to_be_attached()
                    self.assertEqual(errors, [])
                finally:
                    browser.close()
            self.assertEqual(Conversation.objects.filter(user=user).count(), 2)
            self.assertEqual(Message.objects.filter(conversation=conversation).count(), 1)
            self.assertEqual(GeneratedMedia.objects.filter(owner=user).count(), 1)


if __name__ == '__main__':
    raise SystemExit(bool(DiscoverRunner(verbosity=1, interactive=False).run_tests(['__main__.CreationBrowserTests'])))
