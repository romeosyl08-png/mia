from unittest.mock import patch
from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from bara.models import Workspace, WorkFile
from bara.retrieval import index_file, retrieve
from .models import UserMemory, Conversation, Message
from .context import conversation_history
from .memory import memory_context


class MemoryTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user('memory-new')
        self.other = get_user_model().objects.create_user('memory-other-new')
        self.workspace = Workspace.objects.create(user=self.user, memory='Projet Django')

    def test_memory_is_scoped_editable_and_disabled(self):
        UserMemory.objects.create(user=self.user,content='Réponds en français')
        self.assertIn('français',memory_context(self.user,self.workspace))
        self.assertNotIn('français',memory_context(self.other))
        self.client.force_login(self.other)
        self.assertEqual(self.client.get(f'/memory/project/{self.workspace.pk}/').status_code,404)
        self.client.force_login(self.user)
        self.client.post('/memory/',{'content':''})
        self.assertNotIn('français',memory_context(self.user,self.workspace))

    @override_settings(CHAT_HISTORY_MAX_MESSAGES=4, CHAT_HISTORY_MAX_CHARS=8000)
    def test_summary_preserves_early_context_and_excludes_future_edits(self):
        conversation = Conversation.objects.create(user=self.user)
        old = Message.objects.create(conversation=conversation,role='user',content='Le budget est de 500 euros.')
        for i in range(10):
            Message.objects.create(conversation=conversation,role='user',content=f'Question {i}')
        history = conversation_history(conversation.messages.all())
        self.assertIn('500 euros',' '.join(m['content'] for m in history))
        history = conversation_history(conversation.messages.filter(id__lte=old.pk))
        self.assertNotIn('Question 5',' '.join(m['content'] for m in history))

    @override_settings(EMBEDDING_MODEL='test', EMBEDDING_API_KEY='test')
    def test_semantic_index_survives_lexical_reads_and_content_changes(self):
        self.workspace.semantic_search = True
        self.workspace.save()
        file = WorkFile.objects.create(workspace=self.workspace,original_name='a.txt',extracted_text='Une automobile électrique.')
        with patch('bara.retrieval.embeddings', return_value=[[1.0,0.0]]):
            index_file(file,semantic=True)
            file.refresh_from_db()
            self.assertTrue(retrieve(self.workspace,'véhicule'))
        file.refresh_from_db()
        self.assertEqual(file.retrieval_index['model'],'test')
        file.extracted_text = 'Un document modifié.'
        file.save()
        index = index_file(file)
        self.assertNotIn('vector',index['chunks'][0])
