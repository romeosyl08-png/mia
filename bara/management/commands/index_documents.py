from django.core.management.base import BaseCommand, CommandError
from django.conf import settings
from bara.models import Workspace
from bara.retrieval import index_file


class Command(BaseCommand):
    help = 'Indexe les documents ; --semantic nécessite le consentement du projet et un fournisseur configuré.'
    def add_arguments(self, parser):
        parser.add_argument('workspace_id', type=int)
        parser.add_argument('--semantic', action='store_true')
    def handle(self, *args, **options):
        workspace = Workspace.objects.get(pk=options['workspace_id'])
        if options['semantic'] and not workspace.semantic_search:
            raise CommandError('Active la recherche par le sens dans la mémoire du projet avant l’indexation.')
        if options['semantic'] and (not settings.EMBEDDING_MODEL or not settings.EMBEDDING_API_KEY):
            raise CommandError('Configure le modèle et la clé du fournisseur d’embeddings.')
        count,semantic_count = 0,0
        for file in workspace.files.exclude(extracted_text=''):
            index = index_file(file, semantic=options['semantic'])
            count += 1
            semantic_count += int(bool(index.get('model')) and any('vector' in row for row in index['chunks']))
        self.stdout.write(f'{count} documents indexés, dont {semantic_count} avec embeddings.')
        if options['semantic'] and semantic_count<count:
            self.stderr.write('Certains embeddings sont indisponibles ; la recherche par mots-clés reste active.')
