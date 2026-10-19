"""Expose only Big Persona's visual settings to authenticated API clients."""
from django.http import JsonResponse
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_GET

from chat.persona_context import ensure_big_persona
from .views import _guard


@never_cache
@require_GET
def appearance_view(request):
    user, _, error = _guard(request)
    if error:
        return error
    persona = ensure_big_persona(user)
    return JsonResponse({'theme': persona.appearance_theme or 'light',
                         'accent': persona.appearance_accent or '#6C6FCB'})
