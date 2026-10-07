import ipaddress
import re
import socket
from difflib import unified_diff
from urllib.parse import urljoin, urlparse
from .workspace_sandbox import WorkspaceSandbox
from bs4 import BeautifulSoup

from bara.services import StorageQuotaError, check_quota, store_file_content
from .sandbox import run_sandbox_command
from ..models import ToolExecution, WorkFile
from .workspace_runtime import materialize_workspace
from .registry import register_tool
from django.core.files.base import ContentFile
from .registry import register_tool
import requests as http_requests
from django.conf import settings
import os
from django.core.files.base import ContentFile
from ..models import WorkFile



TEXT_EXTENSIONS = (".txt", ".md", ".csv", ".json", ".py", ".js", ".html", ".css")
MAX_FETCH_BYTES = 1_000_000
MAX_FETCH_CHARS = 20_000
MAX_REDIRECTS = 3
MAX_EDITABLE_CHARS = 50_000
MAX_READ_LINES = 500
MAX_TOOL_OUTPUT_CHARS = 20_000


def _clean_name(name):
    name = (name or "").strip().replace("\\", "/").lstrip("/")
    parts = name.split("/")
    if not name or len(name) > 255 or any(p in ("", ".", "..") for p in parts):
        return None
    return name


def _to_int(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _line_number(value):
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.isdecimal():
        return int(value)
    return None


def _get_file(workspace, file_id):
    file_id = _to_int(file_id)
    if file_id is None:
        return None
    return WorkFile.objects.filter(id=file_id, workspace=workspace).first()


def _is_text_file(work_file):
    return (
        work_file.original_name.lower().endswith(TEXT_EXTENSIONS)
        or (work_file.content_type or "").startswith("text/")
    )



@register_tool(
    name="list_files",
    description="Retourne la liste complète des fichiers du workspace, organisés par dossier virtuel.",
    parameters={"type": "object", "properties": {}, "required": []},
)
def list_files(workspace):
    files = WorkFile.objects.filter(workspace=workspace)
    tree = {}
    flat = []
    for f in files:
        if f.original_name.endswith("/.keep"):
            folder = f.original_name[:-len("/.keep")]
            tree.setdefault(folder, [])
            continue
        if "/" in f.original_name:
            folder, name = f.original_name.split("/", 1)
            tree.setdefault(folder, []).append(name)
        else:
            flat.append(f.original_name)
        flat_entry = {"id": f.id, "name": f.original_name, "size": f.size_bytes}
    return {
        "root_files": flat,
        "folders": tree,
        "all_files": [
            {"id": f.id, "name": f.original_name, "size": f.size_bytes, "content_type": f.content_type}
            for f in files
        ],
    }

@register_tool(
    name="read_file",
    description="Lit le contenu textuel d'un fichier du workspace.",
    parameters={
        "type": "object",
        "properties": {
            "file_id": {
                "type": "integer",
                "description": "Identifiant du fichier.",
            }
        },
        "required": ["file_id"],
    },
)
def read_file(workspace, file_id):
    """
    Lit un fichier appartenant au workspace courant.
    """

    file = WorkFile.objects.filter(
        id=file_id,
        workspace=workspace,
    ).first()

    if not file:
        return {
            "error": "Fichier introuvable."
        }

    return {
        "file_id": file.id,
        "name": file.original_name,
        "content": file.extracted_text,
    }


@register_tool(
    name="read_file_lines",
    description=(
        "Lit une plage précise de lignes d'un fichier texte du workspace, sans charger tout le fichier. "
        "La plage est limitée à 500 lignes et la sortie à 20 000 caractères."
    ),
    parameters={
        "type": "object",
        "properties": {
            "file_id": {"type": "integer", "description": "Identifiant du fichier (voir list_files)"},
            "start_line": {"type": "integer", "description": "Première ligne à lire, à partir de 1"},
            "end_line": {"type": "integer", "description": "Dernière ligne à lire, incluse (500 lignes maximum)"},
        },
        "required": ["file_id", "start_line", "end_line"],
    },
)
def read_file_lines(workspace, file_id, start_line, end_line):
    work_file = _get_file(workspace, file_id)
    if not work_file:
        return {"error": "Fichier introuvable dans cet espace de travail."}
    if not _is_text_file(work_file):
        return {"error": "Seuls les fichiers texte peuvent être lus par lignes."}
    start = _line_number(start_line)
    end = _line_number(end_line)
    if start is None or end is None or start < 1 or end < start:
        return {
            "error": "Plage invalide : indique des numéros de ligne entiers positifs et une fin supérieure ou égale au début."
        }
    if end - start + 1 > MAX_READ_LINES:
        return {"error": f"La plage est trop grande : {MAX_READ_LINES} lignes maximum par appel."}

    lines = (work_file.extracted_text or "").splitlines()
    if start > len(lines):
        return {"error": f"Le fichier contient {len(lines)} ligne(s) ; la ligne {start} n'existe pas."}
    actual_end = min(end, len(lines))
    content = "\n".join(lines[start - 1:actual_end])
    truncated = len(content) > MAX_TOOL_OUTPUT_CHARS
    if truncated:
        content = content[:MAX_TOOL_OUTPUT_CHARS]

    return {
        "file_id": work_file.id,
        "name": work_file.original_name,
        "start_line": start,
        "end_line": actual_end,
        "total_lines": len(lines),
        "content": content,
        "truncated": truncated,
    }


@register_tool(
    name="compare_files",
    description=(
        "Compare deux fichiers texte du workspace et retourne leur diff unifié en lecture seule. "
        "Chaque fichier est limité à 50 000 caractères et la sortie à 20 000 caractères."
    ),
    parameters={
        "type": "object",
        "properties": {
            "first_file_id": {"type": "integer", "description": "Identifiant du premier fichier"},
            "second_file_id": {"type": "integer", "description": "Identifiant du second fichier"},
        },
        "required": ["first_file_id", "second_file_id"],
    },
)
def compare_files(workspace, first_file_id, second_file_id):
    first_file = _get_file(workspace, first_file_id)
    second_file = _get_file(workspace, second_file_id)
    if not first_file or not second_file:
        return {"error": "Un des fichiers est introuvable dans cet espace de travail."}
    if not _is_text_file(first_file) or not _is_text_file(second_file):
        return {"error": "Seuls les fichiers texte peuvent être comparés."}

    first_content = first_file.extracted_text or ""
    second_content = second_file.extracted_text or ""
    if len(first_content) > MAX_EDITABLE_CHARS or len(second_content) > MAX_EDITABLE_CHARS:
        return {"error": f"Chaque fichier doit contenir au maximum {MAX_EDITABLE_CHARS} caractères."}

    diff_parts = []
    diff_size = 0
    truncated = False
    for line in unified_diff(
        first_content.splitlines(),
        second_content.splitlines(),
        fromfile=f"a/file-{first_file.id}",
        tofile=f"b/file-{second_file.id}",
        lineterm="",
    ):
        line = f"{line}\n"
        remaining = MAX_TOOL_OUTPUT_CHARS - diff_size
        if len(line) > remaining:
            diff_parts.append(line[:remaining])
            truncated = True
            break
        diff_parts.append(line)
        diff_size += len(line)

    return {
        "first_file": {"id": first_file.id, "name": first_file.original_name},
        "second_file": {"id": second_file.id, "name": second_file.original_name},
        "identical": first_content == second_content,
        "diff": "".join(diff_parts),
        "truncated": truncated,
    }


@register_tool(
    name="write_file",
    description="Crée ou remplace un fichier texte du workspace. Pour une petite modification d'un fichier existant, préfère edit_file.",
    parameters={
        "type": "object",
        "properties": {
            "filename": {"type": "string", "description": "Nom du fichier, ex: notes.txt ou docs/notes.txt"},
            "content": {"type": "string", "description": "Contenu texte complet du fichier"},
        },
        "required": ["filename", "content"],
    },
)
def write_file(workspace, filename, content):
    filename = _clean_name(filename)
    if not filename:
        return {"error": "Nom de fichier invalide."}
    existing = WorkFile.objects.filter(workspace=workspace, original_name=filename).first()
    try:
        if existing:
            if not _is_text_file(existing):
                return {"error": "Ce fichier n'est pas un fichier texte, il ne peut pas être remplacé."}
            store_file_content(existing, content)
            return {"status": "updated", "filename": filename}
        new_file = WorkFile(workspace=workspace, original_name=filename, content_type="text/plain")
        
        store_file_content(new_file, content)
        return {"status": "created", "filename": filename}
    except StorageQuotaError as exc:
        return {"error": str(exc)}

@register_tool(
    name="edit_file",
    description="Remplace un passage précis d'un fichier texte. old_text doit apparaître exactement une fois (sauf replace_all=true).",
    parameters={
        "type": "object",
        "properties": {
            "file_id": {"type": "integer", "description": "Identifiant du fichier (voir list_files)"},
            "old_text": {"type": "string", "description": "Passage exact à remplacer"},
            "new_text": {"type": "string", "description": "Texte de remplacement"},
            "replace_all": {"type": "boolean", "description": "Remplacer toutes les occurrences (défaut: false)"},
        },
        "required": ["file_id", "old_text", "new_text"],
    },
)
def edit_file(workspace, file_id, old_text, new_text, replace_all=False):
    work_file = _get_file(workspace, file_id)
    if not work_file:
        return {"error": "Fichier introuvable."}
    if not _is_text_file(work_file):
        return {"error": "Seuls les fichiers texte peuvent être modifiés."}
    if not old_text:
        return {"error": "old_text ne peut pas être vide."}
    content = work_file.extracted_text
    if len(content) >= MAX_EDITABLE_CHARS:
        return {"error": "Fichier trop volumineux pour être modifié avec edit_file."}
    count = content.count(old_text)
    if count == 0:
        return {"error": "Passage introuvable. Relis le fichier avec read_file et recopie le texte exactement."}
    if count > 1 and not replace_all:
        return {"error": f"Le passage apparaît {count} fois. Ajoute du contexte autour ou utilise replace_all."}
    
    try:
        store_file_content(work_file, content.replace(old_text, new_text))
    except StorageQuotaError as exc:
        return {"error": str(exc)}
    return {"status": "edited", "replacements": count if replace_all else 1}

@register_tool(
    name="rename_file",
    description="Renomme ou déplace un fichier. Le nom peut contenir un dossier virtuel, ex: 'docs/notes.txt'.",
    parameters={
        "type": "object",
        "properties": {
            "file_id": {"type": "integer", "description": "Identifiant du fichier"},
            "new_name": {"type": "string", "description": "Nouveau nom complet"},
        },
        "required": ["file_id", "new_name"],
    },
)
def rename_file(workspace, file_id, new_name):
    work_file = _get_file(workspace, file_id)
    if not work_file:
        return {"error": "Fichier introuvable."}
    new_name = _clean_name(new_name)
    if not new_name:
        return {"error": "Nouveau nom invalide."}
    if WorkFile.objects.filter(workspace=workspace, original_name=new_name).exclude(id=work_file.id).exists():
        return {"error": "Un fichier porte déjà ce nom."}
    old_name = work_file.original_name
    work_file.original_name = new_name
    work_file.save(update_fields=["original_name"])
    return {"status": "renamed", "from": old_name, "to": new_name}


@register_tool(
    name="delete_file",
    description="Demande la suppression d'un fichier. Rien n'est supprimé tant que l'utilisateur n'a pas confirmé dans l'interface.",
    parameters={
        "type": "object",
        "properties": {
            "file_id": {"type": "integer", "description": "Identifiant du fichier à supprimer"},
        },
        "required": ["file_id"],
    },
)
def delete_file(workspace, file_id):
    work_file = _get_file(workspace, file_id)
    if not work_file:
        return {"error": "Fichier introuvable."}
    action = ToolExecution.objects.filter(
        workspace=workspace, tool_name="delete_file", status="pending", arguments__file_id=work_file.id
    ).first()
    if not action:
        action = ToolExecution.objects.create(
            workspace=workspace,
            tool_name="delete_file",
            arguments={"file_id": work_file.id, "name": work_file.original_name},
            status="pending",
        )
    return {
        "status": "pending_confirmation",
        "action_id": action.id,
        "file_id": work_file.id,
        "description": f"Supprimer « {work_file.original_name} »",
        "message": "En attente de la confirmation de l'utilisateur. Informe-le et ne réessaie pas.",
    }

PISTON_URL = "https://emkc.org/api/v2/piston/execute"

@register_tool(
    name="run_python",
    description=(
        "Envoie le code à l'API tierce Piston pour exécuter un court script Python. "
        "Ne transmets aucun secret ni code confidentiel."
    ),
    parameters={
        "type": "object",
        "properties": {
            "code": {"type": "string", "description": "Code Python à exécuter"},
        },
        "required": ["code"],
    },
)
def run_python(workspace, code):
    try:
        response = http_requests.post(
            PISTON_URL,
            json={
                "language": "python",
                "version": "3.10.0",
                "files": [{"content": code}],
            },
            timeout=15,
        )
        data = response.json()
    except Exception as error:
        return {"error": f"Exécution échouée : {error}"}

    run_result = data.get("run", {})
    return {
        "stdout": (run_result.get("stdout") or "")[:5000],
        "stderr": (run_result.get("stderr") or "")[:2000],
        "exit_code": run_result.get("code"),
    }



@register_tool(
    name="web_search",
    description="Recherche des informations actuelles sur le web (actualités, faits récents, pages publiques).",
    parameters={
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "Termes de recherche"},
        },
        "required": ["query"],
    },
)
def web_search(workspace, query):
    api_key = settings.SEARCH_API_KEY
    if not api_key:
        return {"error": "La recherche web n'est pas configurée sur ce serveur."}
    try:
        response = http_requests.post(
            "https://google.serper.dev/search",
            headers={"X-API-KEY": api_key, "Content-Type": "application/json"},
            json={"q": query, "num": 5},
            timeout=10,
        )
        data = response.json()
    except Exception as error:
        return {"error": f"Recherche échouée : {error}"}

    results = data.get("organic", [])[:5]
    if not results:
        return {"results": [], "message": "Aucun résultat trouvé."}

    return {
        "results": [
            {"title": r.get("title", ""), "snippet": r.get("snippet", ""), "link": r.get("link", "")}
            for r in results
        ]
    }

