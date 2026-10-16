"""Optional browser QA for inline ideas and shared links, without paid AI calls."""
import json
import os
import re
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
from chat.billing import credits_for_usage

if settings.DATABASES['default']['ENGINE'].endswith('sqlite3'):
    settings.DATABASES['default'].setdefault('TEST', {})['NAME'] = str(Path(tempfile.gettempdir()) / f'2ai-ideas-{uuid.uuid4().hex}.sqlite3')
    settings.DATABASES['default']['OPTIONS'] = {'timeout': 15, 'init_command': 'PRAGMA journal_mode=WAL;'}


@override_settings(SEARCH_API_KEY='', DEBUG=True)
class IdeaBrowserTests(StaticLiveServerTestCase):
    def test_inline_preview_survives_reload_local_controls_do_not_bill_and_reader_is_editable(self):
        user = get_user_model().objects.create_user('ideas-browser')
        provider = APIProvider.objects.create(name='OpenRouter', slug='openrouter', base_url='https://fixture.example.test')
        provider.keys.create(name='fixture', key='not-real')
        model = AIModel.objects.create(provider=provider, name='Test', model_id='ideas-browser-model',
            supported_parameters=[], input_price_per_million=1, output_price_per_million=1)
        self.client.force_login(user)
        spec = {'kind':'comparison', 'title':'Comparaison IA — aperçu du fonctionnement',
            'models':[{'name':'GPT démo','text':'Première proposition.'}, {'name':'Claude démo','text':'Deuxième proposition.'},
                      {'name':'Mistral démo','text':'<img src=x onerror="window.previewXss=1"> Texte seulement.'}]}
        reply = 'Voici une démonstration.\n```2ai-preview\n' + json.dumps(spec) + '\n```'
        def upstream(*args, **kwargs):
            yield {'type':'delta', 'content':reply}
            yield {'type':'usage', 'input_tokens':100, 'output_tokens':40}
        shared = {'url':'https://chatgpt.com/share/11111111-2222-4333-8444-555555555555',
                  'title':'Idée de test', 'content':'Une idée lisible et modifiable.', 'message_count':2, 'truncated':False,
                  'source_type':'chatgpt_shared_conversation'}
        errors, chat_posts = [], []
        with patch('chat.views.stream_llm_provider', side_effect=upstream) as ai, \
                patch('chat.shared_link_views.fetch_url', return_value=shared) as reader, sync_playwright() as pw:
            browser = pw.chromium.launch(executable_path='/usr/bin/google-chrome', headless=True, args=['--no-sandbox'])
            try:
                context = browser.new_context(viewport={'width':1440,'height':1000})
                context.add_cookies([{'name':settings.SESSION_COOKIE_NAME,
                    'value':self.client.cookies[settings.SESSION_COOKIE_NAME].value, 'url':self.live_server_url}])
                page = context.new_page(); page.on('pageerror', lambda error: errors.append(str(error)))
                page.on('request', lambda r: chat_posts.append(r.url) if r.method == 'POST' and r.url.endswith('/chat/api/send/') else None)
                page.goto(self.live_server_url + '/')
                expect(page.locator('#personaTopSelect option')).not_to_have_count(0)
                page.locator('#attachBtn').click(); page.locator('#ideaPreviewBtn').click()
                expect(page.locator('#composerInput')).to_have_value('Crée un aperçu interactif pour cette idée : ')
                page.locator('#composerInput').fill('Crée un aperçu interactif pour comparer trois idées.')
                page.locator('#sendBtn').click()
                widget = page.locator('#messages .idea-preview')
                expect(widget).to_be_visible(); expect(page.locator('.msg.bot.streaming')).to_have_count(0)
                expect(widget.locator('select')).to_have_count(2)
                widget.locator('select').first.select_option('2')
                expect(widget.locator('img')).to_have_count(0)
                expect(widget.locator('.idea-preview-response').first).to_contain_text('<img')
                widget.get_by_role('button', name='Surprise', exact=True).click()
                expect(widget.locator('.idea-preview-response h4').first).to_have_text('Modèle A')
                widget.get_by_role('button', name='Je préfère A', exact=True).click()
                expect(widget.locator('.idea-preview-status')).to_contain_text('Aucun vote réel enregistré')
                self.assertEqual(ai.call_count, 1)
                page.screenshot(path='/tmp/2ai-ideas-chat-desktop.png', full_page=True)
                page.reload(); expect(page.locator('#messages .idea-preview')).to_be_visible()
                self.assertEqual(len(chat_posts), 1)
                page.locator('#attachBtn').click(); page.locator('#sharedLinkBtn').click()
                expect(page.locator('#sharedLinkDialog')).to_be_visible()
                page.locator('#sharedLinkUrl').fill(shared['url'] + '?ogimg=plain')
                page.locator('#readSharedLink').click()
                expect(page.locator('#sharedLinkContent')).to_have_value(shared['content'])
                page.locator('#sharedLinkContent').fill('Texte relu et corrigé avant envoi.')
                page.locator('#insertSharedLink').click()
                expect(page.locator('#composerInput')).to_have_value(re.compile('> Texte relu et corrigé avant envoi.'))
                self.assertEqual(reader.call_count, 1); self.assertEqual(ai.call_count, 1)
                page.goto(self.live_server_url + '/chat/ideas/')
                expect(page.locator('.idea-preview')).to_have_count(3)
                prototype = page.locator('.idea-preview').nth(1)
                prototype.get_by_role('button', name='Voir un exemple', exact=True).click()
                expect(prototype.locator('.idea-preview-screen h4')).to_have_text('Essayer')
                calculator = page.locator('.idea-preview').nth(2)
                calculator.locator('input[type=range]').fill('20')
                expect(calculator.locator('output')).to_contain_text('100')
                self.assertEqual(ai.call_count, 1)
                page.set_viewport_size({'width':390,'height':844})
                page.evaluate("localStorage.setItem('ai2ai-appearance',JSON.stringify({theme:'dark',accent:'#3F8E8A'}))")
                page.reload(); expect(page.locator('html')).to_have_attribute('data-theme', 'dark')
                dimensions = page.evaluate('({width:innerWidth,scroll:document.documentElement.scrollWidth})')
                self.assertLessEqual(dimensions['scroll'], dimensions['width'] + 1)
                page.screenshot(path='/tmp/2ai-ideas-examples-mobile-dark.png', full_page=True)
                self.assertEqual(errors, [])
            finally:
                browser.close()
        user.credit_account.refresh_from_db()
        self.assertEqual(user.credit_account.balance, 20000 - credits_for_usage(model, 100, 40))


if __name__ == '__main__':
    raise SystemExit(bool(DiscoverRunner(verbosity=1, interactive=False).run_tests(['__main__.IdeaBrowserTests'])))
