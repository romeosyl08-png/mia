import argparse
import fnmatch
import getpass
import json
import os
import subprocess
import sys
import urllib.error
import urllib.request
from urllib.parse import urlsplit, urlunsplit
import webbrowser
from pathlib import Path
from .project import load_session, save_session, record_edit, preview, undo_last, git, git_context, make_worktree
from .terminal import terminal


DEFAULT_API_BASE = "https://mia-vkl1.onrender.com/v1"
MAX_TOOL_ITERATIONS = 8
MAX_FILE_CHARS = 60_000
SKIP_DIRS = {".git", ".venv", "venv", "node_modules", "__pycache__", ".next", "dist", "build"}

TOOLS = [
    {"type": "function", "function": {
        "name": "list_files", "description": "List files in the current project.",
        "parameters": {"type": "object", "properties": {"path": {"type": "string"}}, "additionalProperties": False},
    }},
    {"type": "function", "function": {
        "name": "read_file", "description": "Read a UTF-8 text file from the current project.",
        "parameters": {"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"], "additionalProperties": False},
    }},
    {"type": "function", "function": {
        "name": "search_code", "description": "Search text in project files. Supports a glob such as **/*.py.",
        "parameters": {"type": "object", "properties": {
            "query": {"type": "string"}, "glob": {"type": "string"},
        }, "required": ["query"], "additionalProperties": False},
    }},
    {"type": "function", "function": {
        "name": "write_file", "description": "Create or replace a project text file. Ask the user for approval first.",
        "parameters": {"type": "object", "properties": {
            "path": {"type": "string"}, "content": {"type": "string"},
        }, "required": ["path", "content"], "additionalProperties": False},
    }},
    {"type": "function", "function": {
        "name": "edit_file", "description": "Replace an exact text passage in a project file. Ask the user for approval first.",
        "parameters": {"type": "object", "properties": {
            "path": {"type": "string"}, "old_text": {"type": "string"}, "new_text": {"type": "string"},
        }, "required": ["path", "old_text", "new_text"], "additionalProperties": False},
    }},
    {"type": "function", "function": {
        "name": "run_command", "description": "Run a shell command in the current project. Always ask the user for approval first.",
        "parameters": {"type": "object", "properties": {"command": {"type": "string"}}, "required": ["command"], "additionalProperties": False},
    }},
]


def config_path():
    root = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
    return root / "barrassons" / "config.json"


def load_config():
    try:
        config = json.loads(config_path().read_text(encoding="utf-8"))
        return config if isinstance(config, dict) else {}
    except (OSError, ValueError):
        return {}


