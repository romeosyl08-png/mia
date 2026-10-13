"""Supervise Web + worker sur le même volume persistant.

Les deux partagent le disque (fichiers Bara, médias Studio) : Render ne peut
attacher un disque qu'à un seul service, ils doivent donc cohabiter. En
contrepartie, le superviseur redémarre un enfant qui tombe au lieu de
s'arrêter — sinon un plantage du worker emporte le serveur Web.
"""

import os
import signal
import subprocess
import sys
import time

from django.core.management.base import BaseCommand

RESTART_DELAY_SECONDS = 2
MAX_RESTART_DELAY_SECONDS = 60


class Command(BaseCommand):
    help = 'Lance Gunicorn et le worker avec arrêt commun et redémarrage.'

    def handle(self, *args, **options):
        children = []
        stopping = {'value': False}

        def stop(signum=None, frame=None):
            stopping['value'] = True
            for child in children:
                if child.poll() is None:
                    child.terminate()

        signal.signal(signal.SIGTERM, stop)
        signal.signal(signal.SIGINT, stop)

        def spawn(argv):
            return subprocess.Popen(argv)

        targets = [
            [sys.executable, 'manage.py', 'agent_worker'],
            [sys.executable, '-m', 'gunicorn', 'ai_2ai.wsgi:application',
             '--bind', '0.0.0.0:' + os.environ.get('PORT', '8000'),
             '--timeout', '120', '--graceful-timeout', '30',
             '--worker-class', 'gthread',
             # Le streaming SSE occupe un thread pendant toute la génération :
             # avec 4 threads, cinq conversations simultanées saturent le service.
             '--threads', os.environ.get('GUNICORN_THREADS', '16'),
             '--access-logfile', '-', '--error-logfile', '-'],
        ]
        children = [spawn(argv) for argv in targets]

        try:
            while not stopping['value']:
                time.sleep(1)
                for index, child in enumerate(children):
                    if child.poll() is None:
                        continue
                    if stopping['value']:
                        break
                    self.stderr.write(
                        f"Le service {targets[index][-1]} s'est arrêté (code {child.returncode}). Redémarrage."
                    )
                    delay = RESTART_DELAY_SECONDS
                    # Redémarrage avec temporisation croissante pour éviter un
                    # cycle effréné si la cause est persistante (base coupée…).
                    while not stopping['value']:
                        children[index] = spawn(targets[index])
                        if children[index].poll() is None or stopping['value']:
                            break
                        self.stderr.write(f"Échec immédiat, nouvelle tentative dans {delay}s.")
                        time.sleep(delay)
                        delay = min(delay * 2, MAX_RESTART_DELAY_SECONDS)
        finally:
            stop()
            for child in children:
                try:
                    child.wait(timeout=15)
                except subprocess.TimeoutExpired:
                    child.kill()
                    child.wait()
