import os
from unittest.mock import patch

from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse

from bara.models import ToolExecution, WorkConversation, WorkFile, Workspace, WorkspaceMCPServer
from bara.mcp_client import validate_mcp_endpoint
from bara.tools.definitions import run_command, search_code
from bara.tools.sandbox import run_sandbox_command
from bara.views import _tools_schema


class WorkspaceToolTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="workspace-user", password="pw")
        self.workspace = Workspace.objects.create(user=self.user, title="Workspace test")

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
