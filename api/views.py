import hashlib
import json
import logging
import re
import time
import uuid
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from django.conf import settings
from django.http import JsonResponse, StreamingHttpResponse
from django.shortcuts import render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt

from ai_user.models import InsufficientCreditsError, UserAPIKey
from chat.services import (
    CREDITS_PER_DOLLAR,
    MARGIN_MULTIPLIER,
    check_user_rate_limit,
    credits_for_usage,
    is_local_provider,
)

from .models import AIModel

logger = logging.getLogger(__name__)

DEFAULT_MAX_TOKENS = 1024
MAX_OUTPUT_TOKENS = getattr(settings, "API_MAX_OUTPUT_TOKENS", 4096)
MAX_MESSAGES = 200
MAX_BODY_BYTES = 1_000_000
UPSTREAM_TIMEOUT = 120
USER_AGENT = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36"


class _ConfigError(Exception):
    pass


# ---------- Utilitaires ----------

def _error(message, code, status, param=None):
    return JsonResponse(
        {"error": {"message": message, "type": code, "param": param, "code": code}},
        status=status,
    )


def _authenticate(request):
    header = request.headers.get("Authorization", "")
    if not header.startswith("Bearer "):
        return None, None, _error("Missing API key. Send 'Authorization: Bearer sk-ai2ai-...'.", "invalid_api_key", 401)
    token = header[len("Bearer "):].strip()
    key_hash = hashlib.sha256(token.encode()).hexdigest()
    user_key = UserAPIKey.objects.filter(key_hash=key_hash, is_active=True).select_related("user").first()
    if user_key is None or not user_key.user.is_active:
        return None, None, _error("Invalid API key.", "invalid_api_key", 401)
    return user_key.user, user_key, None


def _guard(request):
    """Authentification + rate limit par clé. Retourne (user, user_key, réponse_d_erreur)."""
    user, user_key, error = _authenticate(request)
    if error:
        return None, None, error
    window = settings.API_RATE_LIMIT_WINDOW_SECONDS
    allowed, retry_after = check_user_rate_limit(
        user_key.id, max_requests=settings.API_RATE_LIMIT_REQUESTS, window_seconds=window, key_prefix="api"
    )
    if not allowed:
        response = _error("Rate limit exceeded. Try again later.", "rate_limit_exceeded", 429)
        response["Retry-After"] = str(retry_after or window)
        return None, None, response
    return user, user_key, None


def _json_body(request):
    if len(request.body) > MAX_BODY_BYTES:
        return None
    try:
        body = json.loads(request.body or b"{}")
    except (TypeError, ValueError):
        return None
    return body if isinstance(body, dict) else None


def _credits_per_million(price_usd):
    return round(float(price_usd or 0) * MARGIN_MULTIPLIER * CREDITS_PER_DOLLAR)


def _is_anthropic(provider):
    return "anthropic" in f"{provider.slug} {provider.name}".lower()


def _public_models():
    # Les modèles locaux (Ollama) tournent sur ta machine, pas sur le serveur : on ne les expose pas.
    models = AIModel.objects.filter(is_active=True, provider__is_active=True).select_related("provider")
    return [m for m in models if not is_local_provider(m.provider)]


def _find_model(model_id):
    if not isinstance(model_id, str):
        return None
    model = (
        AIModel.objects.filter(model_id=model_id, is_active=True, provider__is_active=True)
        .select_related("provider")
        .first()
    )
    if model and is_local_provider(model.provider):
        return None
    return model


# ---------- Validation de la requête (liste blanche) ----------

def _text_of(content):
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for part in content:
            if isinstance(part, dict) and part.get("type") == "text" and isinstance(part.get("text"), str):
                parts.append(part["text"])
            else:
                return None
        return "".join(parts)
    return None


