import json
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
from django.core.cache import cache
from .billing import CREDITS_PER_DOLLAR, MARGIN_MULTIPLIER, credits_for_usage, credits_per_million_tokens

class ProviderError(Exception):
    """Erreur contrôlée lors d'un appel au fournisseur IA."""


def credit_multiplier(model):
    provider_slug = (model.provider.slug or "").lower()
    return 2 if any(name in provider_slug for name in ("opus", "gpt-4", "large")) else 1


DEFAULT_OUTPUT_ESTIMATE = 500


def estimate_cost(content, model, system_prompt="", history=None, max_output_tokens=500):
    """Estime le coût réel en $ selon les tarifs du modèle, applique la marge, convertit en crédits."""
    history = history or []
    priced_history = []
    image_count = 0
    for item in history:
        item_content = item.get('content')
        if isinstance(item_content,list):
            image_count += sum(1 for part in item_content if part.get('type') == 'image_url')
            item_content = '\n'.join(part.get('text','') for part in item_content if part.get('type') == 'text')
        priced_history.append({**item,'content':item_content})
    history_text = json.dumps(priced_history, ensure_ascii=False, separators=(",", ":"))
    input_chars = len(content + system_prompt + history_text)
    input_tokens = max(1, round(input_chars / 4)) + image_count*4096
    output_tokens_estimate = max_output_tokens

    input_price = float(model.input_price_per_million or 0) / 1_000_000
    output_price = float(model.output_price_per_million or 0) / 1_000_000

    cost_usd = (input_tokens * input_price) + (output_tokens_estimate * output_price)
    cost_usd_with_margin = cost_usd * MARGIN_MULTIPLIER

    reserve_credits = max(1, round(cost_usd_with_margin * CREDITS_PER_DOLLAR))
    return reserve_credits, input_tokens


def is_local_provider(provider):
    return provider.slug in {"ollama-local", "mia"}


def generate_openai_image(provider, api_key, prompt, size="1024x1024"):
    if provider.slug != "openai" or not provider.base_url:
        raise ProviderError("La génération d’images n’est pas configurée avec ce modèle.")
    url = provider.base_url.rstrip("/") + "/v1/images/generations"
    payload = {
        "model": "gpt-image-1",
        "prompt": prompt,
        "n": 1,
        "size": size,
        "quality": "medium",
    }
    request = Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with urlopen(request, timeout=180) as response:
            result = json.loads(response.read().decode("utf-8"))
    except HTTPError as error:
        if error.code == 429:
            raise ProviderError("Trop de requêtes image; réessaie dans un instant.") from error
        raise ProviderError(f"La génération d’images a renvoyé une erreur ({error.code}).") from error
    except (OSError, ValueError) as error:
        raise ProviderError("Le service de génération d’images est temporairement indisponible.") from error

    image = (result.get("data") or [{}])[0].get("b64_json")
    if not image:
        raise ProviderError("Aucune image n’a été renvoyée.")
    return image, result.get("usage") or {}


def call_llm_provider(provider, api_key, model_id, messages, system_prompt="", temperature=0.7, max_tokens=500, top_p=1.0):
    """Appelle Anthropic ou un fournisseur compatible OpenAI sans exposer la clé."""
    provider_name = f"{provider.slug} {provider.name}".lower()
    is_anthropic = "anthropic" in provider_name
    if not provider.base_url:
        raise ProviderError("Le modèle n’a pas d’URL de connexion configurée.")

    base_headers = {
        "Content-Type": "application/json",
        "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    }

    if is_anthropic:
        url = provider.base_url.rstrip("/") + "/v1/messages"
        payload = {
            "model": model_id,
            "max_tokens": max_tokens,
            "messages": messages,
        }
        if system_prompt:
            payload["system"] = system_prompt
        payload["temperature"] = temperature
        payload["top_p"] = top_p
        headers = {
            **base_headers,
            "x-api-key": api_key,
            "anthropic-version": "2023-06-01",
        }
    else:
        url = provider.base_url.rstrip("/") + "/v1/chat/completions"
        payload = {
            "model": model_id,
            "messages": ([{"role": "system", "content": system_prompt}] if system_prompt else []) + messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
            "top_p": top_p,
        }
        headers = base_headers.copy()
        if provider.slug == "openrouter":
            headers["X-Title"] = "AI 2 AI"
        if not is_local_provider(provider):
            headers["Authorization"] = f"Bearer {api_key}"

    request = Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers=headers,
        method="POST",
    )
    try:
        with urlopen(request, timeout=120) as response:
            data = json.loads(response.read().decode("utf-8"))
    except HTTPError as error:
        if error.code == 429:
            raise ProviderError("Trop de requêtes, réessaie dans un instant.") from error
        body = error.read().decode("utf-8", errors="replace")
        raise ProviderError(f"Le service a renvoyé une erreur ({error.code}): {body[:300]}") from error
    except (OSError, ValueError) as error:
        raise ProviderError("Le service est temporairement indisponible.") from error

    if is_anthropic:
        reply = "".join(
            block.get("text", "")
            for block in data.get("content", [])
            if block.get("type") == "text"
        )
        usage = data.get("usage", {})
        if not isinstance(reply, str) or not reply.strip():
            raise ProviderError("Le modèle n’a renvoyé aucun texte.")
        return reply, usage.get("input_tokens", 0), usage.get("output_tokens", 0)

    choice = (data.get("choices") or [{}])[0]
    reply = (choice.get("message") or {}).get("content", "")
    usage = data.get("usage", {})
    if not isinstance(reply, str) or not reply.strip():
        raise ProviderError("Le modèle n’a renvoyé aucun texte.")
    return reply, usage.get("prompt_tokens", 0), usage.get("completion_tokens", 0)


