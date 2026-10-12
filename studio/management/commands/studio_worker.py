import time
from django.core.management.base import BaseCommand
from django.db import close_old_connections
from studio.jobs import claim_job, execute_job

class Command(BaseCommand):
    help = 'Traite les générations et montages persistants du studio.'
    def add_arguments(self,parser):
        parser.add_argument('--once',action='store_true')
        parser.add_argument('--job-id',type=int)
    def handle(self,*args,**options):
        try:
            while True:
                close_old_connections()
                job = claim_job(options['job_id'])
                if job:
                    execute_job(job)
                if options['job_id'] or (options['once'] and not job):
                    return
                if not job:
                    time.sleep(2)
        except KeyboardInterrupt:
            return