def _clean_messages(raw):
    if not isinstance(raw, list) or not 1 <= len(raw) <= MAX_MESSAGES:
        return None, f"messages must be a list of 1 to {MAX_MESSAGES} items."
    clean = []
    for item in raw:
        role = item.get("role") if isinstance(item, dict) else None
        if role not in ("system", "user", "assistant", "tool"):
            return None, "Each message needs a supported role (system, user, assistant or tool)."
        text = _text_of(item.get("content"))
        if role == "assistant" and item.get("tool_calls"):
            calls = item["tool_calls"]
            if not isinstance(calls, list) or len(calls) > 16:
                return None, "assistant tool_calls must be a list of at most 16 items."
            clean_calls = []
            for call in calls:
                function = call.get("function") if isinstance(call, dict) else None
                if not isinstance(function, dict) or not isinstance(call.get("id"), str):
                    return None, "Each tool call needs an id and function."
                name = function.get("name")
                arguments = function.get("arguments", "{}")
                if not isinstance(name, str) or not isinstance(arguments, str):
                    return None, "Tool call names and arguments must be strings."
                try:
                    parsed_arguments = json.loads(arguments)
                except ValueError:
                    return None, "Tool call arguments must contain valid JSON."
                if not isinstance(parsed_arguments, dict):
                    return None, "Tool call arguments must be a JSON object."
                clean_calls.append({"id": call["id"], "type": "function", "function": {"name": name, "arguments": arguments}})
            clean.append({"role": role, "content": text, "tool_calls": clean_calls})
            continue
        if role == "assistant" and item.get("content") is None:
            clean.append({"role": role, "content": None})
            continue
        if text is None:
            return None, "Message content must be text; images are not supported."
        message = {"role": role, "content": text}
        if role == "tool":
            tool_call_id = item.get("tool_call_id")
            if not isinstance(tool_call_id, str) or not tool_call_id:
                return None, "Tool messages need a tool_call_id."
            message["tool_call_id"] = tool_call_id
            if isinstance(item.get("name"), str):
                message["name"] = item["name"]
        clean.append(message)
    if not any(m["role"] == "user" for m in clean):
        return None, "At least one user message is required."
    return clean, None


