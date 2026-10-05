import os
from unittest.mock import patch

from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse

from api.models import AIModel, APIProvider
from bara.models import ToolExecution, WorkConversation, WorkFile, Workspace, WorkspaceMCPServer
from bara.mcp_client import validate_mcp_endpoint
from bara.services import call_llm_with_tools_stream, get_workspace_context
from bara.tools.definitions import run_command, search_code
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
        model = AIModel.objects.create(provider=provider, name="Workspace model", model_id="bara-router-model")
        provider.keys.create(name="server-key", key="server-secret")
        self.workspace.active_model = model
        self.workspace.save(update_fields=["active_model"])
        conversation = WorkConversation.objects.create(workspace=self.workspace)
        self.user.credit_account.balance = 10000
        self.user.credit_account.save(update_fields=["balance"])
        self.client.force_login(self.user)

        events = [
            {"type": "delta", "content": "Réponse Bara."},
            {"type": "done", "usage": {"prompt_tokens": 12, "completion_tokens": 8}},
        ]
        with patch("bara.views.call_llm_with_tools_stream", return_value=iter(events)) as provider_call:
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

    def test_run_command_preserves_environment_home(self):
        old_home = os.environ.get("HOME")
        os.environ["HOME"] = "/tmp/workspace-home"
        try:
            result = run_sandbox_command([
                "python3",
                "-c",
                "import os; print(os.environ.get('HOME', ''))",
            ], cwd="/tmp", timeout=10)
        finally:
            if old_home is None:
                os.environ.pop("HOME", None)
            else:
                os.environ["HOME"] = old_home

        self.assertEqual(result.get("exit_code"), 0)
        self.assertIn("/tmp/workspace-home", result["stdout"])

    def test_run_command_executes_in_temp_workspace(self):
        WorkFile.objects.create(
            workspace=self.workspace,
            original_name="notes.txt",
            content_type="text/plain",
            size_bytes=12,
            extracted_text="bonjour\n",
        )

        result = run_command(self.workspace, ["python3", "-c", "from pathlib import Path; print(Path('notes.txt').read_text())"], timeout=15)

        self.assertEqual(result.get("exit_code"), 0)
        self.assertIn("bonjour", result.get("stdout", ""))
        self.assertEqual(result.get("workspace"), "temporary")


class WorkspaceMCPTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="mcp-user", password="pw")
        self.workspace = Workspace.objects.create(user=self.user, title="MCP test")

    def test_provider_key_cannot_be_used_as_base_url(self):
        provider = APIProvider(name="Groq", slug="groq", base_url="sk-test-key")

        with patch("bara.services.urlopen") as urlopen:
            with self.assertRaisesRegex(ProviderError, "URL de base du fournisseur"):
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
        response = self.client.get(reverse("bara:conversation", args=[self.workspace.id, conversation.id]))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Outils &amp; Model Context Protocol")
        self.assertContains(response, "Connecter le serveur MCP")

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
