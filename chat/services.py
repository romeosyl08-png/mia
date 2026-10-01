import json
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
from django.core.cache import cache

class ProviderError(Exception):
    """Erreur contrôlée lors d'un appel au fournisseur IA."""


def credit_multiplier(model):
    provider_slug = (model.provider.slug or "").lower()
    return 2 if any(name in provider_slug for name in ("opus", "gpt-4", "large")) else 1


CREDITS_PER_DOLLAR = 10_000
MARGIN_MULTIPLIER = 1.5
DEFAULT_OUTPUT_ESTIMATE = 500  # tokens de sortie estimés par défaut


def estimate_cost(content, model, system_prompt="", history=None, max_output_tokens=500):
    """Estime le coût réel en $ selon les tarifs du modèle, applique la marge, convertit en crédits."""
    history = history or []
    history_text = json.dumps(history, ensure_ascii=False, separators=(",", ":"))
    input_chars = len(content + system_prompt + history_text)
    input_tokens = max(1, round(input_chars / 4))  # ~4 caractères par token, approximation standard
    output_tokens_estimate = min(max_output_tokens, DEFAULT_OUTPUT_ESTIMATE)

    input_price = float(model.input_price_per_million or 0) / 1_000_000
    output_price = float(model.output_price_per_million or 0) / 1_000_000

    cost_usd = (input_tokens * input_price) + (output_tokens_estimate * output_price)
    cost_usd_with_margin = cost_usd * MARGIN_MULTIPLIER

    reserve_credits = max(1, round(cost_usd_with_margin * CREDITS_PER_DOLLAR))
    return reserve_credits, input_tokens


def is_local_provider(provider):
    return provider.slug in {"ollama-local", "mia"}


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
        }
        headers = base_headers.copy()
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
        raise ProviderError(f"Le fournisseur a renvoyé une erreur ({error.code}): {body[:300]}") from error
    except (OSError, ValueError) as error:
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

def call_llm_team(models, messages, system_prompt="", temperature=0.7, max_tokens=500):
    """Fait répondre plusieurs modèles puis demande au premier de synthétiser."""
    opinions = []
    responding_models = []
    input_tokens = 0
    output_tokens = 0
    for model in models:
        api_key = model.provider.keys.filter(is_active=True).order_by("id").first()
        if (not api_key and not is_local_provider(model.provider)) or not model.provider.base_url:
            continue
        try:
            reply, used_input, used_output = call_llm_provider(
                model.provider,
                api_key.get_key() if api_key else "",
                model.model_id,
                messages,
                system_prompt,
                temperature,
                max_tokens,
            )
        except ProviderError:
            continue
        opinions.append(f"{model.name}:\n{reply}")
        responding_models.append(model)
        input_tokens += used_input or 0
        output_tokens += used_output or 0

    if not opinions:
        raise ProviderError("Aucun modèle de l'équipe n'est disponible.")
    if len(opinions) == 1:
        return opinions[0].split("\n", 1)[1], input_tokens, output_tokens

    synthesis_prompt = (
        "Tu es le coordinateur d'une équipe de modèles IA. Compare leurs avis ci-dessous, "
        "corrige leurs erreurs et réponds directement à la question de l'utilisateur. "
        "Ne mentionne pas la coordination ni les modèles.\n\n"
        + "\n\n--- AVIS SUIVANT ---\n\n".join(opinions)
    )
    synthesizer = responding_models[0]
    api_key = synthesizer.provider.keys.filter(is_active=True).order_by("id").first()
    if not api_key and not is_local_provider(synthesizer.provider):
        raise ProviderError("Le modèle de synthèse n'est pas configuré.")
    reply, used_input, used_output = call_llm_provider(
        synthesizer.provider,
        api_key.get_key() if api_key else "",
        synthesizer.model_id,
        [{"role": "user", "content": synthesis_prompt}],
        "",
        temperature,
        max_tokens,
    )
    return reply, input_tokens + (used_input or 0), output_tokens + (used_output or 0)


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


def credits_for_usage(model, prompt_tokens, completion_tokens):
    """Coût réel d'un appel en crédits : vrais prix du modèle × marge, converti au taux du portefeuille."""
    cost_usd = (
        prompt_tokens * float(model.input_price_per_million or 0)
        + completion_tokens * float(model.output_price_per_million or 0)
    ) / 1_000_000
    return max(1, round(cost_usd * MARGIN_MULTIPLIER * CREDITS_PER_DOLLAR))