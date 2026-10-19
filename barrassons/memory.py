"""Project-local links to shared 2AI memories; never caches their content."""
import hashlib
from urllib.parse import urlencode

from .project import load_session, save_session, preview
from .terminal import terminal


def _connection(config):
    return {'server': config.get('api_base'),
            'key_hash': hashlib.sha256(config.get('api_key', '').encode()).hexdigest()}


def load_link(root, config):
    link = load_session(root).get('memory_link')
    if not isinstance(link, dict) or link.get('connection') != _connection(config):
        return None
    selection = link.get('selection')
    if not isinstance(selection, dict) or type(selection.get('personal')) is not bool:
        return None
    project_id = selection.get('workspace_id')
    if project_id is not None and (type(project_id) is not int or not 0 < project_id < 2**63):
        return None
    return {'personal': selection['personal'], 'workspace_id': project_id}


def link_label(selection):
    if not selection:
        return 'Aucune mémoire liée'
    parts = ['personnelle'] if selection['personal'] else []
    if selection.get('workspace_id') is not None:
        parts.append('projet Bara ' + str(selection['workspace_id']))
    return ' + '.join(parts) or 'Aucune mémoire liée'


def configure_link(config, root, choice, request):
    args = choice.split()
    if not args:
        catalog = request(config, '/memory')
        rows = ['Lien de ce dossier : ' + link_label(load_link(root, config)), '',
                'Personnelle : ' + ('disponible' if catalog['personal']['available'] else 'lien désactivé')]
        rows.extend(f'{w["id"]}. {w["title"]} · ' + ('disponible' if w['available'] else 'lien désactivé')
                    for w in catalog['workspaces'])
        terminal.panel('Mémoires partagées', rows, wrap=True)
        terminal.line('/memory chat · /memory bara ID · /memory both ID · /memory off', 'muted')
        terminal.line('Active les liens dans 2AI → Mémoire. /remember texte ajoute un souvenir après confirmation.', 'muted')
        return
    mode = args[0]
    if mode not in {'off', 'chat', 'bara', 'both'} or len(args) != (2 if mode in {'bara', 'both'} else 1):
        raise ValueError('Utilise /memory chat, /memory bara ID, /memory both ID ou /memory off.')
    project_id = None
    if mode in {'bara', 'both'}:
        if not args[1].isascii() or not args[1].isdigit() or len(args[1]) > 19 or not 0 < int(args[1]) < 2**63:
            raise ValueError('Identifiant de projet Bara invalide.')
        project_id = int(args[1])
    if mode != 'off':
        catalog = request(config, '/memory')
        if mode in {'chat', 'both'} and not catalog['personal']['available']:
            raise ValueError('Active le lien Barrassons dans la page Ma mémoire de 2AI.')
        if project_id is not None and not any(w['id'] == project_id and w['available'] for w in catalog['workspaces']):
            raise ValueError('Projet inaccessible ou lien Barrassons désactivé dans sa mémoire.')
    state = load_session(root)
    if mode == 'off':
        state.pop('memory_link', None)
    else:
        state['memory_link'] = {'connection': _connection(config),
                                'selection': {'personal': mode in {'chat', 'both'}, 'workspace_id': project_id}}
    save_session(root, state)
    terminal.line('Mémoire : ' + link_label(load_link(root, config)), 'green')


def remember(config, root, text, request, confirm):
    selection = load_link(root, config)
    if not selection:
        raise ValueError('Choisis d’abord une mémoire avec /memory.')
    text = text.strip()
    if not text or len(text) > 8000:
        raise ValueError('Le souvenir doit contenir entre 1 et 8 000 caractères.')
    project_id = selection.get('workspace_id')
    target = {'target': 'workspace', 'workspace_id': project_id} if project_id is not None else {'target': 'personal'}
    current = request(config, '/memory?' + urlencode(target))
    if not current['can_write']:
        raise ValueError('Seul le propriétaire peut modifier la mémoire de ce projet.')
    before = current['content']
    after = before if text == before.strip() or before.rstrip().endswith('\n\n' + text) else (
        before.rstrip() + '\n\n' + text if before.strip() else text)
    if len(after) > 8000:
        raise ValueError('La mémoire finale dépasserait 8 000 caractères. Raccourcis-la dans 2AI.')
    terminal.diff(preview(current['name'], before, after))
    if not confirm('Ajouter ce souvenir à « ' + current['name'] + ' », partagée avec les assistants liés ?'):
        return
    request(config, '/memory', {**target, 'content': text, 'revision': current['revision']})
    terminal.line('Souvenir enregistré dans « ' + current['name'] + ' ».', 'green')
