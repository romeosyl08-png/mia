"""Code-specific tools and evidence from checks on the current workspace."""
import hashlib
import json
import re

from django.conf import settings

from chat.capabilities import normalize_request, request_requirements, request_steps
from .base import BaseHarness, successful


CODE_REQUEST = re.compile(
    r'\b(?:code|programmation|coding|debug|bug|django|flask|python|javascript|typescript|react|nodejs|pytest|tests?|compilation|lint)\b'
    r'|\b[\w./-]+\.(?:py|js|ts|tsx|jsx|html|css|rs|go|java|cpp|c)\b'
)
CHANGE_REQUEST = re.compile(
    r'^(?:ajoute|ajouter|cree|creer|corrige|corriger|modifie|modifier|implemente|implementer|developpe|'
    r'developper|refactorise|repare|add|create|fix|modify|edit|update|implement|build|refactor)\b'
)
DEVELOPMENT_COMMANDS = frozenset({'python', 'python3', 'pytest', 'git', 'npm', 'node'})


def workspace_digest(workspace):
    """Hash persisted bytes, including edits made outside this task."""
    digest = hashlib.sha256()
    for file in workspace.files.order_by('pk'):
        digest.update(json.dumps([file.pk, file.original_name], ensure_ascii=False).encode())
        content = hashlib.sha256()
        if file.file:
            with file.file.open('rb') as source:
                for chunk in source.chunks():
                    content.update(chunk)
        else:
            content.update((file.extracted_text or '').encode())
        digest.update(content.digest())
    return digest.hexdigest()


def check_command(command):
    """Recognize development checks, never an arbitrary successful command."""
    if not isinstance(command, list) or not command or any(not isinstance(arg, str) for arg in command):
        return False
    executable, *args = command
    if any(arg in {'--version', '--help', '-h', '--collect-only', '--co', '--dry-run', '--if-present'} for arg in args):
        return False
    if executable == 'pytest':
        return True
    if executable in {'python', 'python3'}:
        if args[:1] == ['-m'] and len(args) >= 2:
            return args[1] in {'pytest', 'unittest', 'compileall', 'py_compile'}
        if len(args) >= 2 and args[0].endswith('manage.py'):
            return args[1] in {'test', 'check'}
        return bool(args and re.fullmatch(r'(?:[\w.-]+/)*(?:test_[\w.-]+|[\w.-]+_test)\.py', args[0]))
    if executable == 'node':
        return args[:1] == ['--test']
    if executable == 'npm':
        return args[:1] == ['test'] or (len(args) >= 2 and args[0] == 'run' and args[1] in {
            'test', 'lint', 'check', 'typecheck', 'build'})
    return executable == 'git' and args == ['diff', '--check']


class CodeHarness(BaseHarness):
    id = 'code'
    name = 'Bara Code'
    tool_names = frozenset({
        'list_files', 'read_file', 'read_file_lines', 'compare_files', 'search_code',
        'write_file', 'edit_file', 'rename_file', 'delete_file', 'create_folder',
        'run_command', 'run_python', 'web_search', 'fetch_url', 'github_repositories', 'github_read_file',
    })

    def can_handle(self, objective):
        # Quoted documents and pasted source are data, not routing instructions.
        instruction = re.split(r'```|\n\s*(?:>|voici\b|code\s*:)', normalize_request(objective), maxsplit=1)[0]
        return bool(CODE_REQUEST.search(instruction[:4000]))

    def category(self, objective):
        return 'coding'

    def plan(self, task):
        return ['Explorer le projet et ses consignes', 'Modifier les fichiers nécessaires',
                'Exécuter les tests ou contrôles adaptés', 'Vérifier la version livrée et présenter les changements']

    def execute(self, task, name, arguments, mcp_map, dispatch):
        state = task.state['harness']
        command = arguments.get('command') if isinstance(arguments, dict) else None
        requested = state.get('verification_command')
        is_check = name == 'run_command' and (command == requested if requested else check_command(command))
        before = workspace_digest(task.workspace) if is_check else None
        result = super().execute(task, name, arguments, mcp_map, dispatch)
        if is_check:
            after = workspace_digest(task.workspace)
            state['execution'] = {
                'command': command, 'workspace_digest': after,
                'passed': successful(result) and result.get('isolated') is True and before == after,
            }
        return result

    def verify(self, task):
        report = super().verify(task)
        state = task.state['harness']
        changes_expected = any(CHANGE_REQUEST.search(clause) for clause, _ in request_steps(task.objective))
        if changes_expected:
            report['checks']['code_changes'] = bool(state.get('changed'))
            if not state.get('changed'):
                report['missing'].append('Modification de code enregistrée et vérifiée')
        needs_check = changes_expected or state.get('changed') or state.get('verification_command') or (
            'execute' in request_requirements(task.objective))
        if needs_check:
            execution = state.get('execution', {})
            passed = bool(execution.get('passed') and execution.get('workspace_digest') == workspace_digest(task.workspace))
            report['checks']['current_code_check'] = passed
            if not passed:
                report['missing'].append('Contrôle réussi en conteneur sur la version actuelle des fichiers')
                if not settings.BARA_CODE_EXECUTION_ENABLED or 'run_command' in (task.workspace.disabled_tools or []):
                    report['retryable'] = False
                    report['missing'].append('Activer le runtime isolé et l’outil run_command pour vérifier le code')
        report['status'] = 'passed' if all(report['checks'].values()) else 'incomplete'
        if state.get('execution'):
            report['command'] = state['execution']['command']
        return report

    def prompt(self, task):
        text = super().prompt(task)
        text += ('\nAprès une modification, utilise run_command pour exécuter un test, une compilation ou un contrôle '
                 'adapté au projet. Une commande arbitraire réussie ou une affirmation ne valide pas le code. '
                 'Toute modification ultérieure invalide ce contrôle. Le conteneur est sans réseau ; '
                 'les fichiers du projet sont en lecture seule et les dépendances doivent être présentes dans l’image. '
                 'Enregistre les modifications avec les outils de fichiers. Signale les limites des contrôles exécutés.')
        command = task.state['harness'].get('verification_command')
        if command:
            text += '\nCommande de contrôle choisie par l’utilisateur (liste exacte pour run_command) : ' + json.dumps(command)
        return text
