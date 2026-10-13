"""Déplace les fichiers Bara déjà présents dans MEDIA_ROOT vers PRIVATE_MEDIA_ROOT.

A utiliser après la migration qui fait passer `WorkFile.file` et
`FileVersion.binary_file` sur le stockage privé : les fichiers déjà
enregistrés restent sinon lisibles publiquement via /media/.

    python manage.py migrate_files_to_private_storage
    python manage.py migrate_files_to_private_storage --dry-run
"""

from pathlib import Path

from django.conf import settings
from django.core.files.storage import FileSystemStorage
from django.core.management.base import BaseCommand
from django.db.models import Q

from bara.models import FileVersion, WorkFile


class Command(BaseCommand):
    help = "Déplace les pièces jointes Bara du stockage public vers le stockage privé."

    def add_arguments(self, parser):
        parser.add_argument("--dry-run", action="store_true", help="Affiche le travail sans rien déplacer.")

    def handle(self, *args, **options):
        dry_run = options["dry_run"]
        old_root = Path(settings.MEDIA_ROOT)
        new_root = Path(settings.PRIVATE_MEDIA_ROOT)
        legacy = FileSystemStorage(location=str(old_root))
        moved = missing = already = 0

        sources = (
            (WorkFile.objects.exclude(file=""), "file"),
            (FileVersion.objects.exclude(binary_file=""), "binary_file"),
        )

        for queryset, field_name in sources:
            for obj in queryset.iterator():
                field = getattr(obj, field_name)
                name = field.name
                if not name:
                    continue
                if field.storage.exists(name):
                    already += 1
                elif legacy.exists(name):
                    moved += 1
                    if not dry_run:
                        (new_root / name).parent.mkdir(parents=True, exist_ok=True)
                        with legacy.open(name, "rb") as handle:
                            field.storage.save(name, handle)
                        legacy.delete(name)
                else:
                    missing += 1
                    self.stderr.write(f"Introuvable : {name} ({obj._meta.label} #{obj.pk})")

        prefix = "[simulation] " if dry_run else ""
        self.stdout.write(
            f"{prefix}{moved} fichier(s) déplacé(s), {already} déjà en place, {missing} introuvable(s)."
        )
        if not dry_run and moved:
            self.stdout.write(
                "Vérifie puis supprime les répertoires devenus vides sous "
                f"{old_root / 'workspaces'} et {old_root / 'versions'}."
            )