def _validate_url(url):
    try:
        parsed = urlparse(url)
        port = parsed.port
    except ValueError:
        return "URL invalide."
    if parsed.scheme not in ("http", "https"):
        return "Seules les URL http(s) sont autorisées."
    if not parsed.hostname:
        return "URL invalide."
    if port not in (None, 80, 443):
        return "Port non autorisé."
    try:
        infos = socket.getaddrinfo(parsed.hostname, None)
    except socket.gaierror:
        return "Nom de domaine introuvable."
    for info in infos:
        ip = ipaddress.ip_address(info[4][0].split("%")[0])
        if ip.version == 6 and ip.ipv4_mapped:
            ip = ip.ipv4_mapped
        if not ip.is_global or ip.is_multicast:
            return "Adresse non autorisée (réseau interne ou réservé)."
    return None


@register_tool(
    name="fetch_url",
    description="Télécharge une page web (http/https) et retourne son texte lisible. Utilise web_search pour trouver l'URL si besoin.",
    parameters={
        "type": "object",
        "properties": {
            "url": {"type": "string", "description": "URL complète de la page"},
        },
        "required": ["url"],
    },
)
def fetch_url(workspace, url):
    current = (url or "").strip()
    response = None
    for _ in range(MAX_REDIRECTS + 1):
        error = _validate_url(current)
        if error:
            return {"error": error}
        try:
            response = http_requests.get(
                current,
                headers={"User-Agent": "BaraBot/1.0 (+https://mia-vkl1.onrender.com)"},
                timeout=10,
                stream=True,
                allow_redirects=False,
            )
        except http_requests.RequestException as exc:
            return {"error": f"Impossible de récupérer la page : {exc}"}
        if response.status_code in (301, 302, 303, 307, 308) and response.headers.get("Location"):
            current = urljoin(current, response.headers["Location"])
            response.close()
            continue
        break
    else:
        return {"error": "Trop de redirections."}

    if response.status_code != 200:
        response.close()
        return {"error": f"La page a répondu avec le code {response.status_code}."}

    content_type = response.headers.get("Content-Type", "").lower()
    if not any(t in content_type for t in ("text/", "json", "xml")):
        response.close()
        return {"error": f"Type de contenu non pris en charge : {content_type or 'inconnu'}."}

    raw = b""
    for chunk in response.iter_content(chunk_size=16384):
        raw += chunk
        if len(raw) >= MAX_FETCH_BYTES:
            break
    response.close()
    try:
        body = raw.decode("utf-8")
    except UnicodeDecodeError:
        body = raw.decode(response.encoding or "latin-1", errors="replace")

    title = ""
    if "html" in content_type:
        soup = BeautifulSoup(body, "html.parser")
        if soup.title and soup.title.string:
            title = soup.title.string.strip()
        for tag in soup(["script", "style", "noscript", "nav", "footer", "header", "aside", "form", "svg"]):
            tag.decompose()
        text = soup.get_text("\n")
    else:
        text = body
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n\s*\n+", "\n\n", text).strip()

    return {
        "url": current,
        "title": title,
        "content": text[:MAX_FETCH_CHARS],
        "truncated": len(text) > MAX_FETCH_CHARS,
    }

