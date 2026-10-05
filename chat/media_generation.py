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
IMAGE_SIZE_CHOICES = {"1024x1024", "1024x1536", "1536x1024"}
VIDEO_ASPECT_CHOICES = {"16:9", "9:16", "1:1"}
VIDEO_RESOLUTION_CHOICES = {"480p", "720p"}
VIDEO_DURATION_CHOICES = {4, 6, 8, 10, 15, 30}
AUDIO_STYLE_CHOICES = {"", "ambient", "cinematic", "electronic", "jazz", "pop", "orchestral"}


def normalize_generation_options(media_type, provider_slug, model_id, options):
    if options is None:
        options = {}
    if not isinstance(options, dict):
        raise ProviderError("Les options de génération doivent être un objet JSON.")
    if media_type == "image":
        image_size = options.get("image_size", "1024x1024")
        if image_size not in IMAGE_SIZE_CHOICES:
            raise ProviderError("Dimensions d’image non prises en charge.")
        return {"image_size": image_size}
    if media_type == "video":
        aspect_ratio = options.get("aspect_ratio", "16:9")
        resolution = options.get("resolution", "720p")
        replicate_fixed_video = provider_slug == "replicate" and model_id == "minimax/video-01"
        duration = options.get("duration_seconds", 6 if replicate_fixed_video else 8)
        generate_audio = options.get("generate_audio", not replicate_fixed_video)
        if aspect_ratio not in VIDEO_ASPECT_CHOICES or resolution not in VIDEO_RESOLUTION_CHOICES:
            raise ProviderError("Format ou résolution vidéo non pris en charge.")
        if isinstance(duration, bool) or duration not in VIDEO_DURATION_CHOICES:
            raise ProviderError("Durée vidéo non prise en charge.")
        if not isinstance(generate_audio, bool):
            raise ProviderError("L’option son vidéo doit être vraie ou fausse.")
        if provider_slug == "replicate" and model_id == "minimax/video-01":
            if aspect_ratio != "16:9" or resolution != "720p" or duration != 6 or generate_audio:
                raise ProviderError("Minimax Video-01 impose 16:9, 720p et 6 secondes; il ne génère pas de son.")
        return {
            "aspect_ratio": aspect_ratio,
            "resolution": resolution,
            "duration_seconds": duration,
            "generate_audio": generate_audio,
        }
    if media_type == "audio":
        style = options.get("style", "")
        instrumental = options.get("instrumental", False)
        if style not in AUDIO_STYLE_CHOICES or not isinstance(instrumental, bool):
            raise ProviderError("Options audio non prises en charge.")
        return {"style": style, "instrumental": instrumental}
    raise ProviderError("Type de média non pris en charge.")


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


def _run_fal(model_id, api_key, media_type, prompt, options):
    arguments = {"prompt": prompt}
    if media_type == "image":
        width, height = (int(value) for value in options["image_size"].split("x"))
        arguments["image_size"] = {"width": width, "height": height}
    elif media_type == "video":
        arguments.update({
            "aspect_ratio": options["aspect_ratio"],
            "duration": options["duration_seconds"],
            "resolution": options["resolution"],
            "generate_audio": options["generate_audio"],
        })
    elif media_type == "audio":
        if options["style"]:
            arguments["prompt"] += f"; musical style: {options['style']}"
        if options["instrumental"]:
            arguments["prompt"] += "; instrumental, no vocals"
    submitted = _json_request(f"https://queue.fal.run/{model_id}", api_key, "fal", arguments, "POST")
    request_id = submitted.get("request_id")
    if not request_id:
        raise ProviderError("fal n’a pas renvoyé d’identifiant de requête.")
    return _poll_fal(model_id, request_id, api_key)


def _replicate_output(model_id, api_key, media_type, prompt, options):
    model_input = {"prompt": prompt}
    if media_type == "image" and model_id == "black-forest-labs/flux-schnell":
        aspect_ratio = {
            "1024x1024": "1:1",
            "1024x1536": "2:3",
            "1536x1024": "3:2",
        }[options["image_size"]]
        model_input.update({"aspect_ratio": aspect_ratio, "megapixels": "1"})
    elif media_type == "video" and model_id == "minimax/video-01":
        pass
    elif media_type == "audio" and model_id == "meta/musicgen":
        if options["style"]:
            model_input["prompt"] += f"; style: {options['style']}"
        if options["instrumental"]:
            model_input["prompt"] += "; instrumental only, no vocals"
    elif media_type == "audio":
        if options["style"]:
            model_input["prompt"] += f"; style: {options['style']}"
        if options["instrumental"]:
            model_input["prompt"] += "; instrumental only, no vocals"
    model_ref, separator, version = model_id.partition(":")
    if separator:
        endpoint = "https://api.replicate.com/v1/predictions"
        payload = {"version": version, "input": model_input}
    else:
        parts = model_ref.split("/")
        if len(parts) != 2 or not all(parts):
            raise ProviderError("Le modèle Replicate doit être au format propriétaire/modèle.")
        endpoint = f"https://api.replicate.com/v1/models/{parts[0]}/{parts[1]}/predictions"
        payload = {"input": model_input}
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


def _run_apiframe(model_id, api_key, media_type, prompt, options):
    endpoint_name = {"image": "images", "video": "videos", "audio": "music"}[media_type]
    root = "https://api.apiframe.ai/v2"
    payload = {"prompt": prompt, "model": model_id}
    if media_type == "image" and model_id == "flux-2-pro":
        width, height = (int(value) for value in options["image_size"].split("x"))
        resolution = "1 MP" if width * height <= 1_100_000 else "2 MP"
        payload["fluxParams"] = {"aspect_ratio": f"{width}:{height}", "resolution": resolution}
    elif media_type == "video" and model_id == "seedance-2.5":
        payload["seedanceParams"] = {
            "duration": options["duration_seconds"],
            "resolution": options["resolution"],
            "aspect_ratio": options["aspect_ratio"],
            "generate_audio": options["generate_audio"],
        }
    elif media_type == "audio" and model_id == "suno":
        payload["sunoParams"] = {
            "model_version": "V4_5PLUS",
            "custom_mode": False,
            "instrumental": options["instrumental"],
        }
        if options["style"]:
            payload["sunoParams"]["style"] = options["style"]
    job = _json_request(f"{root}/{endpoint_name}/generate", api_key, "apiframe", payload, "POST")
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


def generate_media(provider, api_key, media_type, prompt, model_id, options=None):
    options = normalize_generation_options(media_type, provider.slug, model_id, options)
    if provider.slug == "openai" and media_type == "image":
        encoded, _usage = generate_openai_image(provider, api_key, prompt, size=options["image_size"])
        import base64
        return base64.b64decode(encoded, validate=True), "image/png"

    if provider.slug == "fal":
        output = _run_fal(model_id, api_key, media_type, prompt, options)
    elif provider.slug == "replicate":
        output = _replicate_output(model_id, api_key, media_type, prompt, options)
    elif provider.slug == "apiframe":
        output = _run_apiframe(model_id, api_key, media_type, prompt, options)
    else:
        raise ProviderError("Ce fournisseur ne prend pas en charge la génération de médias.")

    output_url = _first_url(output)
    if not output_url:
        raise ProviderError("Le fournisseur n’a renvoyé aucun fichier généré.")
    return _download_output(output_url, provider.slug, media_type, api_key)