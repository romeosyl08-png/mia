"""Outils locaux de Barrassons : lecture, recherche, édition et commandes.

Garde-fous inspirés des assistants de code en terminal :
- aucun chemin ne sort du dossier de travail ;
- un fichier existant n'est modifié qu'après avoir été lu, et seulement s'il
  n'a pas changé depuis (sinon une modification extérieure serait écrasée) ;
- chaque écriture ou commande demande une autorisation, que l'utilisateur
  peut accorder pour toute la session.
"""
import fnmatch
import hashlib
import os
import re
import subprocess
from pathlib import Path

from .project import preview, record_edit
from .terminal import terminal


MAX_FILE_CHARS = 60_000
DEFAULT_READ_LINES = 2000
MAX_LIST = 300
MAX_MATCHES = 50
MAX_SEARCH_FILE_BYTES = 1_000_000
MAX_WRITE_CHARS = 200_000
DEFAULT_COMMAND_TIMEOUT = 120
MAX_COMMAND_TIMEOUT = 600
SKIP_DIRS = {".git", ".venv", "venv", "node_modules", "__pycache__", ".next", "dist", "build"}

TOOLS = [
    {"type": "function", "function": {
        "name": "list_files", "description": "List files in the current project (respects .gitignore).",
        "parameters": {"type": "object", "properties": {"path": {"type": "string"}}, "additionalProperties": False},
    }},
    {"type": "function", "function": {
        "name": "glob", "description": "Find files whose path matches a glob pattern such as **/*.py or src/**/test_*.js. "
                                       "Most recently modified files come first.",
        "parameters": {"type": "object", "properties": {
            "pattern": {"type": "string"}, "path": {"type": "string"},
        }, "required": ["pattern"], "additionalProperties": False},
    }},
    {"type": "function", "function": {
        "name": "read_file",
        "description": "Read a UTF-8 text file. Large files are returned in chunks: pass offset (first line, 1-based) "
                       "and limit (number of lines) to read the rest. Read a file before editing it.",
        "parameters": {"type": "object", "properties": {
            "path": {"type": "string"}, "offset": {"type": "integer"}, "limit": {"type": "integer"},
        }, "required": ["path"], "additionalProperties": False},
    }},
    {"type": "function", "function": {
        "name": "search_code",
        "description": "Search text in project files. glob filters files (for example **/*.py); "
                       "regex=true treats the query as a Python regular expression.",
        "parameters": {"type": "object", "properties": {
            "query": {"type": "string"}, "glob": {"type": "string"},
            "regex": {"type": "boolean"}, "case_sensitive": {"type": "boolean"},
        }, "required": ["query"], "additionalProperties": False},
    }},
    {"type": "function", "function": {
        "name": "write_file",
        "description": "Create a file or replace a whole file. An existing file must have been read first. "
                       "Prefer edit_file for partial changes. Asks the user for approval.",
        "parameters": {"type": "object", "properties": {
            "path": {"type": "string"}, "content": {"type": "string"},
        }, "required": ["path", "content"], "additionalProperties": False},
    }},
    {"type": "function", "function": {
        "name": "edit_file",
        "description": "Replace an exact text passage in a file that was read first. old_text must appear exactly once, "
                       "unless replace_all=true replaces every occurrence. Asks the user for approval.",
        "parameters": {"type": "object", "properties": {
            "path": {"type": "string"}, "old_text": {"type": "string"}, "new_text": {"type": "string"},
            "replace_all": {"type": "boolean"},
        }, "required": ["path", "old_text", "new_text"], "additionalProperties": False},
    }},
    {"type": "function", "function": {
        "name": "run_command",
        "description": "Run a shell command in the project directory (timeout in seconds, 120 by default, 600 at most). "
                       "Asks the user for approval.",
        "parameters": {"type": "object", "properties": {
            "command": {"type": "string"}, "timeout": {"type": "integer"},
        }, "required": ["command"], "additionalProperties": False},
    }},
]