@register_tool(
    name="create_folder",
    description="Crée un dossier virtuel (vide) dans le workspace, pour organiser les fichiers à venir.",
    parameters={
        "type": "object",
        "properties": {
            "folder_name": {"type": "string", "description": "Nom du dossier à créer, ex: images"},
        },
        "required": ["folder_name"],
    },
)
def create_folder(workspace, folder_name):
    folder_name = folder_name.strip().strip("/")
    if not folder_name:
        return {"error": "Nom de dossier invalide."}
    try:
        check_quota(workspace.user, new_files=1)
    except StorageQuotaError as exc:
        return {"error": str(exc)}
    
    marker_name = f"{folder_name}/.keep"
    if WorkFile.objects.filter(workspace=workspace, original_name=marker_name).exists():
        return {"status": "already_exists", "folder": folder_name}

    WorkFile.objects.create(
        workspace=workspace,
        file=ContentFile(b"", name=".keep"),
        original_name=marker_name,
        content_type="text/plain",
        size_bytes=0,
        extracted_text="",
    )
    return {"status": "created", "folder": folder_name}


import re

@register_tool(
    name="search_code",
    description=(
        "Recherche un texte ou un motif dans les fichiers texte du workspace "
        "et retourne les fichiers et lignes correspondants."
    ),
    parameters={
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "Texte ou motif à rechercher."
            },
            "file_pattern": {
                "type": "string",
                "description": "Filtre optionnel, par exemple *.py ou *.js."
            },
            "max_results": {
                "type": "integer",
                "description": "Nombre maximal de résultats.",
                "default": 50
            }
        },
        "required": ["query"]
    },
)
def search_code(workspace, query, file_pattern=None, max_results=50):
    query = (query or "").strip()

    if not query:
        return {"error": "La recherche ne peut pas être vide."}

    try:
        max_results = max(1, min(int(max_results), 200))
    except (TypeError, ValueError):
        max_results = 50

    files = WorkFile.objects.filter(workspace=workspace)

    results = []

    for work_file in files:
        if not _is_text_file(work_file):
            continue

        filename = work_file.original_name

        if file_pattern:
            pattern = re.escape(file_pattern)
            pattern = pattern.replace(r"\*", ".*")
            pattern = "^" + pattern + "$"

            if not re.match(pattern, filename, re.IGNORECASE):
                continue

        content = work_file.extracted_text or ""

        for line_number, line in enumerate(content.splitlines(), start=1):
            if query.lower() in line.lower():
                results.append({
                    "file_id": work_file.id,
                    "file": filename,
                    "line": line_number,
                    "content": line[:500],
                })

                if len(results) >= max_results:
                    return {
                        "query": query,
                        "results": results,
                        "truncated": True,
                    }

    return {
        "query": query,
        "results": results,
        "truncated": False,
    }




