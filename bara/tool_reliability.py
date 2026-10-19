"""Technical evidence for tool actions, shared by chat and Bara."""
import io
import json
import re
import zipfile

from chat.capabilities import filename_matches, normalize_request, request_requirements, request_steps
from .models import WorkFile

FILE_ACTIONS = {'write_file', 'edit_file', 'rename_file', 'create_document'}
EVIDENCE = {
    'file_read': {'read_file', 'read_file_lines', 'read_document', 'extract_document_text', 'compare_files'},
    'file_create': {'write_file', 'create_document'},
    'file_edit': {'edit_file', 'write_file', 'create_document'},
    'file_rename': {'rename_file'},
    'file_delete': {'delete_file'},
    'execute': {'run_python', 'run_command'},
    'web': {'web_search'},
}


def verify_file_action(workspace, name, arguments, result, before=None):
    if name not in FILE_ACTIONS or not isinstance(result, dict) or result.get('error'):
        return result
    try:
        if name in {'edit_file', 'rename_file'}:
            file = WorkFile.objects.get(workspace=workspace, pk=arguments['file_id'])
        elif name == 'create_document':
            file = WorkFile.objects.get(workspace=workspace, pk=result['file_id'])
        else:
            file = WorkFile.objects.get(workspace=workspace, original_name=result['filename'])
        with file.file.open('rb') as source:
            data = source.read(10 * 1024 * 1024 + 1)
        valid = len(data) == file.size_bytes and len(data) <= 10 * 1024 * 1024
        if name == 'write_file':
            valid = valid and data == arguments['content'].encode('utf-8')
        elif name == 'edit_file':
            expected = before.replace(arguments['old_text'], arguments['new_text'],
                                      -1 if arguments.get('replace_all') else 1)
            valid = valid and data == expected.encode('utf-8')
        elif name == 'rename_file':
            valid = valid and file.original_name == result['to']
        elif name == 'create_document':
            if arguments['format'] == 'pdf':
                from pypdf import PdfReader
                valid = valid and bool(PdfReader(io.BytesIO(data)).pages)
            else:
                required = {'docx': 'word/document.xml', 'xlsx': 'xl/workbook.xml',
                            'pptx': 'ppt/presentation.xml'}[arguments['format']]
                with zipfile.ZipFile(io.BytesIO(data)) as archive:
                    valid = valid and archive.testzip() is None and required in archive.namelist()
        if not valid:
            raise ValueError('Invalid persisted file')
    except Exception:
        # The write may have happened. Never retry this uncertain effect blindly.
        return {'error': 'La vérification du fichier a échoué. Consulte le fichier et son historique avant de réessayer.',
                'verification_failed': True}
    return {**result, 'verified': True, 'file_id': file.pk, 'filename': file.original_name,
            'download_url': f'/bara/{workspace.pk}/files/{file.pk}/download/'}


class ToolRun:
    """Bound retries and require execution evidence for explicit action requests."""
    def __init__(self, content, prepared=()):
        self.required = request_requirements(content) & EVIDENCE.keys()
        self.completed = set(prepared)
        self.pending = False
        self.cached = {}
        self.targets = {}
        self.observed = {}
        for clause, kinds in request_steps(content):
            names = set(re.findall(r'[\w./-]+\.(?:pdf|docx|xlsx|pptx|txt|md|csv|py|js)\b', clause))
            for kind in kinds:
                self.targets.setdefault(kind, set()).update(names)

    @staticmethod
    def key(name, arguments):
        return name, json.dumps(arguments, sort_keys=True, ensure_ascii=False)

    def record(self, name, arguments, result):
        if not isinstance(result, dict):
            return
        pending = result.get('status') == 'pending_confirmation'
        failed = bool(result.get('error')) or result.get('status') == 'error' or result.get('exit_code', 0) not in (0, None)
        if name in {'read_file', 'read_file_lines', 'read_document', 'extract_document_text'}:
            failed = failed or not (result.get('content') or result.get('text'))
        if pending or failed or name in FILE_ACTIONS:
            self.cached[self.key(name, arguments)] = result
        if pending:
            self.pending = True
        if not pending and not failed:
            if name not in FILE_ACTIONS or result.get('verified'):
                filenames = {normalize_request(result[key]) for key in ('filename', 'name', 'from', 'to')
                             if isinstance(result.get(key), str)}
                for kind, tools in EVIDENCE.items():
                    if name not in tools:
                        continue
                    observed = self.observed.setdefault(kind, set())
                    targets = self.targets.get(kind, set())
                    observed.update(target for target in targets if any(filename_matches(filename, target) for filename in filenames))
                    if targets <= observed:
                        self.completed.add(kind)

    def finish(self, text):
        missing = self.required - self.completed
        if not missing:
            return text
        if self.pending:
            return 'Une action attend ta confirmation. La tâche n’est pas encore terminée.'
        return ('2AI n’a pas pu vérifier que toutes les opérations demandées ont été effectuées. '
                'Consulte les fichiers et le journal avant de réessayer. '
                'Tu peux choisir « Auto 2AI » ou un modèle compatible avec les outils pour reprendre la tâche.')

    def verification(self):
        return {'type': 'verification', 'status': 'incomplete' if self.required - self.completed else 'complete'}
