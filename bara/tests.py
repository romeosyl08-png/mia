import io
import os
import shutil
import tempfile
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from django.conf import settings
from django.contrib.auth.models import User
from django.core.files.base import ContentFile
from django.core.files.storage import FileSystemStorage
from django.core.management import call_command
from django.test import TestCase, override_settings
from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse

from api.models import AIModel, APIProvider
from bara.models import ToolExecution, WorkConversation, WorkFile, Workspace, WorkspaceMCPServer
from bara.mcp_client import validate_mcp_endpoint
from bara.services import actual_cost_credits, call_llm_with_tools_stream, get_workspace_context
from bara.tools.definitions import compare_files, read_file_lines, run_command, search_code
from bara.tools.sandbox import run_sandbox_command
from bara.views import _tools_schema
from chat.services import ProviderError
from chat.models import RoutingObservation


class WorkspaceToolTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="workspace-user", password="pw")
        self.workspace = Workspace.objects.create(user=self.user, title="Workspace test")

    def test_workspace_home_lists_active_models_and_creation_saves_selection(self):
        provider = APIProvider.objects.create(name="Workspace provider", base_url="https://provider.example.test")
        model = AIModel.objects.create(provider=provider, name="Workspace model", model_id="workspace-model")
        self.client.force_login(self.user)

        response = self.client.get(reverse("bara:home"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Workspace model")
        self.assertContains(response, "Workspace model ·")
        self.assertNotContains(response, "Aucun modèle disponible")
        self.assertContains(response, 'value="auto"')

        response = self.client.post(reverse("bara:create"), {
            "title": "Workspace choisi",
            "active_model_id": model.id,
        })
        self.assertEqual(response.status_code, 302)
        created = Workspace.objects.get(user=self.user, title="Workspace choisi")
        self.assertEqual(created.active_model_id, model.id)

        response = self.client.post(reverse("bara:create"), {
            "title": "Workspace Auto",
            "active_model_id": "auto",
        })
        self.assertEqual(response.status_code, 302)
        auto_workspace = Workspace.objects.get(user=self.user, title="Workspace Auto")
        self.assertTrue(auto_workspace.auto_route)
        self.assertEqual(auto_workspace.active_model_id, model.id)

    @override_settings(
        SECURE_SSL_REDIRECT=False,
        STORAGES={
            "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
            "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
        },
    )
    def test_workspace_detail_renders_editor_workbench_shell(self):
        self.client.force_login(self.user)

        response = self.client.get(reverse("bara:detail", args=[self.workspace.id]), follow=True)

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'class="bara-wrap bara-workbench"')
        self.assertContains(response, 'class="bara-activity"')
        self.assertContains(response, 'id="toggle-workbench-sidebar"')
        self.assertContains(response, 'class="editor-tabs"')
        self.assertContains(response, 'class="bara-statusbar"')
        self.assertContains(response, 'id="workspace-files-panel"')

    def test_chat_file_upload_uses_selected_workspace_and_is_owner_scoped(self):
        self.client.force_login(self.user)
        with TemporaryDirectory() as storage_root:
            # Le champ porte son propre stockage privé : on le redirige vers un
            # dossier temporaire pour isoler le test du disque du projet.
            with patch.object(
                WorkFile._meta.get_field("file"), "storage", FileSystemStorage(location=storage_root)
            ):
                response = self.client.post(
                    reverse("bara:upload", args=[self.workspace.id]),
                    {"file": SimpleUploadedFile("notes.txt", b"Important notes", content_type="text/plain")},
                )

                self.assertEqual(response.status_code, 200)
                self.assertTrue(response.json()["has_text"])
                uploaded_file = WorkFile.objects.get(workspace=self.workspace, original_name="notes.txt")
                self.assertEqual(uploaded_file.extracted_text, "Important notes")
                self.assertEqual(uploaded_file.file.read(), b"Important notes")
                self.addCleanup(uploaded_file.file.delete, save=False)

            other_user = User.objects.create_user(username="other-workspace-user", password="test-password")
            self.client.force_login(other_user)
            response = self.client.post(
                reverse("bara:upload", args=[self.workspace.id]),
                {"file": SimpleUploadedFile("private.txt", b"private", content_type="text/plain")},
            )

        self.assertEqual(response.status_code, 404)
        self.assertFalse(WorkFile.objects.filter(workspace=self.workspace, original_name="private.txt").exists())

    def test_workspace_files_use_a_private_storage_without_public_url(self):
        """Régression : les pièces jointes étaient écrites dans MEDIA_ROOT."""
        storage = WorkFile._meta.get_field("file").storage
        self.assertNotEqual(
            Path(storage.location).resolve(),
            Path(settings.MEDIA_ROOT).resolve(),
        )
        self.assertEqual(Path(storage.location).resolve(), Path(settings.PRIVATE_MEDIA_ROOT).resolve())
        with self.assertRaises(ValueError):
            storage.url("workspaces/secret.txt")

    def test_public_media_root_is_not_served(self):
        """Régression : /media/ servait les fichiers de tous les espaces de travail."""
        # Le lanceur de tests force DEBUG=False, comme en production.
        self.assertFalse(settings.DEBUG)
        self.assertEqual(self.client.get("/media/workspaces/2026/01/01/secret.txt").status_code, 404)

    def test_workspace_file_download_stays_scoped_to_the_workspace(self):
        self.client.force_login(self.user)
        with TemporaryDirectory() as storage_root:
            with patch.object(
                WorkFile._meta.get_field("file"), "storage", FileSystemStorage(location=storage_root)
            ):
                response = self.client.post(
                    reverse("bara:upload", args=[self.workspace.id]),
                    {"file": SimpleUploadedFile("secret.txt", b"confidentiel", content_type="text/plain")},
                )
                self.assertEqual(response.status_code, 200)
                uploaded_file = WorkFile.objects.get(workspace=self.workspace, original_name="secret.txt")
                self.addCleanup(uploaded_file.file.delete, save=False)

                # Le seul accès légitime passe par la vue qui vérifie l'appartenance.
                authorized = self.client.get(
                    reverse("bara:download-file", args=[self.workspace.id, uploaded_file.id])
                )
                self.assertEqual(authorized.status_code, 200)
                self.assertEqual(b"".join(authorized.streaming_content), b"confidentiel")

                intruder = User.objects.create_user(username="intruder", password="test-password")
                self.client.force_login(intruder)
                self.assertEqual(
                    self.client.get(
                        reverse("bara:download-file", args=[self.workspace.id, uploaded_file.id])
                    ).status_code,
                    404,
                )

        other_user = User.objects.create_user(username="other-workspace-user", password="test-password")
        self.client.force_login(other_user)
        response = self.client.post(
            reverse("bara:upload", args=[self.workspace.id]),
            {"file": SimpleUploadedFile("private.txt", b"private", content_type="text/plain")},
        )

        self.assertEqual(response.status_code, 404)
        self.assertFalse(WorkFile.objects.filter(workspace=self.workspace, original_name="private.txt").exists())

    def test_search_code_finds_matching_lines(self):
        WorkFile.objects.create(
            workspace=self.workspace,
            original_name="app.py",
            content_type="text/plain",
            size_bytes=30,
            extracted_text="print('hello')\nprint('target')\n",
        )

        result = search_code(self.workspace, "target", file_pattern="*.py")

        self.assertEqual(result["truncated"], False)
        self.assertEqual(len(result["results"]), 1)
        self.assertEqual(result["results"][0]["line"], 2)
        self.assertEqual(result["results"][0]["file"], "app.py")

    def test_read_file_lines_returns_bounded_requested_range(self):
        work_file = WorkFile.objects.create(
            workspace=self.workspace,
            original_name="app.py",
            content_type="text/plain",
            size_bytes=18,
            extracted_text="one\ntwo\nthree\nfour\n",
        )

        result = read_file_lines(self.workspace, work_file.id, 2, 3)

        self.assertEqual(result["content"], "two\nthree")
        self.assertEqual(result["start_line"], 2)
        self.assertEqual(result["end_line"], 3)
        self.assertEqual(result["total_lines"], 4)
        self.assertFalse(result["truncated"])

    def test_read_file_lines_rejects_invalid_or_oversized_ranges(self):
        work_file = WorkFile.objects.create(
            workspace=self.workspace,
            original_name="app.py",
            content_type="text/plain",
            size_bytes=5,
            extracted_text="one\n",
        )

        self.assertIn("Plage invalide", read_file_lines(self.workspace, work_file.id, 0, 1)["error"])
        self.assertIn("Plage invalide", read_file_lines(self.workspace, work_file.id, 1.5, 2)["error"])
        self.assertIn("500 lignes maximum", read_file_lines(self.workspace, work_file.id, 1, 501)["error"])
        self.assertIn("n'existe pas", read_file_lines(self.workspace, work_file.id, 2, 2)["error"])

    def test_read_file_lines_caps_long_line_output(self):
        work_file = WorkFile.objects.create(
            workspace=self.workspace,
            original_name="large.txt",
            content_type="text/plain",
            size_bytes=20_001,
            extracted_text="x" * 20_001,
        )

        result = read_file_lines(self.workspace, work_file.id, 1, 1)

        self.assertEqual(len(result["content"]), 20_000)
        self.assertTrue(result["truncated"])

    def test_compare_files_returns_read_only_unified_diff(self):
        first = WorkFile.objects.create(
            workspace=self.workspace,
            original_name="before.py",
            content_type="text/plain",
            size_bytes=10,
            extracted_text="value = 1\n",
        )
        second = WorkFile.objects.create(
            workspace=self.workspace,
            original_name="after.py",
            content_type="text/plain",
            size_bytes=10,
            extracted_text="value = 2\n",
        )

        result = compare_files(self.workspace, first.id, second.id)

        self.assertFalse(result["identical"])
        self.assertIn("-value = 1", result["diff"])
        self.assertIn("+value = 2", result["diff"])
        self.assertLessEqual(len(result["diff"]), 20_000)
        first.refresh_from_db()
        second.refresh_from_db()
        self.assertEqual(first.extracted_text, "value = 1\n")
        self.assertEqual(second.extracted_text, "value = 2\n")

    def test_compare_files_caps_large_diff_output(self):
        first = WorkFile.objects.create(
            workspace=self.workspace,
            original_name="before.txt",
            content_type="text/plain",
            size_bytes=25_000,
            extracted_text="a" * 25_000,
        )
        second = WorkFile.objects.create(
            workspace=self.workspace,
            original_name="after.txt",
            content_type="text/plain",
            size_bytes=25_000,
            extracted_text="b" * 25_000,
        )

        result = compare_files(self.workspace, first.id, second.id)

        self.assertEqual(len(result["diff"]), 20_000)
        self.assertTrue(result["truncated"])

    def test_workspace_context_selects_only_relevant_passages(self):
        WorkFile.objects.create(
            workspace=self.workspace,
            original_name="notes.txt",
            content_type="text/plain",
            extracted_text=(
                "Quasar spectroscopy measures distant galaxy redshift and emission lines.\n\n"
                "The bakery schedule lists bread delivery and oven maintenance."
            ),
        )

        context = get_workspace_context(self.workspace, "quasar spectroscopy emission")

        self.assertIn("Quasar spectroscopy", context)
        self.assertNotIn("bakery schedule", context)

    def test_bara_agent_records_shadow_route_without_changing_selected_model(self):
        provider = APIProvider.objects.create(name="Bara router provider", slug="bara-router", base_url="https://provider.example.test")
        model = AIModel.objects.create(
            provider=provider,
            name="Workspace model",
            model_id="bara-router-model",
            input_price_per_million=1000,
            output_price_per_million=2000,
        )
        provider.keys.create(name="server-key", key="server-secret")
        self.workspace.active_model = model
        self.workspace.save(update_fields=["active_model"])
        conversation = WorkConversation.objects.create(workspace=self.workspace)
        self.user.credit_account.balance = 100000
        self.user.credit_account.save(update_fields=["balance"])
        self.client.force_login(self.user)

        events = [
            {"type": "delta", "content": "Réponse Bara."},
            {"type": "done", "usage": {"prompt_tokens": 1200, "completion_tokens": 800}},
        ]
        with patch("bara.views.estimate_cost", return_value=(1, 1)), patch(
            "bara.views.call_llm_with_tools_stream", return_value=iter(events)
        ) as provider_call:
            response = self.client.post(
                reverse("bara:message", args=[self.workspace.id, conversation.id]),
                {"content": "Analyse ce projet"},
                HTTP_ACCEPT="text/event-stream",
            )
            body = b"".join(response.streaming_content)

        self.assertEqual(response.status_code, 200)
        self.assertIn(b"R\xc3\xa9ponse Bara.", body)
        provider_call.assert_called_once()
        self.assertEqual(provider_call.call_args.kwargs["model_id"], model.model_id)
        observation = RoutingObservation.objects.get(user=self.user, surface="bara")
        self.assertEqual(observation.model_id, model.model_id)
        self.assertEqual(observation.task_category, "coding")
        self.assertEqual(observation.outcome, "success")
        self.user.credit_account.refresh_from_db()
        self.assertEqual(
            self.user.credit_account.balance,
            100000 - actual_cost_credits(model, 1200, 800),
        )

    def test_message_requires_an_explicit_model_or_auto_route(self):
        conversation = WorkConversation.objects.create(workspace=self.workspace)
        self.user.credit_account.balance = 100
        self.user.credit_account.save(update_fields=["balance"])
        self.client.force_login(self.user)

        response = self.client.post(
            reverse("bara:message", args=[self.workspace.id, conversation.id]),
            {"content": "Bonjour"},
        )

        self.assertEqual(response.status_code, 400)
        self.assertIn("Choisis un modèle", response.json()["error"])
        self.user.credit_account.refresh_from_db()
        self.assertEqual(self.user.credit_account.balance, 100)
        self.assertFalse(conversation.messages.exists())

    @override_settings(BARA_CODE_EXECUTION_ENABLED=False)
    def test_disabled_code_execution_tools_are_not_offered_or_saved_as_enabled(self):
        schemas, _ = _tools_schema(self.workspace)
        names = {item["function"]["name"] for item in schemas}
        self.assertNotIn("run_python", names)
        self.assertNotIn("run_command", names)
        self.assertIn("read_file", names)

        self.client.force_login(self.user)
        response = self.client.post(
            reverse("bara:update-tools", args=[self.workspace.id]),
            {"enabled_tools": ["list_files", "run_python", "run_command"]},
        )

        self.assertEqual(response.status_code, 200)
        self.workspace.refresh_from_db()
        self.assertIn("run_python", self.workspace.disabled_tools)
        self.assertIn("run_command", self.workspace.disabled_tools)

    @override_settings(BARA_CODE_EXECUTION_ENABLED=False)
    def test_disabled_execution_tool_call_from_model_is_rejected(self):
        provider = APIProvider.objects.create(
            name="Workspace provider", base_url="https://provider.example.test"
        )
        model = AIModel.objects.create(provider=provider, name="Workspace model", model_id="workspace-model")
        provider.keys.create(name="server-key", key="server-secret")
        self.workspace.active_model = model
        self.workspace.save(update_fields=["active_model"])
        conversation = WorkConversation.objects.create(workspace=self.workspace)
        self.user.credit_account.balance = 1000
        self.user.credit_account.save(update_fields=["balance"])
        self.client.force_login(self.user)
        tool_call = [
            {"type": "tool_call_delta", "index": 0, "id": "call-1", "name": "run_command",
             "arguments_delta": '{"command":["python3","-c","print(1)"]}'},
            {"type": "done", "usage": {"prompt_tokens": 10, "completion_tokens": 5}},
        ]
        final_answer = [
            {"type": "delta", "content": "Cet outil n'est pas autorisé."},
            {"type": "done", "usage": {"prompt_tokens": 10, "completion_tokens": 5}},
        ]

        with patch(
            "bara.views.call_llm_with_tools_stream",
            side_effect=[iter(tool_call), iter(final_answer)],
        ) as provider_call, patch("bara.agent_tools.execute_tool") as execute_tool:
            response = self.client.post(
                reverse("bara:message", args=[self.workspace.id, conversation.id]),
                {"content": "Inspecte cet espace"},
                HTTP_ACCEPT="text/event-stream",
            )
            body = b"".join(response.streaming_content)

        self.assertEqual(response.status_code, 200)
        self.assertIn(b'"status": "tool_blocked"', body)
        tool_messages = [
            message for message in provider_call.call_args_list[1].kwargs["messages"]
            if message.get("role") == "tool"
        ]
        self.assertTrue(tool_messages)
        self.assertIn("Cet outil n'est pas activé", tool_messages[-1]["content"])
        execute_tool.assert_not_called()

    def test_unpaid_cost_over_reserve_is_reported_to_user(self):
        provider = APIProvider.objects.create(
            name="Workspace provider", base_url="https://provider.example.test"
        )
        model = AIModel.objects.create(provider=provider, name="Workspace model", model_id="workspace-model")
        provider.keys.create(name="server-key", key="server-secret")
        self.workspace.active_model = model
        self.workspace.save(update_fields=["active_model"])
        conversation = WorkConversation.objects.create(workspace=self.workspace)
        self.user.credit_account.balance = 5
        self.user.credit_account.save(update_fields=["balance"])
        self.client.force_login(self.user)

        with patch("bara.views.estimate_cost", return_value=(1, 1)), patch(
            "bara.views.actual_cost_credits", return_value=20
        ), patch("bara.views.call_llm_with_tools_stream", return_value=iter([
            {"type": "delta", "content": "Réponse"},
            {"type": "done", "usage": {"prompt_tokens": 1, "completion_tokens": 1}},
        ])):
            response = self.client.post(
                reverse("bara:message", args=[self.workspace.id, conversation.id]),
                {"content": "Bonjour"},
                HTTP_ACCEPT="text/event-stream",
            )
            body = b"".join(response.streaming_content)

        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Solde insuffisant", body)
        self.assertIn(b"15 jetons", body)
        self.user.credit_account.refresh_from_db()
        self.assertEqual(self.user.credit_account.balance, 0)

    def test_bara_auto_workspace_routes_to_observed_model_and_keeps_fallback(self):
        fallback_provider = APIProvider.objects.create(
            name="A Bara fallback", slug="bara-auto-fallback", base_url="https://fallback.example.test"
        )
        fallback_model = AIModel.objects.create(
            provider=fallback_provider, name="Fallback", model_id="bara-auto-fallback-model",
            input_price_per_million=1, output_price_per_million=2,
        )
        fallback_provider.keys.create(name="fallback-key", key="fallback-secret")
        preferred_provider = APIProvider.objects.create(
            name="B Bara preferred", slug="bara-auto-preferred", base_url="https://preferred.example.test"
        )
        preferred_model = AIModel.objects.create(
            provider=preferred_provider, name="Preferred", model_id="bara-auto-preferred-model",
            input_price_per_million=1, output_price_per_million=2,
        )
        preferred_provider.keys.create(name="preferred-key", key="preferred-secret")
        self.workspace.active_model = fallback_model
        self.workspace.auto_route = False
        self.workspace.save(update_fields=["active_model", "auto_route"])
        self.client.force_login(self.user)
        selection_response = self.client.post(
            reverse("bara:update-model", args=[self.workspace.id]),
            {"active_model_id": "auto"},
        )
        self.assertEqual(selection_response.status_code, 302)
        self.workspace.refresh_from_db()
        self.assertTrue(self.workspace.auto_route)
        self.assertEqual(self.workspace.active_model_id, fallback_model.id)
        conversation = WorkConversation.objects.create(workspace=self.workspace)
        self.user.credit_account.balance = 10000
        self.user.credit_account.save(update_fields=["balance"])
        for _ in range(5):
            RoutingObservation.objects.create(
                user=self.user,
                workspace=self.workspace,
                surface="bara",
                task_category="coding",
                model_id=preferred_model.model_id,
                provider_slug=preferred_provider.slug,
                outcome="success",
            )
        with patch("bara.views.call_llm_with_tools_stream", return_value=iter([
            {"type": "delta", "content": "Réponse auto Bara."},
            {"type": "done", "usage": {"prompt_tokens": 12, "completion_tokens": 8}},
        ])) as provider_call:
            response = self.client.post(
                reverse("bara:message", args=[self.workspace.id, conversation.id]),
                {"content": "Analyse ce projet"},
                HTTP_ACCEPT="text/event-stream",
            )
            body = b"".join(response.streaming_content)

        self.assertEqual(response.status_code, 200)
        self.assertIn(b"R\xc3\xa9ponse auto Bara.", body)
        self.assertEqual(provider_call.call_args.kwargs["model_id"], preferred_model.model_id)
        self.workspace.refresh_from_db()
        self.assertTrue(self.workspace.auto_route)
        self.assertEqual(self.workspace.active_model_id, fallback_model.id)

    @override_settings(BARA_CODE_EXECUTION_ENABLED=False)
    def test_run_command_disabled_never_executes_on_host(self):
        with patch('bara.tools.sandbox.subprocess.run') as process:
            result = run_sandbox_command(['python3', '-c', 'print(1)'], cwd='/tmp')
        self.assertIn('error',result)
        process.assert_not_called()

    def test_run_command_materializes_only_workspace_files(self):
        WorkFile.objects.create(workspace=self.workspace, original_name='notes.txt',
                                content_type='text/plain', size_bytes=12, extracted_text='bonjour\n')
        def isolated(command,cwd,timeout):
            from pathlib import Path
            self.assertEqual((Path(cwd)/'notes.txt').read_text(),'bonjour\n')
            self.assertNotEqual(Path(cwd),Path('/tmp'))
            return {'exit_code':0,'stdout':'bonjour','isolated':True}
        with patch('bara.tools.definitions.run_sandbox_command',side_effect=isolated):
            result = run_command(self.workspace,['python3','-c','print(1)'],timeout=15)
        self.assertTrue(result['isolated'])
        self.assertEqual(result['workspace'],'temporary')


class WorkspaceMCPTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="mcp-user", password="pw")
        self.workspace = Workspace.objects.create(user=self.user, title="MCP test")

    def test_provider_key_cannot_be_used_as_base_url(self):
        provider = APIProvider(name="Groq", slug="groq", base_url="sk-test-key")

        with patch("bara.services.urlopen") as urlopen:
            with self.assertRaisesRegex(ProviderError, "URL de connexion"):
                list(call_llm_with_tools_stream(provider, "sk-test-key", "model", []))

        urlopen.assert_not_called()

    def test_stream_provider_connection_reset_is_controlled(self):
        provider = APIProvider(name="Groq", slug="groq", base_url="https://api.example.test")

        with patch("bara.services.urlopen", side_effect=ConnectionResetError("connection reset")):
            with self.assertRaisesRegex(ProviderError, "temporairement indisponible"):
                list(call_llm_with_tools_stream(provider, "test-key", "model", []))

    def test_non_https_endpoint_is_rejected(self):
        self.assertIn("HTTPS", validate_mcp_endpoint("http://127.0.0.1/mcp"))

    def test_private_dns_resolution_is_rejected(self):
        private_address = [(2, 1, 6, "", ("10.0.0.4", 443))]
        with patch("bara.mcp_client.socket.getaddrinfo", return_value=private_address):
            self.assertIn("réseau privé", validate_mcp_endpoint("https://mcp.example/mcp"))

    def test_bearer_token_is_encrypted_at_rest(self):
        server = WorkspaceMCPServer(workspace=self.workspace, name="Remote", endpoint="https://mcp.example/mcp")
        server.set_bearer_token("private-token")
        self.assertNotEqual(server.encrypted_bearer_token, "private-token")
        self.assertEqual(server.get_bearer_token(), "private-token")

    def test_tool_schema_honors_workspace_selection_and_adds_mcp(self):
        self.workspace.disabled_tools = ["read_file"]
        self.workspace.save(update_fields=["disabled_tools"])
        server = WorkspaceMCPServer.objects.create(
            workspace=self.workspace,
            name="Remote",
            endpoint="https://mcp.example/mcp",
            available_tools=[{"name": "lookup", "description": "Search remote data", "inputSchema": {"type": "object", "properties": {}}}],
        )
        schemas, mcp_map = _tools_schema(self.workspace)
        names = {item["function"]["name"] for item in schemas}
        self.assertNotIn("read_file", names)
        self.assertIn("read_file_lines", names)
        self.assertIn("compare_files", names)
        self.assertIn(f"mcp_{server.id}_0", names)
        self.assertEqual(mcp_map[f"mcp_{server.id}_0"], (server, "lookup"))

    def test_add_server_discovers_tools_and_encrypts_token(self):
        self.client.force_login(self.user)
        tools = [{"name": "lookup", "description": "Lookup", "inputSchema": {"type": "object", "properties": {}}}]
        with patch("bara.views.validate_mcp_endpoint", return_value=None), patch("bara.views.list_mcp_tools", return_value=tools):
            response = self.client.post(reverse("bara:add-mcp-server", args=[self.workspace.id]), {
                "name": "Remote", "endpoint": "https://mcp.example/mcp", "bearer_token": "secret-token",
            })
        self.assertEqual(response.status_code, 201)
        server = WorkspaceMCPServer.objects.get(workspace=self.workspace, name="Remote")
        self.assertNotIn("secret-token", server.encrypted_bearer_token)
        self.assertEqual(server.get_bearer_token(), "secret-token")
        self.assertEqual(server.available_tools, tools)

    def test_workspace_detail_renders_tool_and_mcp_controls(self):
        self.client.force_login(self.user)
        conversation = WorkConversation.objects.create(workspace=self.workspace)
        with override_settings(BARA_CODE_EXECUTION_ENABLED=False):
            response = self.client.get(reverse("bara:conversation", args=[self.workspace.id, conversation.id]))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Outils intégrés")
        self.assertContains(response, "AUTOMATISATIONS")
        self.assertRegex(response.content.decode(), r'aria-label="(?:Connecter Gmail|Gmail OAuth non configuré|Gmail connecté)"')
        self.assertRegex(response.content.decode(), r'aria-label="(?:Connecter Outlook|Outlook OAuth non configuré|Outlook connecté)"')
        self.assertRegex(response.content.decode(), r'aria-label="(?:Connecter|Renouveler) (?:la connexion )?Blender"')
        self.assertContains(response, "Connecter l'automatisation")
        # Le panneau décrit le runtime réel (conteneur isolé), plus l'ancien service Piston.
        self.assertContains(response, "conteneur isolé sans réseau")
        self.assertNotContains(response, "Piston")
        self.assertContains(response, "désactivés par la configuration du serveur")

    def test_workspace_tool_preferences_are_saved(self):
        self.client.force_login(self.user)
        response = self.client.post(
            reverse("bara:update-tools", args=[self.workspace.id]),
            {"enabled_tools": ["list_files", "read_file"]},
        )
        self.assertEqual(response.status_code, 200)
        self.workspace.refresh_from_db()
        self.assertNotIn("list_files", self.workspace.disabled_tools)
        self.assertIn("run_command", self.workspace.disabled_tools)

    def test_server_management_is_scoped_to_workspace_owner(self):
        other_user = User.objects.create_user(username="other-mcp-user", password="pw")
        self.client.force_login(other_user)
        response = self.client.post(reverse("bara:add-mcp-server", args=[self.workspace.id]), {
            "name": "Remote", "endpoint": "https://mcp.example/mcp",
        })
        self.assertEqual(response.status_code, 404)

    def test_mcp_tool_runs_only_after_confirmation(self):
        self.client.force_login(self.user)
        server = WorkspaceMCPServer.objects.create(
            workspace=self.workspace, name="Remote", endpoint="https://mcp.example/mcp",
        )
        conversation = WorkConversation.objects.create(workspace=self.workspace)
        action = ToolExecution.objects.create(
            workspace=self.workspace,
            tool_name="mcp_call",
            status="pending",
            arguments={
                "server_id": server.id,
                "server_name": server.name,
                "tool_name": "lookup",
                "tool_arguments": {"query": "test"},
                "conversation_id": conversation.id,
            },
        )
        url = reverse("bara:resolve-action", args=[self.workspace.id, action.id, "confirm"])
        with patch("bara.views.call_mcp_tool", return_value={"content": [{"type": "text", "text": "done"}]} ) as call:
            response = self.client.post(url)
        self.assertEqual(response.status_code, 200)
        call.assert_called_once_with(server, "lookup", {"query": "test"})
        action.refresh_from_db()
        self.assertEqual(action.status, "success")
        self.assertTrue(conversation.messages.filter(content__contains="done").exists())


