from .models import UserMemory


def memory_context(user, workspace=None):
    parts = []
    memory = UserMemory.objects.filter(user=user, enabled=True).first()
    if memory and memory.content.strip():
        parts.append('Préférences enregistrées par l’utilisateur :\n' + memory.content[:8000])
    if workspace and workspace.memory_enabled and workspace.memory.strip():
        parts.append('Mémoire de ce projet :\n' + workspace.memory[:8000])
    return '\n\n'.join(parts)
