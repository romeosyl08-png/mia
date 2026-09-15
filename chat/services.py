import json
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


class ProviderError(Exception):
    """Erreur contrôlée lors d'un appel au fournisseur IA."""


def estimate_cost(content, model, system_prompt="", history=None, max_output_tokens=500):
    """Estime le coût en crédits avant l'appel, avec une réserve de sortie."""
    history_text = json.dumps(history or [], ensure_ascii=False)
    input_tokens = max(1, round(len(content + system_prompt + history_text) / 4))
    provider_slug = (model.provider.slug or "").lower()
    multiplier = 2 if any(name in provider_slug for name in ("opus", "gpt-4", "large")) else 1
    return input_tokens + min(max_output_tokens, 500) * multiplier, input_tokens





def call_llm_provider(provider, api_key, model_id, messages, system_prompt="", temperature=0.7, max_tokens=500):
    """Appelle Anthropic ou un fournisseur compatible OpenAI sans exposer la clé."""
    provider_name = f"{provider.slug} {provider.name}".lower()
    is_anthropic = "anthropic" in provider_name
    if not provider.base_url:
        raise ProviderError("Le fournisseur n'a pas d'URL configurée.")

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
        headers = {
            **base_headers,
            "x-api-key": api_key,  # TODO: déchiffrer via Fernet avant lecture.
            "anthropic-version": "2023-06-01",
        }
    else:
        url = provider.base_url.rstrip("/") + "/v1/chat/completions"
        payload = {
            "model": model_id,
            "messages": ([{"role": "system", "content": system_prompt}] if system_prompt else []) + messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        headers = {
            **base_headers,
            "Authorization": f"Bearer {api_key}",  # TODO: clé chiffrée via Fernet.
        }

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
        raise ProviderError(f"Le fournisseur a renvoyé une erreur ({error.code}).") from error
    except (URLError, TimeoutError, ValueError) as error:
        raise ProviderError("Le fournisseur est temporairement indisponible.") from error

    if is_anthropic:
        reply = "".join(
            block.get("text", "")
            for block in data.get("content", [])
            if block.get("type") == "text"
        )
        usage = data.get("usage", {})
        return reply or "(réponse vide)", usage.get("input_tokens", 0), usage.get("output_tokens", 0)

    choice = (data.get("choices") or [{}])[0]
    reply = (choice.get("message") or {}).get("content", "")
    usage = data.get("usage", {})
    return reply or "(réponse vide)", usage.get("prompt_tokens", 0), usage.get("completion_tokens", 0)