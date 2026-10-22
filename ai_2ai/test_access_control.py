"""Matrice de contrôle d'accès : aucune route ne doit exposer les données d'un autre compte.

Le test parcourt automatiquement toutes les routes qui reçoivent un identifiant.
Une nouvelle route oubliée dans la liste fait échouer le test : il faut alors lui
associer un objet de test, pour qu'elle soit vérifiée comme les autres.
"""
import json
import tempfile

from django.contrib.auth import get_user_model
from django.core.files.base import ContentFile
from django.core.files.storage import FileSystemStorage
from django.test import TestCase, override_settings
from django.urls import URLPattern, URLResolver, get_resolver, reverse
from unittest.mock import patch

from api.models import AIModel, APIProvider
from bara.models import (AgentTask, DocumentArtifact, FileVersion, ToolExecution, WorkConversation, WorkFile,
                         Workspace, WorkspaceMCPServer)
from chat.models import Conversation, GeneratedMedia, Message, Persona
from evaluations.models import ComparisonSession
from studio.models import Asset, Job, Project, Version

SECRET = 'SECRET-DU-PROPRIETAIRE-7c1f'
FORBIDDEN_STATUSES = {302, 400, 401, 403, 404, 405}

# Routes avec paramètres qui ne désignent pas un objet d'un compte.
NOT_OWNED = {'password_reset_confirm', 'chat:recovery', 'bara:email-oauth-callback', 'bara:public-page', None}


def routes():
    def walk(resolver, prefix, namespace):
        for pattern in resolver.url_patterns:
            if isinstance(pattern, URLResolver):
                inner = namespace + ':' + pattern.namespace if namespace and pattern.namespace else (
                    pattern.namespace or namespace)
                yield from walk(pattern, prefix + str(pattern.pattern), inner)
            elif isinstance(pattern, URLPattern) and pattern.pattern.converters and not prefix.startswith('admin'):
                name = f'{namespace}:{pattern.name}' if namespace and pattern.name else pattern.name
                yield name, list(pattern.pattern.converters)
    return list(walk(get_resolver(), '', ''))


