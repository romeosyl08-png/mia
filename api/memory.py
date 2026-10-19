"""Explicit, account-scoped memory selection for Barrassons API calls."""
from django.shortcuts import get_object_or_404

from bara.access import accessible_workspaces
from chat.memory import memory_context


def workspace_id(value):
    if type(value) is not int or not 0 < value < 2**63:
        raise ValueError('Identifiant de projet Bara invalide.')
    return value


def selected_context(user, selection):
    if not isinstance(selection, dict) or selection.keys() - {'personal', 'workspace_id'}:
        raise ValueError('Sélection de mémoire invalide.')
    personal = selection.get('personal', False)
    if not isinstance(personal, bool):
        raise ValueError('Le choix de mémoire personnelle doit être un booléen.')
    project_id = selection.get('workspace_id')
    workspace = (get_object_or_404(accessible_workspaces(user), pk=workspace_id(project_id))
                 if project_id is not None else None)
    return memory_context(user, workspace, surface='barrassons', include_personal=personal)