def save_config(config):
    path = config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(config, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    try:
        path.chmod(0o600)
    except OSError:
        pass


def normalize_api_base(value):
    message = "Adresse API invalide : saisis une URL HTTP(S), pas une clé API. Lance `barrassons login` pour la corriger."
    if not isinstance(value, str):
        raise ValueError(message)
    try:
        parsed = urlsplit(value.strip())
        if (parsed.scheme not in {"http", "https"} or not parsed.hostname
                or parsed.username is not None or parsed.password is not None
                or parsed.query or parsed.fragment or any(char.isspace() for char in value)
                or parsed.path.startswith("/sk-ai2ai-")):
            raise ValueError(message)
        parsed.port
    except ValueError:
        raise ValueError(message) from None
    path = parsed.path.rstrip("/") or "/v1"
    return urlunsplit((parsed.scheme, parsed.netloc, path, "", ""))


def resolve_api_base(config, override=None):
    explicit = override if override is not None else os.environ.get("BARRASSONS_API_BASE")
    if explicit is not None:
        return normalize_api_base(explicit)
    for candidate in [config.get("api_base"), DEFAULT_API_BASE]:
        try:
            return normalize_api_base(candidate)
        except ValueError:
            continue
    raise ValueError("Adresse API invalide. Utilise `barrassons login --api-base URL`.")


def connect(config, api_base=None):
    api_base = resolve_api_base(config, api_base)
    terminal.line()
    terminal.panel('✦ Barrassons · Connexion', [f'Serveur : {api_base}', '',
        '1. Ouvrir la page des clés API', '2. Utiliser une clé existante'])
    choice = terminal.prompt('Choix [1/2] : ').strip() or "1"
    if choice not in {"1", "2"}:
        raise ValueError("Choisis 1 ou 2 pour te connecter.")
    if choice == "1":
        webbrowser.open(api_base.removesuffix("/v1") + "/api-keys/")
        terminal.line("Crée une clé sur la page ouverte, puis saisis-la ci-dessous.", 'muted')
    api_key = getpass.getpass("Clé API AI2AI (saisie masquée) : ").strip()
    if not api_key:
        raise ValueError("Aucune clé API fournie.")
    connected = {**config, "api_base": api_base, "api_key": api_key}
    with terminal.busy('Vérification de la connexion…'):
        api_request(connected, '/models')
    save_config(connected)
    terminal.line('✓ Connexion enregistrée. Lance `barrassons` pour commencer.', 'green')
    return connected


def api_request(config, path, payload=None):
    url = normalize_api_base(config.get("api_base")) + path
    headers = {"Authorization": "Bearer " + config["api_key"], "Accept": "application/json"}
    data = None
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"
    request = urllib.request.Request(url, data=data, headers=headers, method="POST" if data else "GET")
    try:
        with urllib.request.urlopen(request, timeout=120) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as error:
        try:
            detail = json.loads(error.read().decode("utf-8"))
            message = detail.get("error", {}).get("message", str(error))
        except (ValueError, AttributeError):
            message = str(error)
        if error.code == 401:
            raise RuntimeError("Clé refusée. Lance `barrassons login` pour te reconnecter.") from error
        raise RuntimeError(message) from error
    except (urllib.error.URLError, TimeoutError) as error:
        raise RuntimeError(f"Connexion impossible à {url} : {error}") from error


def _resolve(root, relative):
    candidate = (root / relative).resolve()
    if candidate != root and root not in candidate.parents:
        raise ValueError("Chemin hors du dossier de travail refusé.")
    return candidate


def _files(root):
    for directory, child_dirs, filenames in os.walk(root):
        child_dirs[:] = [name for name in child_dirs if name not in SKIP_DIRS]
        for filename in filenames:
            yield Path(directory) / filename


def _confirm(action):
    terminal.line()
    terminal.panel('Autorisation', action.splitlines(), wrap=True)
    return terminal.prompt('Autoriser ? [o/N] ').strip().lower() in {"y", "yes", "o", "oui"}


def execute_tool(name, args, root):
    try:
        if name == "list_files":
            base = _resolve(root, args.get("path", "."))
            if not base.is_dir():
                return {"error": "Dossier introuvable."}
            files = [str(path.relative_to(root)) for path in _files(base)][:300]
            return {"files": files, "truncated": len(files) == 300}

        if name == "read_file":
            path = _resolve(root, args["path"])
            if not path.is_file():
                return {"error": "Fichier introuvable."}
            content = path.read_text(encoding="utf-8")
            return {"path": str(path.relative_to(root)), "content": content[:MAX_FILE_CHARS], "truncated": len(content) > MAX_FILE_CHARS}

        if name == "search_code":
            query = args["query"]
            pattern = args.get("glob", "**/*")
            matches = []
            if not query:
                return {"error": "La recherche est vide."}
            for path in _files(root):
                relative = str(path.relative_to(root))
                if not (path.match(pattern) or fnmatch.fnmatch(path.name, pattern)):
                    continue
                try:
                    lines = path.read_text(encoding="utf-8").splitlines()
                except (UnicodeDecodeError, OSError):
                    continue
                for number, line in enumerate(lines, 1):
                    if query.casefold() in line.casefold():
                        matches.append({"path": relative, "line": number, "text": line[:500]})
                        if len(matches) >= 50:
                            return {"matches": matches, "truncated": True}
            return {"matches": matches, "truncated": False}

        if name == "write_file":
            path = _resolve(root, args["path"])
            content = args["content"]
            if len(content) > 200_000:
                return {"error": "Contenu trop volumineux (limite 200 ko)."}
            before = path.read_text(encoding='utf-8') if path.exists() else None
            terminal.diff(preview(path.relative_to(root), before, content)[:20000])
            if not _confirm(f"Créer/remplacer {path.relative_to(root)} ({len(content)} caractères)"):
                return {"status": "refused"}
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8")
            record_edit(root, str(path.relative_to(root)), before, content)
            return {"status": "written", "path": str(path.relative_to(root))}

        if name == "edit_file":
            path = _resolve(root, args["path"])
            old_text, new_text = args["old_text"], args["new_text"]
            if not old_text:
                return {"error": "Le texte à remplacer ne peut pas être vide."}
            content = path.read_text(encoding="utf-8")
            count = content.count(old_text)
            if count != 1:
                return {"error": f"Le passage apparaît {count} fois; il doit apparaître exactement une fois."}
            after = content.replace(old_text, new_text, 1)
            terminal.diff(preview(path.relative_to(root), content, after)[:20000])
            if not _confirm(f"Modifier {path.relative_to(root)} : remplacer un passage de {len(old_text)} caractères"):
                return {"status": "refused"}
            path.write_text(after, encoding="utf-8")
            record_edit(root, str(path.relative_to(root)), content, after)
            return {"status": "updated", "path": str(path.relative_to(root))}

        if name == "run_command":
            command = args["command"]
            if not _confirm(f"Exécuter dans {root}:\n  {command}"):
                return {"status": "refused"}
            result = subprocess.run(
                command, cwd=root, shell=True, text=True, capture_output=True, timeout=120, check=False,
                env={**os.environ,'PYTHONDONTWRITEBYTECODE':'1'},
            )
            return {
                "exit_code": result.returncode,
                "stdout": result.stdout[-20_000:],
                "stderr": result.stderr[-20_000:],
            }
        return {"error": f"Outil inconnu: {name}"}
    except (OSError, UnicodeError, ValueError, KeyError, subprocess.TimeoutExpired) as error:
        return {"error": str(error)}


def _select_model(config, choose=False, requested=None):
    with terminal.busy('Chargement des modèles…'):
        response = api_request(config, "/models")
    models = [model for model in response.get("data", []) if "anthropic" not in model.get("owned_by", "").lower()]
    if not models:
        raise RuntimeError("Aucun modèle compatible avec les outils n'est disponible sur ce compte.")
    available = {row['id'] for row in models}
    if requested is not None:
        if requested not in available:
            raise ValueError('Ce modèle n’est pas disponible sur ce serveur.')
        return requested
    if not choose and config.get('model') in available:
        return config['model']
    default = config.get('model') if config.get('model') in available else models[0]['id']
    choices = list(enumerate(models, 1)); page = 0; per_page = 10
    while True:
        pages = max(1, (len(choices) + per_page - 1) // per_page)
        rows = [f'{number:>3}. {model["id"]}' for number, model in choices[page * per_page:(page + 1) * per_page]]
        terminal.panel('Choisir une IA', rows + ['', f'Page {page + 1}/{pages} · {len(choices)} modèle(s)',
            'Nom ou numéro · /suivant · /précédent · /tous', f'Entrée : {default}'])
        selected = terminal.prompt('Modèle ❯ ').strip()
        if not selected:
            return default
        if selected == '/suivant':
            page = min(pages - 1, page + 1); continue
        if selected == '/précédent':
            page = max(0, page - 1); continue
        if selected == '/tous':
            choices = list(enumerate(models, 1)); page = 0; continue
        if selected.isdigit() and 1 <= int(selected) <= len(models):
            return models[int(selected) - 1]['id']
        if selected in available:
            return selected
        matches = [(number, row) for number, row in enumerate(models, 1) if selected.casefold() in row['id'].casefold()]
        if matches:
            choices = matches; page = 0
        else:
            terminal.line('Aucun modèle ne correspond. Essaie un nom ou un numéro.', 'red')


def run_session(config, root, model, resume=False, test_command=None, balance=None):
    system = (
        "Tu es Barrassons, un agent de développement qui travaille dans le dossier local indiqué. "
        "Inspecte le code avant de proposer des changements. Utilise les outils pour lire, rechercher et modifier les fichiers. "
        "Ne prétends jamais avoir exécuté une action si l'outil ne le confirme pas. "
        "Les écritures et commandes sont soumises à confirmation par l'utilisateur. "
        "Après des modifications, exécute les tests appropriés, observe les résultats et corrige les erreurs. "
        "Lis AGENTS.md et les instructions du projet lorsqu’ils existent. Réponds en français sauf demande contraire."
    )
    messages = [{"role": "system", "content": system + f"\nDossier courant: {root}"}]
    saved = load_session(root)
    if resume and isinstance(saved.get('messages'), list):
        messages = saved['messages']
        # Resolve unfinished calls without executing them again.
        answered = {m.get('tool_call_id') for m in messages if m.get('role') == 'tool'}
        for m in list(messages):
            for call in m.get('tool_calls', []):
                if call.get('id') not in answered:
                    messages.append({'role':'tool','tool_call_id':call['id'],'content':'Exécution précédente interrompue. Résultat incertain ; ne pas répéter automatiquement.'})
    def persist():
        state = load_session(root)
        state.update(messages=messages, model=model, root=str(root))
        save_session(root, state)
    try:
        branch = git(root, 'branch', '--show-current').strip()
    except (OSError, ValueError, subprocess.TimeoutExpired):
        branch = ''
    terminal.setup_input()
    terminal.welcome(root, model, branch, resolve_api_base(config))
    tokens = credits = 0
    verbose = False
    while True:
        try:
            terminal.status(model, tokens, credits, balance)
            text = terminal.request()
        except (EOFError, KeyboardInterrupt):
            terminal.line("\nÀ bientôt.", 'muted')
            return
        if not text:
            continue
        if text in {"/exit", "/quit"}:
            return
        if text == "/clear":
            messages = messages[:1]
            persist()
            terminal.line("Contexte effacé.", 'muted')
            continue
        if text == "/help":
            terminal.panel('Commandes', ['/model    Choisir une IA', '/status   Voir le serveur et les crédits',
                '/verbose  Afficher les résultats détaillés des outils', '/diff     Voir les changements Git',
                '/undo     Annuler la dernière édition', '/verify   Lancer les tests configurés',
                '/review   Demander une revue du diff', '/clear    Effacer le contexte', '/exit     Quitter', '',
                'Tab : compléter · ↑/↓ : historique · \\ puis Entrée : nouvelle ligne'])
            continue
        if text == '/verbose':
            verbose = not verbose
            terminal.line('Affichage détaillé activé.' if verbose else 'Affichage compact activé.', 'muted')
            continue
        if text == '/model':
            model = _select_model({**config, 'model': model}, choose=True)
            config['model'] = model
            save_config(config); persist()
            terminal.line(f'Modèle sélectionné : {model}', 'green')
            continue
        if text == '/status':
            try:
                with terminal.busy('Actualisation des crédits…'):
                    balance = api_request(config, '/balance').get('credits')
                terminal.panel('Session', [f'Modèle : {model}', f'Projet : {root}',
                    f'Branche : {branch or "sans dépôt"}', f'Serveur : {resolve_api_base(config)}'])
            except RuntimeError as error:
                terminal.line(str(error), 'red')
            continue
        if text == '/diff':
            try:
                terminal.diff(git(root, 'diff', 'HEAD') or 'Aucun diff suivi par Git. Les nouveaux fichiers sont visibles dans git status.')
            except ValueError as error:
                terminal.line(str(error), 'red')
            continue
        if text == '/undo':
            terminal.result(undo_last(root, _confirm), verbose)
            continue
        if text == '/verify':
            if not test_command:
                terminal.line('Configure --test-command, par exemple "python -m pytest".', 'muted')
            else:
                result = execute_tool('run_command', {'command':test_command}, root)
                terminal.result(result, verbose)
                messages.append({'role':'user','content':'Résultat réel de la vérification : ' + json.dumps(result, ensure_ascii=False)})
                persist()
            continue
        if text == '/review':
            try:
                text = 'Revois ce diff : cherche les bugs et régressions, puis indique les vérifications nécessaires.\n' + git(root,'diff','HEAD')[:60000]
            except ValueError as error:
                terminal.line(str(error), 'red')
                continue
        messages[0] = {'role':'system','content':system + f'\nDossier: {root}\n' + git_context(root) + ('\nCommande de tests configurée : '+test_command if test_command else '')}
        messages.append({"role": "user", "content": text})
        persist()
        changed_files = False
        verification_attempts = 0
        for _ in range(MAX_TOOL_ITERATIONS):
            with terminal.busy('Réflexion…'):
                response = api_request(config, "/chat/completions", {
                    "model": model,
                    "messages": messages,
                    "tools": TOOLS,
                    "max_tokens": 2048,
                    "temperature": 0.2,
                })
            usage = response.get('usage') or {}
            tokens += int(usage.get('total_tokens') or 0)
            credits += int(usage.get('credits_charged') or 0)
            if usage.get('remaining_balance') is not None:
                balance = usage['remaining_balance']
            choice = response["choices"][0]
            assistant = choice["message"]
            messages.append(assistant)
            persist()
            tool_calls = assistant.get("tool_calls") or []
            if not tool_calls:
                if changed_files and test_command and verification_attempts < 2:
                    verification_attempts += 1
                    verification = execute_tool('run_command',{'command':test_command},root)
                    terminal.result(verification, verbose)
                    messages.append({'role':'user','content':'Résultat réel des tests configurés : '+json.dumps(verification,ensure_ascii=False)+'. Corrige les erreurs si possible ; indique clairement une vérification refusée ou échouée.'})
                    persist()
                    if verification.get('exit_code') == 0:
                        changed_files = False
                    elif verification.get('status') != 'refused':
                        continue
                answer = assistant.get("content") or "(réponse vide)"
                terminal.assistant(answer)
                if changed_files and test_command:
                    terminal.line('La vérification configurée reste non validée. Consulte les résultats ci-dessus.', 'red')
                break
            for call in tool_calls:
                function = call.get("function") or {}
                name = function.get("name", "")
                try:
                    args = json.loads(function.get("arguments") or "{}")
                    if not isinstance(args, dict):
                        raise ValueError('Un objet JSON est attendu.')
                    terminal.tool(name, args)
                    result = execute_tool(name, args, root)
                    if result.get('status') in {'written','updated'}:
                        changed_files = True
                    if name == 'run_command' and args.get('command') == test_command and result.get('exit_code') == 0:
                        changed_files = False
                except (TypeError, ValueError) as error:
                    result = {"error": f"Arguments invalides: {error}"}
                terminal.result(result, verbose)
                messages.append({
                    "role": "tool", "tool_call_id": call.get("id", ""),
                    "name": name, "content": json.dumps(result, ensure_ascii=False),
                })
                persist()
        else:
            terminal.line("Limite d'itérations d'outils atteinte pour cette demande.", 'muted')
        if len(messages) > 41:
            tail = messages[-40:]
            while tail and tail[0].get("role") != "user":
                tail.pop(0)
            if tail:
                messages = messages[:1] + tail


def main():
    parser = argparse.ArgumentParser(prog="barrassons", description="Assistant de développement AI2AI dans le terminal")
    parser.add_argument("command", nargs="?", choices=["login", "logout"], help="Gérer la connexion AI2AI")
    parser.add_argument('--resume', action='store_true', help='Reprendre la session du projet')
    parser.add_argument('--worktree', action='store_true', help='Créer une branche isolée dans un worktree Git')
    parser.add_argument('--test-command', help='Commande de vérification à utiliser avec /verify')
    parser.add_argument('--api-base', metavar='URL', help='Changer explicitement le serveur AI2AI (Render par défaut)')
    parser.add_argument('--model', metavar='ID', help='Utiliser un modèle du catalogue sans ouvrir le sélecteur')
    parser.add_argument('--no-color', action='store_true', help='Désactiver les couleurs du terminal')
    args = parser.parse_args()
    config = load_config()
    if args.no_color:
        os.environ['NO_COLOR'] = '1'
    try:
        if args.command == "logout":
            config_path().unlink(missing_ok=True)
            terminal.line("Clé locale supprimée.", 'muted')
            return 0
        config['api_base'] = resolve_api_base(config, args.api_base)
        if args.command == "login" or not config.get("api_key"):
            config = connect(config, config['api_base'])
            if args.command == 'login':
                return 0
        root = Path.cwd().resolve()
        if args.worktree:
            if not _confirm('Créer une branche et un worktree Git pour cette session ?'):
                return 0
            root = make_worktree(root)
        model = _select_model(config, requested=args.model)
        config['model'] = model
        save_config(config)
        balance = None
        try:
            with terminal.busy('Chargement des crédits…'):
                balance = api_request(config, '/balance').get('credits')
        except RuntimeError:
            pass
        run_session(config, root, model, resume=args.resume, test_command=args.test_command, balance=balance)
        return 0
    except (KeyboardInterrupt, EOFError):
        terminal.line("\nOpération interrompue.", 'muted')
        return 1
    except (RuntimeError, ValueError, KeyError) as error:
        print(f"Erreur: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
