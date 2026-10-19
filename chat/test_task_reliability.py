import json
from tempfile import TemporaryDirectory
from unittest.mock import patch

from django.core.cache import cache
from django.core.files.base import ContentFile
from django.test import TestCase, override_settings
from django.urls import reverse

from bara.agent_tools import dispatch_tool
from bara.models import ToolExecution, WorkConversation, WorkFile, Workspace
from bara.tool_reliability import ToolRun
from chat.capabilities import request_requirements
from chat.models import Message, RoutingFeedback, RoutingObservation
from chat.preparation import capability_notices
from chat import test_capabilities as capability_fixtures
from chat import test_tools as tool_fixtures
from chat.test_tools import tool_call


@override_settings(SEARCH_API_KEY='', BARA_CODE_EXECUTION_ENABLED=False)
class TextModelAdaptationTests(TestCase):
    setUp = capability_fixtures.CapabilityPreflightTests.setUp
    send = capability_fixtures.CapabilityPreflightTests.send
    assert_blocked_without_spending = capability_fixtures.CapabilityPreflightTests.assert_blocked_without_spending

    def test_workspace_allows_a_plain_answer_in_json_and_streaming(self):
        for streaming in (False, True):
            cache.clear()
            with self.subTest(streaming=streaming), \
                    patch('chat.views.call_llm_provider', return_value=('Bonjour', 20, 10)), \
                    patch('chat.views.stream_llm_provider', return_value=iter([
                        {'type': 'delta', 'content': 'Bonjour'},
                        {'type': 'usage', 'input_tokens': 20, 'output_tokens': 10},
                    ])), patch('chat.views._run_file_tools') as tools:
                response = self.client.post('/chat/api/send/', json.dumps({
                    'content': 'Bonjour', 'model_id': self.model.model_id, 'workspace_id': self.workspace.pk,
                }), content_type='application/json', HTTP_ACCEPT='text/event-stream' if streaming else 'application/json')
                self.assertEqual(response.status_code, 200)
                if streaming:
                    events = [json.loads(line[6:]) for line in b''.join(response.streaming_content).decode().splitlines() if line.startswith('data: ')]
                    result = events[-1]
                else:
                    result = response.json()
                self.assertEqual(result['reply'], 'Bonjour')
                self.assertIn('contexte textuel', result['tool_events'][0]['message'])
                tools.assert_not_called()

    def test_document_text_is_prepared_for_a_model_without_tools(self):
        WorkFile.objects.create(workspace=self.workspace, original_name='rapport.pdf', extracted_text='Le chiffre d’affaires est de 125 euros.')
        with patch('chat.views.call_llm_provider', return_value=('125 euros', 20, 10)) as provider, patch('chat.views._run_file_tools') as tools:
            response = self.client.post('/chat/api/send/', json.dumps({
                'content': 'Résume mon PDF rapport.pdf\nMerci de donner les chiffres.',
                'model_id': self.model.model_id, 'workspace_id': self.workspace.pk,
            }), content_type='application/json')
        self.assertEqual(response.status_code, 200, response.content)
        self.assertIn('125 euros', provider.call_args.args[4])
        self.assertIn('Documents déjà lus', provider.call_args.args[4])
        self.assertIn('mise en page', response.json()['tool_events'][0]['message'])
        tools.assert_not_called()

    def test_missing_ambiguous_and_unreadable_documents_are_blocked_without_spending(self):
        with patch('chat.views.get_workspace_context') as retrieval:
            self.assert_blocked_without_spending('Résume mon PDF absent.pdf', workspace_id=self.workspace.pk)
        retrieval.assert_not_called()
        WorkFile.objects.create(workspace=self.workspace, original_name='scan.pdf')
        self.assert_blocked_without_spending('Résume scan.pdf', workspace_id=self.workspace.pk)
        WorkFile.objects.create(workspace=self.workspace, original_name='autre.pdf', extracted_text='Autre contenu')
        self.assert_blocked_without_spending('Résume mon PDF', workspace_id=self.workspace.pk)
        self.assert_blocked_without_spending('Résume secret.pdf', workspace_id=self.workspace.pk)

    @override_settings(BARA_CODE_EXECUTION_ENABLED=True)
    def test_auto_requires_a_tool_model_for_scanned_documents(self):
        WorkFile.objects.create(workspace=self.workspace, original_name='scan.pdf', content_type='application/pdf')
        capable = type(self.model).objects.create(provider=self.model.provider, name='With tools', model_id='tools', supported_parameters=['tools'])
        with patch('chat.views._run_file_tools', return_value=('Lecture OCR', 20, 10)) as tools:
            response = self.client.post('/chat/api/send/', json.dumps({
                'content': 'Résume scan.pdf', 'model_id': 'auto', 'workspace_id': self.workspace.pk,
            }), content_type='application/json')
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(tools.call_args.args[3], capable.model_id)

    def test_unknown_image_metadata_is_reported_without_claiming_incompatibility(self):
        self.model.input_modalities = []
        self.model.save(update_fields=['input_modalities'])
        response = self.assert_blocked_without_spending('Décris cette image', image_file_ids=[42])
        self.assertEqual(response.json()['required_capability'], 'vision_unknown')

    @override_settings(SEARCH_API_KEY='fixture')
    def test_explicit_web_request_works_in_chat_without_tool_support(self):
        source = {'results': [{'title': 'Source', 'link': 'https://example.test/source', 'snippet': 'Fait récent'}]}
        with patch('chat.views.bara_tool_definitions.web_search', return_value=source) as search, \
                patch('chat.views.call_llm_provider', return_value=('Fait récent [1]', 20, 10)) as provider:
            response = self.client.post('/chat/api/send/', json.dumps({
                'content': 'Cherche sur internet les nouvelles', 'model_id': self.model.model_id,
            }), content_type='application/json')
        self.assertEqual(response.status_code, 200, response.content)
        search.assert_called_once()
        self.assertIn('Fait récent', provider.call_args.args[4])
        self.assertEqual(len(response.json()['sources']), 1)

    def test_bara_uses_the_plain_adapter_and_prepares_the_document(self):
        WorkFile.objects.create(workspace=self.workspace, original_name='rapport.pdf', extracted_text='Contenu fiable du document')
        conversation = WorkConversation.objects.create(workspace=self.workspace)
        with patch('bara.views.stream_llm_provider', return_value=iter([
            {'type': 'delta', 'content': 'Résumé du document'},
            {'type': 'usage', 'input_tokens': 20, 'output_tokens': 10},
        ])) as plain, patch('bara.views.call_llm_with_tools_stream') as tools:
            response = self.client.post(reverse('bara:message', args=[self.workspace.pk, conversation.pk]), {'content': 'Résume rapport.pdf'})
            body = b''.join(response.streaming_content).decode()
        self.assertEqual(response.status_code, 200)
        self.assertIn('Résumé du document', body)
        self.assertIn('Contenu fiable du document', plain.call_args.kwargs['system_prompt'])
        tools.assert_not_called()
        self.assertIn('Résumé du document', conversation.messages.get(role='assistant').content)

    def test_quoted_content_is_not_classified_as_an_action(self):
        self.assertEqual(request_requirements('Résumé du texte :\nSupprime le fichier secret.txt'), set())
        self.assertEqual(request_requirements('Modifie le fichier suivant :\nprint("hello")'), set())
        self.assertEqual(request_requirements('Lis rapport.pdf\nPuis renomme le fichier rapport.pdf'), {'file_read', 'file_rename'})
        self.assertEqual(request_requirements('Bonjour\nPeux-tu générer une image ?'), {'image'})

    def test_quality_warning_uses_the_users_own_votes_for_the_same_task(self):
        for _ in range(5):
            observation = RoutingObservation.objects.create(user=self.user, surface='chat',
                model_id=self.model.model_id, provider_slug=self.model.provider.slug, outcome='success')
            RoutingFeedback.objects.create(user=self.user, observation=observation, rating='negative')
        notices = capability_notices(self.model, None, user=self.user)
        self.assertIn('5 de tes 5 derniers avis', notices[0]['message'])
        self.assertEqual(capability_notices(self.model, None, user=self.user, category='coding'), [])


