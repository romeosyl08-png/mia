"""Document workflows with source provenance and independently reopened outputs."""
import hashlib
from pathlib import PurePosixPath
import re

from django.db import transaction

from chat.capabilities import filename_matches, normalize_request, request_steps
from bara.models import DocumentArtifact
from bara.tools.document_inspection import expected_content, inspect_file, read_bytes, specification_hash, validate_document_content
from .base import BaseHarness, REQUIREMENT_LABELS, successful


DOCUMENT_SIGNAL = re.compile(r'\b(?:pdf|docx|xlsx|pptx|word|excel|powerpoint|documents?|contrats?|rapports?|presentations?|classeurs?|factures?)\b')
CODE_OBJECT = re.compile(r'^(?:cree|creer|ecris|developpe|corrige|modifie|create|write|fix|build)\s+'
                         r'(?:(?:un|une|le|la|du|des|a|an|the)\s+)?(?:scripts?|codes?|applications?|programmes?|fonctions?|tests?'
                         r'|[\w./-]+\.(?:py|js|ts|tsx|jsx|rs|go|java|cpp|c))\b')
OUTPUT_ACTION = re.compile(r'^(?:cree|creer|genere|generer|redige|rediger|ecris|prepare|produis|exporte|convertis|convertir|'
                           r'transforme|corrige|modifie|revise|reecris|ajoute|mets a jour|create|generate|write|prepare|produce|'
                           r'export|convert|edit|update|correct|rewrite|add)\b')
SOURCE_ACTION = re.compile(r'^(?:lis|lire|ouvre|analyse|analyser|resume|resumer|corrige|modifie|revise|reecris|mets a jour|'
                           r'convertis|convertir|transforme|read|open|analyze|summarize|edit|update|correct|rewrite|convert)\b')
CONVERT_ACTION = re.compile(r'^(?:convertis|convertir|transforme|convert)\b')
FILE_REFERENCE = re.compile(r'[\w./-]+\.(?:pdf|docx|xlsx|pptx|txt|md|csv)\b')
FORMATS = {'pdf': r'\bpdf\b', 'docx': r'\b(?:docx|word)\b', 'xlsx': r'\b(?:xlsx|excel|classeurs?)\b',
           'pptx': r'\b(?:pptx|powerpoint|presentations?)\b'}


def instructions(objective):
    return [clause for clause, _ in document_steps(objective)]


def document_steps(objective):
    for clause, _ in request_steps(objective):
        leading, separator, _ = clause.partition(':')
        for part in re.split(r'\b(?:et|and)\s+(?=(?:redige|ecris|prepare|produis|exporte|convertis|corrige|'
                             r'modifie|lis|analyse|create|write|produce|export|convert|read|analyze)\b)', leading):
            yield part.strip(), bool(separator)


def requirements(objective):
    outputs, sources, read_required = [], set(), False
    for instruction, inline in document_steps(objective):
        source, destination = instruction, instruction
        from_source = False
        if CONVERT_ACTION.search(instruction):
            parts = re.split(r'\s+(?:en|vers|to|into)\s+', instruction, maxsplit=1)
            source, destination = parts[0], parts[-1] if len(parts) > 1 else ''
        elif OUTPUT_ACTION.search(instruction):
            parts = re.split(r'\s+(?:a partir de|depuis|from|based on)\s+', instruction, maxsplit=1)
            if len(parts) > 1:
                destination, source = parts
                from_source = True
        elif SOURCE_ACTION.search(instruction):
            parts = re.split(r'\s+(?:en|vers|to|into)\s+', instruction, maxsplit=1)
            if len(parts) > 1 and any(re.search(pattern, parts[1]) for pattern in FORMATS.values()):
                source, destination = parts
                from_source = True
        if SOURCE_ACTION.search(instruction) or from_source:
            refs = set(FILE_REFERENCE.findall(source))
            # Inline text is already input, and does not require a workspace file.
            if not inline or refs:
                read_required = True
                sources.update(refs)
        if OUTPUT_ACTION.search(instruction) or from_source:
            outputs.append({'formats': sorted(kind for kind, pattern in FORMATS.items() if re.search(pattern, destination)),
                            'alternatives': bool(re.search(r'\b(?:ou|or)\b', destination)),
                            'files': FILE_REFERENCE.findall(destination)})
    return outputs, sources, read_required


