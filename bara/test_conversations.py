from bs4 import BeautifulSoup
from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from .models import WorkConversation, WorkMessage, Workspace, WorkspaceMember


class ConversationNavigationTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user('bara-tabs-owner')
        self.workspace = Workspace.objects.create(user=self.user, title='Mon espace')
        self.conversation = WorkConversation.objects.create(workspace=self.workspace, title='Projet conservé')
        self.message = WorkMessage.objects.create(workspace=self.workspace, conversation=self.conversation,
                                                  role='user', content='Un échange à conserver.')
        self.client.force_login(self.user)

    def test_tabs_offer_creation_and_non_destructive_closing_with_vertical_history(self):
        response = self.client.get(reverse('bara:conversation', args=[self.workspace.pk, self.conversation.pk]))
        self.assertEqual(response.status_code, 200)
        page = BeautifulSoup(response.content, 'html.parser')
        close = page.select_one('.editor-tab .tab-close')
        self.assertEqual(close['type'], 'button')
        self.assertIsNone(close.find_parent('form'))
        self.assertIn('Fermer', close['aria-label'])
        self.assertEqual(page.select_one('.tab-new-form')['action'],
                         reverse('bara:new-conversation', args=[self.workspace.pk]))
        self.assertEqual(page.select_one('.sidebar-conversation')['href'],
                         reverse('bara:conversation', args=[self.workspace.pk, self.conversation.pk]))

    def test_closing_last_tab_can_show_workspace_without_creating_or_deleting_a_conversation(self):
        response = self.client.get(reverse('bara:detail', args=[self.workspace.pk]) + '?view=conversations')
        self.assertEqual(response.status_code, 200)
        self.assertIsNone(response.context['conversation'])
        self.assertEqual(WorkConversation.objects.filter(workspace=self.workspace).count(), 1)
        self.assertTrue(WorkMessage.objects.filter(pk=self.message.pk).exists())
        page = BeautifulSoup(response.content, 'html.parser')
        self.assertTrue(page.select_one('#agent-form').has_attr('hidden'))
        self.assertTrue(page.select_one('#message-content').has_attr('disabled'))
        self.assertEqual(len(page.select('#messages .bara-msg')), 0)

    def test_overview_of_a_new_workspace_does_not_create_a_blank_conversation(self):
        workspace = Workspace.objects.create(user=self.user)
        response = self.client.get(reverse('bara:detail', args=[workspace.pk]) + '?view=conversations')
        self.assertEqual(response.status_code, 200)
        self.assertFalse(workspace.conversations.exists())

    def test_plus_creates_a_conversation_and_preserves_existing_history(self):
        response = self.client.post(reverse('bara:new-conversation', args=[self.workspace.pk]))
        created = self.workspace.conversations.exclude(pk=self.conversation.pk).get()
        self.assertRedirects(response, reverse('bara:conversation', args=[self.workspace.pk, created.pk]))
        self.assertTrue(WorkMessage.objects.filter(pk=self.message.pk).exists())
        self.assertFalse(created.messages.exists())

    def test_read_only_members_can_manage_their_visible_tabs_but_cannot_create_conversations(self):
        viewer = get_user_model().objects.create_user('bara-tabs-viewer')
        WorkspaceMember.objects.create(workspace=self.workspace, user=viewer, role='viewer')
        self.client.force_login(viewer)
        response = self.client.get(reverse('bara:conversation', args=[self.workspace.pk, self.conversation.pk]))
        page = BeautifulSoup(response.content, 'html.parser')
        self.assertIsNotNone(page.select_one('.tab-close'))
        self.assertIsNone(page.select_one('.tab-new-form'))
        self.assertEqual(self.client.post(reverse('bara:new-conversation', args=[self.workspace.pk])).status_code, 404)

    def test_navigation_is_scoped_to_an_accessible_workspace(self):
        other = get_user_model().objects.create_user('bara-tabs-outsider')
        self.client.force_login(other)
        self.assertEqual(self.client.get(reverse('bara:detail', args=[self.workspace.pk]) + '?view=conversations').status_code, 404)
        self.assertEqual(self.client.post(reverse('bara:new-conversation', args=[self.workspace.pk])).status_code, 404)
