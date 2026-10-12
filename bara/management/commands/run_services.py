"""Supervise Web + worker on the same persistent volume."""
import os
import signal
import subprocess
import sys
import time
from django.core.management.base import BaseCommand, CommandError


class Command(BaseCommand):
    help = 'Lance Gunicorn et le worker avec arrêt commun et surveillance.'

    def handle(self, *args, **options):
        children = []
        def stop(signum=None, frame=None):
            for child in children:
                if child.poll() is None:
                    child.terminate()
        signal.signal(signal.SIGTERM, stop)
        signal.signal(signal.SIGINT, stop)
        try:
            children.append(subprocess.Popen([sys.executable, 'manage.py', 'agent_worker']))
            children.append(subprocess.Popen([sys.executable, '-m', 'gunicorn', 'ai_2ai.wsgi:application',
                '--bind', '0.0.0.0:' + os.environ.get('PORT', '8000'), '--timeout', '120',
                '--worker-class', 'gthread', '--threads', '4']))
            while all(c.poll() is None for c in children):
                time.sleep(1)
        finally:
            stop()
            for child in children:
                try:
                    child.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    child.kill()
                    child.wait()
        if any(c.returncode not in {0, -signal.SIGTERM} for c in children):
            raise CommandError('Un service s’est arrêté de façon inattendue.')
