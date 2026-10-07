import base64
import hashlib
from urllib.parse import parse_qs, urlparse
from unittest.mock import patch

import requests
from django.contrib.auth.models import User
from django.test import TestCase, override_settings
from django.urls import reverse

from api.crypto import encrypt_value
from bara.models import BlenderAutomationTask, ToolExecution, Workspace, WorkspacePluginConnection
from bara.plugin_services import (
    UncertainDeliveryError,
    _provider_request,
    blender_token_digest,
)
from bara.plugin_views import OAUTH_SESSION_KEY
from bara.views import _log_tool


class EmailPluginOAuthTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="plugin-user", password="test-password")
        self.workspace = Workspace.objects.create(user=self.user, title="Plugins")
        self.client.force_login(self.user)

    @override_settings(GOOGLE_CLIENT_ID="google-client", GOOGLE_CLIENT_SECRET="google-secret")
    def test_oauth_uses_state_pkce_and_encrypts_tokens(self):
        connect_url = reverse(
            "bara:plugin-email-connect",
            args=[self.workspace.id, "google_mail"],
        )
        response = self.client.get(connect_url)
        self.assertEqual(response.status_code, 302)
        query = parse_qs(urlparse(response["Location"]).query)
        state = query["state"][0]
        self.assertEqual(query["code_challenge_method"], ["S256"])

        session_state = self.client.session[OAUTH_SESSION_KEY][state]
        expected_challenge = base64.urlsafe_b64encode(
            hashlib.sha256(session_state["code_verifier"].encode()).digest()
        ).decode().rstrip("=")
        self.assertEqual(query["code_challenge"], [expected_challenge])

        callback_url = reverse(
            "bara:email-oauth-callback",
            args=["google_mail"],
        )
        with (
            patch(
                "bara.plugin_views.exchange_email_oauth_code",
                return_value={
                    "access_token": "access-secret",
                    "refresh_token": "refresh-secret",
                    "expires_in": 3600,
                },
            ),
            patch("bara.plugin_views.email_account_address", return_value="person@example.com"),
        ):
            callback = self.client.get(callback_url, {"state": state, "code": "oauth-code"})
        self.assertEqual(callback.status_code, 302)

        connection = WorkspacePluginConnection.objects.get(workspace=self.workspace)
        self.assertEqual(connection.account_email, "person@example.com")
        self.assertNotIn("access-secret", connection.encrypted_access_token)
        self.assertEqual(connection.get_access_token(), "access-secret")
        self.assertEqual(connection.get_refresh_token(), "refresh-secret")

    @override_settings(GOOGLE_CLIENT_ID="google-client", GOOGLE_CLIENT_SECRET="google-secret")
    def test_oauth_callback_rejects_reused_state_and_post(self):
        callback_url = reverse(
            "bara:email-oauth-callback",
            args=["google_mail"],
        )
        self.assertEqual(self.client.post(callback_url).status_code, 405)
        session = self.client.session
        session[OAUTH_SESSION_KEY] = {
            "single-use-state": {
                "workspace_id": self.workspace.id,
                "user_id": self.user.id,
                "plugin": "google_mail",
                "code_verifier": "verifier",
                "created_at": 1,
            }
        }
        session.save()

        callback = self.client.get(callback_url, {"state": "single-use-state", "code": "code"})
        self.assertEqual(callback.status_code, 400)
        self.assertFalse(WorkspacePluginConnection.objects.exists())


class EmailPluginDeliveryTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="mail-user", password="test-password")
        self.workspace = Workspace.objects.create(user=self.user, title="Mail")
        self.client.force_login(self.user)

    def test_network_failure_during_send_is_explicitly_uncertain(self):
        with patch("bara.plugin_services.requests.request", side_effect=requests.Timeout):
            with self.assertRaises(UncertainDeliveryError) as error:
                _provider_request(
                    "https://mail.example.test/send",
                    "access-token",
                    method="POST",
                    uncertain_on_failure=True,
                )
        self.assertIn("Résultat d'envoi incertain", str(error.exception))
        self.assertIn("avant toute nouvelle tentative", str(error.exception))

    def test_email_action_cannot_be_confirmed_twice_after_uncertain_delivery(self):
        action = ToolExecution.objects.create(
            workspace=self.workspace,
            tool_name="email_send",
            arguments={
                "provider": "google_mail",
                "to": "recipient@example.com",
                "subject": "Test",
                "body_encrypted": encrypt_value("message secret"),
            },
            status="pending",
        )
        url = reverse("bara:resolve-action", args=[self.workspace.id, action.id, "confirm"])
        with patch(
            "bara.views.send_email",
            side_effect=UncertainDeliveryError(
                "Résultat d'envoi incertain : vérifie les messages envoyés avant toute nouvelle tentative."
            ),
        ) as send_email:
            first_response = self.client.post(url)
            second_response = self.client.post(url)

        self.assertEqual(first_response.status_code, 502)
        self.assertIn("Résultat d'envoi incertain", first_response.json()["error"])
        self.assertEqual(second_response.status_code, 409)
        self.assertEqual(send_email.call_count, 1)
        action.refresh_from_db()
        self.assertEqual(action.status, "error")
        self.assertNotIn("message secret", action.arguments["body_encrypted"])

    def test_email_tool_logs_redact_message_content_and_identifiers(self):
        _log_tool(
            self.workspace,
            "email_read",
            {"provider": "google_mail", "message_id": "private-message-id"},
            {"provider": "google_mail", "body": "private email body"},
        )

        log = ToolExecution.objects.get(workspace=self.workspace)
        self.assertEqual(
            log.arguments,
            {"provider": "google_mail", "content_redacted": True},
        )
        self.assertNotIn("private-message-id", str(log.arguments))
        self.assertNotIn("private email body", str(log.result))


class BlenderPluginTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="blender-user", password="test-password")
        self.workspace = Workspace.objects.create(user=self.user, title="Blender")
        self.client.force_login(self.user)

    @override_settings(DEBUG=True)
    def test_pair_token_is_hashed_and_authenticates_poll_and_completion(self):
        pair_url = reverse("bara:plugin-blender-pair", args=[self.workspace.id])
        pair_response = self.client.post(pair_url)
        self.assertEqual(pair_response.status_code, 200)
        token = pair_response.json()["token"]
        connection = WorkspacePluginConnection.objects.get(workspace=self.workspace, plugin="blender")
        self.assertEqual(connection.blender_token_hash, blender_token_digest(token))
        self.assertNotEqual(connection.blender_token_hash, token)

        poll_url = reverse("bara:blender-poll")
        self.assertEqual(self.client.get(poll_url).status_code, 401)
        headers = {"HTTP_AUTHORIZATION": f"Bearer {token}"}
        empty_poll = self.client.get(poll_url, **headers)
        self.assertEqual(empty_poll.status_code, 204)

        task = BlenderAutomationTask.objects.create(
            connection=connection,
            action="scene_info",
            arguments={},
        )
        claimed = self.client.get(poll_url, **headers)
        self.assertEqual(claimed.status_code, 200)
        self.assertEqual(claimed.json()["task"]["id"], task.id)
        task.refresh_from_db()
        self.assertEqual(task.status, "running")

        complete_url = reverse("bara:blender-complete")
        completed = self.client.post(
            complete_url,
            data={
                "task_id": task.id,
                "status": "success",
                "result": {"object_count": 3},
            },
            content_type="application/json",
            **headers,
        )
        self.assertEqual(completed.status_code, 200)
        task.refresh_from_db()
        self.assertEqual(task.status, "success")
        self.assertEqual(task.result, {"object_count": 3})

    def test_blender_pairing_is_workspace_scoped(self):
        other_user = User.objects.create_user(username="other-plugin-user", password="test-password")
        other_workspace = Workspace.objects.create(user=other_user, title="Other")
        response = self.client.post(
            reverse("bara:plugin-blender-pair", args=[other_workspace.id])
        )
        self.assertEqual(response.status_code, 404)
        self.assertFalse(WorkspacePluginConnection.objects.exists())
