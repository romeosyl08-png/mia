"""Exercise Bara's conversation tabs against an isolated database, without AI calls."""
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
from django.test import Client, override_settings
from django.test.runner import DiscoverRunner
from playwright.sync_api import expect, sync_playwright

from bara.models import WorkConversation, WorkMessage, Workspace, WorkspaceMember

settings.DATABASES['default'].setdefault('TEST', {})['NAME'] = str(
    Path(tempfile.gettempdir()) / f'2ai-bara-tabs-{uuid.uuid4().hex}.sqlite3')
settings.DATABASES['default']['OPTIONS'] = {'timeout': 15, 'init_command': 'PRAGMA journal_mode=WAL;'}


@override_settings(SEARCH_API_KEY='', DEBUG=True)
class BaraTabBrowserTests(StaticLiveServerTestCase):
    def test_creation_closing_reopening_persistence_and_responsive_layout(self):
        owner = get_user_model().objects.create_user('bara-tab-browser')
        member = get_user_model().objects.create_user('bara-tab-member')
        workspace = Workspace.objects.create(user=owner, title='Atelier de développement')
        WorkspaceMember.objects.create(workspace=workspace, user=member, role='editor')
        first = WorkConversation.objects.create(workspace=workspace, title='Plan du projet')
        WorkConversation.objects.create(workspace=workspace, title='Notes techniques')
        WorkConversation.objects.create(workspace=workspace, title='Budget de production')
        original_ids = list(workspace.conversations.values_list('pk', flat=True))
        WorkMessage.objects.create(workspace=workspace, conversation=first, role='user',
                                   content='Conserve le cahier des charges du projet.')
        self.client.force_login(owner)
        owner_cookie = self.client.cookies[settings.SESSION_COOKIE_NAME].value
        member_client = Client()
        member_client.force_login(member)
        member_cookie = member_client.cookies[settings.SESSION_COOKIE_NAME].value
        errors, deletes = [], []
        first_url = f'{self.live_server_url}/bara/{workspace.pk}/c/{first.pk}/'
        # Playwright's sync driver runs an event loop; this isolated test uses
        # synchronous ORM checks between browser actions against its own file DB.
        with patch.dict(os.environ, {'DJANGO_ALLOW_ASYNC_UNSAFE': 'true'}), sync_playwright() as pw:
            browser = pw.chromium.launch(executable_path='/usr/bin/google-chrome', headless=True,
                                         args=['--no-sandbox'])
            context = browser.new_context(viewport={'width': 1440, 'height': 1000})
            context.add_cookies([{'name': settings.SESSION_COOKIE_NAME, 'value': owner_cookie,
                                 'url': self.live_server_url}])
            page = context.new_page()
            page.on('pageerror', lambda error: errors.append(str(error)))
            page.on('request', lambda request: deletes.append(request.url) if '/delete/' in request.url else None)
            page.goto(first_url)
            expect(page).to_have_url(first_url)
            expect(page.locator('.editor-tab:visible')).to_have_count(1)
            expect(page.locator('.sidebar-conversation:visible')).to_have_count(2)
            expect(page.locator('#messages')).to_contain_text('Conserve le cahier des charges')

            page.locator('.tab-new').click()
            expect(page).to_have_url(re.compile(r'/c/\d+/$'))
            expect(page.locator('.editor-tab:visible')).to_have_count(2)
            created = workspace.conversations.exclude(pk__in=original_ids).get()
            created_url = f'{self.live_server_url}/bara/{workspace.pk}/c/{created.pk}/'
            expect(page).to_have_url(created_url)
            page.locator(f'.editor-tab[data-conversation-id="{first.pk}"] .tab-close').click()
            expect(page).to_have_url(created_url)
            expect(page.locator(f'.sidebar-conversation[data-conversation-id="{first.pk}"]')).to_be_visible()
            expect(page.locator('.editor-tab:visible')).to_have_count(1)
            self.assertTrue(first.messages.exists())
            page.reload()
            expect(page.locator('.editor-tab:visible')).to_have_count(1)
            expect(page.locator(f'.sidebar-conversation[data-conversation-id="{first.pk}"]')).to_be_visible()

            page.locator(f'.sidebar-conversation[data-conversation-id="{first.pk}"]').click()
            expect(page).to_have_url(first_url)
            expect(page.locator('.editor-tab:visible')).to_have_count(2)
            expect(page.locator('#messages')).to_contain_text('Conserve le cahier des charges')
            page.locator('.editor-tab.active .tab-close').click()
            expect(page).to_have_url(created_url)
            expect(page.locator('.editor-tab:visible')).to_have_count(1)
            page.locator('.editor-tab.active .tab-close').click()
            expect(page).to_have_url(f'{self.live_server_url}/bara/{workspace.pk}/?view=conversations')
            expect(page.locator('.editor-tab:visible')).to_have_count(0)
            expect(page.locator('.sidebar-conversation:visible')).to_have_count(4)
            expect(page.locator('#agent-form')).to_be_hidden()
            page.reload()
            expect(page.locator('.editor-tab:visible')).to_have_count(0)
            page.goto(f'{self.live_server_url}/bara/')
            page.locator('.ws-card').click()
            expect(page).to_have_url(f'{self.live_server_url}/bara/{workspace.pk}/?view=conversations')
            expect(page.locator('.editor-tab:visible')).to_have_count(0)
            self.assertEqual(workspace.conversations.count(), 4)
            self.assertTrue(first.messages.exists())

            page.locator(f'.sidebar-conversation[data-conversation-id="{first.pk}"]').click()
            expect(page.locator('.editor-tab:visible')).to_have_count(1)
            expect(page.locator('#messages')).to_contain_text('Conserve le cahier des charges')
            page.goto(f'{self.live_server_url}/bara/')
            page.locator('.ws-card').click()
            expect(page).to_have_url(first_url)
            expect(page.locator('.editor-tab:visible')).to_have_count(1)
            page.screenshot(path='/tmp/2ai-bara-conversation-tabs-desktop.png', full_page=True)

            for index in range(10):
                WorkConversation.objects.create(workspace=workspace, title=f'Conversation supplémentaire longue {index}')
            ids = list(workspace.conversations.values_list('id', flat=True))
            key = f'bara.conversation.tabs.v1.{owner.pk}.{workspace.pk}'
            page.evaluate('([key, ids]) => localStorage.setItem(key, JSON.stringify(ids.map(String)))', [key, ids])
            page.reload()
            expect(page.locator('.editor-tab:visible')).to_have_count(14)
            expect(page.locator('.tab-new')).to_be_in_viewport()
            for width in (390, 320):
                page.set_viewport_size({'width': width, 'height': 844})
                expect(page.locator('.tab-new')).to_be_in_viewport()
                self.assertLessEqual(page.evaluate('document.documentElement.scrollWidth'), width + 1)
            page.set_viewport_size({'width': 390, 'height': 844})
            page.locator('#toggle-workbench-sidebar').click()
            expect(page.locator('.bara-workbench')).to_have_class(re.compile('sidebar-collapsed'))
            page.locator('.editor-tab.active .tab-close').click()
            expect(page.locator('.bara-workbench')).not_to_have_class(re.compile('sidebar-collapsed'))
            expect(page.locator(f'.sidebar-conversation[data-conversation-id="{first.pk}"]')).to_be_visible()
            page.screenshot(path='/tmp/2ai-bara-conversation-tabs-mobile.png', full_page=True)

            # The same browser and workspace must not reuse another account's open tabs.
            context.add_cookies([{'name': settings.SESSION_COOKIE_NAME, 'value': member_cookie,
                                 'url': self.live_server_url}])
            page.goto(first_url)
            expect(page.locator('.editor-tab:visible')).to_have_count(1)
            expect(page.locator('.sidebar-conversation:visible')).to_have_count(13)
            self.assertEqual(errors, [])
            self.assertEqual(deletes, [])
            self.assertEqual(workspace.conversations.count(), 14)
            self.assertEqual(first.messages.count(), 1)
            browser.close()


if __name__ == '__main__':
    raise SystemExit(bool(DiscoverRunner(verbosity=1, interactive=False).run_tests(['__main__.BaraTabBrowserTests'])))