def _clean_tools(raw):
    if raw is None:
        return None, None
    if not isinstance(raw, list) or not 1 <= len(raw) <= 16:
        return None, "tools must be a list of 1 to 16 function tools."
    clean = []
    for tool in raw:
        function = tool.get("function") if isinstance(tool, dict) else None
        if not isinstance(tool, dict) or tool.get("type") != "function" or not isinstance(function, dict):
            return None, "Only function tools are supported."
        name = function.get("name")
        parameters = function.get("parameters")
        if not isinstance(name, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", name):
            return None, "Tool names must contain only letters, numbers, underscores or hyphens."
        if not isinstance(parameters, dict) or parameters.get("type") != "object":
            return None, "Tool parameters must be a JSON object schema."
        clean.append({"type": "function", "function": {
            "name": name,
            "description": str(function.get("description") or "")[:1000],
            "parameters": parameters,
        }})
    return clean, None


def _parse_params(payload):
    raw_max = payload.get("max_tokens", payload.get("max_completion_tokens", DEFAULT_MAX_TOKENS))
    try:
        max_tokens = int(raw_max)
    except (TypeError, ValueError):
        return None, "max_tokens must be an integer."
    params = {"max_tokens": max(1, min(max_tokens, MAX_OUTPUT_TOKENS))}
    try:
        if payload.get("temperature") is not None:
            params["temperature"] = max(0.0, min(float(payload["temperature"]), 2.0))
        if payload.get("top_p") is not None:
            params["top_p"] = max(0.0, min(float(payload["top_p"]), 1.0))
    except (TypeError, ValueError):
        return None, "temperature and top_p must be numbers."
    stop = payload.get("stop")
    if isinstance(stop, str):
        stop = [stop]
    if isinstance(stop, list) and stop and all(isinstance(s, str) for s in stop):
        params["stop"] = stop[:4]
    return params, None


def _estimate_input_tokens(messages, tools=None):
    characters = sum(len(m.get("content") or "") for m in messages)
    characters += sum(
        len(call.get("function", {}).get("arguments", ""))
        for message in messages
        for call in message.get("tool_calls", [])
    )
    if tools:
        characters += len(json.dumps(tools))
    return max(1, round(characters / 4)) + 4 * len(messages)


# ---------- Appel au fournisseur ----------

def _to_anthropic(messages):
    system_parts, converted = [], []
    for m in messages:
        if m["role"] == "system":
            system_parts.append(m["content"])
        elif converted and converted[-1]["role"] == m["role"]:
            converted[-1]["content"] += "\n\n" + m["content"]  # Anthropic exige l'alternance user/assistant
        else:
            converted.append({"role": m["role"], "content": m["content"]})
    while converted and converted[0]["role"] == "assistant":
        converted.pop(0)
    return "\n\n".join(system_parts), converted


def _build_upstream(model, messages, params, stream, tools=None):
    provider = model.provider
    if not provider.base_url:
        raise _ConfigError("The selected provider has no base URL configured.")
    provider_key = provider.keys.filter(is_active=True).order_by("id").first()
    if provider_key is None and not is_local_provider(provider):
        raise _ConfigError("No active API key is configured for the selected provider.")

    headers = {"Content-Type": "application/json", "User-Agent": USER_AGENT}
    base = provider.base_url.rstrip("/")

    if _is_anthropic(provider):
        system, converted = _to_anthropic(messages)
        payload = {"model": model.model_id, "max_tokens": params["max_tokens"], "messages": converted}
        if system:
            payload["system"] = system
        if "temperature" in params:
            payload["temperature"] = min(params["temperature"], 1.0)
        if "stop" in params:
            payload["stop_sequences"] = params["stop"]
        url = base + "/v1/messages"
        headers.update({"x-api-key": provider_key.get_key(), "anthropic-version": "2023-06-01"})
    else:
        token_field = "max_completion_tokens" if provider.slug == "openai" else "max_tokens"
        payload = {"model": model.model_id, "messages": messages, token_field: params["max_tokens"]}
        for name in ("temperature", "top_p", "stop"):
            if name in params:
                payload[name] = params[name]
        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = "auto"
        if stream:
            payload["stream"] = True
        url = base + "/v1/chat/completions"
        if provider_key is not None:
            headers["Authorization"] = f"Bearer {provider_key.get_key()}"

    request = Request(url, data=json.dumps(payload).encode("utf-8"), headers=headers, method="POST")
    return request, provider_key


def _upstream_error(exc):
    detail = exc.read().decode("utf-8", errors="replace")[:300] if hasattr(exc, "read") else ""
    logger.warning("Upstream HTTP %s: %s", exc.code, detail)
    if exc.code in (401, 403):
        return _error("The upstream provider rejected the request (authentication).", "provider_error", 502)
    if exc.code == 429:
        return _error("The upstream provider is rate limiting requests. Try again shortly.", "rate_limit_exceeded", 429)
    return _error(f"Provider request failed ({exc.code}): {detail}", "provider_error", 502)


def _openai_from_anthropic(model, data):
    text = "".join(b.get("text", "") for b in data.get("content", []) if b.get("type") == "text")
    usage = data.get("usage") or {}
    prompt, completion = usage.get("input_tokens", 0), usage.get("output_tokens", 0)
    return {
        "id": "chatcmpl-" + uuid.uuid4().hex,
        "object": "chat.completion",
        "created": int(time.time()),
        "model": model.model_id,
        "choices": [{
            "index": 0,
            "message": {"role": "assistant", "content": text},
            "finish_reason": "length" if data.get("stop_reason") == "max_tokens" else "stop",
        }],
        "usage": {"prompt_tokens": prompt, "completion_tokens": completion, "total_tokens": prompt + completion},
    }


# ---------- Crédits ----------

def _settle(account, model, reserve, prompt_tokens, completion_tokens):
    """Remplace la réserve par le coût réel : rembourse l'excédent, débite le complément."""
    actual = credits_for_usage(model, prompt_tokens, completion_tokens)
    if actual < reserve:
        account.credit(reserve - actual, reason="adjustment", note=f"Ajustement API — {model.model_id}")
    elif actual > reserve:
        try:
            account.debit(actual - reserve, reason="usage", note=f"Complément API — {model.model_id}")
        except InsufficientCreditsError:
            actual = reserve
    return actual


def _balance(account):
    account.refresh_from_db(fields=["balance"])
    return account.balance


def _touch_keys(user_key, provider_key):
    now = timezone.now()
    user_key.last_used_at = now
    user_key.save(update_fields=["last_used_at"])
    if provider_key is not None:
        provider_key.last_used_at = now
        provider_key.save(update_fields=["last_used_at"])


# ---------- Endpoints ----------

def api_home(request):
    rows = [
        {
            "id": m.model_id,
            "name": m.name,
            "provider": m.provider.name,
            "input": _credits_per_million(m.input_price_per_million),
            "output": _credits_per_million(m.output_price_per_million),
        }
        for m in _public_models()
    ]
    return render(request, "api/api.html", {
        "base_url": request.build_absolute_uri("/v1"),
        "models": rows,
        "keys_url": reverse("api-keys-page"),
        "max_output_tokens": MAX_OUTPUT_TOKENS,
        "rate_limit": settings.API_RATE_LIMIT_REQUESTS,
        "rate_window": settings.API_RATE_LIMIT_WINDOW_SECONDS,
    })


def models_view(request):
    user, user_key, error = _guard(request)
    if error:
        return error
    now = int(time.time())
    return JsonResponse({
        "object": "list",
        "data": [
            {
                "id": m.model_id,
                "object": "model",
                "created": int(m.created_at.timestamp()) if m.created_at else now,
                "owned_by": m.provider.slug,
                "pricing": {
                    "unit": "credits per million tokens",
                    "input": _credits_per_million(m.input_price_per_million),
                    "output": _credits_per_million(m.output_price_per_million),
                },
            }
            for m in _public_models()
        ],
    })


def balance_view(request):
    user, user_key, error = _guard(request)
    if error:
        return error
    account = getattr(user, "credit_account", None)
    return JsonResponse({"object": "balance", "credits": _balance(account) if account else 0})


@csrf_exempt
def chat_completions(request):
    if request.method != "POST":
        return _error("Only POST is supported for chat completions.", "method_not_allowed", 405)
    user, user_key, error = _guard(request)
    if error:
        return error

    payload = _json_body(request)
    if payload is None:
        return _error("The body must be a JSON object of at most 1 MB.", "invalid_request_error", 400)
    messages, problem = _clean_messages(payload.get("messages"))
    if problem:
        return _error(problem, "invalid_request_error", 400, "messages")
    tools, problem = _clean_tools(payload.get("tools"))
    if problem:
        return _error(problem, "invalid_request_error", 400, "tools")
    params, problem = _parse_params(payload)
    if problem:
        return _error(problem, "invalid_request_error", 400)
    model = _find_model(payload.get("model"))
    if model is None:
        return _error(f"Model {payload.get('model')!r} was not found or is inactive.", "model_not_found", 404, "model")
    stream = bool(payload.get("stream"))
    is_anthropic = _is_anthropic(model.provider)
    if tools and is_anthropic:
        return _error("Function tools are not supported for this provider yet.", "invalid_request_error", 400, "tools")
    if tools and stream:
        return _error("Streaming with function tools is not supported yet.", "invalid_request_error", 400, "stream")
    if stream and is_anthropic:
        return _error("Streaming is not supported for this model yet.", "invalid_request_error", 400, "stream")

    account = getattr(user, "credit_account", None)
    if account is None:
        return _error("This account has no credit balance.", "insufficient_quota", 402)

    # Réserve du pire cas (tout max_tokens consommé), ajustée au coût réel après l'appel.
    est_input = _estimate_input_tokens(messages, tools)
    reserve = credits_for_usage(model, est_input, params["max_tokens"])
    try:
        account.debit(reserve, reason="usage", note=f"API — {model.model_id}")
    except InsufficientCreditsError:
        return _error(
            f"Insufficient credits: this request may cost up to {reserve} credits (lower max_tokens to reduce it).",
            "insufficient_quota",
            402,
        )

    try:
        upstream_request, provider_key = _build_upstream(model, messages, params, stream, tools)
        if stream:
            upstream = urlopen(upstream_request, timeout=UPSTREAM_TIMEOUT)
        else:
            with urlopen(upstream_request, timeout=UPSTREAM_TIMEOUT) as response:
                provider_data = json.loads(response.read().decode("utf-8"))
            if not isinstance(provider_data, dict):
                raise ValueError("unexpected provider payload")
    except _ConfigError as exc:
        account.credit(reserve, reason="adjustment", note="Remboursement API — configuration")
        return _error(str(exc), "configuration_error", 503)
    except HTTPError as exc:
        account.credit(reserve, reason="adjustment", note="Remboursement API — erreur fournisseur")
        return _upstream_error(exc)
    except (URLError, TimeoutError, ValueError) as exc:
        logger.warning("Upstream failure: %s", exc)
        account.credit(reserve, reason="adjustment", note="Remboursement API — fournisseur indisponible")
        return _error("Provider request failed.", "provider_error", 502)

    _touch_keys(user_key, provider_key)

    # ----- Streaming : on relaie le flux SSE tel quel, puis on règle les crédits -----
    if stream:
        state = {"prompt": 0, "completion": 0, "chars": 0, "id": "chatcmpl-" + uuid.uuid4().hex, "charged": None}

        def settle():
            if state["charged"] is None:
                prompt = state["prompt"] or est_input
                completion = state["completion"] or max(1, round(state["chars"] / 4))
                state["tokens"] = (prompt, completion)
                state["charged"] = _settle(account, model, reserve, prompt, completion)
            return state["charged"]

        def event_stream():
            try:
                for raw in upstream:
                    line = raw.decode("utf-8", errors="replace").strip()
                    if line.startswith("data:"):
                        data = line[5:].strip()
                        if data == "[DONE]":
                            charged = settle()
                            prompt, completion = state["tokens"]
                            summary = {
                                "id": state["id"],
                                "object": "chat.completion.chunk",
                                "created": int(time.time()),
                                "model": model.model_id,
                                "choices": [],
                                "usage": {
                                    "prompt_tokens": prompt,
                                    "completion_tokens": completion,
                                    "total_tokens": prompt + completion,
                                    "credits_charged": charged,
                                    "remaining_balance": _balance(account),
                                },
                            }
                            yield ("data: " + json.dumps(summary) + "\n\n").encode("utf-8")
                        else:
                            try:
                                chunk = json.loads(data)
                            except ValueError:
                                chunk = None
                            if isinstance(chunk, dict):
                                state["id"] = chunk.get("id") or state["id"]
                                usage = chunk.get("usage") or (chunk.get("x_groq") or {}).get("usage")
                                if usage:
                                    state["prompt"] = usage.get("prompt_tokens", state["prompt"])
                                    state["completion"] = usage.get("completion_tokens", state["completion"])
                                for choice in chunk.get("choices") or []:
                                    state["chars"] += len((choice.get("delta") or {}).get("content") or "")
                    yield raw
            finally:
                upstream.close()
                settle()  # couvre aussi le cas où le client coupe la connexion en plein flux

        response = StreamingHttpResponse(event_stream(), content_type="text/event-stream")
        response["Cache-Control"] = "no-cache"
        response["X-Accel-Buffering"] = "no"
        return response

    # ----- Réponse complète -----
    result = _openai_from_anthropic(model, provider_data) if is_anthropic else provider_data
    result["model"] = model.model_id
    usage = result.setdefault("usage", {})
    completion_text = "".join(
        (c.get("message") or {}).get("content") or "" for c in result.get("choices") or []
    )
    prompt_tokens = usage.get("prompt_tokens") or est_input
    completion_tokens = usage.get("completion_tokens") or max(1, round(len(completion_text) / 4))
    charged = _settle(account, model, reserve, prompt_tokens, completion_tokens)
    balance = _balance(account)
    clean = _clean_completion(result, model, prompt_tokens, completion_tokens)
    clean["usage"]["credits_charged"] = charged
    clean["usage"]["remaining_balance"] = balance

    response = JsonResponse(clean)
    response["X-Credits-Charged"] = str(charged)
    response["X-Credits-Remaining"] = str(balance)
    return response


def _clean_completion(result, model, prompt_tokens, completion_tokens):
    """Réponse au format OpenAI, limitée aux champs que tu choisis de garantir."""
    choices = []
    for c in result.get("choices") or []:
        msg = c.get("message") or {}
        clean_msg = {"role": msg.get("role") or "assistant", "content": msg.get("content") or ""}
        tool_calls = msg.get("tool_calls")
        if isinstance(tool_calls, list):
            clean_msg["tool_calls"] = [
                {
                    "id": call.get("id"),
                    "type": "function",
                    "function": {
                        "name": (call.get("function") or {}).get("name"),
                        "arguments": (call.get("function") or {}).get("arguments", "{}"),
                    },
                }
                for call in tool_calls
                if isinstance(call, dict) and isinstance(call.get("function"), dict)
            ]
        if msg.get("reasoning"):
            clean_msg["reasoning"] = msg["reasoning"]
        choices.append({
            "index": c.get("index", 0),
            "message": clean_msg,
            "finish_reason": c.get("finish_reason") or ("tool_calls" if clean_msg.get("tool_calls") else "stop"),
        })
    usage = {
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "total_tokens": prompt_tokens + completion_tokens,
    }
    details = (result.get("usage") or {}).get("completion_tokens_details")
    if isinstance(details, dict):
        usage["completion_tokens_details"] = details
    return {
        "id": result.get("id") or "chatcmpl-" + uuid.uuid4().hex,
        "object": "chat.completion",
        "created": result.get("created") or int(time.time()),
        "model": model.model_id,
        "choices": choices,
        "usage": usage,
    }