@override_settings(BARA_CODE_EXECUTION_ENABLED=False)
class OwnedObjectsTestCase(TestCase):
    """Un propriétaire avec un objet de chaque sorte, et un second compte."""

    @classmethod
    def setUpTestData(cls):
        users = get_user_model().objects
        cls.owner = users.create_user('owner', password='pw')
        cls.intruder = users.create_user('intruder', password='pw')
        for user in (cls.owner, cls.intruder):
            user.credit_account.credit(100_000)
        provider = APIProvider.objects.create(name='Fixture', slug='fixture', base_url='https://example.test')
        provider.keys.create(name='key', key='fixture-key')
        cls.model = AIModel.objects.create(provider=provider, name='Fixture', model_id='fixture-model',
                                           input_price_per_million=1, output_price_per_million=1)

    def setUp(self):
        storage = FileSystemStorage(location=self.enterContext(tempfile.TemporaryDirectory()))
        for model, field in ((WorkFile, 'file'), (FileVersion, 'binary_file'), (GeneratedMedia, 'image'),
                             (Asset, 'file')):
            self.enterContext(patch.object(model._meta.get_field(field), 'storage', storage))
        owner = self.owner
        self.workspace = Workspace.objects.create(user=owner, title=SECRET, active_model=self.model,
                                                  instructions=SECRET)
        self.bara_conversation = WorkConversation.objects.create(workspace=self.workspace, title=SECRET)
        self.file = WorkFile(workspace=self.workspace, original_name='rapport.docx', extracted_text=SECRET)
        self.file.file.save('rapport.docx', ContentFile(SECRET.encode()))
        DocumentArtifact.objects.create(file=self.file, kind='docx', specification={'body': SECRET})
        self.image = WorkFile(workspace=self.workspace, original_name='image.png', content_type='image/png')
        self.image.file.save('image.png', ContentFile(b'\x89PNG' + SECRET.encode()))
        self.version = FileVersion.objects.create(file=self.file, content=SECRET)
        self.task = AgentTask.objects.create(workspace=self.workspace, creator=owner, model=self.model, objective=SECRET)
        self.mcp = WorkspaceMCPServer.objects.create(workspace=self.workspace, name='mcp', endpoint='https://mcp.example')
        self.action = ToolExecution.objects.create(workspace=self.workspace, tool_name='write_file', status='pending',
                                                   arguments={'filename': 'x.txt', 'content': SECRET})
        self.persona = Persona.objects.create(user=owner, name=SECRET)
        self.conversation = Conversation.objects.create(user=owner, title=SECRET, persona=self.persona)
        Message.objects.create(conversation=self.conversation, role='user', content=SECRET)
        self.message = Message.objects.create(conversation=self.conversation, role='assistant', content=SECRET)
        self.media = GeneratedMedia(owner=owner, conversation=self.conversation, prompt=SECRET)
        self.media.image.save('media.png', ContentFile(b'\x89PNG' + SECRET.encode()))
        self.project = Project.objects.create(owner=owner, title=SECRET)
        self.project_version = Version.objects.create(project=self.project, number=1, label=SECRET, scene={'title': SECRET})
        self.project.current_version = self.project_version
        self.project.save()
        self.job = Job.objects.create(project=self.project, base_version=self.project_version)
        self.asset = Asset(project=self.project, kind='image', name=SECRET, content_type='image/png', size_bytes=12)
        self.asset.file.save('asset.png', ContentFile(b'\x89PNG' + SECRET.encode()))
        self.comparison = ComparisonSession.objects.create(user=owner, prompt=SECRET, selection_mode='manual',
                                                           request_digest='x' * 64, max_output_tokens=100)

    def ids_for(self, name):
        """Identifiants des objets du propriétaire pour chaque route."""
        base = name.split(':')[-1]
        if name.startswith('studio:'):
            return {'pk': self.project.pk, 'job_id': self.job.pk, 'asset_id': self.asset.pk}
        if name.startswith('evaluations:'):
            return {'pk': self.comparison.pk}
        return {
            'workspace_id': self.workspace.pk, 'conversation_id': self.bara_conversation.pk,
            'file_id': self.image.pk if base == 'image-file' else self.file.pk, 'task_id': self.task.pk,
            'server_id': self.mcp.pk, 'action_id': self.action.pk, 'version_id': self.version.pk,
            'message_id': self.message.pk, 'action': 'cancel', 'decision': 'approve', 'plugin': 'gmail',
            'pk': {'generated-media': self.media.pk}.get(base, self.persona.pk if 'persona' in base else self.conversation.pk),
        }

    def snapshot(self):
        """État des données du propriétaire, qui ne doit jamais changer."""
        def values(queryset, *fields):
            return sorted(queryset.values_list(*fields))
        return {
            'workspaces': values(Workspace.objects.filter(user=self.owner), 'pk', 'title', 'instructions',
                                 'disabled_tools', 'active_model_id'),
            'bara_conversations': values(WorkConversation.objects.filter(workspace=self.workspace), 'pk', 'title'),
            'files': values(WorkFile.objects.filter(workspace=self.workspace), 'pk', 'original_name',
                            'extracted_text', 'public_token'),
            'versions': values(FileVersion.objects.filter(file__workspace=self.workspace), 'pk'),
            'tasks': values(AgentTask.objects.filter(workspace=self.workspace), 'pk', 'status'),
            'mcp': values(WorkspaceMCPServer.objects.filter(workspace=self.workspace), 'pk', 'is_enabled'),
            'actions': values(ToolExecution.objects.filter(workspace=self.workspace), 'pk', 'status'),
            'members': values(self.workspace.members.all(), 'pk', 'user_id'),
            'schedules': values(self.workspace.schedules.all(), 'pk'),
            'conversations': values(Conversation.objects.filter(user=self.owner), 'pk', 'title', 'is_archived'),
            'messages': values(Message.objects.filter(conversation__user=self.owner), 'pk', 'content'),
            'personas': values(Persona.objects.filter(user=self.owner), 'pk', 'name'),
            'media': values(GeneratedMedia.objects.filter(owner=self.owner), 'pk'),
            'projects': values(Project.objects.filter(owner=self.owner), 'pk', 'title', 'current_version_id'),
            'jobs': values(Job.objects.filter(project=self.project), 'pk', 'status'),
            'assets': values(Asset.objects.filter(project=self.project), 'pk'),
            'comparisons': values(ComparisonSession.objects.filter(user=self.owner), 'pk', 'status'),
            'owner_balance': self.owner.credit_account.__class__.objects.get(pk=self.owner.credit_account.pk).balance,
        }

    def requests(self, url):
        hostile = {'title': 'PIRATE', 'name': 'PIRATE', 'content': 'PIRATE', 'instructions': 'PIRATE',
                   'username': 'intruder', 'role': 'editor', 'decision': 'approve'}
        yield 'GET', self.client.get(url)
        yield 'POST', self.client.post(url, hostile)
        yield 'POST json', self.client.post(url, json.dumps(hostile), content_type='application/json')
        for method in ('put', 'patch', 'delete'):
            yield method.upper(), getattr(self.client, method)(url, json.dumps(hostile), content_type='application/json')

    def attempt_every_route(self, prefix=''):
        """Essaie toutes les méthodes HTTP sur chaque route et renvoie chaque réponse."""
        for name, params in routes():
            if name in NOT_OWNED or not name.startswith(prefix):
                continue
            kwargs = {key: value for key, value in self.ids_for(name).items() if key in params}
            url = reverse(name, kwargs=kwargs)
            for method, response in self.requests(url):
                body = b''.join(response.streaming_content) if response.streaming else response.content
                yield name, url, method, response, body


