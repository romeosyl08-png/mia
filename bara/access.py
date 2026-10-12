from django.db.models import Q
from django.http import Http404
from django.shortcuts import get_object_or_404
from .models import Workspace, WorkspaceAudit


def accessible_workspaces(user):
    return Workspace.objects.filter(Q(user=user)|Q(members__user=user)).distinct()


def get_workspace(request, pk, write=None, admin=False):
    workspace = get_object_or_404(accessible_workspaces(request.user),pk=pk)
    write = request.method not in {'GET','HEAD','OPTIONS'} if write is None else write
    if workspace.user_id != request.user.pk:
        if admin or (write and not workspace.members.filter(user=request.user,role='editor').exists()):
            raise Http404
    if write:
        WorkspaceAudit.objects.create(workspace=workspace,actor=request.user,action=request.path[:150])
    workspace.can_write = workspace.user_id == request.user.pk or workspace.members.filter(user=request.user,role='editor').exists()
    return workspace
