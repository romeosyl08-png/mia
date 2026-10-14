"""Provider-independent, local parsing of user-supplied memory exports."""
import hashlib
import json
from pathlib import Path
import re


MEMORY_MAX_CHARS = 8000
IMPORT_MAX_CHARS = 64000
IMPORT_MAX_BYTES = 256 * 1024
IMPORT_EXTENSIONS = {'.txt', '.md', '.markdown', '.json'}

MEMORY_EXPORT_PROMPT = """Je souhaite transférer ma mémoire vers 2AI.
Restitue les informations que tu as réellement mémorisées à mon sujet et le contexte auquel tu as accès :
- mes consignes sur le ton, la langue, le format et le style des réponses ;
- mes préférences et les corrections que je t'ai demandées ;
- mes projets, objectifs, outils et sujets récurrents ;
- les éléments de mon profil utiles pour m'accompagner.
N'invente rien et indique si certaines informations ou conversations ne te sont pas accessibles.
Ne restitue aucun mot de passe, clé API ou autre secret.
Produis uniquement une liste de souvenirs en texte ou Markdown, dans un seul bloc de code, avec au maximum 8 000 caractères. Préserve mes consignes exactes autant que possible."""


class MemoryImportError(ValueError):
    pass


def _validate_text(text):
    if len(text) > IMPORT_MAX_CHARS:
        raise MemoryImportError('Le texte dépasse 64 000 caractères. Exporte une mémoire plus courte.')
    if any((ord(char) < 32 and char not in '\n\r\t') or 0xD800 <= ord(char) <= 0xDFFF for char in text):
        raise MemoryImportError('Le contenu doit être un texte lisible, sans données binaires.')


def _json_memories(value, depth=0):
    if depth > 8:
        raise MemoryImportError('La structure JSON est trop imbriquée. Utilise un export texte ou Markdown.')
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, list):
        entries = [_json_memories(item, depth + 1) for item in value]
        return '\n'.join(entry for entry in entries if entry)
    if isinstance(value, dict):
        if {'messages', 'chat_messages', 'mapping', 'conversations'} & value.keys():
            raise MemoryImportError(
                'Ce JSON contient un historique de conversations. Utilise la consigne ci-dessous '
                'pour exporter les souvenirs à importer.'
            )
        # Common memory containers and individual entries; ignore their metadata.
        for key in ('memories', 'memory', 'souvenirs', 'content', 'text'):
            if key in value:
                return _json_memories(value[key], depth + 1)
        entries = []
        for key, item in value.items():
            if key in {'id', 'uuid', 'created_at', 'updated_at', 'timestamp', 'date', 'version', 'provider'}:
                continue
            text = _json_memories(item, depth + 1)
            if text:
                entries.append(f'{key} : {text}')
        return '\n'.join(entries)
    return ''


def parse_memory_import(text='', uploaded_file=None):
    """Read one bounded text/Markdown/JSON export, without contacting an LLM."""
    expects_json = False
    if uploaded_file is not None:
        if text.strip():
            raise MemoryImportError('Choisis un fichier ou colle du texte, un seul à la fois.')
        extension = Path(uploaded_file.name).suffix.lower()
        if extension not in IMPORT_EXTENSIONS:
            raise MemoryImportError('Choisis un fichier TXT, Markdown ou JSON de mémoire.')
        if uploaded_file.size > IMPORT_MAX_BYTES:
            raise MemoryImportError('Le fichier dépasse 256 Ko. Exporte une mémoire plus courte.')
        raw = uploaded_file.read(IMPORT_MAX_BYTES + 1)
        if len(raw) > IMPORT_MAX_BYTES:
            raise MemoryImportError('Le fichier dépasse 256 Ko. Exporte une mémoire plus courte.')
        try:
            text = raw.decode('utf-8-sig')
        except UnicodeDecodeError as error:
            raise MemoryImportError('Le fichier doit être enregistré en texte UTF-8.') from error
        expects_json = extension == '.json'

    _validate_text(text)
    text = text.lstrip('\ufeff').replace('\r\n', '\n').replace('\r', '\n').strip()
    fence = re.fullmatch(r'```([\w-]*)[^\S\n]*\n(.*?)\n```', text, re.DOTALL)
    if fence:
        expects_json = expects_json or fence[1].lower() == 'json'
        text = fence[2].strip()
    if not text:
        raise MemoryImportError('Colle une mémoire ou choisis un fichier non vide.')

    if expects_json or text.startswith(('{', '[', '"')):
        try:
            value = json.loads(text)
        except RecursionError as error:
            raise MemoryImportError('La structure JSON est trop imbriquée. Utilise du texte ou Markdown.') from error
        except ValueError as error:
            if expects_json or text.startswith('{'):
                raise MemoryImportError('Le JSON est invalide. Vérifie le fichier ou utilise du texte.') from error
            # A plain-text memory may start with a date such as [2026-10-14].
        else:
            text = _json_memories(value)
    _validate_text(text)
    if not text.strip():
        raise MemoryImportError('Aucun souvenir textuel trouvé dans cet export.')
    return text.strip()


def memory_revision(content):
    return hashlib.sha256(content.encode('utf-8')).hexdigest()


def combine_memory(current, imported, mode='append'):
    """Keep the existing memory unless the user explicitly selects replacement."""
    if mode not in {'append', 'replace'}:
        raise MemoryImportError('Choisis Ajouter ou Remplacer pour cet import.')
    _validate_text(imported)
    imported = imported.strip()
    if not imported:
        raise MemoryImportError('La mémoire à importer est vide.')
    if mode == 'append' and current.strip():
        # Re-submitting the same export should not append it twice.
        already_added = imported == current.strip() or current.rstrip().endswith('\n\n' + imported)
        result = current if already_added else current.rstrip() + '\n\n' + imported
    else:
        result = imported
    if len(result) > MEMORY_MAX_CHARS:
        raise MemoryImportError(
            f'La mémoire finale contient {len(result):,} caractères (maximum 8 000). '
            'Raccourcis le texte à importer ou choisis Remplacer.'
        )
    return result
