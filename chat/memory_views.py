from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.db import transaction
from django.shortcuts import render, get_object_or_404, redirect
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_http_methods
from bara.models import Workspace
from .models import UserMemory
from .memory_import import (
    IMPORT_MAX_CHARS, MEMORY_EXPORT_PROMPT, MEMORY_MAX_CHARS,
    MemoryImportError, combine_memory, memory_revision, parse_memory_import,
)


@login_required
@never_cache
@require_http_methods(['GET', 'POST'])
def memory_settings(request, workspace_id=None):
    workspace = get_object_or_404(Workspace, pk=workspace_id, user=request.user) if workspace_id else None
    memory, _ = UserMemory.objects.get_or_create(user=request.user)
    target = workspace or memory
    content_field = 'memory' if workspace else 'content'
    context = {
        'workspace': workspace,
        'content': getattr(target, content_field),
        'enabled': workspace.memory_enabled if workspace else memory.enabled,
        'export_prompt': MEMORY_EXPORT_PROMPT,
        'memory_limit': MEMORY_MAX_CHARS,
        'import_limit': IMPORT_MAX_CHARS,
        'import_text': '',
        'import_mode': 'append',
        'revision': memory_revision(getattr(target, content_field)),
        'links': target,
    }
    if request.method == 'POST':
        action = request.POST.get('action', 'save')
        try:
            if action == 'save_links':
                fields = (['memory_in_chat', 'memory_in_barrassons'] if workspace else
                          ['use_in_chat', 'use_in_bara', 'use_in_barrassons'])
                for field in fields:
                    setattr(target, field, field in request.POST)
                target.save(update_fields=fields)
                messages.success(request, 'Liens de mémoire enregistrés.')
                return redirect(request.path + '#memory-links')
            if action == 'preview_import':
                context['import_text'] = request.POST.get('import_text', '')[:IMPORT_MAX_CHARS]
                context['import_preview'] = parse_memory_import(
                    request.POST.get('import_text', ''), request.FILES.get('import_file'),
                )
                context['preview_chars'] = len(context['import_preview'])
                return render(request, 'memory.html', context)
            if action == 'apply_import':
                imported = request.POST.get('import_content', '')
                mode = request.POST.get('import_mode', 'append')
                context.update(import_preview=imported[:IMPORT_MAX_CHARS], import_mode=mode,
                               preview_chars=len(imported))
                with transaction.atomic():
                    locked = type(target).objects.select_for_update().get(pk=target.pk, user=request.user)
                    current = getattr(locked, content_field)
                    # Keep other edits made while this preview was open.
                    if request.POST.get('revision') != memory_revision(current):
                        context.update(content=current, revision=memory_revision(current))
                        raise MemoryImportError(
                            'Ta mémoire a changé depuis cet aperçu. Relis la mémoire actuelle '
                            'ci-dessous avant de valider à nouveau.'
                        )
                    setattr(locked, content_field, combine_memory(current, imported, mode))
                    fields = [content_field] + ([] if workspace else ['updated_at'])
                    locked.save(update_fields=fields)
                messages.success(request, 'Mémoire importée. Tu peux la modifier ci-dessous.')
                return redirect(request.path + '#saved-memory')
            if action != 'save':
                raise MemoryImportError('Action de mémoire inconnue.')
            content = request.POST.get('content', '')
            context.update(content=content[:IMPORT_MAX_CHARS], enabled='enabled' in request.POST)
            if workspace:
                workspace.semantic_search = 'semantic_search' in request.POST
            if len(content) > MEMORY_MAX_CHARS:
                raise MemoryImportError('La mémoire dépasse 8 000 caractères. Raccourcis le texte avant de l’enregistrer.')
            if workspace:
                workspace.memory = content
                workspace.memory_enabled = 'enabled' in request.POST
                workspace.semantic_search = 'semantic_search' in request.POST
                target.save(update_fields=['memory', 'memory_enabled', 'semantic_search'])
            else:
                memory.content = content
                memory.enabled = 'enabled' in request.POST
                target.save(update_fields=['content', 'enabled', 'updated_at'])
            return redirect(request.path)
        except MemoryImportError as error:
            context['error'] = str(error)
            return render(request, 'memory.html', context, status=400)
    return render(request, 'memory.html', context)
