import argparse
import getpass
import json
import os
import re
import subprocess
import sys
import urllib.error
import urllib.request
from urllib.parse import urlsplit, urlunsplit
import webbrowser
from pathlib import Path
from .project import load_session, save_session, undo_last, git, git_context, make_worktree
from .terminal import terminal
from .memory import configure_link, link_label, load_link, remember
from .themes import apply_theme, configure_theme
from .tools import MAX_FILE_CHARS, SKIP_DIRS, TOOLS, ToolSession, confirm, execute_tool, read_file, resolve as _resolve
from .tools import run_command


DEFAULT_API_BASE = "https://mia-vkl1.onrender.com/v1"
MAX_TOOL_ITERATIONS = 25
MAX_HISTORY_MESSAGES = 41
INSTRUCTION_FILES = ('BARRASSONS.md', 'AGENTS.md', 'CLAUDE.md')
MAX_INSTRUCTION_CHARS = 20_000
MENTION = re.compile(r'(?<![\w`])@([\w./-]+[\w/])')

__all__ = ['MAX_FILE_CHARS', 'SKIP_DIRS', 'TOOLS', 'execute_tool', '_resolve']


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
    terminal.line('✦ Bienvenue dans Barrassons', 'accent')
    terminal.line(f'Serveur : {api_base}', 'muted')
    terminal.line('Colle ta clé API, ou appuie sur Entrée pour en créer une.')
    api_key = getpass.getpass("Clé API AI2AI (saisie masquée) : ").strip()
    if not api_key:
        keys_url = api_base.removesuffix('/v1') + '/api-keys/'
        terminal.line(keys_url, 'muted')
        webbrowser.open(keys_url)
        terminal.line("Crée une clé sur la page ouverte, puis saisis-la ci-dessous.", 'muted')
        api_key = getpass.getpass("Clé API AI2AI (saisie masquée) : ").strip()
    if not api_key:
        raise ValueError("Aucune clé API fournie.")
    connected = {**config, "api_base": api_base, "api_key": api_key}
    with terminal.busy('Vérification de la connexion…'):
        api_request(connected, '/models')
    save_config(connected)
    terminal.line('✓ Connexion enregistrée.', 'green')
    return connected


def api_request(config, path, payload=None, *, timeout=120):
    url = normalize_api_base(config.get("api_base")) + path
    headers = {"Authorization": "Bearer " + config["api_key"], "Accept": "application/json"}
    data = None
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"
    request = urllib.request.Request(url, data=data, headers=headers, method="POST" if data else "GET")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
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


def _confirm(action):
    return confirm(action)


def _select_model(config, choose=False, requested=None):
    with terminal.busy('Chargement des modèles…'):
        response = api_request(config, "/models")
    models = [model for model in response.get("data", [])
              if "anthropic" not in model.get("owned_by", "").lower() and model.get('supports_tools') is not False]
    if not models:
        raise RuntimeError("Aucun modèle compatible avec les outils n'est disponible sur ce compte.")
    available = {row['id'] for row in models}
    if requested is not None:
        if requested not in available:
            raise ValueError('Ce modèle n’est pas disponible sur ce serveur.')
        return requested
    if not choose and config.get('model') in available:
        return config['model']
    default = next((value for value in (config.get('model'), response.get('default_model'), 'auto')
                    if value in available), models[0]['id'])
    if not choose:
        return default
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


SYSTEM_PROMPT = (
    "Tu es Barrassons, un agent de développement qui travaille dans le dossier local indiqué. "
    "Inspecte le code avant de proposer des changements : utilise glob et search_code pour trouver les fichiers, "
    "read_file pour les lire (par morceaux avec offset et limit pour les gros fichiers). "
    "Lis toujours un fichier avant de le modifier ; préfère edit_file à write_file pour une modification partielle. "
    "Ne prétends jamais avoir exécuté une action si l'outil ne le confirme pas. "
    "Les écritures et commandes sont soumises à confirmation par l'utilisateur ; une action refusée ne doit pas être "
    "relancée telle quelle. Après des modifications, exécute les tests appropriés, observe les résultats et corrige "
    "les erreurs. Respecte les instructions du projet ci-dessous lorsqu'elles existent. "
    "Réponds en français sauf demande contraire, de façon concise."
)