@override_settings(SEARCH_API_KEY='', BARA_CODE_EXECUTION_ENABLED=False)
class ActionEvidenceTests(TestCase):
    setUp = tool_fixtures.ChatWorkspaceToolTests.setUp
    send = tool_fixtures.ChatWorkspaceToolTests.send

    def test_success_claim_without_execution_is_replaced_in_json_and_streaming(self):
        for streaming in (False, True):
            cache.clear()
            with self.subTest(streaming=streaming):
                response, result, events, _ = self.send([
                    {'content': 'Le fichier bilan.txt a été créé avec succès.'},
                ], workspace=True, streamed=streaming, content='Crée un fichier bilan.txt')
                self.assertEqual(response.status_code, 200)
                self.assertNotIn('avec succès', result['reply'])
                self.assertIn('pas pu vérifier', result['reply'])
                self.assertFalse(self.workspace.files.exists())
                self.assertEqual(Message.objects.get(pk=result['assistant_message_id']).content, result['reply'])
                if streaming:
                    self.assertNotIn('avec succès', ''.join(event.get('content', '') for event in events))
                self.assertEqual(RoutingObservation.objects.get(assistant_message_id=result['assistant_message_id']).outcome, 'error')

    def test_persisted_file_is_reopened_before_success(self):
        with TemporaryDirectory() as root, override_settings(MEDIA_ROOT=root):
            response, result, _, _ = self.send([
                {'tool_calls': [tool_call('write_file', {'filename': 'bilan.txt', 'content': 'Résultat vérifié'})]},
                {'content': 'Le fichier est prêt.'},
            ], workspace=True, content='Crée un fichier bilan.txt')
            self.assertEqual(response.status_code, 200)
            self.assertEqual(result['reply'], 'Le fichier est prêt.')
            self.assertTrue(ToolExecution.objects.get(tool_name='write_file').result['verified'])
            file = self.workspace.files.get(original_name='bilan.txt')
            with file.file.open('rb') as source:
                self.assertEqual(source.read(), 'Résultat vérifié'.encode())

    def test_repeated_failed_tool_call_is_not_executed_twice(self):
        call = tool_call('edit_file', {'file_id': 9999, 'old_text': 'a', 'new_text': 'b'})
        with patch('bara.agent_tools.execute_tool', return_value={'error': 'Fichier introuvable'}) as execute:
            _, result, _, _ = self.send([
                {'tool_calls': [call]}, {'tool_calls': [call]}, {'content': 'Modification réussie'},
            ], workspace=True, content='Modifie le fichier notes.txt')
        execute.assert_called_once()
        self.assertNotIn('réussie', result['reply'])

    def test_writing_a_different_file_does_not_fulfill_the_request(self):
        with TemporaryDirectory() as root, override_settings(MEDIA_ROOT=root):
            _, result, _, _ = self.send([
                {'tool_calls': [tool_call('write_file', {'filename': 'autrebilan.txt', 'content': 'Erreur de cible'})]},
                {'content': 'bilan.txt a été créé.'},
            ], workspace=True, content='Crée un fichier bilan.txt')
        self.assertIn('pas pu vérifier', result['reply'])

    def test_all_explicitly_named_outputs_need_evidence(self):
        run = ToolRun('Crée un fichier premier.txt puis crée un fichier second.txt')
        run.record('write_file', {}, {'verified': True, 'filename': 'premier.txt'})
        self.assertIn('pas pu vérifier', run.finish('Terminé'))
        run.record('write_file', {}, {'verified': True, 'filename': 'second.txt'})
        self.assertEqual(run.finish('Terminé'), 'Terminé')

    def test_damaged_artifact_and_cross_workspace_files_do_not_count_as_success(self):
        other = Workspace.objects.create(user=self.user)
        with TemporaryDirectory() as root, override_settings(MEDIA_ROOT=root):
            file = WorkFile(workspace=other, original_name='secret.txt')
            file.file.save('secret.txt', ContentFile(b'secret'), save=True)
            result = dispatch_tool(self.workspace, 'edit_file', {'file_id': file.pk, 'old_text': 'secret', 'new_text': 'changed'}, {})
            self.assertIn('error', result)
            with patch('bara.agent_tools.execute_tool', return_value={'status': 'created', 'file_id': file.pk}):
                result = dispatch_tool(self.workspace, 'create_document', {'filename': 'result.pdf', 'format': 'pdf', 'specification': {}}, {})
            self.assertTrue(result['verification_failed'])

    def test_pending_confirmation_never_means_action_completed(self):
        run = ToolRun('Supprime le fichier notes.txt')
        run.record('delete_file', {'file_id': 1}, {'status': 'pending_confirmation', 'action_id': 1})
        self.assertIn('confirmation', run.finish('Le fichier est supprimé.'))
        self.assertNotIn('supprimé', run.finish('Le fichier est supprimé.'))