class ToolSession:
    """État d'une session : autorisations accordées et fichiers lus."""

    def __init__(self):
        self.allow_edits = False
        self.allowed_commands = set()
        self.read_hashes = {}

    def allows(self, kind, key=None):
        return self.allow_edits if kind == 'edit' else key in self.allowed_commands

    def grant(self, kind, key=None):
        if kind == 'edit':
            self.allow_edits = True
        else:
            self.allowed_commands.add(key)

    def remember_content(self, path, content):
        self.read_hashes[str(path)] = _digest(content)

    def check_fresh(self, path, current):
        """Refuse d'écraser un fichier non lu ou modifié depuis sa lecture."""
        known = self.read_hashes.get(str(path))
        if known is None:
            return 'Lis le fichier avec read_file avant de le modifier.'
        if known != _digest(current):
            return 'Le fichier a changé depuis ta dernière lecture. Relis-le avant de le modifier.'
        return None


def _digest(text):
    return hashlib.sha256(text.encode('utf-8')).hexdigest()


def resolve(root, relative):
    candidate = (root / relative).resolve()
    if candidate != root and root not in candidate.parents:
        raise ValueError("Chemin hors du dossier de travail refusé.")
    return candidate


def _git_files(base):
    """Fichiers suivis ou non ignorés par Git, ou None hors dépôt Git."""
    try:
        result = subprocess.run(['git', '-C', str(base), 'ls-files', '-z', '--cached', '--others', '--exclude-standard'],
                                capture_output=True, timeout=30, check=False)
    except (OSError, subprocess.TimeoutExpired):
        return None
    if result.returncode:
        return None
    paths = (base / name for name in result.stdout.decode('utf-8', 'surrogateescape').split('\0') if name)
    return [path for path in paths if path.is_file() and not SKIP_DIRS.intersection(path.relative_to(base).parts)]


def project_files(base):
    """Fichiers du projet sous `base`, en respectant .gitignore quand c'est un dépôt Git."""
    files = _git_files(base)
    if files is not None:
        return files
    found = []
    for directory, child_dirs, filenames in os.walk(base):
        child_dirs[:] = sorted(name for name in child_dirs if name not in SKIP_DIRS)
        found.extend(Path(directory) / filename for filename in sorted(filenames))
    return found


def _is_binary(path):
    try:
        with path.open('rb') as handle:
            return b'\0' in handle.read(8192)
    except OSError:
        return True


def _matches(relative, pattern):
    path = Path(relative)
    # « **/ » peut aussi ne correspondre à aucun dossier : src/**/a.py trouve src/a.py.
    flat = pattern.replace('/**/', '/')
    return (path.match(pattern) or fnmatch.fnmatch(relative, pattern) or fnmatch.fnmatch(path.name, pattern)
            or fnmatch.fnmatch(relative, flat) or (pattern.startswith('**/') and fnmatch.fnmatch(relative, pattern[3:])))


def confirm(action, session=None, kind=None, key=None):
    """Demande l'autorisation ; « toujours » la mémorise pour la session."""
    if session is not None and kind and session.allows(kind, key):
        terminal.line('  ⎿ Autorisé pour la session', 'muted')
        return True
    terminal.line()
    terminal.panel('Autorisation', action.splitlines(), wrap=True)
    always = {'edit': 't = toujours pour les fichiers', 'command': 't = toujours pour cette commande'}.get(kind)
    label = 'Autoriser ? [o/N' + ('/t' if session is not None and always else '') + '] '
    if session is not None and always:
        terminal.line(always + ' (cette session)', 'muted')
    answer = terminal.prompt(label).strip().lower()
    if answer in {'t', 'toujours', 'a', 'always'} and session is not None and always:
        session.grant(kind, key)
        return True
    return answer in {'y', 'yes', 'o', 'oui'}


