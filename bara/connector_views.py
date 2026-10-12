from django.contrib.auth.decorators import login_required
from django.conf import settings
from django.shortcuts import render, redirect
from .access import get_workspace
from .models import WorkspacePluginConnection


@login_required
def connections(request,workspace_id):
    workspace = get_workspace(request,workspace_id,admin=True)
    error = ''
    if request.method=='POST':
        token = request.POST.get('token','').strip()
        if not 10<=len(token)<=4096:
            error = 'Jeton GitHub invalide.'
        elif not request.is_secure() and not settings.DEBUG:
            error = 'Une connexion HTTPS est requise.'
        else:
            connection,_ = WorkspacePluginConnection.objects.get_or_create(workspace=workspace,plugin='github')
            connection.set_oauth_tokens(token)
            connection.is_enabled = True
            connection.save()
            return redirect(request.path)
    return render(request,'bara/connections.html',{'workspace':workspace,'connections':workspace.plugin_connections.filter(is_enabled=True),'error':error})