@register_tool(
    name="run_command",
    description=(
        "Exécute une commande de développement sur le serveur de l'application, "
        "dans une copie temporaire du workspace. Ce processus n'est pas isolé "
        "du système hôte et peut accéder à ses permissions et au réseau. "
        "Les modifications ne sont pas automatiquement enregistrées dans le workspace."
    ),
    parameters={
        "type": "object",
        "properties": {
            "command": {
                "type": "array",
                "items": {
                    "type": "string"
                },
                "description": (
                    "Commande sous forme de liste. "
                    "Exemple : ['python', 'manage.py', 'check']"
                ),
            },
            "timeout": {
                "type": "integer",
                "description": "Durée maximale en secondes.",
                "default": 30,
            },
        },
        "required": ["command"],
    },
)
def run_command(workspace, command, timeout=30):
    if not isinstance(command, list) or not command:
        return {
            "error": "La commande doit être une liste non vide."
        }

    command = [str(part) for part in command]

    allowed_commands = {
        "python",
        "python3",
        "pytest",
        "git",
        "npm",
        "node",
    }

    executable = command[0]

    if executable not in allowed_commands:
        return {
            "error": (
                f"Commande '{executable}' non autorisée. "
                f"Commandes disponibles : "
                f"{', '.join(sorted(allowed_commands))}"
            )
        }

    try:
        timeout = int(timeout)
    except (TypeError, ValueError):
        timeout = 30

    timeout = max(1, min(timeout, 120))

    with materialize_workspace(workspace) as workspace_dir:

        result = run_sandbox_command(
            command=command,
            cwd=str(workspace_dir),
            timeout=timeout,
        )

        result["workspace"] = "temporary"

        return result