INIT_REQUEST = (
    "Analyse ce projet (structure, langages, commandes de build, de test et de lint, conventions de code, "
    "points d'attention) puis crée ou mets à jour le fichier BARRASSONS.md à la racine. Il servira d'instructions "
    "aux prochaines sessions : reste concis (moins de 150 lignes), factuel et vérifié dans le code."
)

COMPACT_REQUEST = (
    "Résume la conversation ci-dessus pour pouvoir la poursuivre sans l'historique : objectif de l'utilisateur, "
    "décisions prises, fichiers lus ou modifiés, commandes exécutées et leurs résultats, travail restant. "
    "Sois factuel et complet, sans phrase d'introduction."
)


def project_instructions(root):
    """Instructions du projet (BARRASSONS.md, AGENTS.md, CLAUDE.md), chargées à chaque demande."""
    parts, remaining = [], MAX_INSTRUCTION_CHARS
    for name in INSTRUCTION_FILES:
        path = root / name
        try:
            if not path.is_file():
                continue
            text = path.read_text(encoding='utf-8').strip()
        except (OSError, UnicodeError):
            continue
        if text and remaining > 0:
            parts.append(f'--- {name} ---\n{text[:remaining]}')
            remaining -= len(text)
    return '\n\n'.join(parts)


def system_message(root, test_command=None):
    content = SYSTEM_PROMPT + f'\nDossier: {root}\n' + git_context(root)
    if test_command:
        content += '\nCommande de tests configurée : ' + test_command
    instructions = project_instructions(root)
    if instructions:
        content += '\n\nInstructions du projet :\n' + instructions
    return {'role': 'system', 'content': content}


def close_pending_tool_calls(messages, reason):
    """Répond aux appels d'outils restés sans résultat : l'API refuse un historique incomplet."""
    answered = {m.get('tool_call_id') for m in messages if m.get('role') == 'tool'}
    for message in list(messages):
        for call in message.get('tool_calls') or []:
            if call.get('id') not in answered:
                messages.append({'role': 'tool', 'tool_call_id': call.get('id', ''), 'content': reason})
                answered.add(call.get('id'))


def expand_mentions(text, root, session):
    """Joint le contenu des fichiers cités avec @chemin à la demande."""
    attachments, seen, names = [], set(), []
    for match in MENTION.finditer(text):
        relative = match.group(1)
        if relative in seen:
            continue
        seen.add(relative)
        try:
            if not _resolve(root, relative).is_file():
                continue
        except ValueError:
            continue
        result = read_file(root, {'path': relative}, session)
        if 'error' in result:
            continue
        suffix = f' (lignes 1 à {result["end_line"]} sur {result["total_lines"]})' if result['truncated'] else ''
        names.append(result['path'])
        attachments.append(f'Contenu de {result["path"]}{suffix} :\n```\n{result["content"]}\n```')
    if names:
        terminal.line('  ⎿ Joint : ' + ', '.join(names), 'muted')
    return text + ('\n\n' + '\n\n'.join(attachments) if attachments else '')


def trim_history(messages):
    """Garde le message système et la fin de la conversation, en commençant par une demande."""
    if len(messages) <= MAX_HISTORY_MESSAGES:
        return messages
    tail = messages[-(MAX_HISTORY_MESSAGES - 1):]
    while tail and tail[0].get('role') != 'user':
        tail.pop(0)
    return messages[:1] + tail if tail else messages


class Turn:
    """Compteurs d'une session : jetons de texte, jetons facturés et solde."""

    def __init__(self, balance=None):
        self.tokens = self.credits = 0
        self.balance = balance

    def record(self, response):
        usage = response.get('usage') or {}
        self.tokens += int(usage.get('total_tokens') or 0)
        self.credits += int(usage.get('credits_charged') or 0)
        if usage.get('remaining_balance') is not None:
            self.balance = usage['remaining_balance']


def compact(config, model, messages, counters):
    """Remplace l'historique par un résumé produit par le modèle."""
    with terminal.busy('Résumé de la conversation…'):
        response = api_request(config, '/chat/completions', {
            'model': model, 'messages': messages + [{'role': 'user', 'content': COMPACT_REQUEST}],
            'max_tokens': 2048, 'temperature': 0.2,
        })
    counters.record(response)
    summary = (response['choices'][0]['message'].get('content') or '').strip()
    if not summary:
        raise RuntimeError('Le modèle n’a pas produit de résumé ; l’historique est conservé.')
    return messages[:1] + [{'role': 'user', 'content': 'Résumé de la conversation précédente :\n' + summary},
                           {'role': 'assistant', 'content': 'Compris, je reprends à partir de ce résumé.'}]


