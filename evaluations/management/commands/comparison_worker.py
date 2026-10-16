import time

from django.core.management.base import BaseCommand
from django.db import close_old_connections, connection

from evaluations import jobs


class Command(BaseCommand):
    help = 'Exécute les comparaisons réservées et rembourse les réservations abandonnées.'

    def add_arguments(self, parser):
        parser.add_argument('--once', action='store_true')

    def handle(self, *args, **options):
        while True:
            if not connection.in_atomic_block:
                close_old_connections()
            jobs.recover_stale()
            session = jobs.claim()
            if session:
                jobs.execute(session)
            if options['once']:
                return
            if not session:
                time.sleep(1)
