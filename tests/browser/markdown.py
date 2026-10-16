"""Exercise Rust Markdown in the real chat, without paid provider calls."""
import json
import os
from pathlib import Path
import sys
import tempfile
import time
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

settings.DATABASES['default'].setdefault('TEST', {})['NAME'] = str(
    Path(tempfile.gettempdir()) / f'2ai-markdown-{uuid.uuid4().hex}.sqlite3'
)
settings.DATABASES['default']['OPTIONS'] = {'timeout': 15, 'init_command': 'PRAGMA journal_mode=WAL;'}


@override_settings(SEARCH_API_KEY='', DEBUG=True)
class MarkdownBrowserTests(StaticLiveServerTestCase):
    def test_streaming_rust_rendering_sanitization_and_blocked_wasm_fallback(self):
        user = get_user_model().objects.create_user('markdown-browser')
        provider = APIProvider.objects.create(name='OpenRouter', slug='openrouter', base_url='https://fixture.example.test')
        provider.keys.create(name='fixture', key='not-real')
        AIModel.objects.create(provider=provider, name='Test', model_id='markdown-browser-model',
            supported_parameters=[], input_price_per_million=1, output_price_per_million=1)
        self.client.force_login(user)
        paragraph = 'Été 🐘 : une réponse **importante**, avec du `code` et une explication.\n\n'
        preview = {'kind': 'comparison', 'title': 'Aperçu préservé',
            'models': [{'name': 'A', 'text': 'Premier.'}, {'name': 'B', 'text': 'Second.'}]}
        ending = '\n| A | B |\n|---|---|\n| 1 | 2 |\n\n- [x] Vérifié\n\n'
        ending += '```2ai-preview\n' + json.dumps(preview) + '\n```\n\n'
        ending += '<img src="data:image/png;base64," onerror="window.markdownXss=1">\n\n'
        ending += '[Lien dangereux](javascript:alert(1))\n'

        def upstream(*args, **kwargs):
            yield {'type': 'delta', 'content': '# Réponse longue\n\n' + paragraph * 150}
            for _ in range(10):
                time.sleep(.15)
                yield {'type': 'delta', 'content': paragraph * 10}
            yield {'type': 'delta', 'content': ending}
            yield {'type': 'usage', 'input_tokens': 100, 'output_tokens': 100}

        errors, wasm_requests = [], []
        with patch('chat.views.stream_llm_provider', side_effect=upstream), sync_playwright() as pw:
            browser = pw.chromium.launch(executable_path='/usr/bin/google-chrome', headless=True, args=['--no-sandbox'])
            try:
                context = browser.new_context(service_workers='block')
                context.add_cookies([{'name': settings.SESSION_COOKIE_NAME,
                    'value': self.client.cookies[settings.SESSION_COOKIE_NAME].value, 'url': self.live_server_url}])
                page = context.new_page()
                page.on('pageerror', lambda error: errors.append(str(error)))
                page.on('request', lambda request: wasm_requests.append(request.url) if '.wasm' in request.url else None)
                page.goto(self.live_server_url + '/')
                expect(page.locator('#personaTopSelect option')).not_to_have_count(0)
                self.assertEqual(wasm_requests, [])
                page.evaluate('''() => {
                    window.rustMarkdownUses = 0;
                    const parse = AI2AIMarkdown.parse;
                    AI2AIMarkdown.parse = text => {
                        if (text.length >= 8192 && AI2AIMarkdown.backend === 'rust') window.rustMarkdownUses++;
                        return parse(text);
                    };
                }''')
                page.locator('#composerInput').fill('Écris une longue explication structurée.')
                page.locator('#sendBtn').click()
                expect(page.locator('#messages .idea-preview')).to_be_visible()
                expect(page.locator('.msg.bot.streaming')).to_have_count(0)
                if not page.evaluate('AI2AIMarkdown.load()'):
                    self.fail(page.evaluate('''async () => {
                    try {
                        const url = document.querySelector('script[data-wasm-url]').dataset.wasmUrl;
                        const response = await fetch(url);
                        await WebAssembly.compile(await response.arrayBuffer());
                        return 'Wasm compiled; loader rejected it';
                    } catch (error) { return String(error); }
                    }'''))
                self.assertGreater(page.evaluate('window.rustMarkdownUses'), 0, page.evaluate('AI2AIMarkdown.backend'))
                self.assertEqual(len(wasm_requests), 1)
                self.assertEqual(page.evaluate('AI2AIMarkdown.backend'), 'rust')
                content = page.locator('#messages .msg.bot .msg-content')
                expect(content.locator('h1')).to_have_text('Réponse longue')
                expect(content.locator('table')).to_have_count(1)
                expect(content.locator('input[type=checkbox]')).to_have_count(1)
                expect(content.locator('[onerror], a[href^="javascript:"]')).to_have_count(0)
                self.assertFalse(page.evaluate('Boolean(window.markdownXss)'))
                page.route('**/*.wasm', lambda route: route.abort())
                page.reload()
                expect(page.locator('#messages .idea-preview')).to_be_visible()
                self.assertFalse(page.evaluate('AI2AIMarkdown.load()'))
                self.assertEqual(page.evaluate('AI2AIMarkdown.backend'), 'javascript')
                expect(page.locator('#messages .msg.bot table')).to_have_count(1)
                self.assertEqual(errors, [])
            finally:
                browser.close()


if __name__ == '__main__':
    raise SystemExit(bool(DiscoverRunner(verbosity=1, interactive=False).run_tests(['__main__.MarkdownBrowserTests'])))
