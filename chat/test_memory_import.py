import json

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client, SimpleTestCase, TestCase, override_settings
from django.urls import reverse

from bara.models import Workspace
from .memory import memory_context
from .memory_import import (
    IMPORT_MAX_BYTES, IMPORT_MAX_CHARS, MemoryImportError, parse_memory_import,
)
from .models import UserMemory


class MemoryExportParsingTests(SimpleTestCase):
    def test_plain_text_and_markdown_from_any_assistant_preserve_preferences(self):
        for text in (
            'Réponds en français.\nJe travaille avec Django.',
            '```markdown\nRéponds en français.\nJe travaille avec Django.\n```',
        ):
            with self.subTest(text=text):
                self.assertEqual(parse_memory_import(text), 'Réponds en français.\nJe travaille avec Django.')
        self.assertEqual(parse_memory_import('[2026-10-14] - Réponses courtes'), '[2026-10-14] - Réponses courtes')

    def test_utf8_markdown_upload_keeps_accents_and_removes_bom(self):
        file = SimpleUploadedFile('souvenirs.MD', '\ufeff- Préfère Python\r\n- Utilise le français'.encode())
        self.assertEqual(parse_memory_import(uploaded_file=file), '- Préfère Python\n- Utilise le français')

    def test_structured_json_ignores_metadata_and_keeps_memory_entries(self):
        data = {'memories': [
            {'id': 'unrelated-id', 'content': 'Réponds en français', 'created_at': '2026-10-14'},
            {'text': 'Utilise Python'},
        ], 'provider': 'another-assistant'}
        file = SimpleUploadedFile('memory.json', json.dumps(data).encode())
        self.assertEqual(parse_memory_import(uploaded_file=file), 'Réponds en français\nUtilise Python')
        self.assertEqual(parse_memory_import('["Réponses courtes", "Exemples concrets"]'), 'Réponses courtes\nExemples concrets')
        self.assertEqual(parse_memory_import('```json\n{"preferences": {"langue": "français"}}\n```'), 'preferences : langue : français')

    def test_conversation_archives_are_not_mistaken_for_memory_exports(self):
        for archive in (
            {'mapping': {'node': {'message': {'content': 'chat'}}}},
            [{'uuid': 'chat-id', 'chat_messages': [{'text': 'chat'}]}],
            {'conversations': [{'messages': []}]},
        ):
            with self.subTest(archive=archive):
                with self.assertRaisesMessage(MemoryImportError, 'historique de conversations'):
                    parse_memory_import(json.dumps(archive))

    def test_bad_files_and_ambiguous_inputs_are_rejected(self):
        cases = (
            ('', SimpleUploadedFile('memory.zip', b'archive')),
            ('', SimpleUploadedFile('memory.txt', b'\xff')),
            ('', SimpleUploadedFile('memory.txt', b'text\x00binary')),
            ('', SimpleUploadedFile('memory.json', b'{not-json')),
            ('texte', SimpleUploadedFile('memory.md', b'other text')),
            ('', SimpleUploadedFile('memory.txt', b' ')),
        )
        for text, file in cases:
            with self.subTest(filename=file.name, text=text):
                with self.assertRaises(MemoryImportError):
                    parse_memory_import(text, file)

    def test_limits_and_escaped_binary_data_cannot_bypass_validation(self):
        with self.assertRaisesMessage(MemoryImportError, '256 Ko'):
            parse_memory_import(uploaded_file=SimpleUploadedFile('memory.txt', b'x' * (IMPORT_MAX_BYTES + 1)))
        with self.assertRaisesMessage(MemoryImportError, '64 000'):
            parse_memory_import('x' * (IMPORT_MAX_CHARS + 1))
        for text in ('{"content": "binary\\u0000"}', '{"content": "invalid\\ud800"}'):
            with self.subTest(text=text):
                with self.assertRaisesMessage(MemoryImportError, 'texte lisible'):
                    parse_memory_import(text)
        with self.assertRaisesMessage(MemoryImportError, 'imbriquée'):
            parse_memory_import(json.dumps([[[[[[[[[['too deep']]]]]]]]]]))
        with self.assertRaisesMessage(MemoryImportError, 'imbriquée'):
            parse_memory_import('[' * 2000 + '"text"' + ']' * 2000)


