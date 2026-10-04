import json
import mimetypes
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import HTTPRedirectHandler, Request, build_opener

from .services import ProviderError, generate_openai_image


MAX_MEDIA_BYTES = 100 * 1024 * 1024
MEDIA_TIMEOUT_SECONDS = 105
POLL_INTERVAL_SECONDS = 1
OUTPUT_HOSTS = {
    "fal": ("fal.media",),
    "replicate": ("replicate.delivery",),
    "apiframe": ("cdn2.apiframe.ai",),
}


class _SafeRedirectHandler(HTTPRedirectHandler):
    def __init__(self, allowed_hosts):
        self.allowed_hosts = allowed_hosts

    def redirect_request(self, request, response, code, message, headers, new_url):
        if not _allowed_url(new_url, self.allowed_hosts):
            raise ProviderError("Le fournisseur a renvoyé un domaine de média non autorisé.")
        return super().redirect_request(request, response, code, message, headers, new_url)


def _allowed_url(url, allowed_hosts):
    parsed = urlparse(url)
    if parsed.scheme != "https" or not parsed.hostname:
        return False
    host = parsed.hostname.lower()
    return any(host == allowed or host.endswith(f".{allowed}") for allowed in allowed_hosts)


def _json_request(url, api_key, provider_slug, payload=None, method=None, timeout=30):
    headers = {"Accept": "application/json"}
    if provider_slug == "fal":
        headers["Authorization"] = f"Key {api_key}"
    elif provider_slug == "apiframe":
        headers["X-API-Key"] = api_key
    else:
        headers["Authorization"] = f"Bearer {api_key}"
    data = None
    if payload is not None:
        headers["Content-Type"] = "application/json"
        data = json.dumps(payload).encode("utf-8")
    request = Request(url, data=data, headers=headers, method=method)
    try:
        with urlopen(request, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))
    except HTTPError as error:
        body = error.read().decode("utf-8", errors="replace")
        raise ProviderError(f"{provider_slug} a renvoyé une erreur ({error.code}): {body[:240]}") from error
    except (OSError, URLError, ValueError) as error:
        raise ProviderError(f"Le service {provider_slug} est temporairement indisponible.") from error


def _poll_fal(model_id, request_id, api_key):
    base = f"https://queue.fal.run/{model_id}/requests/{request_id}"
    deadline = time.monotonic() + MEDIA_TIMEOUT_SECONDS
    while time.monotonic() < deadline:
        status = _json_request(base + "/status", api_key, "fal")
        state = status.get("status")
        if state == "COMPLETED":
            return _json_request(base, api_key, "fal")
        if state in {"FAILED", "CANCELLED", "CANCELED"}:
            raise ProviderError(str(status.get("error") or "La génération fal a échoué.")[:240])
        time.sleep(POLL_INTERVAL_SECONDS)
    raise ProviderError("La génération fal dépasse le délai autorisé.")


def _run_fal(model_id, api_key, prompt):
    submitted = _json_request(f"https://queue.fal.run/{model_id}", api_key, "fal", {"prompt": prompt}, "POST")
    request_id = submitted.get("request_id")
    if not request_id:
        raise ProviderError("fal n’a pas renvoyé d’identifiant de requête.")
    return _poll_fal(model_id, request_id, api_key)


def _replicate_output(model_id, api_key, prompt):
    model_ref, separator, version = model_id.partition(":")
    if separator:
        endpoint = "https://api.replicate.com/v1/predictions"
        payload = {"version": version, "input": {"prompt": prompt}}
    else:
        parts = model_ref.split("/")
        if len(parts) != 2 or not all(parts):
            raise ProviderError("Le modèle Replicate doit être au format propriétaire/modèle.")
        endpoint = f"https://api.replicate.com/v1/models/{parts[0]}/{parts[1]}/predictions"
        payload = {"input": {"prompt": prompt}}
    prediction = _json_request(endpoint, api_key, "replicate", payload, "POST", timeout=70)
    deadline = time.monotonic() + MEDIA_TIMEOUT_SECONDS
    while prediction.get("status") not in {"succeeded", "failed", "canceled"}:
        if prediction.get("urls", {}).get("get"):
            prediction_url = prediction["urls"]["get"]
            if not _allowed_url(prediction_url, ("api.replicate.com",)):
                raise ProviderError("Replicate a renvoyé une URL de suivi non autorisée.")
        else:
            prediction_url = f"https://api.replicate.com/v1/predictions/{prediction.get('id', '')}"
        if time.monotonic() >= deadline:
            raise ProviderError("La génération Replicate dépasse le délai autorisé.")
        time.sleep(POLL_INTERVAL_SECONDS)
        prediction = _json_request(prediction_url, api_key, "replicate", timeout=30)
    if prediction.get("status") != "succeeded":
        raise ProviderError(str(prediction.get("error") or "La génération Replicate a échoué.")[:240])
    return prediction.get("output")


