from pathlib import Path

from bs4 import BeautifulSoup
from django.conf import settings
from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse

from api.models import AIModel, APIProvider
from bara.models import Workspace


@override_settings(
    STORAGES={
        'default': {'BACKEND': 'django.core.files.storage.FileSystemStorage'},
        'staticfiles': {'BACKEND': 'django.contrib.staticfiles.storage.StaticFilesStorage'},
    },
)
class FrontendAssetTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = get_user_model().objects.create_user('frontend-assets')
        provider = APIProvider.objects.create(name='Frontend', slug='openrouter')
        model = AIModel.objects.create(provider=provider, name='Test model', model_id='frontend-test')
        cls.workspace = Workspace.objects.create(user=cls.user, title='Frontend workspace', active_model=model)

    def page(self, url):
        response = self.client.get(url, follow=True)
        self.assertEqual(response.status_code, 200)
        return BeautifulSoup(response.content, 'html.parser')

    def test_content_pages_have_a_small_asset_budget_and_no_chat_runtime(self):
        self.client.force_login(self.user)
        for name in ['login', 'tariffs-page', 'recharge-page', 'api-keys-page', 'api-home', 'chat:about', 'chat:legal', 'chat:memory']:
            with self.subTest(page=name):
                page = self.page(reverse(name))
                scripts = [script['src'] for script in page.select('script[src]')]
                self.assertFalse(any('/script.js' in src or '/chat_tools.js' in src or '/marked/' in src for src in scripts))
                self.assertNotIn('fonts.googleapis.com', str(page))
                self.assertTrue(page.body.has_attr('data-keep-title'))
                assets = scripts + [link['href'] for link in page.select('link[rel="stylesheet"]')]
                total_bytes = sum(
                    (Path(settings.BASE_DIR) / url.split('?')[0].lstrip('/')).stat().st_size
                    for url in assets if url.startswith('/static/')
                )
                self.assertLess(total_bytes, 45_000, f'{name}: {total_bytes} bytes')
                for script in page.select('script[src]'):
                    if '/theme.js' not in script['src'] and not script.has_attr('async'):
                        self.assertTrue(script.has_attr('defer'), script['src'])

    def test_chat_dependencies_load_once_and_before_the_entry_point(self):
        self.client.force_login(self.user)
        page = self.page(reverse('chat:chat-home'))
        scripts = [script['src'].split('?')[0] for script in page.select('script[src]')]
        self.assertEqual(len(scripts), len(set(scripts)))
        self.assertIsNotNone(page.select_one('#chat-config'))
        self.assertIsNotNone(page.select_one('aside.sidebar #pwa-install-controls'))
        self.assertEqual(len(page.select('#pwa-install-controls')), 1)
        for dependency in ['appearance.js', 'chat_transport.js', 'message_sources.js', 'charts.js', 'chat_tools.js', 'model_favorites.js']:
            self.assertLess(scripts.index('/static/app/js/' + dependency), scripts.index('/static/app/js/script.js'))
        self.assertFalse(page.body.has_attr('data-keep-title'))

    def test_temporary_chat_starts_without_memory_and_exposes_separate_actions(self):
        self.client.force_login(self.user)
        page = self.page(reverse('chat:chat-home'))
        controls = page.select_one('#temporaryChatControls')
        self.assertIsNotNone(controls)
        self.assertIsNotNone(controls.select_one('#temporaryPrivacySummary'))
        self.assertEqual(controls.select_one('#temporaryMemoryPersona option')['value'], '')
        self.assertTrue(controls.select_one('#temporaryMemoryControl').has_attr('hidden'))
        self.assertTrue(controls.select_one('#temporarySaveControl').has_attr('hidden'))
        self.assertEqual(controls.select_one('#temporaryMemoryBtn')['aria-expanded'], 'false')
        self.assertEqual(controls.select_one('#temporarySaveBtn')['aria-expanded'], 'false')

    def test_workspace_index_skips_markdown_and_detail_retains_its_dependencies(self):
        self.client.force_login(self.user)
        index = self.page(reverse('bara:home'))
        self.assertNotIn('marked.min.js', str(index))
        detail = self.page(reverse('bara:detail', args=[self.workspace.pk]))
        scripts = [script['src'].split('?')[0] for script in detail.select('script[src]')]
        self.assertEqual(len(scripts), len(set(scripts)))
        self.assertEqual(sum('marked.min.js' in src for src in scripts), 1)
        self.assertIn('/static/app/js/charts.js', scripts)
        self.assertIn('/static/app/js/workspace.js', scripts)
        self.assertNotIn('/static/app/js/script.js', scripts)
        self.assertEqual(len(detail.select('#pwa-install-controls')), 1)
        conversation = self.workspace.conversations.get()
        self.assertEqual(detail.select_one('#agent-form')['action'], reverse('bara:message', args=[self.workspace.pk, conversation.pk]))

    def test_tasks_retain_charts_without_the_chat_runtime(self):
        self.client.force_login(self.user)
        page = self.page(reverse('bara:tasks', args=[self.workspace.pk]))
        scripts = [script['src'].split('?')[0] for script in page.select('script[src]')]
        self.assertIn('/static/app/js/charts.js', scripts)
        self.assertIn('/static/app/js/tasks.js', scripts)
        self.assertNotIn('/static/app/js/script.js', scripts)
        self.assertEqual(page.select_one('[data-tasks-root]')['data-tasks-root'], reverse('bara:tasks', args=[self.workspace.pk]))