class CrossAccountAccessTests(OwnedObjectsTestCase):
    def test_every_route_with_an_identifier_is_covered(self):
        unknown = []
        for name, params in routes():
            if name in NOT_OWNED:
                continue
            missing = set(params) - set(self.ids_for(name))
            if missing:
                unknown.append((name, missing))
        self.assertEqual(unknown, [], 'Associe un objet de test à ces routes dans ids_for().')

    def test_owner_can_reach_the_fixtures(self):
        """Garde-fou : si les identifiants étaient faux, le test d'intrusion ne prouverait rien."""
        self.client.force_login(self.owner)
        readable = ['bara:detail', 'bara:conversation', 'bara:tasks', 'bara:task-detail', 'bara:download-file',
                    'bara:image-file', 'bara:document-detail', 'bara:file-history', 'bara:sharing',
                    'chat:chat-conversation-detail', 'chat:persona-context', 'chat:generated-media',
                    'studio:detail', 'studio:asset', 'evaluations:detail']
        params_by_route = dict(routes())
        for name in readable:
            with self.subTest(route=name):
                params = {key: value for key, value in self.ids_for(name).items() if key in params_by_route[name]}
                response = self.client.get(reverse(name, kwargs=params))
                self.assertIn(response.status_code, {200, 302}, name)

    @patch('bara.mcp_client.list_mcp_tools', side_effect=AssertionError('appel réseau interdit'))
    @patch('chat.services.urlopen', side_effect=AssertionError('appel fournisseur interdit'))
    def test_another_account_can_neither_read_nor_change_owned_objects(self, *_):
        before = self.snapshot()
        self.client.force_login(self.intruder)
        failures = [f'{method} {url} ({name}) → {response.status_code}'
                    for name, url, method, response, body in self.attempt_every_route()
                    if response.status_code not in FORBIDDEN_STATUSES or SECRET.encode() in body]
        self.assertEqual(failures, [])
        self.assertEqual(self.snapshot(), before)

    def test_anonymous_visitors_are_turned_away(self):
        before = self.snapshot()
        failures = [f'{method} {url} ({name}) → {response.status_code}'
                    for name, url, method, response, body in self.attempt_every_route()
                    if response.status_code not in FORBIDDEN_STATUSES or SECRET.encode() in body]
        self.assertEqual(failures, [])
        self.assertEqual(self.snapshot(), before)