def model_turn(config, root, model, messages, session, counters, persist, test_command, verbose):
    """Boucle modèle → outils jusqu'à une réponse finale."""
    changed_files = False
    verification_attempts = 0
    for _ in range(MAX_TOOL_ITERATIONS):
        with terminal.busy('Réflexion…'):
            selection = load_link(root, config)
            response = api_request(config, "/chat/completions", {
                "model": model,
                "messages": messages,
                "tools": TOOLS,
                "max_tokens": 4096,
                "temperature": 0.2,
                **({'memory': selection} if selection else {}),
            })
        counters.record(response)
        assistant = response["choices"][0]["message"]
        messages.append(assistant)
        persist()
        tool_calls = assistant.get("tool_calls") or []
        if not tool_calls:
            if changed_files and test_command and verification_attempts < 2:
                verification_attempts += 1
                verification = execute_tool('run_command', {'command': test_command}, root, session)
                terminal.result(verification, verbose)
                messages.append({'role': 'user', 'content': 'Résultat réel des tests configurés : '
                                 + json.dumps(verification, ensure_ascii=False)
                                 + '. Corrige les erreurs si possible ; indique clairement une vérification refusée ou échouée.'})
                persist()
                if verification.get('exit_code') == 0:
                    changed_files = False
                elif verification.get('status') != 'refused':
                    continue
            terminal.assistant(assistant.get("content") or "(réponse vide)")
            if changed_files and test_command:
                terminal.line('La vérification configurée reste non validée. Consulte les résultats ci-dessus.', 'red')
            return
        if assistant.get('content'):
            terminal.assistant(assistant['content'])
        for call in tool_calls:
            function = call.get("function") or {}
            name = function.get("name", "")
            try:
                args = json.loads(function.get("arguments") or "{}")
                if not isinstance(args, dict):
                    raise ValueError('Un objet JSON est attendu.')
                terminal.tool(name, args)
                result = execute_tool(name, args, root, session)
                if result.get('status') in {'written', 'updated'}:
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
    terminal.line("Limite d'itérations d'outils atteinte pour cette demande. Écris « continue » pour poursuivre.", 'muted')


HELP_ESSENTIALS = ['Écris ta demande en français pour travailler.', '',
                   '@fichier  Joindre un fichier à la demande', '!commande Lancer une commande shell',
                   '/model    Changer d’IA', '/undo     Annuler la dernière modification', '/exit     Quitter', '',
                   '/help tout    Toutes les commandes']
HELP_ALL = ['/model    Choisir une IA', '/theme    Apparence de Big Persona, thème et couleur',
            '/status   Voir le serveur et les jetons', '/init     Créer BARRASSONS.md, les instructions du projet',
            '/compact  Résumer la conversation pour libérer le contexte',
            '/verbose  Afficher les résultats détaillés des outils', '/diff     Voir les changements Git',
            '/undo     Annuler la dernière édition', '/verify   Lancer les tests configurés',
            '/permissions  Voir ou retirer les autorisations de la session',
            '/memory   Lier la mémoire personnelle ou un projet Bara',
            '/remember texte  Enregistrer un souvenir partagé après confirmation',
            '/review   Demander une revue du diff', '/clear    Effacer le contexte', '/exit     Quitter', '',
            '@chemin : joindre un fichier · !commande : exécuter directement',
            'Ctrl+C : interrompre la réponse en cours · Tab : compléter · ↑/↓ : historique',
            '\\ puis Entrée : nouvelle ligne']


