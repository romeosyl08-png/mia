import json
from django.test import TestCase, override_settings
from unittest.mock import patch
from . import test_tools
from .models import Message, Conversation
from .services import ProviderError


@override_settings(SEARCH_API_KEY='')
class RecoveryTests(TestCase):
    setUp = test_tools.ChatWorkspaceToolTests.setUp

    def request(self,temporary=False):
        return self.client.post('/chat/api/send/',json.dumps({'content':'Explique ce projet','model_id':self.model.model_id,
            'workspace_id':self.workspace.pk,'temporary':temporary}),content_type='application/json',HTTP_ACCEPT='text/event-stream')

    def test_partial_reply_is_saved_and_recoverable_after_provider_failure(self):
        def broken():
            yield {'type':'delta','content':'Première partie de la réponse.'}
            raise ProviderError('Connexion coupée')
        before = self.user.credit_account.balance
        with patch('chat.file_tools.call_llm_with_tools_stream',return_value=broken()):
            response = self.request()
            events = [json.loads(line[6:]) for line in b''.join(response.streaming_content).decode().splitlines() if line.startswith('data: ')]
        self.assertEqual(events[-1]['type'],'error')
        self.assertEqual(Message.objects.count(),2)
        partial = Message.objects.get(interrupted=True)
        token = events[0]['recovery_token']
        self.assertEqual(partial.recovery_token,token)
        self.assertEqual(self.client.get('/chat/api/recovery/'+token+'/').json()['reply'],partial.content)
        self.user.credit_account.refresh_from_db()
        self.assertEqual(self.user.credit_account.balance,before)
        from django.contrib.auth import get_user_model
        other = get_user_model().objects.create_user('recovery-other')
        self.client.force_login(other)
        self.assertEqual(self.client.get('/chat/api/recovery/'+token+'/').status_code,404)

    def test_disconnection_saves_received_text_and_temporary_mode_stays_unsaved(self):
        for temporary in (False,True):
            Message.objects.all().delete();Conversation.objects.all().delete()
            with patch('chat.file_tools.call_llm_with_tools_stream',return_value=iter([{'type':'delta','content':'Début du résultat'},{'type':'delta','content':'Suite'}])):
                response = self.request(temporary)
                stream = iter(response.streaming_content)
                next(stream);next(stream)
                response.close()
            self.assertEqual(Message.objects.filter(interrupted=True).count(),0 if temporary else 1)

    def test_empty_failure_does_not_create_a_recovery_conversation(self):
        with patch('chat.file_tools.call_llm_with_tools_stream',side_effect=ProviderError('Indisponible')):
            response = self.request()
            body = b''.join(response.streaming_content).decode()
        self.assertIn('"recovery": null',body)
        self.assertEqual(Conversation.objects.count(),0)
