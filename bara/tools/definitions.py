from ..models import WorkFile
from .registry import register_tool
from django.core.files.base import ContentFile
from .registry import register_tool
import requests as http_requests
from django.conf import settings
import subprocess
import tempfile
import os
from django.core.files.base import ContentFile
from ..models import WorkFile


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
    name="write_file",
    description="Crée ou remplace un fichier texte dans le workspace avec le contenu fourni.",
    parameters={
        "type": "object",
        "properties": {
            "filename": {"type": "string", "description": "Nom du fichier à créer, ex: notes.txt"},
            "content": {"type": "string", "description": "Contenu texte à écrire dans le fichier"},
        },
        "required": ["filename", "content"],
    },
)
def write_file(workspace, filename, content):
    existing = WorkFile.objects.filter(workspace=workspace, original_name=filename).first()
    if existing:
        existing.extracted_text = content
        existing.file = ContentFile(content.encode("utf-8"), name=filename)
        existing.size_bytes = len(content.encode("utf-8"))
        existing.save()
        return {"status": "updated", "filename": filename}

    WorkFile.objects.create(
        workspace=workspace,
        file=ContentFile(content.encode("utf-8"), name=filename),
        original_name=filename,
        content_type="text/plain",
        size_bytes=len(content.encode("utf-8")),
        extracted_text=content,
    )
    return {"status": "created", "filename": filename}



@register_tool(
    name="run_python",
    description="Exécute un court script Python et retourne sa sortie standard. Pas d'accès réseau ni fichiers persistants.",
    parameters={
        "type": "object",
        "properties": {
            "code": {"type": "string", "description": "Code Python à exécuter"},
        },
        "required": ["code"],
    },
)
def run_python(workspace, code):
    with tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False) as f:
        f.write(code)
        script_path = f.name

    try:
        result = subprocess.run(
            ["python3", script_path],
            capture_output=True,
            text=True,
            timeout=8,
        )
        return {
            "stdout": result.stdout[:5000],
            "stderr": result.stderr[:2000],
            "returncode": result.returncode,
        }
    except subprocess.TimeoutExpired:
        return {"error": "Le script a dépassé le délai de 8 secondes."}
    finally:
        os.unlink(script_path)





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