def run_session(config, root, model, resume=False, test_command=None, balance=None):
    messages = [system_message(root, test_command)]
    saved = load_session(root)
    if resume and isinstance(saved.get('messages'), list):
        messages = saved['messages']
        # Resolve unfinished calls without executing them again.
        close_pending_tool_calls(messages, 'Exécution précédente interrompue. Résultat incertain ; ne pas répéter automatiquement.')
    session = ToolSession()
    counters = Turn(balance)

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
    instructions = [name for name in INSTRUCTION_FILES if (root / name).is_file()]
    if instructions:
        terminal.line('Instructions : ' + ', '.join(instructions), 'muted')
    linked_memory = load_link(root, config)
    if linked_memory:
        terminal.line('Mémoire : ' + link_label(linked_memory), 'muted')
    verbose = False
    while True:
        try:
            terminal.status(model, counters.tokens, counters.credits, counters.balance)
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
            session.read_hashes.clear()
            persist()
            terminal.line("Contexte effacé.", 'muted')
            continue
        if text == '/help':
            terminal.panel('L’essentiel', HELP_ESSENTIALS, wrap=True)
            continue
        if text in {'/help tout', '/help all'}:
            terminal.panel('Toutes les commandes', HELP_ALL, wrap=True)
            continue
        if text == '/theme' or text.startswith('/theme '):
            try:
                configure_theme(config, text.removeprefix('/theme').strip(), api_request, save_config)
            except (RuntimeError, ValueError) as error:
                terminal.line(str(error), 'red')
            continue
        if text == '/memory' or text.startswith('/memory '):
            try:
                configure_link(config, root, text.removeprefix('/memory').strip(), api_request)
            except (RuntimeError, ValueError) as error:
                terminal.line(str(error), 'red')
            continue
        if text == '/remember' or text.startswith('/remember '):
            try:
                remember(config, root, text.removeprefix('/remember').strip(), api_request, _confirm)
            except (RuntimeError, ValueError) as error:
                terminal.line(str(error), 'red')
            continue
        if text == '/verbose':
            verbose = not verbose
            terminal.line('Affichage détaillé activé.' if verbose else 'Affichage compact activé.', 'muted')
            continue
        if text == '/permissions' or text == '/permissions reset':
            if text.endswith('reset'):
                session.allow_edits = False
                session.allowed_commands.clear()
                terminal.line('Autorisations de la session retirées.', 'green')
            else:
                rows = ['Fichiers : ' + ('modifications autorisées' if session.allow_edits else 'confirmation demandée')]
                rows += ['Commande autorisée : ' + command for command in sorted(session.allowed_commands)]
                terminal.panel('Autorisations de la session', rows + ['', '/permissions reset pour tout retirer'], wrap=True)
            continue
        if text == '/model':
            model = _select_model({**config, 'model': model}, choose=True)
            config['model'] = model
            save_config(config); persist()
            terminal.line(f'Modèle sélectionné : {model}', 'green')
            continue
        if text == '/status':
            try:
                with terminal.busy('Actualisation des jetons…'):
                    counters.balance = api_request(config, '/balance').get('credits')
                terminal.panel('Session', [f'Modèle : {model}', f'Projet : {root}',
                    f'Branche : {branch or "sans dépôt"}', f'Serveur : {resolve_api_base(config)}',
                    f'Messages en contexte : {len(messages)}',
                    'Mémoire : ' + link_label(load_link(root, config))])
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
            result = undo_last(root, _confirm)
            # Le fichier a changé : il faudra le relire avant toute nouvelle modification.
            if result.get('path'):
                session.read_hashes.pop(str(_resolve(root, result['path'])), None)
            terminal.result(result, verbose)
            continue
        if text == '/compact':
            if len(messages) < 3:
                terminal.line('Rien à résumer pour l’instant.', 'muted')
                continue
            try:
                close_pending_tool_calls(messages, 'Résultat indisponible.')
                messages = compact(config, model, messages, counters)
                session.read_hashes.clear()
                persist()
                terminal.line('Conversation résumée. Les fichiers devront être relus avant modification.', 'green')
            except (RuntimeError, KeyError, IndexError) as error:
                terminal.line(str(error), 'red')
            except KeyboardInterrupt:
                terminal.line('\nRésumé interrompu ; l’historique est conservé.', 'muted')
            continue
        if text == '/verify':
            if not test_command:
                terminal.line('Configure --test-command, par exemple "python -m pytest".', 'muted')
            else:
                result = execute_tool('run_command', {'command': test_command}, root, session)
                terminal.result(result, verbose)
                messages.append({'role': 'user', 'content': 'Résultat réel de la vérification : ' + json.dumps(result, ensure_ascii=False)})
                persist()
            continue
        if text.startswith('!'):
            command = text[1:].strip()
            if not command:
                terminal.line('Écris une commande après « ! », par exemple !git status.', 'muted')
                continue
            # La commande est tapée par l'utilisateur lui-même : pas de confirmation.
            terminal.tool('run_command', {'command': command})
            try:
                result = run_command(root, {'command': command}, approved=True)
            except KeyboardInterrupt:
                result = {'error': 'Commande interrompue.'}
            terminal.result(result, verbose)
            messages.append({'role': 'user', 'content': f'J’ai exécuté moi-même la commande `{command}`. Résultat : '
                             + json.dumps(result, ensure_ascii=False)})
            messages.append({'role': 'assistant', 'content': 'Résultat noté.'})
            persist()
            continue
        if text == '/review':
            try:
                text = 'Revois ce diff : cherche les bugs et régressions, puis indique les vérifications nécessaires.\n' + git(root, 'diff', 'HEAD')[:60000]
            except ValueError as error:
                terminal.line(str(error), 'red')
                continue
        elif text == '/init':
            text = INIT_REQUEST
        elif text.startswith('/'):
            terminal.line('Commande inconnue. /help affiche les commandes.', 'muted')
            continue
        else:
            text = expand_mentions(text, root, session)
        messages[0] = system_message(root, test_command)
        messages.append({"role": "user", "content": text})
        persist()
        try:
            model_turn(config, root, model, messages, session, counters, persist, test_command, verbose)
        except KeyboardInterrupt:
            close_pending_tool_calls(messages, 'Interrompu par l’utilisateur avant la fin de cet outil.')
            messages.append({'role': 'user', 'content': 'J’ai interrompu ta réponse. Attends ma prochaine demande.'})
            messages.append({'role': 'assistant', 'content': 'D’accord, j’attends ta prochaine demande.'})
            persist()
            terminal.line('\nInterrompu. Tu peux écrire une nouvelle demande.', 'muted')
        except (RuntimeError, KeyError, IndexError, TypeError) as error:
            # Erreur réseau, serveur ou réponse mal formée : la session continue, l'historique reste valide.
            close_pending_tool_calls(messages, 'Résultat indisponible.')
            persist()
            terminal.line(str(error) if isinstance(error, RuntimeError) else 'Réponse inattendue du serveur.', 'red')
        messages = trim_history(messages)