def read_file(root, args, session=None):
    path = resolve(root, args['path'])
    if not path.is_file():
        return {'error': 'Fichier introuvable.'}
    if _is_binary(path):
        return {'error': 'Fichier binaire : il ne peut pas être lu comme du texte.'}
    content = path.read_text(encoding='utf-8')
    lines = content.splitlines(keepends=True)
    try:
        offset = max(1, int(args.get('offset') or 1))
        limit = max(1, min(int(args.get('limit') or DEFAULT_READ_LINES), DEFAULT_READ_LINES))
    except (TypeError, ValueError):
        return {'error': 'offset et limit doivent être des nombres entiers.'}
    selected, size = [], 0
    for line in lines[offset - 1:offset - 1 + limit]:
        if selected and size + len(line) > MAX_FILE_CHARS:
            break
        selected.append(line[:MAX_FILE_CHARS])
        size += len(selected[-1])
    end = offset + len(selected) - 1
    if session is not None:
        session.remember_content(path, content)
    result = {'path': str(path.relative_to(root)), 'content': ''.join(selected), 'start_line': offset,
              'end_line': end, 'total_lines': len(lines), 'truncated': end < len(lines)}
    if end < len(lines):
        result['next_offset'] = end + 1
    return result


def list_files(root, args):
    base = resolve(root, args.get('path') or '.')
    if not base.is_dir():
        return {'error': 'Dossier introuvable.'}
    files = sorted(str(path.relative_to(root)) for path in project_files(base))
    return {'files': files[:MAX_LIST], 'truncated': len(files) > MAX_LIST}


def glob_files(root, args):
    pattern = args.get('pattern') or ''
    if not pattern:
        return {'error': 'Le motif est vide.'}
    base = resolve(root, args.get('path') or '.')
    if not base.is_dir():
        return {'error': 'Dossier introuvable.'}
    found = [path for path in project_files(base) if _matches(str(path.relative_to(base)), pattern)]
    found.sort(key=lambda path: path.stat().st_mtime, reverse=True)
    return {'files': [str(path.relative_to(root)) for path in found[:MAX_LIST]], 'truncated': len(found) > MAX_LIST}


def search_code(root, args):
    query = args['query']
    if not query:
        return {'error': 'La recherche est vide.'}
    pattern = args.get('glob') or '**/*'
    flags = 0 if args.get('case_sensitive') else re.IGNORECASE
    try:
        expression = re.compile(query if args.get('regex') else re.escape(query), flags)
    except re.error as error:
        return {'error': f'Expression régulière invalide : {error}'}
    matches = []
    for path in project_files(root):
        relative = str(path.relative_to(root))
        if not _matches(relative, pattern):
            continue
        try:
            if path.stat().st_size > MAX_SEARCH_FILE_BYTES or _is_binary(path):
                continue
            lines = path.read_text(encoding='utf-8').splitlines()
        except (UnicodeDecodeError, OSError):
            continue
        for number, line in enumerate(lines, 1):
            if expression.search(line):
                matches.append({'path': relative, 'line': number, 'text': line[:500]})
                if len(matches) >= MAX_MATCHES:
                    return {'matches': matches, 'truncated': True}
    return {'matches': matches, 'truncated': False}


def write_file(root, args, session=None):
    path = resolve(root, args['path'])
    content = args['content']
    if not isinstance(content, str):
        raise ValueError('Le contenu doit être du texte.')
    if len(content) > MAX_WRITE_CHARS:
        return {'error': 'Contenu trop volumineux (limite 200 ko).'}
    if path.is_dir():
        return {'error': 'Ce chemin est un dossier.'}
    before = path.read_text(encoding='utf-8') if path.exists() else None
    if before is not None and session is not None:
        problem = session.check_fresh(path, before)
        if problem:
            return {'error': problem}
    relative = path.relative_to(root)
    terminal.diff(preview(relative, before, content)[:20000])
    action = ('Remplacer' if before is not None else 'Créer') + f' {relative} ({len(content)} caractères)'
    if not confirm(action, session, 'edit'):
        return {'status': 'refused'}
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding='utf-8')
    record_edit(root, str(relative), before, content)
    if session is not None:
        session.remember_content(path, content)
    return {'status': 'written', 'path': str(relative)}