def stream_llm_provider(provider, api_key, model_id, messages, system_prompt="", temperature=0.7, max_tokens=500, top_p=1.0):
    """Yield provider text deltas and one final usage record."""
    provider_name = f"{provider.slug} {provider.name}".lower()
    is_anthropic = "anthropic" in provider_name
    if not provider.base_url:
        raise ProviderError("Le modèle n’a pas d’URL de connexion configurée.")

    base_headers = {
        "Content-Type": "application/json",
        "Accept": "text/event-stream",
        "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    }
    if is_anthropic:
        url = provider.base_url.rstrip("/") + "/v1/messages"
        payload = {
            "model": model_id,
            "max_tokens": max_tokens,
            "messages": messages,
            "stream": True,
            "temperature": temperature,
            "top_p": top_p,
        }
        if system_prompt:
            payload["system"] = system_prompt
        headers = {
            **base_headers,
            "x-api-key": api_key,
            "anthropic-version": "2023-06-01",
        }
    else:
        url = provider.base_url.rstrip("/") + "/v1/chat/completions"
        payload = {
            "model": model_id,
            "messages": ([{"role": "system", "content": system_prompt}] if system_prompt else []) + messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
            "stream": True,
            "top_p": top_p,
        }
        headers = base_headers.copy()
        if provider.slug == "openrouter":
            headers["X-Title"] = "AI 2 AI"
        if not is_local_provider(provider):
            headers["Authorization"] = f"Bearer {api_key}"

    request = Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers=headers,
        method="POST",
    )
    input_tokens = 0
    output_tokens = 0
    try:
        with urlopen(request, timeout=120) as response:
            for raw_line in response:
                line = raw_line.decode("utf-8", errors="replace").strip()
                if not line.startswith("data:"):
                    continue
                data = line[5:].strip()
                if not data or data == "[DONE]":
                    continue
                try:
                    event = json.loads(data)
                except ValueError:
                    continue

                if is_anthropic:
                    event_type = event.get("type")
                    if event_type == "message_start":
                        input_tokens = event.get("message", {}).get("usage", {}).get("input_tokens", 0)
                    elif event_type == "message_delta":
                        output_tokens = event.get("usage", {}).get("output_tokens", output_tokens)
                    elif event_type == "content_block_delta":
                        text = event.get("delta", {}).get("text", "")
                        if text:
                            yield {"type": "delta", "content": text}
                else:
                    usage = event.get("usage") or {}
                    input_tokens = usage.get("prompt_tokens", input_tokens)
                    output_tokens = usage.get("completion_tokens", output_tokens)
                    choices = event.get("choices") or []
                    if choices:
                        text = (choices[0].get("delta") or {}).get("content", "")
                        if text:
                            yield {"type": "delta", "content": text}
    except HTTPError as error:
        if error.code == 429:
            raise ProviderError("Trop de requêtes, réessaie dans un instant.") from error
        body = error.read().decode("utf-8", errors="replace")
        raise ProviderError(f"Le service a renvoyé une erreur ({error.code}): {body[:300]}") from error
    except (OSError, ValueError) as error:
        raise ProviderError("Le service est temporairement indisponible.") from error

    yield {"type": "usage", "input_tokens": input_tokens, "output_tokens": output_tokens}

def call_llm_team(models, messages, system_prompt="", temperature=0.7, max_tokens=500, top_p=1.0, strategy="consensus", usage_records=None):
    from .team import call_llm_team as orchestrate
    return orchestrate(models, messages, system_prompt, temperature, max_tokens, top_p, strategy, usage_records)


def stream_llm_team(models, messages, system_prompt="", temperature=0.7, max_tokens=500, top_p=1.0, strategy="consensus", usage_records=None):
    from .team import stream_llm_team as orchestrate
    yield from orchestrate(models, messages, system_prompt, temperature, max_tokens, top_p, strategy, usage_records)


PRICING_TIERS = {
    "economique": {"label": "Économique", "price_fcfa": 30, "providers": ("groq", "together")},
    "standard": {"label": "Standard", "price_fcfa": 80, "model_match": "haiku"},
    "premium": {"label": "Premium", "price_fcfa": 150, "model_match": "sonnet"},
    "ultra": {"label": "Ultra", "price_fcfa": 400, "model_match": "opus"},
}

def get_tier_for_model(model):
    """Retourne le palier (slug) correspondant à un AIModel donné."""
    provider_slug = (model.provider.slug or "").lower()
    model_id = (model.model_id or "").lower()

    if "opus" in model_id:
        return "ultra"
    if "sonnet" in model_id:
        return "premium"
    if "haiku" in model_id:
        return "standard"
    if provider_slug in ("groq", "together"):
        return "economique"
    return "standard"  # repli par défaut


def tier_price_fcfa(tier_slug):
    return PRICING_TIERS.get(tier_slug, PRICING_TIERS["standard"])["price_fcfa"]




def check_user_rate_limit(user_id, max_requests=10, window_seconds=60, key_prefix="chat"):
    """Retourne (autorisé: bool, retry_after_secondes: int). Limite par utilisateur, indépendante du fournisseur."""
    cache_key = f"rate-limit:{key_prefix}:{user_id}"
    if cache.add(cache_key, 1, timeout=window_seconds):
        return True, 0
    try:
        request_count = cache.incr(cache_key)
    except ValueError:
        cache.add(cache_key, 1, timeout=window_seconds)
        return True, 0
    if request_count > max_requests:
        ttl = cache.ttl(cache_key) if hasattr(cache, "ttl") else window_seconds
        return False, ttl or window_seconds
    return True, 0