def _run_apiframe(model_id, api_key, media_type, prompt):
    endpoint_name = {"image": "images", "video": "videos", "audio": "music"}[media_type]
    root = "https://api.apiframe.ai/v2"
    job = _json_request(f"{root}/{endpoint_name}/generate", api_key, "apiframe", {"prompt": prompt, "model": model_id}, "POST")
    job_id = job.get("jobId")
    if not job_id:
        raise ProviderError("Apiframe n’a pas renvoyé d’identifiant de génération.")
    deadline = time.monotonic() + MEDIA_TIMEOUT_SECONDS
    while time.monotonic() < deadline:
        result = _json_request(f"{root}/jobs/{job_id}", api_key, "apiframe")
        status = str(result.get("status", "")).upper()
        if status in {"COMPLETED", "SUCCEEDED", "SUCCESS"}:
            return result.get("result") or result
        if status in {"FAILED", "ERROR", "CANCELED", "CANCELLED"}:
            raise ProviderError(str(result.get("error") or "La génération Apiframe a échoué.")[:240])
        time.sleep(POLL_INTERVAL_SECONDS)
    raise ProviderError("La génération Apiframe dépasse le délai autorisé.")


def _first_url(value):
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        for item in value:
            url = _first_url(item)
            if url:
                return url
    if isinstance(value, dict):
        for key in ("url", "videoUrl", "audioUrl", "audio_url"):
            if isinstance(value.get(key), str):
                return value[key]
        for key in ("images", "videos", "tracks", "video", "audio", "audio_file", "output"):
            if key in value:
                url = _first_url(value[key])
                if url:
                    return url
    return None


def _download_output(url, provider_slug, media_type, api_key):
    hosts = OUTPUT_HOSTS[provider_slug]
    if not _allowed_url(url, hosts):
        raise ProviderError("Le fournisseur a renvoyé un domaine de média non autorisé.")
    headers = {"User-Agent": "AI2AI-media/1.0"}
    if provider_slug == "replicate":
        headers["Authorization"] = f"Bearer {api_key}"
    request = Request(url, headers=headers)
    opener = build_opener(_SafeRedirectHandler(hosts))
    try:
        with opener.open(request, timeout=60) as response:
            content_type = response.headers.get_content_type()
            data = response.read(MAX_MEDIA_BYTES + 1)
    except ProviderError:
        raise
    except (OSError, URLError, HTTPError) as error:
        raise ProviderError("Impossible de télécharger le média généré.") from error
    if len(data) > MAX_MEDIA_BYTES:
        raise ProviderError("Le fichier généré dépasse la taille maximale de 100 Mo.")
    if content_type == "application/octet-stream":
        content_type = mimetypes.guess_type(urlparse(url).path)[0] or content_type
    expected_prefix = {"image": "image/", "video": "video/", "audio": "audio/"}[media_type]
    if not content_type.startswith(expected_prefix):
        raise ProviderError("Le fournisseur a renvoyé un format de fichier inattendu.")
    return data, content_type


def generate_media(provider, api_key, media_type, prompt, model_id):
    if provider.slug == "openai" and media_type == "image":
        encoded, _usage = generate_openai_image(provider, api_key, prompt)
        import base64
        return base64.b64decode(encoded, validate=True), "image/png"

    if provider.slug == "fal":
        output = _run_fal(model_id, api_key, prompt)
    elif provider.slug == "replicate":
        output = _replicate_output(model_id, api_key, prompt)
    elif provider.slug == "apiframe":
        output = _run_apiframe(model_id, api_key, media_type, prompt)
    else:
        raise ProviderError("Ce fournisseur ne prend pas en charge la génération de médias.")

    output_url = _first_url(output)
    if not output_url:
        raise ProviderError("Le fournisseur n’a renvoyé aucun fichier généré.")
    return _download_output(output_url, provider.slug, media_type, api_key)