from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.shortcuts import get_object_or_404
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_GET

from .memory import memory_context
from .continuity import related_conversation_context
from .models import Persona
from .persona_context import ensure_big_persona, persona_settings, persona_snapshot, shared_persona_context
from .temporary import persona_memory_context, selected_memory_persona


@login_required
@never_cache
@require_GET
def big_persona(request):
    persona = ensure_big_persona(request.user)
    snapshot = persona_snapshot(request.user, persona)
    return JsonResponse({
        'persona': {**persona_settings(persona), 'is_big_persona': True, 'share_with_personas': False},
        **snapshot,
        'context_chars': len(shared_persona_context(request.user, persona)),
    })


@login_required
@never_cache
@require_GET
def persona_context(request, pk):
    persona = get_object_or_404(Persona, pk=pk, user=request.user)
    if request.GET.get('temporary') == '1':
        try:
            memory_persona = selected_memory_persona(request.user, request.GET.get('memory_persona_id'))
        except ValueError as error:
            return JsonResponse({'error': {'message': str(error)}}, status=400)
        return JsonResponse({'system_prompt': persona.prompt + persona_memory_context(memory_persona)})
    parts = [persona.prompt, memory_context(request.user)]
    if persona.memory.strip():
        parts.append('Préférences mémorisées pour ce persona :\n' + persona.memory[:8000])
    parts.append(shared_persona_context(request.user, persona))
    # A local-only conversation never uploads its messages for server-side recall.
    parts.append(related_conversation_context(request.user, persona=persona))
    return JsonResponse({'system_prompt': '\n\n'.join(part for part in parts if part.strip())})
