from django.db import transaction
from django.http import Http404, JsonResponse
from django.shortcuts import get_object_or_404
from django.views.decorators.cache import never_cache
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods

from bara.access import accessible_workspaces
from chat.models import UserMemory
from chat.memory_import import MemoryImportError, combine_memory, memory_revision
from .memory import workspace_id
from .views import _error, _guard, _json_body


def _target(user, scope, project_id, *, write=False, lock=False):
    if scope == 'personal':
        query = UserMemory.objects.filter(user=user)
        target = (query.select_for_update() if lock else query).first()
        if target is None or not target.enabled or not target.use_in_barrassons:
            raise PermissionError('Active le lien Barrassons dans la page Ma mémoire de 2AI.')
        return target, 'content', 'Mémoire personnelle', True
    if scope != 'workspace':
        raise ValueError('Choisis une mémoire personnelle ou un projet Bara.')
    query = accessible_workspaces(user)
    # Lock a plain model queryset; accessible_workspaces uses DISTINCT.
    target = get_object_or_404(query, pk=workspace_id(project_id))
    if lock:
        target = type(target).objects.select_for_update().get(pk=target.pk)
    if not target.memory_enabled or not target.memory_in_barrassons:
        raise PermissionError('Active le lien Barrassons dans la mémoire de ce projet Bara.')
    can_write = target.user_id == user.pk
    if write and not can_write:
        raise PermissionError('Seul le propriétaire peut modifier la mémoire de ce projet.')
    return target, 'memory', target.title, can_write


@csrf_exempt
@never_cache
@require_http_methods(['GET', 'POST'])
def memory_view(request):
    user, _, error = _guard(request)
    if error:
        return error
    try:
        if request.method == 'GET':
            scope = request.GET.get('target')
            if scope is None:
                memory = UserMemory.objects.filter(user=user).first()
                return JsonResponse({
                    'personal': {'available': bool(memory and memory.enabled and memory.use_in_barrassons)},
                    'workspaces': [
                        {'id': w.pk, 'title': w.title, 'available': w.memory_enabled and w.memory_in_barrassons,
                         'can_write': w.user_id == user.pk}
                        for w in accessible_workspaces(user)
                    ],
                })
            raw_id = request.GET.get('workspace_id', '')
            project_id = int(raw_id) if raw_id.isascii() and raw_id.isdigit() and len(raw_id) <= 19 else None
            target, field, name, can_write = _target(user, scope, project_id)
            content = getattr(target, field)
            return JsonResponse({'name': name, 'content': content, 'revision': memory_revision(content),
                                 'can_write': can_write})

        payload = _json_body(request)
        if payload is None or not isinstance(payload.get('content'), str):
            raise ValueError('Un souvenir au format texte est attendu.')
        with transaction.atomic():
            target, field, name, _ = _target(user, payload.get('target'), payload.get('workspace_id'),
                                             write=True, lock=True)
            current = getattr(target, field)
            if payload.get('revision') != memory_revision(current):
                return _error('La mémoire a changé. Relis-la avant de confirmer à nouveau.',
                              'memory_conflict', 409)
            setattr(target, field, combine_memory(current, payload['content']))
            target.save(update_fields=[field, 'updated_at'])
        return JsonResponse({'name': name, 'status': 'saved'})
    except PermissionError as error:
        return _error(str(error), 'memory_unavailable', 403)
    except Http404:
        return _error('Projet de mémoire introuvable ou inaccessible.', 'not_found', 404)
    except (ValueError, MemoryImportError) as error:
        return _error(str(error), 'invalid_request_error', 400)
