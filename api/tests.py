import json
from unittest.mock import MagicMock, patch
from urllib.error import URLError

from cryptography.fernet import Fernet
from django.contrib.auth.models import User
from django.core.cache import cache
from django.core.exceptions import ImproperlyConfigured
from django.core.management import call_command
from django.test import SimpleTestCase, TestCase, override_settings

from ai_user.models import UserAPIKey
from api.constants import OPENROUTER_RANKING_OPTIONS
from api.crypto import decrypt_value, encrypt_value
from api.models import AIModel, APIKey, APIProvider
from chat.models import RoutingObservation
from chat.services import call_llm_provider

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

    def test_known_tool_incompatibility_is_rejected_before_charge_or_upstream(self):
        AIModel.objects.filter(model_id='fake-model').update(supported_parameters=[])
        before = self.balance()
        with patch('api.views.urlopen') as upstream:
            response = self.call({'tools': [{'type': 'function', 'function': {
                'name': 'read_file', 'parameters': {'type': 'object', 'properties': {}},
            }}]})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()['error']['type'], 'capability_unavailable')
        self.assertEqual(self.balance(), before)
        upstream.assert_not_called()

    def test_openrouter_provider_uses_its_chat_completions_endpoint(self):
        provider = APIProvider.objects.create(
            name="OpenRouter", slug="openrouter", base_url="https://openrouter.ai/api", is_active=True
        )

        with patch("chat.services.urlopen", return_value=fake_response(UPSTREAM_OK)) as mocked:
            reply, input_tokens, output_tokens = call_llm_provider(
                provider,
                "test-openrouter-key",
                "open/model",
                [{"role": "user", "content": "Bonjour"}],
            )

        request = mocked.call_args.args[0]
        self.assertEqual(request.full_url, "https://openrouter.ai/api/v1/chat/completions")
        self.assertEqual(request.get_header("Authorization"), "Bearer test-openrouter-key")
        self.assertEqual(reply, "Bonjour")
        self.assertEqual((input_tokens, output_tokens), (12, 5))

    def test_openrouter_catalog_sync_updates_text_models_and_deactivates_stale_entries(self):
        provider = APIProvider.objects.create(
            name="OpenRouter", slug="openrouter", base_url="https://openrouter.ai/api", is_active=True
        )
        stale = AIModel.objects.create(provider=provider, name="Stale", model_id="stale/model")
        catalog = {
            "data": [
                {
                    "id": "open/model",
                    "name": "Open Model",
                    "architecture": {"input_modalities": ["text"], "output_modalities": ["text"]},
                    "supported_parameters": ["temperature", "tools"],
                    "pricing": {"prompt": "0.000001", "completion": "0.000002"},
                },
                {
                    "id": "image/model",
                    "name": "Image Model",
                    "architecture": {"input_modalities": ["text"], "output_modalities": ["image"]},
                    "pricing": {"prompt": "0", "completion": "0"},
                },
                {
                    "id": "typesafe/jev-router",
                    "name": "Jev Router",
                    "architecture": {"input_modalities": ["text"], "output_modalities": ["text"]},
                    "pricing": {"prompt": "-1", "completion": "-1"},
                },
            ]
        }

        with patch("api.management.commands.sync_openrouter_models.urlopen", return_value=fake_response(catalog)):
            call_command("sync_openrouter_models")

        model = AIModel.objects.get(provider=provider, model_id="open/model")
        stale.refresh_from_db()
        self.assertEqual(model.input_price_per_million, 1)
        self.assertEqual(model.output_price_per_million, 2)
        self.assertEqual(model.supported_parameters, ["temperature", "tools"])
        self.assertFalse(stale.is_active)
        self.assertFalse(AIModel.objects.filter(model_id="image/model").exists())
        self.assertFalse(AIModel.objects.filter(model_id="typesafe/jev-router").exists())

    def test_openrouter_catalog_sync_records_general_and_task_rankings(self):
        provider = APIProvider.objects.create(
            name="OpenRouter", slug="openrouter", base_url="https://openrouter.ai/api", is_active=True
        )
        key = APIKey(provider=provider, name="rankings-test")
        key.set_key("test-openrouter-key")
        key.save()
        catalog = {
            "data": [
                {
                    "id": "open/model",
                    "name": "Open Model",
                    "architecture": {"input_modalities": ["text"], "output_modalities": ["text"]},
                    "pricing": {"prompt": "0.000001", "completion": "0.000002"},
                },
                {
                    "id": "second/model",
                    "name": "Second Model",
                    "architecture": {"input_modalities": ["text"], "output_modalities": ["text"]},
                    "pricing": {"prompt": "0.000001", "completion": "0.000002"},
                },
            ]
        }
        requests = []

        def openrouter_response(request, timeout):
            requests.append(request)
            if request.full_url.endswith("/models"):
                return fake_response(catalog)
            is_programming = "category=programming" in request.full_url
            rows = [
                {"date": "2026-09-28", "model_permaslug": "open/model", "total_tokens": "9000"},
                {"date": "2026-10-05", "model_permaslug": "open/model", "total_tokens": "400"},
                {
                    "date": "2026-10-05",
                    "model_permaslug": "second/model",
                    "total_tokens": "950" if is_programming else "100",
                },
            ]
            return fake_response({
                "data": rows,
                "meta": {"as_of": "2026-10-06T02:00:00.000Z"},
            })

        with patch(
            "api.management.commands.sync_openrouter_models.urlopen",
            side_effect=openrouter_response,
        ):
            call_command("sync_openrouter_models")

        model = AIModel.objects.get(provider=provider, model_id="second/model")
        self.assertEqual(model.openrouter_rankings["all"]["total_tokens"], 100)
        self.assertEqual(model.openrouter_rankings["programming"]["total_tokens"], 950)
        self.assertEqual(model.openrouter_rankings["programming"]["as_of"], "2026-10-06T02:00:00.000Z")
        self.assertEqual(len(requests), 1 + len(OPENROUTER_RANKING_OPTIONS))
        ranking_request = next(request for request in requests if "/datasets/rankings-daily" in request.full_url)
        self.assertEqual(ranking_request.get_header("Authorization"), "Bearer test-openrouter-key")

    def test_model_catalog_lists_auto_when_an_eligible_provider_exists(self):
        response = self.client.get(
            "/v1/models",
            HTTP_AUTHORIZATION=f"Bearer {self.token}",
        )

        self.assertEqual(response.status_code, 200)
        auto_model = next(item for item in response.json()["data"] if item["id"] == "auto")
        self.assertEqual(auto_model["owned_by"], "ai2ai-router")
        self.assertEqual(auto_model["pricing"]["unit"], "dynamic credits per request")

    def test_real_usage_is_charged_not_the_reserve(self):
        before = self.balance()
        with patch("api.views.urlopen", return_value=fake_response(UPSTREAM_OK)):
            response = self.call()
        self.assertEqual(response.status_code, 200)
        charged = before - self.balance()
        self.assertEqual(charged, response.json()["usage"]["credits_charged"])
        self.assertGreaterEqual(charged, 1)
        self.assertLess(charged, 10)  # 17 jetons : la réserve pour max_tokens a été remboursée

    def test_shadow_recommendation_preserves_explicit_api_model(self):
        alternate_provider = APIProvider.objects.create(
            name="ObservedProvider", slug="observed-provider", base_url="https://observed.example.test", is_active=True
        )
        alternate_key = APIKey(provider=alternate_provider, name="k")
        alternate_key.set_key("alternate-secret")
        alternate_key.save()
        alternate_model = AIModel.objects.create(
            provider=alternate_provider, name="Observed", model_id="observed-model",
            input_price_per_million=1, output_price_per_million=2,
        )
        for _ in range(5):
            RoutingObservation.objects.create(
                user=self.user,
                surface="api",
                task_category="general",
                model_id=alternate_model.model_id,
                provider_slug=alternate_provider.slug,
                recommended_model_id=alternate_model.model_id,
                recommended_provider_slug=alternate_provider.slug,
                outcome="success",
            )

        with patch("api.views.urlopen", return_value=fake_response(UPSTREAM_OK)) as mocked:
            response = self.call()

        self.assertEqual(response.status_code, 200)
        sent = json.loads(mocked.call_args.args[0].data)
        self.assertEqual(sent["model"], "fake-model")
        observation = RoutingObservation.objects.filter(
            user=self.user, surface="api", model_id="fake-model", outcome="success"
        ).latest("created_at")
        self.assertEqual(observation.recommended_model_id, alternate_model.model_id)
        self.assertEqual(observation.recommended_provider_slug, alternate_provider.slug)

    def test_auto_model_routes_to_observed_public_api_candidate(self):
        alternate_provider = APIProvider.objects.create(
            name="AutoProvider", slug="auto-provider", base_url="https://auto.example.test", is_active=True
        )
        alternate_key = APIKey(provider=alternate_provider, name="k")
        alternate_key.set_key("auto-secret")
        alternate_key.save()
        alternate_model = AIModel.objects.create(
            provider=alternate_provider, name="Auto", model_id="auto-candidate",
            input_price_per_million=1, output_price_per_million=2,
        )
        for _ in range(5):
            RoutingObservation.objects.create(
                user=self.user,
                surface="api",
                task_category="general",
                model_id=alternate_model.model_id,
                provider_slug=alternate_provider.slug,
                outcome="success",
                charged_credits=5,
                duration_ms=100,
            )

        with patch("api.views.urlopen", return_value=fake_response(UPSTREAM_OK)) as mocked:
            response = self.call({"model": "auto"})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["model"], alternate_model.model_id)
        sent = json.loads(mocked.call_args.args[0].data)
        self.assertEqual(sent["model"], alternate_model.model_id)
        self.assertGreaterEqual(response.json()["usage"]["credits_charged"], 1)

    def test_streaming_api_records_completed_shadow_observation(self):
        upstream = MagicMock()
        upstream.__iter__.return_value = iter([
            b'data: {"id":"chunk-1","choices":[{"delta":{"content":"Bonjour"}}],"usage":{"prompt_tokens":12,"completion_tokens":5}}\n\n',
            b"data: [DONE]\n\n",
        ])
        before = self.balance()
        with patch("api.views.urlopen", return_value=upstream):
            response = self.call({"stream": True})
            body = b"".join(response.streaming_content)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "text/event-stream")
        self.assertIn(b"data: [DONE]", body)
        observation = RoutingObservation.objects.get(user=self.user, surface="api", outcome="success")
        self.assertEqual(observation.model_id, "fake-model")
        self.assertEqual(observation.prompt_tokens, 12)
        self.assertEqual(observation.completion_tokens, 5)
        self.assertGreater(observation.duration_ms, 0)
        self.assertLess(self.balance(), before)

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

    def test_connection_reset_and_unexpected_errors_are_refunded(self):
        for error in (ConnectionResetError('reset'), RuntimeError('unexpected')):
            with self.subTest(error=type(error).__name__):
                before = self.balance()
                with patch('api.views.urlopen', side_effect=error):
                    response = self.call()
                self.assertEqual(response.status_code, 502)
                self.assertEqual(self.balance(), before)

    def test_malformed_provider_completion_is_refunded(self):
        for payload in ({'choices': []}, {'choices': [{'message': {'content': 'Reply'}}], 'usage': {'prompt_tokens': 'invalid'}}):
            with self.subTest(payload=payload):
                before = self.balance()
                with patch('api.views.urlopen', return_value=fake_response(payload)):
                    response = self.call()
                self.assertEqual(response.status_code, 502)
                self.assertEqual(self.balance(), before)

    def test_stream_failure_before_first_token_refunds_reserve(self):
        before = self.balance()
        upstream = MagicMock()
        upstream.__iter__.side_effect = ConnectionResetError('stream reset')
        with patch('api.views.urlopen', return_value=upstream):
            response = self.call({'stream': True})
            body = b''.join(response.streaming_content)
        self.assertIn(b'provider_error', body)
        self.assertEqual(self.balance(), before)
        upstream.close.assert_called()

    def test_unconsumed_stream_is_refunded_when_response_closes(self):
        before = self.balance()
        upstream = MagicMock()
        with patch('api.views.urlopen', return_value=upstream):
            response = self.call({'stream': True})
        response.close()
        self.assertEqual(self.balance(), before)
        upstream.close.assert_called()

    def test_empty_stream_refunds_once_even_when_response_is_closed_twice(self):
        before = self.balance()
        upstream = MagicMock()
        upstream.__iter__.return_value = iter([b'data: [DONE]\n\n'])
        with patch('api.views.urlopen', return_value=upstream):
            response = self.call({'stream': True})
            body = b''.join(response.streaming_content)
        response.close()
        response.close()
        self.assertIn(b'"credits_charged": 0', body)
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


