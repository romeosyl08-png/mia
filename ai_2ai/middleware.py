"""En-têtes de sécurité HTTP, dont une Content-Security-Policy.

Sans CSP, une seule injection HTML dans une réponse de modèle (ou une
compromission d'une dépendance tierce) donne un XSS sur tout le compte.
La politique est volontairement stricte et centralisée ici pour rester
lisible ; chaque dérogation est commentée.
"""

from django.conf import settings


def _policy_string():
    extra = getattr(settings, "CSP_EXTRA_DIRECTIVES", None) or {}
    directives = {
        "default-src": ["'self'"],
        # Tous les scripts sont servis par l'application (voir static/app/vendor),
        # à l'exception du widget officiel de connexion Telegram.
        # Autorise la compilation du moteur Markdown Wasm ; eval() reste interdit.
        "script-src": ["'self'", "'wasm-unsafe-eval'", "https://telegram.org"],
        # Les gabarits utilisent massivement des attributs style="...".
        "style-src": ["'self'", "'unsafe-inline'"],
        "img-src": ["'self'", "data:", "blob:"],
        "font-src": ["'self'", "data:"],
        "media-src": ["'self'"],
        "object-src": ["'none'"],
        "base-uri": ["'self'"],
        # Chrome applique aussi form-action aux redirections après un POST.
        # Les formulaires de recharge redirigent vers ces prestataires validés.
        "form-action": [
            "'self'",
            "https://app.paydunya.com",
            "https://paydunya.com",
            "https://checkout.stripe.com",
        ],
        "frame-ancestors": ["'self'"],
        # Dérogation : l'IA locale est appelée depuis le navigateur vers
        # Ollama / LM Studio / Jan sur la machine de l'utilisateur.
        "connect-src": [
            "'self'",
            "http://localhost:*",
            "http://127.0.0.1:*",
            "http://[::1]:*",
        ],
        # Dérogation : l'aperçu de site de Bara affiche une URL choisie
        # par l'utilisateur dans une iframe sandboxée, et le widget Telegram
        # s'affiche lui aussi dans une iframe.
        "frame-src": ["'self'", "https://telegram.org", "https:", "http:", "data:"],
    }
    for name, values in extra.items():
        if values is None:
            directives.pop(name, None)
        else:
            directives[name] = list(values)
    return "; ".join(f"{name} {' '.join(values)}".strip() for name, values in directives.items())


class SecurityHeadersMiddleware:
    """Ajoute une CSP et quelques en-têtes défensifs absents de Django."""

    header = "Content-Security-Policy"

    def __init__(self, get_response):
        self.get_response = get_response
        self.report_only = bool(getattr(settings, "CSP_REPORT_ONLY", False))
        if self.report_only:
            self.header = "Content-Security-Policy-Report-Only"

    def __call__(self, request):
        response = self.get_response(request)
        # Les vues qui servent du contenu imposent déjà leur propre politique
        # (par exemple les pages publiées, servies en sandbox).
        if response.headers.get("Content-Security-Policy"):
            return response
        response.headers.setdefault(self.header, _policy_string())
        response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
        response.headers.setdefault(
            "Permissions-Policy",
            "geolocation=(), microphone=(), camera=(), payment=(), usb=()",
        )
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("Cross-Origin-Opener-Policy", "same-origin")
        return response
