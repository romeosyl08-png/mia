"""Optional real-browser integration check, using an isolated test DB and mocked AI.

Run: ../../venv/bin/python tests/browser/comparisons.py
Requires Playwright and a local Chrome installation; no external AI is called.
"""
import os
from pathlib import Path
import sys
import tempfile
import uuid
from concurrent.futures import ThreadPoolExecutor
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
from evaluations import jobs
from evaluations.models import ComparisonSession, EvaluationBudget

if settings.DATABASES['default']['ENGINE'].endswith('sqlite3'):
    # A file with WAL permits the browser's polling thread and the worker to
    # share the test DB. SQLite's shared in-memory mode takes table locks.
    settings.DATABASES['default'].setdefault('TEST', {})['NAME'] = str(Path(tempfile.gettempdir()) / f'2ai-browser-{uuid.uuid4().hex}.sqlite3')
    settings.DATABASES['default']['OPTIONS'] = {'timeout': 15, 'init_command': 'PRAGMA journal_mode=WAL;'}


@override_settings(COMPARISON_ENABLED=True, COMPARISON_AUTOMATIC_ENABLED=True,
    COMPARISON_PROVIDER_ALLOWLIST=['openrouter'], EVALUATION_MIN_READING_SECONDS=0,
    COMPARISON_RATE_LIMIT=100, DEBUG=True)
class ComparisonBrowserTests(StaticLiveServerTestCase):
    def test_manual_surprise_refresh_mobile_theme_and_deletion(self):
        EvaluationBudget.objects.get_or_create(pk=1)
        user = get_user_model().objects.create_user('browser-comparison-user')
        provider = APIProvider.objects.create(name='OpenRouter', slug='openrouter', base_url='https://fixture.example.test')
        provider.keys.create(name='test', key='not-real')
        for i in range(2):
            AIModel.objects.create(provider=provider, name=f'IA de test {i}', model_id=f'fixture/model-{i}',
                                   input_price_per_million=1, output_price_per_million=2)
        self.client.force_login(user)
        errors, posts = [], []
        with ThreadPoolExecutor(max_workers=1) as database, sync_playwright() as playwright:
            browser = playwright.chromium.launch(executable_path='/usr/bin/google-chrome', headless=True, args=['--no-sandbox'])
            try:
                context = browser.new_context(viewport={'width': 1440, 'height': 1000})
                context.add_cookies([{'name': settings.SESSION_COOKIE_NAME,
                    'value': self.client.cookies[settings.SESSION_COOKIE_NAME].value,
                    'url': self.live_server_url}])
                page = context.new_page()
                page.on('pageerror', lambda error: errors.append(str(error)))
                page.on('request', lambda request: posts.append(request.url) if request.method == 'POST' and '/api/sessions/' in request.url else None)
                page.goto(self.live_server_url + '/comparisons/')
                expect(page.locator('#modelB option')).to_have_count(3)
                page.locator('#comparisonForm textarea').fill('Explique les nombres premiers.')
                page.locator('#compareButton').click()
                page.wait_for_url('**/comparisons/?session=*')
                session = database.submit(ComparisonSession.objects.get).result()
                with patch('evaluations.jobs.call_llm_provider', return_value=('Réponse avec un exemple vérifiable.', 20, 10)):
                    database.submit(lambda: jobs.execute(jobs.claim(session.pk))).result()
                page.locator('#voteForm').wait_for(state='visible')
                a = page.locator('.comparison-columns article').nth(0).bounding_box()
                b = page.locator('.comparison-columns article').nth(1).bounding_box()
                self.assertAlmostEqual(a['y'], b['y'], delta=2)
                page.screenshot(path='/tmp/2ai-comparisons-desktop.png', full_page=True)
                page.reload()
                page.locator('#voteForm').wait_for(state='visible')
                self.assertEqual(len(posts), 1)
                page.locator('#voteForm [value="A"]').check()
                page.locator('#voteForm button[type=submit]').click()
                expect(page.locator('#comparisonNotice')).to_contain_text('enregistrée')
                page.locator('#newComparison').click()
                page.locator('[data-mode=automatic]').click()
                page.locator('#comparisonForm textarea').fill('Compare deux méthodes de calcul mental.')
                page.locator('#compareButton').click()
                page.wait_for_url('**/comparisons/?session=*')
                second = database.submit(lambda: ComparisonSession.objects.order_by('-pk').first()).result()
                with patch('evaluations.jobs.call_llm_provider', return_value=('Je suis IA de test 0, via OpenRouter. Voici ma réponse.', 20, 10)):
                    database.submit(lambda: jobs.execute(jobs.claim(second.pk))).result()
                page.locator('#voteForm').wait_for(state='visible')
                self.assertEqual(page.locator('#titleA').inner_text(), 'Modèle A')
                self.assertEqual(page.locator('#manualModels option').count(), 0)
                self.assertNotIn('IA de test', page.locator('#responseA').inner_text())
                response = page.request.get(self.live_server_url + f'/comparisons/api/sessions/{second.pk}/').json()
                self.assertNotIn('model', response['responses'][0])
                page.set_viewport_size({'width': 390, 'height': 844})
                page.evaluate("localStorage.setItem('ai2ai-appearance', JSON.stringify({theme:'dark', accent:'#3F8E8A'}))")
                page.reload()
                page.locator('#voteForm').wait_for(state='visible')
                self.assertEqual(page.locator('html').get_attribute('data-theme'), 'dark')
                dimensions = page.evaluate('({width: innerWidth, scroll: document.documentElement.scrollWidth})')
                self.assertLessEqual(dimensions['scroll'], dimensions['width'] + 1)
                a = page.locator('.comparison-columns article').nth(0).bounding_box()
                b = page.locator('.comparison-columns article').nth(1).bounding_box()
                self.assertGreater(b['y'], a['y'] + a['height'])
                page.screenshot(path='/tmp/2ai-comparisons-mobile-dark.png', full_page=True)
                page.locator('#voteForm [value=tie]').check()
                page.locator('#voteForm button[type=submit]').click()
                expect(page.locator('#titleA')).to_contain_text('IA de test')
                self.assertEqual(len([url for url in posts if url.endswith('/api/sessions/')]), 2)
                page.evaluate('''async id => {
                    const csrf = document.querySelector('[name=csrfmiddlewaretoken]').value;
                    const response = await fetch(`/comparisons/api/sessions/${id}/`, {method:'DELETE', headers:{'X-CSRFToken':csrf}});
                    if (!response.ok) throw new Error('Deletion failed');
                }''', second.pk)
                erased = page.request.get(self.live_server_url + f'/comparisons/api/sessions/{second.pk}/').json()
                self.assertEqual(erased['prompt'], '')
                self.assertFalse(erased['can_vote'])
                self.assertEqual(errors, [])
            finally:
                browser.close()


if __name__ == '__main__':
    raise SystemExit(bool(DiscoverRunner(verbosity=2, interactive=False).run_tests(['__main__.ComparisonBrowserTests'])))
