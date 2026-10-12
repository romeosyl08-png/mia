import time
from django.core.management.base import BaseCommand
from django.db import close_old_connections
from bara.tasks import claim_task, execute_task
from bara.scheduling import enqueue_due_schedules
from studio.jobs import claim_job, execute_job


class Command(BaseCommand):
    help = 'Traite les tâches persistantes (même stockage que le serveur Web).'

    def add_arguments(self, parser):
        parser.add_argument('--once', action='store_true')

    def handle(self, *args, **options):
        try:
            while True:
                close_old_connections()
                enqueue_due_schedules()
                task = claim_task()
                if task:
                    execute_task(task)
                studio_job = claim_job()
                if studio_job:
                    execute_job(studio_job)
                if not task and not studio_job and options['once']:
                    return
                if not task and not studio_job:
                    time.sleep(2)
        except KeyboardInterrupt:
            return
