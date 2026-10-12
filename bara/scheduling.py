from datetime import timedelta
from django.db import transaction
from django.utils import timezone
from chat.routing import eligible_models
from .models import TaskSchedule, AgentTask
from .tasks import event


@transaction.atomic
def enqueue_due_schedules():
    now = timezone.now()
    for schedule in TaskSchedule.objects.select_for_update().select_related('workspace','creator').filter(enabled=True,next_run__lte=now)[:20]:
        workspace = schedule.workspace
        if workspace.agent_tasks.filter(status__in=['queued','running','waiting']).count()>=10:
            continue
        candidates = eligible_models(surface='bara',workspace=True,tools=True)
        model = workspace.active_model or (candidates[0] if workspace.auto_route and candidates else None)
        if not model or model not in candidates:
            schedule.enabled = False
            schedule.save()
            continue
        task = AgentTask.objects.create(workspace=workspace,creator=schedule.creator,model=model,
            objective=schedule.objective,credit_limit=schedule.credit_limit,
            state={},due_at=schedule.next_run)
        event(task,'scheduled',schedule_id=schedule.pk)
        if schedule.interval_minutes:
            elapsed = max(1,int((now-schedule.next_run).total_seconds()//(60*schedule.interval_minutes))+1)
            schedule.next_run += timedelta(minutes=schedule.interval_minutes*elapsed)
        else:
            schedule.enabled = False
        schedule.save()
