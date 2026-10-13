"""Vérifications de déploiement exécutées par `manage.py check`.

Elles signalent les réglages qui fonctionnent en développement mais
dégradent silencieusement la production (limitation de débit non partagée,
chiffrement absent, etc.).
"""

from django.conf import settings
from django.core.checks import Warning, register


@register("deploy")
def check_shared_cache(app_configs, **kwargs):
    """Sans cache partagé, la limitation de débit n'est pas fiable."""
    if settings.DEBUG:
        return []
    if not getattr(settings, "REDIS_URL", ""):
        return [
            Warning(
                "REDIS_URL n'est pas configuré : Django retombe sur un cache "
                "local par processus. La limitation de débit par utilisateur "
                "n'est alors ni partagée entre les instances ni conservée après "
                "un redéploiement.",
                hint="Renseigne REDIS_URL (voir .env.example).",
                id="ai_2ai.W001",
            )
        ]
    return []


@register("deploy")
def check_email_for_password_reset(app_configs, **kwargs):
    """La réinitialisation par e-mail est morte sans serveur SMTP."""
    if settings.DEBUG:
        return []
    if getattr(settings, "EMAIL_BACKEND", "").endswith("smtp.EmailBackend") and not getattr(
        settings, "EMAIL_HOST", ""
    ):
        return [
            Warning(
                "Aucun serveur de messagerie configuré : le mot de passe oublié "
                "ne pourra pas envoyer ses liens.",
                hint="Configure EMAIL_* ou utilise un backend transactionnel (voir .env.example).",
                id="ai_2ai.W002",
            )
        ]
    return []


@register("deploy")
def check_media_root_is_not_served(app_configs, **kwargs):
    """MEDIA_ROOT ne doit jamais être servi directement."""
    if settings.DEBUG:
        return []
    from django.urls import get_resolver

    exposed = [
        pattern
        for pattern in get_resolver().url_patterns
        if "media" in str(getattr(pattern, "pattern", ""))
    ]
    if exposed:
        return [
            Warning(
                "MEDIA_ROOT est servi sans contrôle d'accès : les fichiers des "
                "utilisateurs sont téléchargeables sans authentification.",
                hint="Réserve ce servage à DEBUG, ou passe les fichiers sur un stockage privé.",
                id="ai_2ai.W003",
            )
        ]
    return []