class ToolExecutorErrorTests(TestCase):
    """Les erreurs d'outil ne doivent pas fuiter dans le contexte du modèle."""

    class Boom:
        pass

    def test_unexpected_errors_are_logged_and_summarized(self):
        from bara.tools import executor

        def failing(workspace):
            raise RuntimeError("secret /srv/app/config.py et détails internes")

        executor.TOOLS["probe_failure"] = {"function": failing}
        self.addCleanup(executor.TOOLS.pop, "probe_failure", None)

        with self.assertLogs("bara.tools.executor", level="ERROR") as logs:
            result = executor.execute_tool("probe_failure", None, {})

        self.assertEqual(result, {"error": executor.GENERIC_ERROR})
        self.assertIn("secret /srv/app/config.py", "\n".join(logs.output))

    def test_bad_arguments_stay_explicit_for_the_model(self):
        from bara.tools import executor

        def needs_argument(workspace, required):
            return required

        executor.TOOLS["probe_arguments"] = {"function": needs_argument}
        self.addCleanup(executor.TOOLS.pop, "probe_arguments", None)

        result = executor.execute_tool("probe_arguments", None, {})
        self.assertIn("Arguments invalides", result["error"])

    def test_unknown_tools_are_reported(self):
        from bara.tools import executor

        self.assertEqual(executor.execute_tool("inexistant", None, {})["error"], "Outil inconnu : inexistant")


