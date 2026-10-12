import json
from importlib import import_module
from types import SimpleNamespace
from unittest.mock import patch

from django.apps import apps
from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied
from django.db import IntegrityError, connection, transaction
from django.test import TestCase, override_settings
from django.urls import reverse

from api.models import AIModel, APIProvider
from .models import ChatPreferences, Conversation, Message, Persona
from .persona_context import ensure_big_persona, persona_snapshot, shared_persona_context


@override_settings(SEARCH_API_KEY='')
class BigPersonaTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = get_user_model().objects.create_user('big-owner', email='owner@example.test', first_name='Mia', last_name='Test')
        cls.other = get_user_model().objects.create_user('big-other', email='other@example.test')
        cls.big = cls.user.personas.get(is_big_persona=True)
        cls.source = cls.user.personas.filter(is_big_persona=False).first()
        cls.source.prompt = 'Instructions source autorisées'
        cls.source.memory = 'Préfère des exemples Django'
        cls.source.save()
        cls.private = cls.user.personas.filter(is_big_persona=False).exclude(pk=cls.source.pk).first()
        cls.private.memory = 'Mémoire privée non autorisée'
        cls.private.save()
        cls.foreign = cls.other.personas.filter(is_big_persona=False).first()
        cls.foreign.memory = 'Mémoire d’un autre compte'
        cls.foreign.share_with_personas = True
        cls.foreign.save()
        provider = APIProvider.objects.create(name='Big fixture', slug='openrouter', base_url='https://example.test')
        provider.keys.create(name='fixture', key='private-provider-key')
        cls.model = AIModel.objects.create(provider=provider, name='Fixture', model_id='big-fixture', input_price_per_million=1, output_price_per_million=1)
        cls.preferences, _ = ChatPreferences.objects.get_or_create(user=cls.user)

    def setUp(self):
        self.client.force_login(self.user)

    def authorize(self, persona=None, value=True):
        persona = persona or self.source
        response = self.client.patch(reverse('chat:chat-persona-detail', args=[persona.pk]),
                                     data=json.dumps({'share_with_personas': value}), content_type='application/json')
        self.assertEqual(response.status_code, 200)
        persona.refresh_from_db()
        return response

    def test_new_accounts_receive_one_big_persona_and_three_private_classic_personas(self):
        self.assertEqual(self.user.personas.filter(is_big_persona=True).count(), 1)
        self.assertEqual(self.user.personas.filter(is_big_persona=False).count(), 3)
        self.assertFalse(self.user.personas.filter(share_with_personas=True).exists())
        for _ in range(2):
            self.client.get(reverse('chat:chat-persona-list'))
            ensure_big_persona(self.user)
        self.assertEqual(self.user.personas.filter(is_big_persona=True).count(), 1)

    def test_big_persona_is_unique_and_cannot_be_a_shared_source(self):
        with self.assertRaises(IntegrityError), transaction.atomic():
            Persona.objects.create(user=self.user, name='Duplicate', is_big_persona=True)
        with self.assertRaises(IntegrityError), transaction.atomic():
            Persona.objects.filter(pk=self.big.pk).update(share_with_personas=True)
        self.assertEqual(self.client.delete(reverse('chat:chat-persona-detail', args=[self.big.pk])).status_code, 400)

    def test_only_explicitly_authorized_personas_from_this_account_are_included(self):
        self.authorize()
        data = persona_snapshot(self.user, self.big)
        self.assertEqual([persona['id'] for persona in data['personas']], [self.source.pk])
        self.assertEqual(data['personas'][0]['memory'], self.source.memory)
        self.assertEqual(data['personas'][0]['prompt'], self.source.prompt)
        self.assertNotIn(self.private.memory, json.dumps(data, ensure_ascii=False))
        self.assertNotIn(self.foreign.memory, json.dumps(data, ensure_ascii=False))

    def test_regular_personas_receive_peer_context_only_when_they_also_authorize_linking(self):
        self.authorize()
        self.assertEqual(shared_persona_context(self.user, self.private), '')
        self.authorize(self.private)
        context = shared_persona_context(self.user, self.private)
        self.assertIn(self.source.memory, context)
        self.assertNotIn(self.private.memory, context)
        self.assertNotIn(self.user.email, context)
        self.assertNotIn(self.foreign.memory, context)

    def test_revocation_and_memory_changes_take_effect_without_copying_into_big_persona(self):
        self.authorize()
        self.assertIn(self.source.memory, shared_persona_context(self.user, self.big))
        self.source.memory = 'Nouvelle préférence partagée'
        self.source.save(update_fields=['memory'])
        self.assertIn(self.source.memory, shared_persona_context(self.user, self.big))
        self.authorize(value=False)
        self.assertNotIn(self.source.memory, shared_persona_context(self.user, self.big))
        self.big.refresh_from_db()
        self.assertEqual(self.big.memory, '')

    def test_other_personas_conversations_are_never_part_of_the_shared_snapshot(self):
        self.authorize()
        conversation = Conversation.objects.create(user=self.user, persona=self.source)
        Message.objects.create(conversation=conversation, role='user', content='Conversation privée non partagée')
        self.assertNotIn('Conversation privée non partagée', shared_persona_context(self.user, self.big))

    def test_account_profile_and_functional_site_settings_exclude_credentials(self):
        self.preferences.default_model_id = self.model.model_id
        self.preferences.max_tokens = 700
        self.preferences.save()
        with override_settings(SECRET_KEY='server-secret-sentinel', GOOGLE_CLIENT_SECRET='oauth-secret-sentinel', DATABASE_URL='database-secret-sentinel'):
            self.client.force_login(self.user)
            response = self.client.get(reverse('chat:big-persona'))
        self.assertEqual(response.status_code, 200)
        self.assertIn('no-store', response['Cache-Control'])
        data = response.json()
        self.assertEqual(data['user']['email'], self.user.email)
        self.assertEqual(data['user']['first_name'], 'Mia')
        self.assertEqual(data['site']['generation']['max_tokens'], 700)
        self.assertEqual(data['site']['generation']['default_model_id'], self.model.model_id)
        for secret in ['server-secret-sentinel', 'oauth-secret-sentinel', 'database-secret-sentinel', 'private-provider-key', self.user.password, self.other.email]:
            self.assertNotIn(secret, response.content.decode())

    def test_context_routes_require_authentication_and_owner_access(self):
        self.assertEqual(self.client.get(reverse('chat:persona-context', args=[self.foreign.pk])).status_code, 404)
        self.assertEqual(self.client.patch(reverse('chat:chat-persona-detail', args=[self.foreign.pk]),
                                          data=json.dumps({'share_with_personas': False}), content_type='application/json').status_code, 404)
        with self.assertRaises(PermissionDenied):
            shared_persona_context(self.user, self.foreign)
        self.client.logout()
        self.assertEqual(self.client.get(reverse('chat:big-persona')).status_code, 302)
        self.assertEqual(self.client.get(reverse('chat:persona-context', args=[self.big.pk])).status_code, 302)

    def test_consent_is_a_strict_boolean_and_big_persona_type_cannot_be_forged(self):
        for value in ['false', 'true', 1, None, {}]:
            with self.subTest(value=value):
                response = self.client.patch(reverse('chat:chat-persona-detail', args=[self.source.pk]),
                                             data=json.dumps({'share_with_personas': value}), content_type='application/json')
                self.assertEqual(response.status_code, 400)
        for url in [reverse('chat:chat-persona-list'), reverse('chat:chat-persona-detail', args=[self.source.pk])]:
            method = self.client.post if url.endswith('/personas/') else self.client.patch
            self.assertEqual(method(url, data=json.dumps({'is_big_persona': True}), content_type='application/json').status_code, 400)
        response = self.client.patch(reverse('chat:chat-persona-detail', args=[self.big.pk]),
                                     data=json.dumps({'share_with_personas': True}), content_type='application/json')
        self.assertEqual(response.status_code, 400)

    def test_unrelated_persona_save_does_not_write_or_reset_the_sharing_permission(self):
        self.authorize()
        original_save = Persona.save

        def revoke_during_save(instance, *args, **kwargs):
            Persona.objects.filter(pk=instance.pk).update(share_with_personas=False)
            return original_save(instance, *args, **kwargs)

        with patch.object(Persona, 'save', autospec=True, side_effect=revoke_during_save) as save:
            response = self.client.patch(reverse('chat:chat-persona-detail', args=[self.source.pk]),
                                         data=json.dumps({'memory': 'Updated'}), content_type='application/json')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(save.call_args.kwargs['update_fields'], {'memory'})
        self.source.refresh_from_db()
        self.assertFalse(self.source.share_with_personas)
        self.assertEqual(self.source.memory, 'Updated')
        self.assertFalse(response.json()['share_with_personas'])

    def test_big_persona_context_reaches_the_cloud_provider_and_follows_revocation(self):
        self.authorize()
        payload = {'model_id': self.model.model_id, 'persona_id': self.big.pk, 'content': 'Aide-moi'}
        with patch('chat.views.call_llm_provider', return_value=('Réponse', 30, 10)) as provider:
            response = self.client.post('/chat/api/send/', data=json.dumps(payload), content_type='application/json')
        self.assertEqual(response.status_code, 200)
        self.assertIn(self.source.memory, provider.call_args.args[4])
        self.assertIn(self.user.email, provider.call_args.args[4])
        self.assertNotIn(self.private.memory, provider.call_args.args[4])
        self.authorize(value=False)
        payload = {'model_id': self.model.model_id, 'conversation_id': response.json()['conversation_id'], 'content': 'Et maintenant ?'}
        with patch('chat.views.call_llm_provider', return_value=('Nouvelle réponse', 30, 10)) as provider:
            response = self.client.post('/chat/api/send/', data=json.dumps(payload), content_type='application/json')
        self.assertEqual(response.status_code, 200)
        self.assertIn(self.user.email, provider.call_args.args[4])
        self.assertNotIn(self.source.memory, provider.call_args.args[4])

    def test_local_model_context_endpoint_refreshes_permissions_and_keeps_own_memory(self):
        self.authorize()
        self.big.memory = 'Mémoire personnelle de Big Persona'
        self.big.save(update_fields=['memory'])
        url = reverse('chat:persona-context', args=[self.big.pk])
        response = self.client.get(url)
        self.assertIn('no-store', response['Cache-Control'])
        self.assertIn(self.source.memory, response.json()['system_prompt'])
        self.assertIn(self.big.memory, response.json()['system_prompt'])
        self.authorize(value=False)
        self.assertNotIn(self.source.memory, self.client.get(url).json()['system_prompt'])

    def test_data_migration_backfills_existing_accounts_idempotently_and_without_sharing(self):
        self.big.delete()
        migration = import_module('chat.migrations.0024_persona_sharing_big_persona')
        for _ in range(2):
            migration.create_big_personas(apps, SimpleNamespace(connection=connection))
        self.assertEqual(self.user.personas.filter(is_big_persona=True).count(), 1)
        self.assertFalse(self.user.personas.filter(is_big_persona=True).get().share_with_personas)