class WorkspaceRoleTests(OwnedObjectsTestCase):
    """Membres d'un espace Bara : le lecteur lit, l'éditeur modifie, seul le propriétaire administre."""

    def member(self, role):
        from bara.models import WorkspaceMember
        WorkspaceMember.objects.create(workspace=self.workspace, user=self.intruder, role=role)
        self.client.force_login(self.intruder)

    def admin_state(self):
        state = self.snapshot()
        return {key: state[key] for key in ('members', 'schedules', 'mcp')} | {
            'workspace': list(Workspace.objects.filter(pk=self.workspace.pk).values_list('disabled_tools', 'user_id')),
            'plugins': list(self.workspace.plugin_connections.values_list('pk', 'is_enabled')),
        }

    @patch('bara.mcp_client.list_mcp_tools', side_effect=AssertionError('appel réseau interdit'))
    @patch('chat.services.urlopen', side_effect=AssertionError('appel fournisseur interdit'))
    @patch('chat.file_tools.call_llm_with_tools', side_effect=AssertionError('appel fournisseur interdit'))
    def test_viewer_can_read_but_no_route_changes_anything(self, *_):
        self.member('viewer')
        before = self.snapshot()
        readable = {}
        for name, url, method, response, body in self.attempt_every_route('bara:'):
            if method == 'GET' and response.status_code == 200:
                readable[name] = True
        self.assertEqual(self.snapshot(), before)
        for name in ('bara:conversation', 'bara:download-file', 'bara:file-history', 'bara:export-zip'):
            self.assertIn(name, readable)
        for name in ('bara:sharing', 'bara:schedules', 'bara:connections'):
            self.assertNotIn(name, readable)

    @patch('bara.mcp_client.list_mcp_tools', side_effect=AssertionError('appel réseau interdit'))
    @patch('chat.services.urlopen', side_effect=AssertionError('appel fournisseur interdit'))
    @patch('chat.file_tools.call_llm_with_tools', side_effect=AssertionError('appel fournisseur interdit'))
    def test_editor_cannot_change_owner_only_settings(self, *_):
        self.member('editor')
        before = self.admin_state()
        for _ in self.attempt_every_route('bara:'):
            pass
        # Les routes d'administration refusent l'éditeur : membres, planifications, outils, MCP, connexions.
        self.assertEqual(self.admin_state(), before)

    def test_editor_changes_are_audited_with_their_author(self):
        from bara.models import WorkspaceAudit
        self.member('editor')
        response = self.client.post(reverse('bara:rename-workspace', args=[self.workspace.pk]), {'title': 'Nouveau'})
        self.assertEqual(response.status_code, 302)
        self.workspace.refresh_from_db()
        self.assertEqual(self.workspace.title, 'Nouveau')
        audit = WorkspaceAudit.objects.get(workspace=self.workspace)
        self.assertEqual((audit.actor, audit.action), (self.intruder, f'/bara/{self.workspace.pk}/rename/'))

    def test_viewer_write_attempts_are_refused_before_being_audited(self):
        from bara.models import WorkspaceAudit
        self.member('viewer')
        response = self.client.post(reverse('bara:rename-workspace', args=[self.workspace.pk]), {'title': 'PIRATE'})
        self.assertEqual(response.status_code, 404)
        self.assertFalse(WorkspaceAudit.objects.exists())


