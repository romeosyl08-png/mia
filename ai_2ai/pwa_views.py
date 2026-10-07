from pathlib import Path

from django.conf import settings
from django.http import FileResponse, HttpResponseNotFound
from django.views.decorators.http import require_GET


PWA_FILES = Path(settings.BASE_DIR) / "static" / "app" / "pwa"


def _serve_pwa_file(filename, content_type):
    path = PWA_FILES / filename
    if not path.is_file():
        return HttpResponseNotFound("Fichier PWA indisponible.")
    response = FileResponse(path.open("rb"), content_type=content_type)
    response["X-Content-Type-Options"] = "nosniff"
    response["Cache-Control"] = "no-cache"
    return response


@require_GET
def web_manifest(request):
    return _serve_pwa_file("manifest.webmanifest", "application/manifest+json")


@require_GET
def service_worker(request):
    response = _serve_pwa_file("service-worker.js", "application/javascript; charset=utf-8")
    response["Service-Worker-Allowed"] = "/"
    return response
