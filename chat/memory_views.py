from django.contrib.auth.decorators import login_required
from django.shortcuts import render, get_object_or_404, redirect
from bara.models import Workspace
from .models import UserMemory


@login_required
def memory_settings(request, workspace_id=None):
    workspace = get_object_or_404(Workspace, pk=workspace_id, user=request.user) if workspace_id else None
    memory, _ = UserMemory.objects.get_or_create(user=request.user)
    target = workspace or memory
    if request.method == 'POST':
        content = request.POST.get('content', '')[:8000]
        if workspace:
            workspace.memory = content
            workspace.memory_enabled = 'enabled' in request.POST
            workspace.semantic_search = 'semantic_search' in request.POST
        else:
            memory.content = content
            memory.enabled = 'enabled' in request.POST
        target.save()
        return redirect(request.path)
    return render(request, 'memory.html', {'workspace':workspace, 'content':workspace.memory if workspace else memory.content,
        'enabled':workspace.memory_enabled if workspace else memory.enabled})