def fully_read(proof):
    end = 0
    for start, stop in sorted(proof.get('ranges', [])):
        if start > end:
            return False
        end = max(end, stop)
    return 0 < proof.get('total_chars', 0) <= end


class DocumentsHarness(BaseHarness):
    id = 'documents'
    name = 'Bara Docs'
    tool_names = frozenset({'list_files', 'read_document', 'read_file', 'read_file_lines', 'compare_files',
                            'create_document', 'rename_file', 'delete_file', 'create_folder',
                            'extract_document_text', 'web_search', 'fetch_url'})

    def can_handle(self, objective):
        for clause in instructions(objective):
            if CODE_OBJECT.search(clause):
                return False
            if DOCUMENT_SIGNAL.search(clause):
                return True
        return False

    def category(self, objective):
        return 'documents'

    def plan(self, task):
        return ['Lire les documents sources et leurs structures', 'Préparer le contenu et les corrections autorisées',
                'Créer ou réviser les documents', 'Relire les fichiers enregistrés et livrer avec les limites de vérification']

    def initialize(self, task):
        state = task.state.setdefault('harness', {'id': self.id})
        if 'plan' not in state:
            _, refs, read_required = requirements(task.objective)
            candidates = {str(file.pk): file.original_name for file in task.workspace.files.all()
                          if not file.original_name.endswith('/.keep')}
            selected = {}
            if read_required and refs:
                for ref in sorted(refs):
                    exact = {pk: name for pk, name in candidates.items() if normalize_request(name) == ref}
                    matches = exact or {pk: name for pk, name in candidates.items()
                                        if filename_matches(normalize_request(name), ref)}
                    if len(matches) != 1:
                        state['input_problem'] = 'Document source absent ou ambigu : ' + ref
                    selected.update(matches)
            elif read_required:
                selected = candidates
                plural = re.search(r'\b(?:les|ces|mes|des|the)\s+(?:documents|contrats|rapports|fichiers)\b', normalize_request(task.objective))
                if not candidates or (len(candidates) > 1 and not plural):
                    state['input_problem'] = 'Préciser le document source dans cet espace'
            state.update(inputs=selected, sources={}, documents={}, input_hashes={
                pk: hashlib.sha256(read_bytes(task.workspace.files.get(pk=pk))).hexdigest() for pk in selected})
        return super().initialize(task)

    def execute(self, task, name, arguments, mcp_map, dispatch):
        if name == 'create_document' and isinstance(arguments, dict):
            with transaction.atomic():
                return self._create_document(task, arguments, mcp_map, dispatch)
        return super().execute(task, name, arguments, mcp_map, dispatch)

    def _create_document(self, task, arguments, mcp_map, dispatch):
        try:
            validate_document_content(arguments.get('format'), arguments.get('specification'))
        except (ValueError, TypeError) as error:
            return {'error': str(error)}
        filename = arguments.get('filename')
        if isinstance(filename, str):
            try:
                filename = str(PurePosixPath(filename).with_suffix('.' + arguments['format']))
            except ValueError:
                return {'error': 'Nom de fichier invalide.'}
            state = task.state['harness']
            # Hold the destination lock through the shared writer's transaction.
            # An editor update must precede this check or follow our commit.
            file = task.workspace.files.select_for_update().filter(original_name=filename).first()
            artifact = DocumentArtifact.objects.select_for_update().filter(file=file).first() if file else None
            owned = next((item for item in state.get('documents', {}).values() if item['filename'] == filename), None)
            source_id = next((pk for pk, name in state.get('inputs', {}).items() if name == filename), None)
            baseline = owned['sha256'] if owned else state.get('input_hashes', {}).get(source_id)
            if baseline:
                try:
                    current = hashlib.sha256(read_bytes(file)).hexdigest() if file else None
                except (OSError, ValueError):
                    current = None
                if current != baseline or (owned and (file.pk != owned['file_id'] or not artifact or
                        specification_hash(artifact.specification) != owned['specification_hash'])):
                    result = {'error': 'Le document a changé en dehors de cette tâche. Examine-le avant une nouvelle demande.',
                              'verification_failed': True}
                    self.observe(task, 'create_document', arguments, result)
                    return result
            if source_id and not fully_read(state.get('sources', {}).get(source_id, {})):
                return {'error': 'Lis d’abord entièrement le document source avec read_document avant de le réviser.'}
        return super().execute(task, 'create_document', arguments, mcp_map, dispatch)

    def observe(self, task, name, arguments, result):
        super().observe(task, name, arguments, result)
        if not successful(result):
            return
        state = task.state['harness']
        file_id = result.get('file_id')
        if name == 'create_document' and result.get('verified') is True and isinstance(arguments, dict):
            spec = arguments.get('specification')
            file = task.workspace.files.filter(pk=file_id).first()
            if file and isinstance(spec, dict):
                state.setdefault('documents', {})[str(file_id)] = {
                    'file_id': file_id, 'filename': file.original_name, 'format': arguments.get('format'),
                    'sha256': hashlib.sha256(read_bytes(file)).hexdigest(),
                    'specification_hash': specification_hash(spec),
                }
        elif name == 'rename_file' and result.get('verified') is True:
            document = state.get('documents', {}).get(str(file_id))
            if document:
                document['filename'] = result['filename']
        elif name == 'read_document' and str(file_id) in state.get('inputs', {}) and result.get('content'):
            if result['sha256'] != state.get('input_hashes', {}).get(str(file_id)):
                return
            proofs = state.setdefault('sources', {})
            proof = proofs.get(str(file_id), {})
            if proof.get('sha256') != result['sha256']:
                proof = {'sha256': result['sha256'], 'ranges': [], 'total_chars': result['total_chars']}
            proof['ranges'].append([result['offset'], result['end']])
            # Merge intervals to keep checkpoints small across repeated reads.
            merged = []
            for start, end in sorted(proof['ranges']):
                if merged and start <= merged[-1][1]:
                    merged[-1][1] = max(merged[-1][1], end)
                else:
                    merged.append([start, end])
            proof['ranges'] = merged
            proofs[str(file_id)] = proof
        elif name == 'extract_document_text' and str(file_id) in state.get('inputs', {}) and result.get('text'):
            file = task.workspace.files.filter(pk=file_id).first()
            data = read_bytes(file)
            original_hash = state.get('input_hashes', {}).get(str(file_id))
            if hashlib.sha256(data).hexdigest() != original_hash or result.get('source_sha256') != original_hash:
                return
            complete = not result.get('may_be_truncated')
            if file.content_type == 'application/pdf':
                import io
                from pypdf import PdfReader
                complete = complete and len(PdfReader(io.BytesIO(data)).pages) <= result.get('max_pages', 0)
            size = len(result['text'])
            state.setdefault('sources', {})[str(file_id)] = {
                'sha256': hashlib.sha256(data).hexdigest(), 'ranges': [[0, size]] if complete else [],
                'total_chars': size, 'ocr': True,
            }

    def verify(self, task):
        report = super().verify(task)
        state = task.state['harness']
        output_requests, _, read_required = requirements(task.objective)
        # The shared detector can attach an input filename to a creation clause
        # ("create report.pdf from source.docx"). This contract verifies source
        # reads and output destinations separately, using actual document IDs.
        specialized = ({'file_read'} if read_required else set()) | (
            {'file_create', 'file_edit'} if output_requests else set())
        for key in specialized:
            report['checks'].pop(key, None)
            report['missing'] = [message for message in report['missing'] if message != REQUIREMENT_LABELS[key]]
        report['limitations'] = ['La mise en page et le rendu visuel restent à relire dans le fichier final.',
                                 'La conformité du contenu à la demande et l’exactitude métier demandent une relecture.',
                                 'Les images, annotations et objets intégrés ne sont pas analysés.']
        if state.get('input_problem'):
            report['checks']['document_sources'] = False
            report['missing'].append(state['input_problem'])
            report['retryable'] = False
        elif read_required:
            read = bool(state.get('inputs'))
            for pk in state.get('inputs', {}):
                proof = state.get('sources', {}).get(pk, {})
                read = read and fully_read(proof) and proof.get('sha256') == state.get('input_hashes', {}).get(pk)
                # Sources rewritten by this task are checked as outputs below.
                if pk not in state.get('documents', {}):
                    file = task.workspace.files.filter(pk=pk).first()
                    if file is None or hashlib.sha256(read_bytes(file)).hexdigest() != state.get('input_hashes', {}).get(pk):
                        read = False
                        report['retryable'] = False
                        report['missing'].append('Un document source a changé après sa lecture')
            report['checks']['document_sources'] = read
            if not read:
                report['missing'].append('Lecture complète du texte et des tableaux des sources avec read_document ou l’OCR non tronqué')
        documents = []
        for expected in state.get('documents', {}).values():
            item = {**expected, 'render_verified': False, 'checks': {}}
            item.pop('specification_hash')
            try:
                file = task.workspace.files.filter(pk=expected['file_id']).first()
                if file is None:
                    raise ValueError('Missing document')
                actual = inspect_file(file)
                unchanged = file.original_name == expected['filename'] and actual['sha256'] == expected['sha256']
                item['checks']['unchanged'] = unchanged
                if not unchanged:
                    report['retryable'] = False
                artifact = file.artifact
                item['checks']['format'] = artifact.kind == expected['format'] == actual['summary']['format']
                item['checks']['specification'] = specification_hash(artifact.specification) == expected['specification_hash']
                if not item['checks']['specification']:
                    report['retryable'] = False
                item['checks']['content'] = specification_hash(actual['content']) == specification_hash(
                    expected_content(expected['format'], artifact.specification))
                item['summary'] = actual['summary']
                item['download_url'] = f'/bara/{task.workspace_id}/files/{file.pk}/download/'
                item['preview_url'] = f'/bara/{task.workspace_id}/documents/{file.pk}/'
                if expected['format'] == 'xlsx':
                    report['limitations'].append('Les formules Excel sont conservées ; leurs résultats ne sont pas recalculés par 2AI.')
            except Exception:
                item['checks']['readable'] = False
                # Missing or changed bytes must not trigger an automatic overwrite.
                report['retryable'] = False
            valid = bool(item['checks']) and all(item['checks'].values())
            report['checks']['document_' + str(expected['file_id'])] = valid
            if not valid:
                report['missing'].append('Document à vérifier : ' + expected['filename'])
            documents.append(item)
        if output_requests:
            report['checks']['document_outputs'] = bool(documents)
            if not documents:
                report['missing'].append('Créer le livrable avec create_document')
            produced = {item['format'] for item in documents if all(item['checks'].values())}
            names = {normalize_request(item['filename']) for item in documents if all(item['checks'].values())}
            for index, request in enumerate(output_requests):
                formats = set(request['formats'])
                valid = (bool(formats & produced) if request['alternatives'] else formats <= produced)
                valid = valid and all(any(filename_matches(name, ref) for name in names) for ref in request['files'])
                report['checks'][f'document_request_{index}'] = valid
                if not valid:
                    report['missing'].append('Respecter les formats et noms de fichiers demandés')
        if any(re.search(r'\b(?:verifie|controle|valide|check|verify)\b.*\b(?:mise en page|rendu|layout|render)\b', clause)
               for clause in instructions(task.objective)):
            report['checks']['visual_review'] = False
            report['missing'].append('Relecture visuelle du rendu demandée : ouvrir les fichiers pour la réaliser')
            report['retryable'] = False
        report['documents'] = documents
        report['limitations'] = list(dict.fromkeys(report['limitations']))
        report['status'] = 'passed' if all(report['checks'].values()) else 'incomplete'
        return report

    def prompt(self, task):
        return super().prompt(task) + (
            '\nLis les documents sources avec read_document ; suis next_offset jusqu’à la fin. '
            'Cet outil relit le fichier réel et inclut les tableaux et formules ; read_file peut contenir un extrait ancien. '
            'Pour les scans, utilise uniquement l’OCR isolé disponible et signale les limites. '
            'Crée ou révise les livrables avec create_document, aux formats et noms demandés. '
            'Word et PDF prennent en charge titre, texte et tableaux ; Excel utilise tables ; PowerPoint utilise slides. '
            'Relis les données et conserve les formules. Ne prétends pas avoir recalculé les formules ou vérifié visuellement '
            'la mise en page. Indique les limites et utilise les liens de fichiers renvoyés par les outils.')
