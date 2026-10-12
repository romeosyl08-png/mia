from datetime import datetime
from django.contrib.auth import get_user_model
from django.contrib.auth.decorators import login_required
from django.shortcuts import render, redirect
from django.utils import timezone
from .access import get_workspace
from .models import WorkspaceMember, TaskSchedule, WorkspaceAudit


@login_required
def sharing(request, workspace_id):
    workspace = get_workspace(request,workspace_id,admin=True)
    error = ''
    if request.method == 'POST':
        user = get_user_model().objects.filter(username=request.POST.get('username','')).first()
        role = request.POST.get('role')
        if not user or user == workspace.user or role not in {'viewer','editor','remove'}:
            error = 'Compte existant et rôle valide requis.'
        elif role == 'remove':
            WorkspaceMember.objects.filter(workspace=workspace,user=user).delete()
        else:
            WorkspaceMember.objects.update_or_create(workspace=workspace,user=user,defaults={'role':role})
        if not error:
            return redirect(request.path)
    return render(request,'bara/sharing.html',{'workspace':workspace,'members':workspace.members.select_related('user'),
        'audit':workspace.audit_events.select_related('actor').order_by('-id')[:100],'error':error})


@login_required
def schedules(request, workspace_id):
    workspace = get_workspace(request,workspace_id,admin=True)
    error = ''
    if request.method == 'POST':
        try:
            if request.POST.get('delete'):
                workspace.schedules.filter(pk=int(request.POST['delete'])).delete()
                return redirect(request.path)
            date = datetime.fromisoformat(request.POST.get('next_run',''))
            if timezone.is_naive(date):
                date = timezone.make_aware(date)
            interval = int(request.POST.get('interval_minutes',0))
            limit = int(request.POST.get('credit_limit',1000))
            objective = request.POST.get('objective','').strip()
            if not objective or len(objective)>16000 or not 0<=interval<=525600 or (interval and interval<60) or not 1<=limit<=100000 or date<=timezone.now():
                raise ValueError
            if workspace.schedules.count()>=20:
                raise ValueError
            TaskSchedule.objects.create(workspace=workspace,creator=request.user,objective=objective,next_run=date,
                                        interval_minutes=interval,credit_limit=limit)
            return redirect(request.path)
        except (ValueError,TypeError):
            error = 'Date future, objectif et budgets valides requis. Récurrence minimum : 60 minutes.'
    return render(request,'bara/schedules.html',{'workspace':workspace,'schedules':workspace.schedules.all(),'error':error})