def edit_file(root, args, session=None):
    path = resolve(root, args['path'])
    old_text, new_text = args['old_text'], args['new_text']
    if not isinstance(old_text, str) or not isinstance(new_text, str):
        raise ValueError('old_text et new_text doivent être du texte.')
    if not old_text:
        return {'error': 'Le texte à remplacer ne peut pas être vide.'}
    if old_text == new_text:
        return {'error': 'Le nouveau texte est identique à l’ancien.'}
    if not path.is_file():
        return {'error': 'Fichier introuvable. Utilise write_file pour créer un fichier.'}
    content = path.read_text(encoding='utf-8')
    if session is not None:
        problem = session.check_fresh(path, content)
        if problem:
            return {'error': problem}
    count = content.count(old_text)
    if count == 0:
        return {'error': 'Passage introuvable. Relis le fichier et copie le texte exact, espaces compris.'}
    replace_all = args.get('replace_all') is True
    if count > 1 and not replace_all:
        return {'error': f'Le passage apparaît {count} fois ; ajoute du contexte pour qu’il soit unique, '
                         'ou utilise replace_all=true.'}
    after = content.replace(old_text, new_text) if replace_all else content.replace(old_text, new_text, 1)
    relative = path.relative_to(root)
    terminal.diff(preview(relative, content, after)[:20000])
    occurrences = f'{count} occurrences' if count > 1 else f'un passage de {len(old_text)} caractères'
    if not confirm(f'Modifier {relative} : remplacer {occurrences}', session, 'edit'):
        return {'status': 'refused'}
    path.write_text(after, encoding='utf-8')
    record_edit(root, str(relative), content, after)
    if session is not None:
        session.remember_content(path, after)
    return {'status': 'updated', 'path': str(relative), 'replacements': count if replace_all else 1}


def run_command(root, args, session=None, approved=False):
    command = args['command']
    if not isinstance(command, str) or not command.strip():
        return {'error': 'La commande est vide.'}
    try:
        timeout = max(1, min(int(args.get('timeout') or DEFAULT_COMMAND_TIMEOUT), MAX_COMMAND_TIMEOUT))
    except (TypeError, ValueError):
        return {'error': 'timeout doit être un nombre entier de secondes.'}
    if not approved and not confirm(f'Exécuter dans {root}:\n  {command}', session, 'command', command):
        return {'status': 'refused'}
    try:
        result = subprocess.run(
            command, cwd=root, shell=True, text=True, capture_output=True, timeout=timeout, check=False,
            env={**os.environ, 'PYTHONDONTWRITEBYTECODE': '1'},
        )
    except subprocess.TimeoutExpired as error:
        return {'error': f'Commande interrompue après {timeout} secondes.', 'timed_out': True,
                'stdout': _tail(error.stdout), 'stderr': _tail(error.stderr)}
    return {'exit_code': result.returncode, 'stdout': _tail(result.stdout), 'stderr': _tail(result.stderr)}


def _tail(value):
    if isinstance(value, bytes):
        value = value.decode('utf-8', 'replace')
    return (value or '')[-20_000:]


HANDLERS = {
    'list_files': lambda root, args, session: list_files(root, args),
    'glob': lambda root, args, session: glob_files(root, args),
    'read_file': read_file,
    'search_code': lambda root, args, session: search_code(root, args),
    'write_file': write_file,
    'edit_file': edit_file,
    'run_command': run_command,
}


def execute_tool(name, args, root, session=None):
    handler = HANDLERS.get(name)
    if handler is None:
        return {'error': f'Outil inconnu : {name}'}
    try:
        return handler(root, args, session)
    except KeyError as error:
        return {'error': f'Argument manquant : {error.args[0]}'}
    except (OSError, UnicodeError, ValueError, TypeError) as error:
        return {'error': str(error)}