class SharingTests(OwnedObjectsTestCase):
    def setUp(self):
        super().setUp()
        self.client.force_login(self.owner)
        self.url = reverse('bara:sharing', args=[self.workspace.pk])

    def share(self, username, role):
        return self.client.post(self.url, {'username': username, 'role': role})

    def test_owner_grants_changes_and_revokes_access(self):
        detail = reverse('bara:download-file', args=[self.workspace.pk, self.file.pk])
        rename = reverse('bara:rename-workspace', args=[self.workspace.pk])
        self.assertEqual(self.share('intruder', 'viewer').status_code, 302)
        self.client.force_login(self.intruder)
        self.assertEqual(self.client.get(detail).status_code, 200)
        self.assertEqual(self.client.post(rename, {'title': 'x'}).status_code, 404)
        self.client.force_login(self.owner)
        self.share('intruder', 'editor')
        self.client.force_login(self.intruder)
        self.assertEqual(self.client.post(rename, {'title': 'Par l’éditeur'}).status_code, 302)
        self.client.force_login(self.owner)
        self.share('intruder', 'remove')
        self.client.force_login(self.intruder)
        self.assertEqual(self.client.get(detail).status_code, 404)
        self.assertFalse(self.workspace.members.exists())

    def test_invalid_shares_are_rejected_without_changes(self):
        for username, role in [('inconnu', 'viewer'), ('owner', 'editor'), ('intruder', 'admin'), ('', 'viewer')]:
            with self.subTest(username=username, role=role):
                response = self.share(username, role)
                self.assertEqual(response.status_code, 200)
                self.assertContains(response, 'Compte existant et rôle valide requis.')
        self.assertFalse(self.workspace.members.exists())


class ScheduleTests(OwnedObjectsTestCase):
    def setUp(self):
        super().setUp()
        self.client.force_login(self.owner)
        self.url = reverse('bara:schedules', args=[self.workspace.pk])

    def plan(self, **overrides):
        from datetime import timedelta
        from django.utils import timezone
        data = {'objective': 'Rapport hebdomadaire', 'next_run': (timezone.now() + timedelta(days=1)).isoformat(),
                'interval_minutes': '60', 'credit_limit': '1000', **overrides}
        return self.client.post(self.url, data)

    def test_valid_schedule_is_created(self):
        self.assertEqual(self.plan().status_code, 302)
        schedule = self.workspace.schedules.get()
        self.assertEqual((schedule.objective, schedule.interval_minutes, schedule.creator), ('Rapport hebdomadaire', 60, self.owner))

    def test_invalid_schedules_are_rejected(self):
        from datetime import timedelta
        from django.utils import timezone
        cases = {'date passée': {'next_run': (timezone.now() - timedelta(minutes=1)).isoformat()},
                 'récurrence trop courte': {'interval_minutes': '59'}, 'récurrence trop longue': {'interval_minutes': '525601'},
                 'budget nul': {'credit_limit': '0'}, 'budget excessif': {'credit_limit': '100001'},
                 'objectif vide': {'objective': '  '}, 'date illisible': {'next_run': 'demain'},
                 'nombre illisible': {'interval_minutes': 'souvent'}}
        for label, overrides in cases.items():
            with self.subTest(label):
                self.assertContains(self.plan(**overrides), 'Récurrence minimum : 60 minutes.')
        self.assertFalse(self.workspace.schedules.exists())

    def test_at_most_twenty_schedules_per_workspace(self):
        for _ in range(20):
            self.plan()
        self.assertContains(self.plan(), 'Récurrence minimum')
        self.assertEqual(self.workspace.schedules.count(), 20)

    def test_deleting_cannot_reach_another_workspace_schedule(self):
        from datetime import timedelta
        from django.utils import timezone
        from bara.models import TaskSchedule
        other_workspace = Workspace.objects.create(user=self.intruder)
        foreign = TaskSchedule.objects.create(workspace=other_workspace, creator=self.intruder, objective='x',
                                              next_run=timezone.now() + timedelta(days=1))
        self.client.post(self.url, {'delete': foreign.pk})
        self.assertTrue(TaskSchedule.objects.filter(pk=foreign.pk).exists())