def main():
    parser = argparse.ArgumentParser(prog="barrassons", description="Assistant de développement AI2AI dans le terminal")
    parser.add_argument("command", nargs="?", choices=["login", "logout"], help="Gérer la connexion AI2AI")
    parser.add_argument('--resume', action='store_true', help='Reprendre la session du projet')
    parser.add_argument('--worktree', action='store_true', help='Créer une branche isolée dans un worktree Git')
    parser.add_argument('--test-command', help='Commande de vérification à utiliser avec /verify')
    parser.add_argument('--memory', choices=['off', 'chat', 'bara', 'both'], help='Mémoire partagée à lier à ce dossier')
    parser.add_argument('--memory-workspace', type=int, metavar='ID', help='Projet Bara pour --memory bara ou both')
    parser.add_argument('--api-base', metavar='URL', help='Changer explicitement le serveur AI2AI (Render par défaut)')
    parser.add_argument('--model', metavar='ID', help='Utiliser un modèle du catalogue sans ouvrir le sélecteur')
    parser.add_argument('--theme', choices=['persona', 'light', 'dark', 'clair', 'sombre'],
                        help='Apparence : Big Persona par défaut, clair ou sombre')
    parser.add_argument('--no-color', action='store_true', help='Désactiver les couleurs du terminal')
    args = parser.parse_args()
    if args.memory in {'bara', 'both'} and args.memory_workspace is None:
        parser.error('--memory bara/both demande --memory-workspace ID')
    if args.memory_workspace is not None and args.memory not in {'bara', 'both'}:
        parser.error('--memory-workspace demande --memory bara ou both')
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
                terminal.line('Lance `barrassons` pour commencer.', 'muted')
                return 0
        if args.theme:
            config['theme'] = {'clair': 'light', 'sombre': 'dark'}.get(args.theme, args.theme)
        if not apply_theme(config, api_request):
            terminal.line('Apparence Big Persona indisponible ; thème clair violet utilisé. /theme pour choisir.', 'muted')
        root = Path.cwd().resolve()
        if args.worktree:
            if not _confirm('Créer une branche et un worktree Git pour cette session ?'):
                return 0
            root = make_worktree(root)
        if args.memory:
            choice = args.memory + (f' {args.memory_workspace}' if args.memory_workspace is not None else '')
            configure_link(config, root, choice, api_request)
        model = _select_model(config, requested=args.model)
        config['model'] = model
        save_config(config)
        balance = None
        try:
            with terminal.busy('Chargement des jetons…'):
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
