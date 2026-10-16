import json
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TestCase, override_settings
from django.urls import reverse

from api.models import AIModel, APIProvider
from bara.models import WorkConversation, WorkMessage, Workspace, WorkspaceMember
from bara.views import _build_system_prompt
from .context import conversation_history
from .continuity import message_entries, related_conversation_context
from .models import Conversation, Message, Persona


@override_settings(CHAT_HISTORY_MAX_MESSAGES=6, CHAT_HISTORY_MAX_CHARS=8000, SEARCH_API_KEY='')
class ContinuityTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = get_user_model().objects.create_user('continuity-owner')
        cls.other = get_user_model().objects.create_user('continuity-other')
        cls.persona = Persona.objects.create(user=cls.user, name='Projet Atlas')
        cls.other_persona = Persona.objects.create(user=cls.user, name='Autre persona')
        cls.workspace = Workspace.objects.create(user=cls.user, title='Atlas')

    def chat(self, text, **fields):
        conversation = Conversation.objects.create(user=fields.pop('user', self.user), **fields)
        Message.objects.create(conversation=conversation, role='user', content=text)
        return conversation

    def work(self, text, workspace=None):
        workspace = workspace or self.workspace
        conversation = WorkConversation.objects.create(workspace=workspace)
        WorkMessage.objects.create(workspace=workspace, conversation=conversation, role='user', content=text)
        return conversation

    def history_text(self, conversation, query=''):
        history = conversation_history(conversation.messages.all(), query)
        self.assertLessEqual(len(history), 6)
        self.assertLessEqual(sum(len(row['content']) for row in history), 8000)
        return '\n'.join(row['content'] for row in history)

    def test_summary_processes_entire_backlog_and_preserves_message_endings(self):
        conversation = self.chat('Introduction. ' * 100 + '\nBudget décidé : 572 euros.')
        for index in range(130):
            Message.objects.create(conversation=conversation, role='user', content='Discussion sans décision.')
        last_old = Message.objects.create(conversation=conversation, role='user', content='Échéance retenue : 17 décembre.')
        for index in range(6):
            Message.objects.create(conversation=conversation, role='user', content=f'Nouvelle question {index}')
        text = self.history_text(conversation)
        self.assertIn('572 euros', text)
        self.assertIn('17 décembre', text)
        conversation.refresh_from_db()
        self.assertGreaterEqual(conversation.summary_through_id, last_old.pk)
        self.assertEqual(conversation.messages.count(), 138)

    def test_recall_finds_detail_lost_from_compact_summary(self):
        conversation = self.chat('Préambule.\n' + '\n'.join(
            f'Le nom interne est {word}.' for word in ['abricot', 'cerise', 'kumquat', 'melon', 'pomme', 'poire']))
        for index in range(40):
            Message.objects.create(conversation=conversation, role='user', content=f'Budget décidé : {1000 + index} euros.')
        text = self.history_text(conversation, 'Quel était le nom kumquat ?')
        self.assertIn('Le nom interne est kumquat.', text)
        self.assertIn('Passages anciens retrouvés', text)

    def test_character_limit_alone_triggers_compression(self):
        conversation = self.chat('Budget initial : 731 euros.')
        for index in range(3):
            Message.objects.create(conversation=conversation, role='user', content=('Long paragraphe. ' * 210) + str(index))
        self.assertIn('731 euros', self.history_text(conversation))

    def test_long_final_assistant_message_keeps_the_previous_user_turn(self):
        conversation = self.chat('Budget initial : 310 euros.')
        Message.objects.create(conversation=conversation, role='assistant', content='Détails. ' * 5000)
        history = conversation_history(conversation.messages.all(), 'Quel budget ?')
        self.assertEqual(history[0]['role'], 'user')
        self.assertTrue(any(row['role'] == 'assistant' for row in history))
        self.assertIn('310 euros', '\n'.join(row['content'] for row in history))
        self.assertLessEqual(sum(len(row['content']) for row in history), 8000)

    def test_old_prefix_never_uses_future_cached_summary_or_recall(self):
        conversation = self.chat('Départ du projet.')
        messages = [Message.objects.create(conversation=conversation, role='user', content=f'Étape {index}')
                    for index in range(20)]
        Message.objects.create(conversation=conversation, role='user', content='Décision future : code secret origan.')
        for index in range(10):
            Message.objects.create(conversation=conversation, role='user', content=f'Suite {index}')
        self.history_text(conversation)
        prefix = conversation.messages.filter(id__lte=messages[9].pk)
        history = conversation_history(prefix, 'code secret origan')
        text = '\n'.join(row['content'] for row in history)
        self.assertNotIn('origan', text)
        self.assertNotIn('Étape 15', text)
        conversation.refresh_from_db()
        self.assertGreater(conversation.summary_through_id, messages[9].pk)

    def test_legacy_summary_is_rebuilt_from_originals(self):
        conversation = self.chat('Budget réel : 310 euros.')
        Conversation.objects.filter(pk=conversation.pk).update(summary='Ancienne copie obsolète', summary_through_id=1)
        for _ in range(10):
            Message.objects.create(conversation=conversation, role='user', content='Suite du travail.')
        text = self.history_text(conversation)
        self.assertIn('310 euros', text)
        self.assertNotIn('copie obsolète', text)

    def test_an_edit_during_extraction_cannot_restore_a_deleted_branch_in_the_cache(self):
        conversation = self.chat('Départ du projet.')
        boundary = Message.objects.create(conversation=conversation, role='user', content='Décision origan à supprimer.')
        for index in range(20):
            Message.objects.create(conversation=conversation, role='user', content=f'Suite {index}')

        def edit_during_extraction(row):
            entries = message_entries(row)
            if row['id'] == boundary.pk:
                conversation.messages.filter(id__gte=boundary.pk).delete()
                Conversation.objects.filter(pk=conversation.pk).update(summary='', summary_through_id=0)
            return entries

        with patch('chat.context.message_entries', side_effect=edit_during_extraction):
            conversation_history(conversation.messages.all())
        conversation.refresh_from_db()
        self.assertEqual(conversation.summary, '')
        for index in range(20):
            Message.objects.create(conversation=conversation, role='user', content=f'Nouvelle branche {index}')
        self.assertNotIn('origan', self.history_text(conversation, 'Décision origan'))

    def test_persona_recall_excludes_other_personas_accounts_and_current_conversation(self):
        source = self.chat('Atlas utilise le nom kumquat.', persona=self.persona)
        current = self.chat('Atlas réponse future à exclure.', persona=self.persona)
        self.chat('Atlas autre persona privé.', persona=self.other_persona)
        self.chat('Atlas autre compte privé.', user=self.other, persona=self.persona)
        context = related_conversation_context(self.user, 'Atlas kumquat', persona=self.persona, conversation=current)
        self.assertIn('kumquat', context)
        self.assertNotIn('future à exclure', context)
        self.assertNotIn('autre persona privé', context)
        self.assertNotIn('autre compte privé', context)
        source.delete()
        self.assertEqual(related_conversation_context(
            self.user, 'kumquat', persona=self.persona, conversation=current), '')

    def test_generic_followup_covers_multiple_conversations(self):
        self.chat('Ancienne conversation alpha.', persona=self.persona)
        busy = self.chat('Nouvelle conversation beta.', persona=self.persona)
        for _ in range(40):
            Message.objects.create(conversation=busy, role='user', content='Suite beta.')
        context = related_conversation_context(self.user, persona=self.persona)
        self.assertIn('alpha', context)
        self.assertIn('beta', context)

    def test_workspace_recall_combines_bara_and_own_chat_but_keeps_private_chat_private(self):
        current = self.work('Message courant à exclure.')
        self.work('Atlas planning partagé.')
        self.chat('Atlas budget personnel.', workspace=self.workspace)
        self.chat('Atlas autre compte privé.', user=self.other, workspace=self.workspace)
        outside = Workspace.objects.create(user=self.user)
        self.work('Atlas autre espace privé.', outside)
        context = related_conversation_context(self.user, 'Atlas', workspace=self.workspace, work_conversation=current)
        self.assertIn('planning partagé', context)
        self.assertIn('budget personnel', context)
        self.assertNotIn('courant à exclure', context)
        self.assertNotIn('autre compte privé', context)
        self.assertNotIn('autre espace privé', context)
        self.assertEqual(related_conversation_context(self.other, 'Atlas', workspace=self.workspace), '')
        WorkspaceMember.objects.create(workspace=self.workspace, user=self.other, role='editor')
        member_context = related_conversation_context(self.other, 'Atlas', workspace=self.workspace)
        self.assertIn('planning partagé', member_context)
        self.assertNotIn('budget personnel', member_context)
        self.workspace.members.all().delete()
        self.assertEqual(related_conversation_context(self.other, 'Atlas', workspace=self.workspace), '')

    def test_workspace_memory_switch_and_global_budget_disable_related_recall(self):
        self.work('Atlas planning partagé.')
        self.workspace.memory_enabled = False
        self.assertEqual(related_conversation_context(self.user, 'Atlas', workspace=self.workspace), '')
        self.chat('Atlas personnel.', persona=self.persona)
        with override_settings(CHAT_CONTEXT_RELATED_MAX_CHARS=0):
            self.assertEqual(related_conversation_context(self.user, 'Atlas', persona=self.persona), '')
        with override_settings(CHAT_CONTEXT_RELATED_MAX_CHARS=600):
            context = related_conversation_context(self.user, 'Atlas', persona=self.persona)
            self.assertIn('Atlas personnel', context)
            self.assertLessEqual(len(context), 600)

    def test_bara_uses_the_same_history_compression_and_scoped_recall(self):
        conversation = self.work('Budget décidé : 629 euros.')
        for _ in range(12):
            WorkMessage.objects.create(workspace=self.workspace, conversation=conversation, role='user', content='Suite.')
        self.assertIn('629 euros', self.history_text(conversation))
        self.work('Atlas livrable vendredi.')
        prompt = _build_system_prompt(self.workspace, 'Atlas', actor=self.user, conversation=conversation)
        self.assertIn('livrable vendredi', prompt)
        self.assertNotIn('629 euros', prompt)  # The active conversation is already in history.

    def test_chat_sends_recall_to_provider_but_temporary_chat_does_not(self):
        cache.clear()
        self.user.credit_account.credit(10000)
        provider = APIProvider.objects.create(name='Continuity', slug='openrouter', base_url='https://example.test')
        provider.keys.create(name='fixture', key='fixture-key')
        model = AIModel.objects.create(provider=provider, name='Fixture', model_id='continuity-test')
        self.chat('Atlas budget confirmé : 417 euros.', persona=self.persona)
        self.client.force_login(self.user)
        data = {'content': 'Quel budget Atlas ?', 'persona_id': self.persona.pk, 'model_id': model.model_id}
        with patch('chat.views.call_llm_provider', return_value=('Réponse', 10, 10)) as call:
            response = self.client.post('/chat/api/send/', json.dumps(data), content_type='application/json')
        self.assertEqual(response.status_code, 200, response.content)
        self.assertIn('417 euros', call.call_args.args[4])
        with patch('chat.views.call_llm_provider', return_value=('Réponse', 10, 10)) as call:
            response = self.client.post('/chat/api/send/', json.dumps({**data, 'temporary': True}), content_type='application/json')
        self.assertEqual(response.status_code, 200, response.content)
        self.assertNotIn('417 euros', call.call_args.args[4])
        url = reverse('chat:persona-context', args=[self.persona.pk])
        self.assertIn('417 euros', self.client.get(url).json()['system_prompt'])
        self.assertNotIn('417 euros', self.client.get(url + '?temporary=1').json()['system_prompt'])

    def test_bara_provider_receives_old_details_and_other_workspace_conversations(self):
        cache.clear()
        self.user.credit_account.credit(10000)
        provider = APIProvider.objects.create(name='Bara continuity', slug='openrouter', base_url='https://example.test')
        provider.keys.create(name='fixture', key='fixture-key')
        model = AIModel.objects.create(provider=provider, name='Fixture', model_id='bara-continuity-test')
        self.workspace.active_model = model
        self.workspace.save(update_fields=['active_model'])
        current = self.work('Budget Atlas : 629 euros.')
        for _ in range(12):
            WorkMessage.objects.create(workspace=self.workspace, conversation=current, role='user', content='Suite du travail.')
        self.work('Livrable Atlas : vendredi.')
        self.client.force_login(self.user)
        events = iter([{'type': 'delta', 'content': 'Réponse Bara.'},
                       {'type': 'done', 'usage': {'prompt_tokens': 10, 'completion_tokens': 10}}])
        with patch('bara.views.call_llm_with_tools_stream', return_value=events) as call:
            response = self.client.post(reverse('bara:message', args=[self.workspace.pk, current.pk]),
                                        {'content': 'Quel budget et livrable Atlas ?'})
            self.assertEqual(response.status_code, 200)
            b''.join(response.streaming_content)
        self.assertIn('Livrable Atlas : vendredi.', call.call_args.kwargs['system_prompt'])
        self.assertIn('629 euros', '\n'.join(row['content'] for row in call.call_args.kwargs['messages']))
