"""Local project state, reversible edits and Git context (stdlib only)."""
import difflib
import hashlib
import json
import os
import subprocess
import tempfile
import uuid
from pathlib import Path


def state_path(root):
    config = Path(os.environ.get('XDG_CONFIG_HOME', Path.home() / '.config'))
    digest = hashlib.sha256(str(root.resolve()).encode()).hexdigest()[:24]
    return config / 'barrassons' / 'sessions' / (digest + '.json')


def save_session(root, data):
    path = state_path(root)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd, tmp = tempfile.mkstemp(dir=path.parent)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False)
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def load_session(root):
    try:
        data = json.loads(state_path(root).read_text(encoding='utf-8'))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def git(root, *args):
    result = subprocess.run(['git', '-C', str(root), *args], capture_output=True, text=True, timeout=30)
    if result.returncode:
        raise ValueError(result.stderr.strip() or 'Commande Git échouée.')
    return result.stdout


def git_context(root):
    try:
        return 'Branche: ' + git(root, 'branch', '--show-current').strip() + '\nÉtat Git:\n' + git(root, 'status', '--short')[:8000]
    except (OSError, ValueError, subprocess.TimeoutExpired):
        return 'Projet sans dépôt Git disponible.'


def make_worktree(root):
    # Require a clean committed starting point; original uncommitted work stays intact.
    if git(root, 'status', '--porcelain').strip():
        raise ValueError('Le dépôt contient des changements non enregistrés. Enregistre-les avant de créer un worktree.')
    branch = 'barrassons/' + uuid.uuid4().hex[:12]
    path = state_path(root).parent.parent / 'worktrees' / branch.replace('/', '-')
    path.parent.mkdir(parents=True, exist_ok=True)
    git(root, 'worktree', 'add', '-b', branch, str(path), 'HEAD')
    return path.resolve()


def preview(path, before, after):
    return ''.join(difflib.unified_diff((before or '').splitlines(True), after.splitlines(True),
                                      fromfile=str(path), tofile=str(path)))


def record_edit(root, relative, before, after):
    data = load_session(root)
    edits = data.setdefault('edits', [])
    edits.append({'path':relative, 'before':before, 'after_hash':hashlib.sha256(after.encode()).hexdigest()})
    data['edits'] = edits[-100:]
    save_session(root, data)


def undo_last(root, confirm):
    data = load_session(root)
    edits = data.get('edits', [])
    if not edits:
        return {'error':'Aucune modification à annuler.'}
    edit = edits[-1]
    path = (root / edit['path']).resolve()
    if root.resolve() not in path.parents:
        return {'error':'Chemin interdit.'}
    current = path.read_text(encoding='utf-8')
    if hashlib.sha256(current.encode()).hexdigest() != edit['after_hash']:
        return {'error':'Le fichier a changé depuis la modification. Annulation refusée pour conserver ces changements.'}
    print(preview(edit['path'], current, edit['before'] or ''))
    if not confirm('Annuler cette modification ?'):
        return {'status':'refused'}
    if edit['before'] is None:
        path.unlink()
    else:
        path.write_text(edit['before'], encoding='utf-8')
    edits.pop()
    save_session(root, data)
    return {'status':'reverted','path':edit['path']}
