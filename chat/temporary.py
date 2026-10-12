"""Explicit memory selection for temporary conversations."""
from django.shortcuts import get_object_or_404

from .models import Persona


def selected_memory_persona(user, persona_id):
    if persona_id in (None, ''):
        return None
    if isinstance(persona_id, bool) or not str(persona_id).isdigit() or int(persona_id) <= 0:
        raise ValueError('Identifiant de mémoire de persona invalide.')
    return get_object_or_404(Persona, pk=int(persona_id), user=user)


def persona_memory_context(persona):
    if persona is None or not persona.memory.strip():
        return ''
    return ('\n\nPréférences mémorisées par l’utilisateur pour le persona « '
            + persona.name + ' » (à suivre si elles sont pertinentes pour la demande) :\n'
            + persona.memory.strip()[:8000])
