import json
from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, render, redirect
from django.views.decorators.http import require_POST
from chat.routing import eligible_models, recommend_model
from chat.capabilities import capability_issue
from .access import get_workspace
from .models import AgentTask, Workspace, WorkConversation
from .tasks import event
from .harnesses import configure_harness, select_harness


@login_required
def task_list(request, workspace_id):
    workspace = get_workspace(request, workspace_id)
    return render(request, 'bara/tasks.html', {'workspace': workspace, 'tasks': workspace.agent_tasks.all()[:50]})


@login_required
@require_POST
def create_task(request, workspace_id):
    workspace = get_workspace(request, workspace_id)
    objective = request.POST.get('objective', '').strip()
    if not objective or len(objective) > 16000:
        return JsonResponse({'error': 'Objectif requis (16 000 caractères maximum).'}, status=400)
    try:
        config = configure_harness(objective, request.POST.get('harness', 'auto'),
                                   request.POST.get('verification_command', '').strip())
    except ValueError as error:
        return JsonResponse({'error': str(error)}, status=400)
    harness = select_harness(objective, config['id'])
    problem = harness.preflight(objective, workspace)
    if problem:
        return JsonResponse({'error': problem, 'charged_credits': 0}, status=400)
    candidates = eligible_models(surface='bara', workspace=True, tools=True)
    model = workspace.active_model or (candidates[0] if workspace.auto_route and candidates else None)
    if workspace.auto_route and candidates:
        decision = recommend_model(request.user,candidates,harness.category(objective),current_model=model)
        model = next((m for m in candidates if m.model_id==decision['model_id'] and m.provider.slug==decision['provider_slug']),model)
    if not model or model not in candidates:
        return JsonResponse({'error': 'Choisis un modèle compatible avec les outils.'}, status=400)
    problem = capability_issue(objective, 'chat', [model], workspace)
    if problem:
        return JsonResponse({'error': problem['message'], 'charged_credits': 0}, status=400)
    try:
        max_steps = int(request.POST.get('max_steps', 12))
        credit_limit = int(request.POST.get('credit_limit', 1000))
        if not 1 <= max_steps <= 100 or not 1 <= credit_limit <= 100000:
            raise ValueError
    except (ValueError, TypeError):
        return JsonResponse({'error': 'Budget invalide.'}, status=400)
    if workspace.agent_tasks.filter(status__in=['queued', 'running', 'waiting']).count() >= 10:
        return JsonResponse({'error': 'Dix tâches actives maximum par espace.'}, status=429)
    conv = WorkConversation.objects.filter(workspace=workspace, pk=request.POST.get('conversation_id')).first() if request.POST.get('conversation_id', '').isdigit() else None
    task = AgentTask.objects.create(workspace=workspace, creator=request.user, model=model,
                                   objective=objective, conversation=conv, max_steps=max_steps, credit_limit=credit_limit,
                                   state={'harness': config})
    event(task, 'queued')
    return redirect('bara:tasks', workspace_id=workspace.pk)


@login_required
def task_detail(request, workspace_id, task_id):
    workspace = get_workspace(request, workspace_id)
    task = get_object_or_404(AgentTask, pk=task_id, workspace=workspace)
    harness = task.state.get('harness', {})
    try:
        after = max(0, int(request.GET.get('after', 0)))
    except ValueError:
        return JsonResponse({'error': 'Curseur invalide.'}, status=400)
    return JsonResponse({'id': task.pk, 'status': task.status, 'result': task.result, 'error': task.error,
                         'steps': task.steps, 'max_steps': task.max_steps, 'credits': task.spent_credits,
                         'credit_limit': task.credit_limit,
                         'harness': {'id': harness.get('id'), 'plan': harness.get('plan', []),
                                     'verification': harness.get('verification')},
                         'events': list(task.events.filter(pk__gt=after).order_by('id').values('id', 'kind', 'data')[:100])})


@login_required
@require_POST
@transaction.atomic
def control_task(request, workspace_id, task_id, action):
    workspace = get_workspace(request, workspace_id)
    task = get_object_or_404(AgentTask.objects.select_for_update(), pk=task_id, workspace=workspace)
    if action == 'pause' and task.status in {'queued', 'running'}:
        task.status = 'pause_requested' if task.status == 'running' else 'paused'
    elif action == 'cancel' and task.status not in {'completed', 'cancelled'}:
        task.status = 'cancel_requested' if task.lease_token else 'cancelled'
    elif action == 'resume' and task.status in {'paused', 'waiting', 'failed'}:
        try:
            steps = int(request.POST.get('max_steps', task.max_steps))
            limit = int(request.POST.get('credit_limit', task.credit_limit))
            if not max(1, task.steps) <= steps <= 100 or not max(1, task.spent_credits) <= limit <= 100000:
                raise ValueError
        except (ValueError, TypeError):
            return JsonResponse({'error': 'Budget invalide.'}, status=400)
        task.max_steps, task.credit_limit = steps, limit
        task.status, task.error = 'queued', ''
    else:
        return JsonResponse({'error': 'Action incompatible avec l’état actuel.'}, status=409)
    task.save()
    event(task, task.status)
    return JsonResponse({'status': task.status})
