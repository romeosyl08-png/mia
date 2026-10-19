import json
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TestCase

from ai_user.models import UserAPIKey
from bara.models import Workspace, WorkspaceMember
from chat.memory import memory_context
from chat.memory_import import memory_revision
from chat.models import UserMemory
from .models import AIModel, APIKey, APIProvider
from .tests import UPSTREAM_OK, fake_response


class SharedMemoryAPITests(TestCase):
    def setUp(self):
        cache.clear()
        self.user = get_user_model().objects.create_user('shared-memory-api')
        self.other = get_user_model().objects.create_user('shared-memory-other')
        self.memory = UserMemory.objects.create(user=self.user, content='PERSONAL_SECRET', use_in_barrassons=True)
        UserMemory.objects.create(user=self.other, content='FOREIGN_PERSONAL', use_in_barrassons=True)
        self.workspace = Workspace.objects.create(user=self.user, title='Accessible project', memory='PROJECT_SECRET',
                                                   memory_in_barrassons=True)
        self.foreign = Workspace.objects.create(user=self.other, title='Foreign project', memory='FOREIGN_PROJECT',
                                                 memory_in_barrassons=True)
        self.token, prefix, key_hash = UserAPIKey.generate()
        self.key = UserAPIKey.objects.create(user=self.user, name='memory', key_prefix=prefix, key_hash=key_hash)
        self.user.credit_account.credit(100_000, reason='adjustment')

    def api(self, method='get', path='/v1/memory', data=None):
        return getattr(self.client, method)(path, data=json.dumps(data) if method == 'post' else data,
                                            content_type='application/json', HTTP_AUTHORIZATION='Bearer ' + self.token)

    def append(self, scope='personal', **options):
        payload = {'target': scope, 'content': 'NEW_MEMORY', 'revision': memory_revision('PERSONAL_SECRET')}
        payload.update(options)
        return self.api('post', data=payload)

    def setup_model(self):
        provider = APIProvider.objects.create(name='Memory fixture', slug='memory-fixture', base_url='https://example.test')
        key = APIKey(provider=provider, name='fixture')
        key.set_key('fixture')
        key.save()
        AIModel.objects.create(provider=provider, name='Fixture', model_id='memory-fixture')

    def complete(self, **extra):
        return self.api('post', '/v1/chat/completions', {
            'model': 'memory-fixture', 'messages': [{'role': 'user', 'content': 'Bonjour'}], **extra,
        })

    def test_catalog_requires_active_api_key_and_only_lists_accessible_projects(self):
        self.assertEqual(self.client.get('/v1/memory').status_code, 401)
        response = self.api()
        self.assertIn('no-store', response.headers['Cache-Control'])
        self.assertEqual(response.json()['workspaces'][0]['id'], self.workspace.pk)
        for secret in ('PERSONAL_SECRET', 'PROJECT_SECRET', 'FOREIGN_PERSONAL', 'Foreign project'):
            self.assertNotContains(response, secret)
        self.key.is_active = False
        self.key.save()
        self.assertEqual(self.api().status_code, 401)

    def test_append_is_visible_to_chat_and_bara_and_preserves_existing_memory(self):
        source = self.api(data={'target': 'personal'}).json()
        self.assertEqual(source['content'], 'PERSONAL_SECRET')
        self.assertEqual(self.append().status_code, 200)
        for surface in ('chat', 'bara', 'barrassons'):
            context = memory_context(self.user, surface=surface)
            self.assertIn('PERSONAL_SECRET', context)
            self.assertIn('NEW_MEMORY', context)
        self.assertEqual(self.append().status_code, 409)
        self.memory.refresh_from_db()
        self.assertEqual(self.memory.content, 'PERSONAL_SECRET\n\nNEW_MEMORY')

    def test_project_append_and_limits_do_not_overwrite_or_enable_memory(self):
        self.assertEqual(self.append('workspace', workspace_id=self.workspace.pk,
                                    revision=memory_revision('PROJECT_SECRET')).status_code, 200)
        self.workspace.refresh_from_db()
        self.assertEqual(self.workspace.memory, 'PROJECT_SECRET\n\nNEW_MEMORY')
        for content in ('', 'x' * 8001, 'invalid\x00text'):
            self.assertEqual(self.append(content=content).status_code, 400)
        self.memory.refresh_from_db()
        self.assertEqual(self.memory.content, 'PERSONAL_SECRET')
        self.memory.enabled = False
        self.memory.save()
        self.assertEqual(self.append().status_code, 403)
        self.assertFalse(self.api().json()['personal']['available'])

    def test_shared_project_read_is_allowed_but_only_owner_can_write_and_revocation_is_immediate(self):
        WorkspaceMember.objects.create(workspace=self.foreign, user=self.user, role='editor')
        url = f'/v1/memory?target=workspace&workspace_id={self.foreign.pk}'
        source = self.api(path=url).json()
        self.assertEqual(source['content'], 'FOREIGN_PROJECT')
        self.assertFalse(source['can_write'])
        self.assertEqual(self.append('workspace', workspace_id=self.foreign.pk,
                                    revision=source['revision']).status_code, 403)
        self.foreign.members.all().delete()
        self.assertEqual(self.api(path=url).status_code, 404)
        self.assertEqual(self.append('workspace', workspace_id=self.foreign.pk).status_code, 404)

    def test_completions_load_selected_memory_each_call_and_do_not_change_plain_api_requests(self):
        self.setup_model()
        selection = {'personal': True, 'workspace_id': self.workspace.pk}
        with patch('api.views.urlopen', return_value=fake_response(UPSTREAM_OK)) as upstream:
            self.assertEqual(self.complete(memory=selection).status_code, 200)
            forwarded = json.loads(upstream.call_args.args[0].data)['messages']
            text = json.dumps(forwarded)
            self.assertIn('PERSONAL_SECRET', text)
            self.assertIn('PROJECT_SECRET', text)
            self.assertNotIn('FOREIGN_', text)
            self.memory.content = 'UPDATED_SECRET'
            self.memory.save()
            self.assertEqual(self.complete(memory=selection).status_code, 200)
            text = upstream.call_args.args[0].data.decode()
            self.assertIn('UPDATED_SECRET', text)
            self.assertNotIn('PERSONAL_SECRET', text)
            self.memory.use_in_barrassons = False
            self.memory.save()
            self.workspace.memory_in_barrassons = False
            self.workspace.save()
            self.assertEqual(self.complete(memory=selection).status_code, 200)
            self.assertNotIn('_SECRET', upstream.call_args.args[0].data.decode())
            self.assertEqual(self.complete().status_code, 200)
            self.assertNotIn('_SECRET', upstream.call_args.args[0].data.decode())

    def test_invalid_selection_and_foreign_project_are_rejected_before_billing_or_provider_call(self):
        before = self.user.credit_account.balance
        with patch('api.views.urlopen') as upstream:
            for selection in (None, [], {'personal': 'true'}, {'workspace_id': True},
                              {'workspace_id': -1}, {'workspace_id': 2**70}, {'unexpected': True}):
                self.assertEqual(self.complete(memory=selection).status_code, 400)
            self.assertEqual(self.complete(memory={'workspace_id': self.foreign.pk}).status_code, 404)
        upstream.assert_not_called()
        self.user.credit_account.refresh_from_db()
        self.assertEqual(self.user.credit_account.balance, before)