@override_settings(SECURE_SSL_REDIRECT=False, STORAGES={
    'default': {'BACKEND': 'django.core.files.storage.FileSystemStorage'},
    'staticfiles': {'BACKEND': 'django.contrib.staticfiles.storage.StaticFilesStorage'},
})
class MemoryImportTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user('memory-import-owner')
        self.other = get_user_model().objects.create_user('memory-import-other')
        self.memory = UserMemory.objects.create(user=self.user, content='Mémoire déjà enregistrée', enabled=False)
        self.workspace = Workspace.objects.create(user=self.user, title='Projet privé', memory='Contexte du projet',
                                                  memory_enabled=False, semantic_search=True)
        self.client.force_login(self.user)
        self.url = reverse('chat:memory')

    def preview(self, text='Réponds en français', url=None):
        response = self.client.post(url or self.url, {'action': 'preview_import', 'import_text': text})
        self.assertEqual(response.status_code, 200)
        return response

    def apply(self, preview, text='Réponds en français', mode='append', url=None):
        return self.client.post(url or self.url, {
            'action': 'apply_import', 'import_content': text,
            'import_mode': mode, 'revision': preview.context['revision'],
        })

    def test_preview_does_not_save_and_is_editable_and_private(self):
        preview = self.preview('```\nRéponds en français\n```')
        self.assertEqual(preview.context['import_preview'], 'Réponds en français')
        self.assertContains(preview, 'id="importContent"')
        self.assertContains(preview, 'Ajouter à la mémoire existante')
        self.assertIn('no-store', preview.headers['Cache-Control'])
        self.memory.refresh_from_db()
        self.assertEqual(self.memory.content, 'Mémoire déjà enregistrée')
        self.assertFalse(self.memory.enabled)

    def test_add_uses_reviewed_text_and_preserves_existing_memory_and_disabled_state(self):
        preview = self.preview()
        response = self.apply(preview, text='Texte relu et corrigé')
        self.assertRedirects(response, self.url + '#saved-memory')
        self.memory.refresh_from_db()
        self.assertEqual(self.memory.content, 'Mémoire déjà enregistrée\n\nTexte relu et corrigé')
        self.assertFalse(self.memory.enabled)
        self.assertNotIn('Texte relu', memory_context(self.user))
        self.memory.enabled = True
        self.memory.save()
        self.assertIn('Texte relu', memory_context(self.user))
        self.assertNotIn('Texte relu', memory_context(self.other))

    def test_explicit_replace_and_repeated_add_do_not_accumulate_duplicates(self):
        self.assertEqual(self.apply(self.preview(), mode='replace').status_code, 302)
        self.memory.refresh_from_db()
        self.assertEqual(self.memory.content, 'Réponds en français')
        self.assertEqual(self.apply(self.preview()).status_code, 302)
        self.memory.refresh_from_db()
        self.assertEqual(self.memory.content, 'Réponds en français')

    def test_large_preview_can_be_edited_before_saving_without_silent_truncation(self):
        preview = self.preview('x' * 9000)
        self.assertEqual(len(preview.context['import_preview']), 9000)
        response = self.apply(preview, text='x' * 9000)
        self.assertContains(response, 'maximum 8 000', status_code=400)
        self.memory.refresh_from_db()
        self.assertEqual(self.memory.content, 'Mémoire déjà enregistrée')
        response = self.apply(preview, text='Préférence raccourcie')
        self.assertEqual(response.status_code, 302)

    def test_append_checks_combined_limit_and_replace_can_use_full_capacity(self):
        self.memory.content = 'x' * 7999
        self.memory.save()
        preview = self.preview('court')
        self.assertEqual(self.apply(preview, text='court').status_code, 400)
        self.memory.refresh_from_db()
        self.assertEqual(len(self.memory.content), 7999)
        self.assertEqual(self.apply(preview, text='y' * 8000, mode='replace').status_code, 302)
        self.memory.refresh_from_db()
        self.assertEqual(self.memory.content, 'y' * 8000)

    def test_concurrent_memory_edit_requires_a_new_review(self):
        preview = self.preview()
        self.memory.content = 'Préférence ajoutée dans un autre onglet'
        self.memory.save()
        response = self.apply(preview, mode='replace')
        self.assertContains(response, 'a changé depuis cet aperçu', status_code=400)
        self.memory.refresh_from_db()
        self.assertEqual(self.memory.content, 'Préférence ajoutée dans un autre onglet')
        self.assertEqual(response.context['content'], self.memory.content)
        self.assertEqual(self.apply(response).status_code, 302)
        self.memory.refresh_from_db()
        self.assertIn('Préférence ajoutée dans un autre onglet', self.memory.content)

    def test_workspace_import_preserves_scope_and_project_switches(self):
        url = reverse('chat:project-memory', args=[self.workspace.pk])
        preview = self.preview(url=url)
        self.assertEqual(self.apply(preview, url=url).status_code, 302)
        self.workspace.refresh_from_db()
        self.memory.refresh_from_db()
        self.assertEqual(self.workspace.memory, 'Contexte du projet\n\nRéponds en français')
        self.assertFalse(self.workspace.memory_enabled)
        self.assertTrue(self.workspace.semantic_search)
        self.assertEqual(self.memory.content, 'Mémoire déjà enregistrée')
        self.client.force_login(self.other)
        self.assertEqual(self.client.post(url, {'action': 'preview_import', 'import_text': 'secret'}).status_code, 404)
        self.assertEqual(self.client.post(url, {'action': 'apply_import', 'import_content': 'secret'}).status_code, 404)

    def test_upload_is_previewed_without_persisting_the_file(self):
        file = SimpleUploadedFile('memory.json', b'{"memories": ["Utilise Python"]}', content_type='application/json')
        response = self.client.post(self.url, {'action': 'preview_import', 'import_file': file})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context['import_preview'], 'Utilise Python')
        self.memory.refresh_from_db()
        self.assertEqual(self.memory.content, 'Mémoire déjà enregistrée')

    def test_empty_invalid_and_oversized_imports_never_modify_memory(self):
        preview = self.preview()
        for text, mode in (('', 'replace'), ('x' * (IMPORT_MAX_CHARS + 1), 'append'), ('text', 'invalid')):
            with self.subTest(mode=mode, length=len(text)):
                self.assertEqual(self.apply(preview, text=text, mode=mode).status_code, 400)
        self.memory.refresh_from_db()
        self.assertEqual(self.memory.content, 'Mémoire déjà enregistrée')

    def test_import_requires_login_and_csrf(self):
        client = Client(enforce_csrf_checks=True)
        client.force_login(self.user)
        self.assertEqual(client.post(self.url, {'action': 'preview_import', 'import_text': 'test'}).status_code, 403)
        self.client.logout()
        self.assertEqual(self.client.post(self.url, {'action': 'preview_import', 'import_text': 'test'}).status_code, 302)

    def test_exported_html_is_escaped_in_the_preview(self):
        payload = '</textarea><script>alert(1)</script>'
        preview = self.preview(payload)
        self.assertContains(preview, '&lt;/textarea&gt;&lt;script&gt;')
        self.assertNotContains(preview, payload)

    def test_existing_manual_editor_still_saves_and_disables_memory(self):
        self.assertEqual(self.client.post(self.url, {'content': 'Mémoire manuelle', 'enabled': 'on'}).status_code, 302)
        self.memory.refresh_from_db()
        self.assertEqual(self.memory.content, 'Mémoire manuelle')
        self.assertTrue(self.memory.enabled)
        self.assertEqual(self.client.post(self.url, {'content': ''}).status_code, 302)
        self.memory.refresh_from_db()
        self.assertEqual(self.memory.content, '')
        self.assertFalse(self.memory.enabled)

    def test_manual_save_over_limit_keeps_submitted_text_for_correction(self):
        response = self.client.post(self.url, {'content': 'x' * 8001, 'enabled': 'on'})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.context['content'], 'x' * 8001)
        self.memory.refresh_from_db()
        self.assertEqual(self.memory.content, 'Mémoire déjà enregistrée')

    def test_imported_memory_is_available_to_local_models_and_excluded_from_temporary_chat(self):
        self.assertEqual(self.apply(self.preview(), mode='replace').status_code, 302)
        self.memory.enabled = True
        self.memory.save(update_fields=['enabled'])
        persona = self.user.personas.filter(is_big_persona=False).first()
        url = reverse('chat:persona-context', args=[persona.pk])
        self.assertIn('Réponds en français', self.client.get(url).json()['system_prompt'])
        self.assertNotIn('Réponds en français', self.client.get(url + '?temporary=1').json()['system_prompt'])
