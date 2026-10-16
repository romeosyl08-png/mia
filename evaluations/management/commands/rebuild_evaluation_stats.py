from django.core.management.base import BaseCommand

from evaluations.analytics import rebuild


class Command(BaseCommand):
    help = 'Recalcule les cohortes d’observation et les statistiques du routeur.'

    def handle(self, *args, **options):
        rebuild()
        self.stdout.write(self.style.SUCCESS('Statistiques recalculées.'))
