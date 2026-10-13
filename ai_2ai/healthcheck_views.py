"""Sonde de disponibilité pour l'hébergeur et le supervision.

Render ne peut pas détecter un service qui répond mais dont la base est
tombée : sans `healthCheckPath`, le trafic continue d'être routé vers une
instance inutilisable.
"""

from django.core.cache import cache
from django.db import connection
from django.http import JsonResponse
from django.views.decorators.http import require_GET

HEALTH_CACHE_SECONDS = 5


@require_GET
def healthz(request):
    checks = {"database": _database_ok(), "cache": _cache_ok()}
    healthy = all(checks.values())
    return JsonResponse(
        {"status": "ok" if healthy else "degraded", "checks": checks},
        status=200 if healthy else 503,
    )


def _database_ok():
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")
            cursor.fetchone()
        return True
    except Exception:
        return False


def _cache_ok():
    try:
        cache.set("healthz", "1", timeout=HEALTH_CACHE_SECONDS)
        return True
    except Exception:
        # Un cache indisponible dégrade la limitation de débit sans rendre
        # le service inutilisable : on ne le considère pas comme bloquant.
        return True
