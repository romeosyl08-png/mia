import json
from datetime import timedelta
from unittest.mock import patch, MagicMock
from django.contrib.auth import get_user_model
from django.core.files.base import ContentFile
from django.test import TestCase, override_settings
from django.utils import timezone
from api.models import AIModel, APIProvider
from chat.models import Message, UserMemory
from .models import Workspace, WorkspaceMember, WorkFile, TaskSchedule, AgentTask, WorkspacePluginConnection
from .scheduling import enqueue_due_schedules
from .agent_tools import _tools_schema
from .tools.connectors import drive_search, github_read_file
from .tools.ocr import extract_document_text
from .views import _build_system_prompt


@override_settings(SEARCH_API_KEY='')
class ProductTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user('product-owner')
        self.other = get_user_model().objects.create_user('product-member')
        self.user.credit_account.credit(10000)
        provider = APIProvider.objects.create(name='Product provider',slug='product',base_url='https://example.test')
        provider.keys.create(name='key',key='test-key')
        self.model = AIModel.objects.create(provider=provider,name='Vision',model_id='vision',input_modalities=['text','image'],input_price_per_million=1)
        self.workspace = Workspace.objects.create(user=self.user,active_model=self.model)
        self.file = WorkFile(workspace=self.workspace,original_name='image.png',content_type='image/png',size_bytes=8)
        self.file.file.save('image.png',ContentFile(b'\x89PNG\r\n\x1a\n'))

    def test_viewer_reads_files_but_cannot_write_or_connect(self):
        WorkspaceMember.objects.create(workspace=self.workspace,user=self.other,role='viewer')
        self.client.force_login(self.other)
        self.assertEqual(self.client.get(f'/bara/{self.workspace.pk}/files/{self.file.pk}/download/').status_code,200)
        self.assertEqual(self.client.post(f'/bara/{self.workspace.pk}/files/{self.file.pk}/delete/').status_code,404)
        self.assertEqual(self.client.get(f'/bara/{self.workspace.pk}/connections/').status_code,404)
        self.assertContains(self.client.get('/bara/'),self.workspace.title)

    def test_editor_cannot_delete_workspace_change_tool_policy_or_see_owner_memory(self):
        WorkspaceMember.objects.create(workspace=self.workspace,user=self.other,role='editor')
        UserMemory.objects.create(user=self.user,content='Private owner preference')
        self.assertNotIn('Private owner preference',_build_system_prompt(self.workspace,actor=self.other))
        self.client.force_login(self.other)
        self.assertEqual(self.client.post(f'/bara/{self.workspace.pk}/delete/').status_code,404)
        from django.urls import reverse
        self.assertEqual(self.client.post(reverse('bara:update-tools',args=[self.workspace.pk])).status_code,404)

    def test_schedule_creates_one_run_and_advances_without_catchup_burst(self):
        schedule = TaskSchedule.objects.create(workspace=self.workspace,creator=self.user,objective='Rapport',next_run=timezone.now()-timedelta(days=1),interval_minutes=60)
        enqueue_due_schedules(); enqueue_due_schedules()
        self.assertEqual(AgentTask.objects.count(),1)
        schedule.refresh_from_db()
        self.assertGreater(schedule.next_run,timezone.now())

    def test_images_reach_provider_and_are_saved_for_followup(self):
        self.client.force_login(self.user)
        payload = {'content':'Décris cette image','model_id':'vision','workspace_id':self.workspace.pk,'image_file_ids':[self.file.pk]}
        with patch('chat.file_tools.call_llm_with_tools',return_value=({'content':'Une image'},100,10)) as provider:
            response = self.client.post('/chat/api/send/',json.dumps(payload),content_type='application/json')
        self.assertEqual(response.status_code,200,response.content)
        parts = provider.call_args.args[3][-1]['content']
        self.assertTrue(parts[1]['image_url']['url'].startswith('data:image/png;base64,'))
        user_message = Message.objects.get(pk=response.json()['user_message_id'])
        self.assertEqual(user_message.image_file_ids,[self.file.pk])
        payload.update(content='Et sa couleur ?',conversation_id=response.json()['conversation_id'],image_file_ids=[])
        with patch('chat.file_tools.call_llm_with_tools',return_value=({'content':'Couleur'},100,10)) as provider:
            response = self.client.post('/chat/api/send/',json.dumps(payload),content_type='application/json')
        self.assertEqual(response.status_code,200)
        self.assertIsInstance(provider.call_args.args[3][0]['content'],list)

    def test_chat_document_event_keeps_links_after_reload(self):
        self.client.force_login(self.user)
        args = {'filename':'résultat.docx','format':'docx','specification':{'title':'Rapport','body':'Contenu vérifié'}}
        call = {'id':'document','type':'function','function':{'name':'create_document','arguments':json.dumps(args)}}
        with patch('chat.file_tools.call_llm_with_tools',side_effect=[({'tool_calls':[call]},10,10),({'content':'Document terminé'},10,10)]):
            response = self.client.post('/chat/api/send/',json.dumps({'content':'Crée un Word','model_id':self.model.model_id,'workspace_id':self.workspace.pk}),content_type='application/json')
        self.assertEqual(response.status_code,200)
        artifact = response.json()['tool_events'][0]
        self.assertEqual(artifact['type'],'artifact')
        self.assertEqual(self.client.get(artifact['url']).status_code,200)
        saved = self.client.get('/chat/api/conversations/'+str(response.json()['conversation_id'])+'/').json()
        self.assertEqual(saved['messages'][-1]['tool_events'][0],artifact)

    def test_attachments_cannot_read_another_workspace(self):
        self.client.force_login(self.other)
        workspace = Workspace.objects.create(user=self.other)
        payload = {'content':'Image','model_id':'vision','workspace_id':workspace.pk,'image_file_ids':[self.file.pk]}
        with patch('chat.file_tools.call_llm_with_tools') as provider:
            response = self.client.post('/chat/api/send/',json.dumps(payload),content_type='application/json')
        self.assertEqual(response.status_code,400)
        provider.assert_not_called()

    def test_connectors_are_exposed_only_when_connected_and_are_read_only(self):
        self.assertNotIn('drive_search',{s['function']['name'] for s in _tools_schema(self.workspace)[0]})
        connection = WorkspacePluginConnection.objects.create(workspace=self.workspace,plugin='google_drive',access_token_expires_at=timezone.now()+timedelta(hours=1))
        connection.set_oauth_tokens('test-token'); connection.save()
        self.assertIn('drive_search',{s['function']['name'] for s in _tools_schema(self.workspace)[0]})
        with patch('bara.tools.connectors._request_json',return_value={'files':[]}) as request:
            drive_search(self.workspace,"l'équipe")
        self.assertEqual(request.call_args.args[0],'https://www.googleapis.com/drive/v3/files')
        self.assertNotIn('method',request.call_args.kwargs)
        self.assertIn("l\\'équipe",request.call_args.kwargs['params']['q'])
        with patch('bara.tools.connectors._request_json') as request:
            self.assertIn('error',github_read_file(self.workspace,'owner/repo','../secret'))
        request.assert_not_called()

    def test_ocr_updates_searchable_text_without_running_generated_code_on_host(self):
        with patch('bara.tools.ocr.run_sandbox_command',return_value={'exit_code':0,'stdout':'[Page 1]\nÉlectricité'}) as runtime:
            result = extract_document_text(self.workspace,self.file.pk)
        self.assertIn('Électricité',result['text'])
        self.file.refresh_from_db()
        self.assertIn('Électricité',self.file.extracted_text)
        self.assertEqual(runtime.call_args.args[0][0],'python')
