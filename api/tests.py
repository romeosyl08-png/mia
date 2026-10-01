import json
from unittest.mock import MagicMock, patch
from urllib.error import URLError

from django.contrib.auth.models import User
from django.core.cache import cache
from django.test import TestCase

from ai_user.models import UserAPIKey
from api.models import AIModel, APIKey, APIProvider

UPSTREAM_OK = {
    "id": "x",
    "object": "chat.completion",
    "choices": [{"index": 0, "message": {"role": "assistant", "content": "Bonjour"}, "finish_reason": "stop"}],
    "usage": {"prompt_tokens": 12, "completion_tokens": 5, "total_tokens": 17},
}


def fake_response(payload):
    response = MagicMock()
    response.read.return_value = json.dumps(payload).encode()
    context = MagicMock()
    context.__enter__.return_value = response
    return context


class ChatCompletionsTests(TestCase):
    def setUp(self):
        cache.clear()
        self.user = User.objects.create_user("tester", password="x")
        self.account = self.user.credit_account
        self.account.credit(100_000, reason="adjustment", note="test")
        provider = APIProvider.objects.create(
            name="FakeProvider", slug="fakeprovider", base_url="https://fake.example", is_active=True
        )
        provider_key = APIKey(provider=provider, name="k")
        provider_key.set_key("secret")
        provider_key.save()
        AIModel.objects.create(
            provider=provider, name="Fake", model_id="fake-model",
            input_price_per_million=1, output_price_per_million=2,
        )
        self.token, prefix, key_hash = UserAPIKey.generate()
        UserAPIKey.objects.create(user=self.user, name="t", key_prefix=prefix, key_hash=key_hash)

    def call(self, extra=None, token=None):
        body = {"model": "fake-model", "messages": [{"role": "user", "content": "Salut"}]}
        body.update(extra or {})
        return self.client.post(
            "/v1/chat/completions",
            data=json.dumps(body),
            content_type="application/json",
            HTTP_AUTHORIZATION=f"Bearer {token or self.token}",
        )

    def balance(self):
        self.account.refresh_from_db()
        return self.account.balance

    def test_invalid_key_is_rejected(self):
        self.assertEqual(self.call(token="sk-ai2ai-wrong").status_code, 401)

    def test_real_usage_is_charged_not_the_reserve(self):
        before = self.balance()
        with patch("api.views.urlopen", return_value=fake_response(UPSTREAM_OK)):
            response = self.call()
        self.assertEqual(response.status_code, 200)
        charged = before - self.balance()
        self.assertEqual(charged, response.json()["usage"]["credits_charged"])
        self.assertGreaterEqual(charged, 1)
        self.assertLess(charged, 10)  # 17 jetons : la réserve pour max_tokens a été remboursée

    def test_insufficient_credits_never_reaches_the_provider(self):
        self.account.debit(self.balance(), reason="usage", note="vider")
        with patch("api.views.urlopen") as mocked:
            response = self.call()
        self.assertEqual(response.status_code, 402)
        mocked.assert_not_called()

    def test_provider_failure_is_fully_refunded(self):
        before = self.balance()
        with patch("api.views.urlopen", side_effect=URLError("down")):
            response = self.call()
        self.assertEqual(response.status_code, 502)
        self.assertEqual(self.balance(), before)

    def test_unsafe_parameters_are_not_forwarded(self):
        tool = {"type": "function", "function": {"name": "read_file", "parameters": {"type": "object", "properties": {}}}}
        with patch("api.views.urlopen", return_value=fake_response(UPSTREAM_OK)) as mocked:
            self.call({"n": 50, "max_tokens": 10**7, "tools": [tool]})
        sent = json.loads(mocked.call_args[0][0].data)
        self.assertNotIn("n", sent)
        self.assertEqual(sent["tools"][0]["function"]["name"], "read_file")
        self.assertEqual(sent["tool_choice"], "auto")
        self.assertLessEqual(sent["max_tokens"], 4096)

    def test_tool_calls_are_preserved_in_completion_response(self):
        tool_call = {
            "id": "call_1",
            "type": "function",
            "function": {"name": "list_files", "arguments": "{}"},
        }
        upstream = {
            **UPSTREAM_OK,
            "choices": [{
                "index": 0,
                "message": {"role": "assistant", "content": None, "tool_calls": [tool_call]},
                "finish_reason": "tool_calls",
            }],
        }
        with patch("api.views.urlopen", return_value=fake_response(upstream)):
            response = self.call({"tools": [{"type": "function", "function": {
                "name": "list_files", "parameters": {"type": "object", "properties": {}},
            }}]})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["choices"][0]["message"]["tool_calls"], [tool_call])

    def test_followup_with_tool_result_is_forwarded(self):
        messages = [
            {"role": "user", "content": "List files"},
            {"role": "assistant", "content": None, "tool_calls": [{
                "id": "call_1", "type": "function",
                "function": {"name": "list_files", "arguments": "{}"},
            }]},
            {"role": "tool", "tool_call_id": "call_1", "name": "list_files", "content": "{}"},
        ]
        with patch("api.views.urlopen", return_value=fake_response(UPSTREAM_OK)) as mocked:
            response = self.call({"messages": messages})
        self.assertEqual(response.status_code, 200)
        sent = json.loads(mocked.call_args[0][0].data)
        self.assertEqual(sent["messages"][1]["tool_calls"][0]["id"], "call_1")
        self.assertEqual(sent["messages"][2]["tool_call_id"], "call_1")

    def test_invalid_tool_schema_is_rejected(self):
        response = self.call({"tools": [{"type": "function"}]})
        self.assertEqual(response.status_code, 400)

    def test_openai_provider_uses_max_completion_tokens_field(self):
        openai_provider = APIProvider.objects.create(
            name="OpenAI",
            slug="openai",
            base_url="https://api.openai.com",
            is_active=True,
        )
        openai_provider.keys.create(name="openai-key", key="secret")
        AIModel.objects.create(
            provider=openai_provider,
            name="GPT-4o mini",
            model_id="gpt-4o-mini",
            input_price_per_million=1,
            output_price_per_million=2,
        )

        with patch("api.views.urlopen", return_value=fake_response(UPSTREAM_OK)) as mocked:
            self.client.post(
                "/v1/chat/completions",
                data=json.dumps({"model": "gpt-4o-mini", "messages": [{"role": "user", "content": "Salut"}], "max_tokens": 128}),
                content_type="application/json",
                HTTP_AUTHORIZATION=f"Bearer {self.token}",
            )

        sent = json.loads(mocked.call_args[0][0].data)
        self.assertIn("max_completion_tokens", sent)
        self.assertNotIn("token_field", sent)
        self.assertEqual(sent["max_completion_tokens"], 128)