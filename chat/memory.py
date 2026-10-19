from .models import UserMemory


def memory_context(user, workspace=None, *, surface='chat', include_personal=True):
    if surface not in {'chat', 'bara', 'barrassons'}:
        raise ValueError('Surface de mémoire inconnue.')
    parts = []
    memory = UserMemory.objects.filter(user=user, enabled=True).first()
    if include_personal and memory and getattr(memory, 'use_in_' + surface) and memory.content.strip():
        parts.append('Préférences enregistrées par l’utilisateur :\n' + memory.content[:8000])
    project_allowed = (workspace and (workspace.user_id == user.pk or workspace.members.filter(user=user).exists()))
    project_linked = workspace and (surface == 'bara' or getattr(workspace, 'memory_in_' + surface))
    if project_allowed and project_linked and workspace.memory_enabled and workspace.memory.strip():
        parts.append('Mémoire de ce projet :\n' + workspace.memory[:8000])
    return '\n\n'.join(parts)
