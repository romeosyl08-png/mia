from datetime import timedelta

from django.conf import settings
from django.core.management.base import BaseCommand
from django.utils import timezone

from evaluations.jobs import recover_stale
from evaluations.models import ComparisonSession
from evaluations.services import erase_content


class Command(BaseCommand):
    help = 'Supprime les comparaisons expirées ; préserve le registre comptable et les budgets.'

    def handle(self, *args, **options):
        recover_stale()
        threshold = timezone.now() - timedelta(days=max(1, settings.EVALUATION_RETENTION_DAYS))
        expired = list(ComparisonSession.objects.filter(created_at__lt=threshold, settled=True)
                       .exclude(prompt='').select_related('user'))
        for session in expired:
            erase_content(session.user, session.pk, erase_vote=True)
        self.stdout.write(f'{len(expired)} contenu(s) supprimé(s). Registre comptable et idempotence conservés.')
