import argparse
import fnmatch
import json
import os
import subprocess
import sys
import urllib.error
import urllib.request
import webbrowser
from pathlib import Path


DEFAULT_API_BASE = "https://ai-2ai.onrender.com/v1"
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


def api_base_prompt(config):
    default = os.environ.get("BARRASSONS_API_BASE") or config.get("api_base") or DEFAULT_API_BASE
    value = input(f"URL API AI2AI [{default}] : ").strip() or default
    return value.rstrip("/")


def connect(config):
    print("\nConnexion à AI2AI")
    print("1. Ouvrir AI2AI dans le navigateur et récupérer une clé API")
    print("2. Saisir une clé API existante")
    choice = input("Choix [1/2] : ").strip() or "1"
    api_base = api_base_prompt(config)
    if choice == "1":
        webbrowser.open(api_base.removesuffix("/v1") + "/api-keys/")
        print("Crée une clé API sur la page ouverte, puis colle-la ici.")
    api_key = input("Clé API AI2AI : ").strip()
    if not api_key:
        raise ValueError("Aucune clé API fournie.")
    config.update({"api_base": api_base, "api_key": api_key})
    save_config(config)
    print(f"Clé enregistrée dans {config_path()} avec accès limité à ton compte.")
    return config


def api_request(config, path, payload=None):
    url = config["api_base"].rstrip("/") + path
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
    return input(f"\n{action}\nAutoriser ? [y/N] ").strip().lower() in {"y", "yes", "o", "oui"}


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
            if not _confirm(f"Créer/remplacer {path.relative_to(root)} ({len(content)} caractères)"):
                return {"status": "refused"}
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8")
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
            if not _confirm(f"Modifier {path.relative_to(root)} : remplacer un passage de {len(old_text)} caractères"):
                return {"status": "refused"}
            path.write_text(content.replace(old_text, new_text, 1), encoding="utf-8")
            return {"status": "updated", "path": str(path.relative_to(root))}

        if name == "run_command":
            command = args["command"]
            if not _confirm(f"Exécuter dans {root}:\n  {command}"):
                return {"status": "refused"}
            result = subprocess.run(
                command, cwd=root, shell=True, text=True, capture_output=True, timeout=120, check=False,
            )
            return {
                "exit_code": result.returncode,
                "stdout": result.stdout[-20_000:],
                "stderr": result.stderr[-20_000:],
            }
        return {"error": f"Outil inconnu: {name}"}
    except (OSError, UnicodeError, ValueError, KeyError, subprocess.TimeoutExpired) as error:
        return {"error": str(error)}


def _select_model(config):
    response = api_request(config, "/models")
    models = [model for model in response.get("data", []) if "anthropic" not in model.get("owned_by", "").lower()]
    if not models:
        raise RuntimeError("Aucun modèle compatible avec les outils n'est disponible sur ce compte.")
    print("Modèles compatibles :")
    for index, model in enumerate(models, 1):
        print(f"  {index}. {model['id']} ({model.get('owned_by', 'AI2AI')})")
    selected = input(f"Modèle [{models[0]['id']}] : ").strip()
    if not selected:
        return models[0]["id"]
    if selected.isdigit() and 1 <= int(selected) <= len(models):
        return models[int(selected) - 1]["id"]
    if any(model["id"] == selected for model in models):
        return selected
    raise ValueError("Modèle inconnu dans la liste.")


def run_session(config, root, model):
    system = (
        "Tu es Barrassons, un agent de développement qui travaille dans le dossier local indiqué. "
        "Inspecte le code avant de proposer des changements. Utilise les outils pour lire, rechercher et modifier les fichiers. "
        "Ne prétends jamais avoir exécuté une action si l'outil ne le confirme pas. "
        "Les écritures et commandes sont soumises à confirmation par l'utilisateur. Réponds en français sauf demande contraire."
    )
    messages = [{"role": "system", "content": system + f"\nDossier courant: {root}"}]
    print(f"\nBarrassons · {root} · {model}")
    print("Entre une demande; /exit quitte, /clear efface le contexte, /help affiche l'aide.\n")
    while True:
        try:
            text = input("barrassons> ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\nÀ bientôt.")
            return
        if not text:
            continue
        if text in {"/exit", "/quit"}:
            return
        if text == "/clear":
            messages = messages[:1]
            print("Contexte effacé.")
            continue
        if text == "/help":
            print("Outils: liste, lecture, recherche, modification de fichiers, commandes shell avec confirmation.\nCommandes: /clear, /exit, /help")
            continue
        messages.append({"role": "user", "content": text})
        for _ in range(MAX_TOOL_ITERATIONS):
            response = api_request(config, "/chat/completions", {
                "model": model,
                "messages": messages,
                "tools": TOOLS,
                "max_tokens": 2048,
                "temperature": 0.2,
            })
            choice = response["choices"][0]
            assistant = choice["message"]
            messages.append(assistant)
            tool_calls = assistant.get("tool_calls") or []
            if not tool_calls:
                answer = assistant.get("content") or "(réponse vide)"
                print("\n" + answer + "\n")
                break
            for call in tool_calls:
                function = call.get("function") or {}
                name = function.get("name", "")
                print(f"\n› {name}")
                try:
                    args = json.loads(function.get("arguments") or "{}")
                    result = execute_tool(name, args, root)
                except (TypeError, ValueError) as error:
                    result = {"error": f"Arguments invalides: {error}"}
                print(json.dumps(result, ensure_ascii=False)[:1000])
                messages.append({
                    "role": "tool", "tool_call_id": call.get("id", ""),
                    "name": name, "content": json.dumps(result, ensure_ascii=False),
                })
        else:
            print("Limite d'itérations d'outils atteinte pour cette demande.")
        if len(messages) > 41:
            tail = messages[-40:]
            while tail and tail[0].get("role") != "user":
                tail.pop(0)
            if tail:
                messages = messages[:1] + tail


def main():
    parser = argparse.ArgumentParser(prog="barrassons", description="Assistant de développement AI2AI dans le terminal")
    parser.add_argument("command", nargs="?", choices=["login", "logout"], help="Gérer la connexion AI2AI")
    args = parser.parse_args()
    config = load_config()
    try:
        if args.command == "logout":
            config_path().unlink(missing_ok=True)
            print("Clé locale supprimée.")
            return 0
        if args.command == "login" or not config.get("api_key"):
            config = connect(config)
        root = Path.cwd().resolve()
        model = _select_model(config)
        run_session(config, root, model)
        return 0
    except (KeyboardInterrupt, EOFError):
        print("\nConnexion annulée.")
        return 1
    except (RuntimeError, ValueError, KeyError) as error:
        print(f"Erreur: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())