class PrivateStorageMigrationTests(TestCase):
    """La commande déplace les pièces jointes déjà enregistrées dans MEDIA_ROOT."""

    def setUp(self):
        self.user = User.objects.create_user("migration-user")
        self.workspace = Workspace.objects.create(user=self.user, title="Migration")
        self.public = tempfile.mkdtemp(prefix="2ai-media-")
        self.private = tempfile.mkdtemp(prefix="2ai-private-")
        self.addCleanup(shutil.rmtree, self.public, True)
        self.addCleanup(shutil.rmtree, self.private, True)

    def create_legacy_file(self, name="secret.txt", content=b"confidentiel"):
        """Écrit une pièce jointe là où l'ancienne version l'aurait mise."""
        legacy = FileSystemStorage(location=self.public)
        saved = legacy.save(f"workspaces/2026/01/01/{name}", ContentFile(content))
        return WorkFile.objects.create(
            workspace=self.workspace, original_name=name, file=saved, size_bytes=len(content)
        )

    def run_command(self, dry_run=False):
        with override_settings(MEDIA_ROOT=self.public, PRIVATE_MEDIA_ROOT=self.private):
            with patch.object(WorkFile._meta.get_field("file"), "storage", _PrivateStorage(self.private)):
                out = io.StringIO()
                call_command("migrate_files_to_private_storage",
                             *(["--dry-run"] if dry_run else []), stdout=out, stderr=out)
                return out.getvalue()

    def test_files_are_moved_out_of_the_public_media_root(self):
        work_file = self.create_legacy_file()
        output = self.run_command()
        self.assertIn("1 fichier(s) déplacé(s)", output)
        self.assertTrue((Path(self.private) / work_file.file.name).is_file())
        self.assertFalse((Path(self.public) / work_file.file.name).exists())

    def test_dry_run_changes_nothing(self):
        work_file = self.create_legacy_file()
        output = self.run_command(dry_run=True)
        self.assertIn("[simulation]", output)
        self.assertFalse((Path(self.private) / work_file.file.name).exists())
        self.assertTrue((Path(self.public) / work_file.file.name).exists())

    def test_already_private_files_are_left_alone(self):
        work_file = self.create_legacy_file()
        (Path(self.private) / work_file.file.name).parent.mkdir(parents=True, exist_ok=True)
        (Path(self.private) / work_file.file.name).write_bytes(b"deja la")
        output = self.run_command()
        self.assertIn("1 déjà en place", output)

    def test_missing_files_are_reported(self):
        WorkFile.objects.create(
            workspace=self.workspace, original_name="absent.txt", file="workspaces/2026/01/01/absent.txt"
        )
        output = self.run_command()
        self.assertIn("1 introuvable(s)", output)


class _PrivateStorage(FileSystemStorage):
    """Stockage privé reconstruit à la volée pour que la commande lise le bon dossier."""

    def __init__(self, location):
        super().__init__(location=location)

    def url(self, name):
        raise ValueError("Private media must be served through the owner-checked view.")
