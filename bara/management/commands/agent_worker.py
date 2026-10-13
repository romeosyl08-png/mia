"""Traite les tâches persistantes (même stockage que le serveur Web).

Les tâches agent et les travaux Studio partagent le même volume que le
serveur Web, donc le même processus de supervision. Elles tournent dans
deux boucles indépendantes : une génération Studio peut durer plusieurs
minutes et ne doit pas retarder les tâches agent.

    python manage.py agent_worker              # les deux files
    python manage.py agent_worker --queue tasks
    python manage.py agent_worker --queue studio
"""

import logging
import threading
import time

from django.core.management.base import BaseCommand, CommandError
from django.db import close_old_connections

from bara.scheduling import enqueue_due_schedules
from bara.tasks import claim_task, execute_task
from studio.jobs import claim_job, execute_job

logger = logging.getLogger(__name__)

POLL_SECONDS = 2
MAX_BACKOFF_SECONDS = 30


def _loop(name, claim, execute, once, stop):
    """Boucle d'un seul type de tâche. Ne laisse jamais remonter d'exception."""
    backoff = 0
    while not stop.is_set():
        close_old_connections()
        try:
            enqueue_due_schedules()
            item = claim()
        except Exception:
            logger.exception("Le dépilage de la file %s a échoué", name)
            item = None
        if item is not None:
            backoff = 0
            try:
                execute(item)
            except Exception:
                # Une tâche qui échoue ne doit jamais tuer le worker, et donc
                # le serveur Web qui partage sa supervision.
                logger.exception("La tâche %s #%s a échoué", name, getattr(item, "pk", "?"))
            continue
        if once:
            return
        if backoff:
            stop.wait(backoff)
        else:
            stop.wait(POLL_SECONDS)


class Command(BaseCommand):
    help = 'Traite les tâches persistantes (même stockage que le serveur Web).'

    def add_arguments(self, parser):
        parser.add_argument('--once', action='store_true')
        parser.add_argument(
            '--queue',
            choices=['all', 'tasks', 'studio'],
            default='all',
            help='Limiter le worker à une seule file.',
        )

    def handle(self, *args, **options):
        queues = []
        if options['queue'] in {'all', 'tasks'}:
            queues.append(('tasks', claim_task, execute_task))
        if options['queue'] in {'all', 'studio'}:
            queues.append(('studio', claim_job, execute_job))

        if not queues:
            raise CommandError('Aucune file sélectionnée.')

        stop = threading.Event()
        threads = [
            threading.Thread(
                target=_loop,
                args=(name, claim, execute, options['once'], stop),
                name=f'worker-{name}',
                daemon=True,
            )
            for name, claim, execute in queues
        ]
        for thread in threads:
            thread.start()

        try:
            while any(thread.is_alive() for thread in threads):
                time.sleep(1)
        except KeyboardInterrupt:
            self.stdout.write('Arrêt demandé : dernière tâche en cours terminée.')
            stop.set()
            for thread in threads:
                thread.join(timeout=60)
        finally:
            stop.set()
