from .models import Workspace, WorkFile, WorkMessage
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
from chat.services import ProviderError, is_local_provider


import json as json_module


def call_llm_with_tools(provider, api_key, model_id, messages, system_prompt="", temperature=0.7, max_tokens=500, tools=None):
    """Variante dédiée à l'agent : renvoie le message complet (dict), pas juste le texte."""
    provider_name = f"{provider.slug} {provider.name}".lower()
    is_anthropic = "anthropic" in provider_name
    if not provider.base_url:
        raise ProviderError("Le fournisseur n'a pas d'URL configurée.")
    if is_anthropic:
        raise ProviderError("Le mode outils n'est pas encore supporté pour Anthropic.")

    url = provider.base_url.rstrip("/") + "/v1/chat/completions"
    payload = {
        "model": model_id,
        "messages": ([{"role": "system", "content": system_prompt}] if system_prompt else []) + messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
    }
    if tools:
        payload["tools"] = tools
        payload["tool_choice"] = "auto"

    headers = {"Content-Type": "application/json", "User-Agent": "Mozilla/5.0 (X11; Linux x86_64)"}
    if not is_local_provider(provider):
        headers["Authorization"] = f"Bearer {api_key}"

    request = Request(url, data=json_module.dumps(payload).encode("utf-8"), headers=headers, method="POST")
    try:
        with urlopen(request, timeout=120) as response:
            data = json_module.loads(response.read().decode("utf-8"))
    except HTTPError as error:
        raise ProviderError(f"Le fournisseur a renvoyé une erreur ({error.code}).") from error
    except (URLError, TimeoutError, ValueError) as error:
        raise ProviderError("Le fournisseur est temporairement indisponible.") from error

    choice = (data.get("choices") or [{}])[0]
    message_response = choice.get("message", {})
    usage = data.get("usage", {})
    return message_response, usage.get("prompt_tokens", 0), usage.get("completion_tokens", 0)

def call_llm_with_tools_stream(provider, api_key, model_id, messages, system_prompt="", temperature=0.7, max_tokens=500, tools=None):
    """
    Générateur : yield des événements au fur et à mesure de la réponse en streaming.
    Événements possibles :
      {"type": "delta", "content": "..."}                      -> morceau de texte de la réponse finale
      {"type": "tool_call_delta", "index": i, "id":..,          -> construction progressive d'un tool_call
                "name":.., "arguments_delta": ".."}
      {"type": "done", "finish_reason": .., "usage": {...}}     -> fin du flux
    """
    provider_name = f"{provider.slug} {provider.name}".lower()
    if "anthropic" in provider_name:
        raise ProviderError("Le mode outils/streaming n'est pas supporté pour Anthropic.")
    if not provider.base_url:
        raise ProviderError("Le fournisseur n'a pas d'URL configurée.")

    url = provider.base_url.rstrip("/") + "/v1/chat/completions"
    payload = {
        "model": model_id,
        "messages": ([{"role": "system", "content": system_prompt}] if system_prompt else []) + messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
        "stream": True,
    }
    if tools:
        payload["tools"] = tools
        payload["tool_choice"] = "auto"

    headers = {"Content-Type": "application/json", "User-Agent": "Mozilla/5.0 (X11; Linux x86_64)"}
    if not is_local_provider(provider):
        headers["Authorization"] = f"Bearer {api_key}"

    request = Request(url, data=json_module.dumps(payload).encode("utf-8"), headers=headers, method="POST")

    try:
        response = urlopen(request, timeout=120)
    except HTTPError as error:
        raise ProviderError(f"Le fournisseur a renvoyé une erreur ({error.code}).") from error
    except (URLError, TimeoutError) as error:
        raise ProviderError("Le fournisseur est temporairement indisponible.") from error

    usage = {}
    finish_reason = None
    try:
        for raw_line in response:
            line = raw_line.decode("utf-8", errors="replace").strip()
            if not line or not line.startswith("data:"):
                continue
            data_str = line[len("data:"):].strip()
            if data_str == "[DONE]":
                break
            try:
                chunk = json_module.loads(data_str)
            except ValueError:
                continue

            choice = (chunk.get("choices") or [{}])[0]
            delta = choice.get("delta", {})
            finish_reason = choice.get("finish_reason") or finish_reason
            if chunk.get("usage"):
                usage = chunk["usage"]

            if delta.get("content"):
                yield {"type": "delta", "content": delta["content"]}

            for tc in delta.get("tool_calls") or []:
                yield {
                    "type": "tool_call_delta",
                    "index": tc.get("index", 0),
                    "id": tc.get("id"),
                    "name": (tc.get("function") or {}).get("name"),
                    "arguments_delta": (tc.get("function") or {}).get("arguments", ""),
                }
    finally:
        response.close()

    yield {"type": "done", "finish_reason": finish_reason, "usage": usage}

    
MAX_TEXT_CHARS = 50_000


def extract_text(uploaded_file, content_type):
    """
    Extrait le texte des fichiers simples.
    """

    if not (
        content_type.startswith("text/")
        or content_type in (
            "application/json",
            "application/csv",
        )
    ):
        return ""

    try:
        uploaded_file.seek(0)

        raw = uploaded_file.read()

        text = raw.decode(
            "utf-8",
            errors="replace",
        )

        return text[:MAX_TEXT_CHARS]

    except Exception:
        return ""


def get_workspace_context(workspace):
    """
    Construit le contexte envoyé au modèle
    à partir des fichiers du workspace.
    """

    files = workspace.files.exclude(
        extracted_text=""
    )

    if not files.exists():
        return ""

    parts = [
        "Fichiers disponibles dans cet espace de travail :"
    ]

    for work_file in files:
        parts.append(
            f"\n--- {work_file.original_name} ---\n"
            f"{work_file.extracted_text}"
        )

    return "\n".join(parts)