class FernetRotationTests(SimpleTestCase):
    """La rotation de FERNET_KEY ne doit pas rendre les clés illisibles."""

    def setUp(self):
        cache.clear()

    def test_old_keys_stay_readable_after_a_rotation(self):
        old_key = Fernet.generate_key().decode()
        new_key = Fernet.generate_key().decode()
        with override_settings(FERNET_KEY=old_key, FERNET_PREVIOUS_KEYS=""):
            encrypted = encrypt_value("cle-fournisseur")
        # Après rotation : l'ancienne clé passe en secondaire, tout se lit encore.
        with override_settings(FERNET_KEY=new_key, FERNET_PREVIOUS_KEYS=old_key):
            self.assertEqual(decrypt_value(encrypted), "cle-fournisseur")
            # Et les nouvelles écritures utilisent bien la clé courante.
            self.assertNotEqual(encrypt_value("cle-fournisseur"), encrypted)

    def test_an_undeclared_previous_key_is_rejected_with_a_clear_message(self):
        with override_settings(FERNET_KEY=Fernet.generate_key().decode()):
            secret = encrypt_value("cle-fournisseur")
        with override_settings(FERNET_KEY=Fernet.generate_key().decode(), FERNET_PREVIOUS_KEYS=""):
            with self.assertRaises(Exception):
                decrypt_value(secret)

    def test_a_missing_key_explains_how_to_generate_one(self):
        with override_settings(FERNET_KEY="", FERNET_PREVIOUS_KEYS=""):
            with self.assertRaises(ImproperlyConfigured) as caught:
                encrypt_value("x")
        self.assertIn("FERNET_KEY", str(caught.exception))
