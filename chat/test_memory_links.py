from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from bara.models import Workspace, WorkspaceMember
from bara.views import _build_system_prompt
from .memory import memory_context
from .models import UserMemory


class MemoryLinkTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user('memory-link-owner')
        self.other = get_user_model().objects.create_user('memory-link-other')
        self.memory = UserMemory.objects.create(user=self.user, content='PERSONAL_SECRET')
        self.workspace = Workspace.objects.create(user=self.user, memory='PROJECT_SECRET')
        self.client.force_login(self.user)

    def test_personal_links_apply_independently_and_do_not_change_memory(self):
        url = reverse('chat:memory')
        self.assertContains(self.client.get(url), 'Lier la mémoire entre les assistants')
        response = self.client.post(url, {'action': 'save_links', 'use_in_bara': 'on', 'use_in_barrassons': 'on'})
        self.assertRedirects(response, url + '#memory-links')
        self.assertNotIn('PERSONAL_SECRET', memory_context(self.user))
        self.assertIn('PERSONAL_SECRET', _build_system_prompt(self.workspace, actor=self.user))
        self.assertIn('PERSONAL_SECRET', memory_context(self.user, surface='barrassons'))
        self.memory.refresh_from_db()
        self.assertEqual(self.memory.content, 'PERSONAL_SECRET')
        self.assertTrue(self.memory.enabled)
        self.assertFalse(self.memory.use_in_chat)
        self.assertTrue(self.memory.use_in_bara)
        self.assertTrue(self.memory.use_in_barrassons)
        self.client.post(url, {'action': 'save_links', 'use_in_chat': 'on'})
        self.assertIn('PERSONAL_SECRET', memory_context(self.user))
        self.assertNotIn('PERSONAL_SECRET', _build_system_prompt(self.workspace, actor=self.user))

    def test_project_links_and_master_switch_apply_independently(self):
        url = reverse('chat:project-memory', args=[self.workspace.pk])
        self.assertNotIn('PROJECT_SECRET', memory_context(self.user, self.workspace, surface='barrassons'))
        self.client.post(url, {'action': 'save_links', 'memory_in_barrassons': 'on'})
        self.workspace.refresh_from_db()
        self.assertNotIn('PROJECT_SECRET', memory_context(self.user, self.workspace))
        self.assertIn('PROJECT_SECRET', memory_context(self.user, self.workspace, surface='barrassons'))
        self.assertIn('PROJECT_SECRET', _build_system_prompt(self.workspace, actor=self.user))
        self.assertEqual(self.workspace.memory, 'PROJECT_SECRET')
        self.workspace.memory_enabled = False
        self.workspace.save()
        for surface in ('chat', 'bara', 'barrassons'):
            self.assertNotIn('PROJECT_SECRET', memory_context(self.user, self.workspace, surface=surface))

    def test_shared_projects_never_expose_owner_personal_memory_and_links_are_owner_only(self):
        WorkspaceMember.objects.create(workspace=self.workspace, user=self.other, role='editor')
        self.assertIn('PROJECT_SECRET', memory_context(self.other, self.workspace))
        self.assertNotIn('PERSONAL_SECRET', memory_context(self.other, self.workspace))
        self.client.force_login(self.other)
        url = reverse('chat:project-memory', args=[self.workspace.pk])
        self.assertEqual(self.client.post(url, {'action': 'save_links', 'memory_in_barrassons': 'on'}).status_code, 404)
        self.workspace.members.all().delete()
        self.assertNotIn('PROJECT_SECRET', memory_context(self.other, self.workspace))

    def test_editor_and_import_keep_links_and_local_chat_obeys_personal_switch(self):
        self.memory.use_in_chat = False
        self.memory.use_in_barrassons = True
        self.memory.save()
        url = reverse('chat:memory')
        self.client.post(url, {'action': 'save', 'content': 'NEW_SECRET', 'enabled': 'on'})
        self.memory.refresh_from_db()
        self.assertFalse(self.memory.use_in_chat)
        self.assertTrue(self.memory.use_in_barrassons)
        persona = self.user.personas.first()
        response = self.client.get(reverse('chat:persona-context', args=[persona.pk]))
        self.assertNotIn('NEW_SECRET', response.json()['system_prompt'])
