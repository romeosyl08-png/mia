from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from django.db.models.aggregates import Sum
from chat.services import CREDITS_PER_DOLLAR, MARGIN_MULTIPLIER, ProviderError, is_local_provider
from django.conf import settings

import json as json_module

def actual_cost_credits(model, prompt_tokens, completion_tokens):
		input_price = float(model.input_price_per_million or 0) / 1_000_000
		output_price = float(model.output_price_per_million or 0) / 1_000_000
		cost_usd = (prompt_tokens * input_price) + (completion_tokens * output_price)
		cost_usd_with_margin = cost_usd * MARGIN_MULTIPLIER
		return max(1, round(cost_usd_with_margin * CREDITS_PER_DOLLAR))

def call_llm_with_tools(provider, api_key, model_id, messages, system_prompt="", temperature=0.7, max_tokens=500, tools=None):
    """Variante dédiée à l'agent : renvoie le message complet (dict), pas juste le texte."""
    provider_name = f"{provider.slug} {provider.name}".lower()
    is_anthropic = "anthropic" in provider_name
    if not provider.base_url:
        raise ProviderError("Le fournisseur n'a pas d'URL configurée.")
    if is_anthropic:
        raise ProviderError("Le mode outils n'est pas encore supporté pour Anthropic.")

    url = provider.base_url.rstrip("/") + "/v1/chat/completions"
    payload = {
        "model": model_id,
        "messages": ([{"role": "system", "content": system_prompt}] if system_prompt else []) + messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
    }
    if tools:
        payload["tools"] = tools
        payload["tool_choice"] = "auto"

    headers = {"Content-Type": "application/json", "User-Agent": "Mozilla/5.0 (X11; Linux x86_64)"}
    if not is_local_provider(provider):
        headers["Authorization"] = f"Bearer {api_key}"

    request = Request(url, data=json_module.dumps(payload).encode("utf-8"), headers=headers, method="POST")
    try:
        with urlopen(request, timeout=120) as response:
            data = json_module.loads(response.read().decode("utf-8"))
    except HTTPError as error:
        raise ProviderError(f"Le fournisseur a renvoyé une erreur ({error.code}).") from error
    except (URLError, TimeoutError, ValueError) as error:
        raise ProviderError("Le fournisseur est temporairement indisponible.") from error

    choice = (data.get("choices") or [{}])[0]
    message_response = choice.get("message", {})
    usage = data.get("usage", {})
    return message_response, usage.get("prompt_tokens", 0), usage.get("completion_tokens", 0)

def call_llm_with_tools_stream(provider, api_key, model_id, messages, system_prompt="", temperature=0.7, max_tokens=500, tools=None):
    """
    Générateur : yield des événements au fur et à mesure de la réponse en streaming.
    Événements possibles :
      {"type": "delta", "content": "..."}                      -> morceau de texte de la réponse finale
      {"type": "tool_call_delta", "index": i, "id":..,          -> construction progressive d'un tool_call
                "name":.., "arguments_delta": ".."}
      {"type": "done", "finish_reason": .., "usage": {...}}     -> fin du flux
    """
    provider_name = f"{provider.slug} {provider.name}".lower()
    if "anthropic" in provider_name:
        raise ProviderError("Le mode outils/streaming n'est pas supporté pour Anthropic.")
    if not provider.base_url:
        raise ProviderError("Le fournisseur n'a pas d'URL configurée.")

    url = provider.base_url.rstrip("/") + "/v1/chat/completions"
    payload = {
        "model": model_id,
        "messages": ([{"role": "system", "content": system_prompt}] if system_prompt else []) + messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
        "stream": True,
    }
    if tools:
        payload["tools"] = tools
        payload["tool_choice"] = "auto"

    headers = {"Content-Type": "application/json", "User-Agent": "Mozilla/5.0 (X11; Linux x86_64)"}
    if not is_local_provider(provider):
        headers["Authorization"] = f"Bearer {api_key}"

    request = Request(url, data=json_module.dumps(payload).encode("utf-8"), headers=headers, method="POST")

    try:
        response = urlopen(request, timeout=120)
    except HTTPError as error:
        raise ProviderError(f"Le fournisseur a renvoyé une erreur ({error.code}).") from error
    except (URLError, TimeoutError) as error:
        raise ProviderError("Le fournisseur est temporairement indisponible.") from error

    usage = {}
    finish_reason = None
    try:
        for raw_line in response:
            line = raw_line.decode("utf-8", errors="replace").strip()
            if not line or not line.startswith("data:"):
                continue
            data_str = line[len("data:"):].strip()
            if data_str == "[DONE]":
                break
            try:
                chunk = json_module.loads(data_str)
            except ValueError:
                continue

            choice = (chunk.get("choices") or [{}])[0]
            delta = choice.get("delta", {})
            finish_reason = choice.get("finish_reason") or finish_reason
            if chunk.get("usage"):
                usage = chunk["usage"]
            elif (chunk.get("x_groq") or {}).get("usage"):
                usage = chunk["x_groq"]["usage"]
            if delta.get("content"):
                yield {"type": "delta", "content": delta["content"]}

            for tc in delta.get("tool_calls") or []:
                yield {
                    "type": "tool_call_delta",
                    "index": tc.get("index", 0),
                    "id": tc.get("id"),
                    "name": (tc.get("function") or {}).get("name"),
                    "arguments_delta": (tc.get("function") or {}).get("arguments", ""),
                }
    finally:
        response.close()

    yield {"type": "done", "finish_reason": finish_reason, "usage": usage}

    
MAX_TEXT_CHARS = 50_000


def get_workspace_context(workspace):
    """
    Construit le contexte envoyé au modèle
    à partir des fichiers du workspace.
    """
    if workspace is None:
        return ""

    files = workspace.files.exclude(
        extracted_text=""
    )

    if not files.exists():
        return ""

    parts = [
        "Fichiers disponibles dans cet espace de travail :"
    ]

    for work_file in files:
        parts.append(
            f"\n--- {work_file.original_name} ---\n"
            f"{work_file.extracted_text}"
        )

    return "\n".join(parts)


import io
from pypdf import PdfReader
from docx import Document as DocxDocument


import os
from django.core.files.base import ContentFile


from .models import FileVersion, WorkFile

MAX_VERSIONS_PER_FILE = 20


def store_file_content(work_file, content):
    data = content.encode("utf-8")
    if len(data) > MAX_TEXT_FILE_BYTES:
        raise StorageQuotaError("Fichier texte trop volumineux (1 Mo maximum).")
    changed = not work_file.pk or work_file.extracted_text != content
    if changed:
        # Écrire un fichier de N octets ajoute N octets au total : le nouveau contenu, et l'ancien
        # (qui passe dans l'historique) remplace celui qui vient de disparaître du fichier.
        check_quota(work_file.workspace.user, len(data), new_files=0 if work_file.pk else 1)
    if work_file.pk and changed:
        FileVersion.objects.create(file=work_file, content=work_file.extracted_text, size_bytes=work_file.size_bytes)
        old_ids = list(work_file.versions.values_list("id", flat=True)[MAX_VERSIONS_PER_FILE:])
        if old_ids:
            FileVersion.objects.filter(id__in=old_ids).delete()
    if work_file.file:
        work_file.file.delete(save=False)
    work_file.file.save(os.path.basename(work_file.original_name), ContentFile(data), save=False)
    work_file.extracted_text = content
    work_file.size_bytes = len(data)
    work_file.save()
    
import io
from openpyxl import load_workbook
from pptx import Presentation


def extract_text(uploaded_file, content_type, filename=""):
    MAX_CHARS = 50_000
    filename_lower = filename.lower()

    is_text_type = content_type.startswith("text/") or content_type in ("application/json", "application/csv")
    is_text_ext = filename_lower.endswith((".txt", ".md", ".csv", ".json", ".py", ".js", ".html", ".css"))
    if is_text_type or is_text_ext:
        try:
            uploaded_file.seek(0)
            return uploaded_file.read().decode("utf-8", errors="replace")[:MAX_CHARS]
        except Exception:
            return ""

    if content_type == "application/pdf" or filename_lower.endswith(".pdf"):
        try:
            from pypdf import PdfReader
            uploaded_file.seek(0)
            reader = PdfReader(io.BytesIO(uploaded_file.read()))
            text = "\n\n".join(page.extract_text() or "" for page in reader.pages)
            return text[:MAX_CHARS]
        except Exception:
            return ""

    if filename_lower.endswith(".docx"):
        try:
            from docx import Document as DocxDocument
            uploaded_file.seek(0)
            doc = DocxDocument(io.BytesIO(uploaded_file.read()))
            return "\n".join(p.text for p in doc.paragraphs if p.text.strip())[:MAX_CHARS]
        except Exception:
            return ""

    # ---- Excel ----
    is_xlsx = filename_lower.endswith(".xlsx") or content_type == "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    if is_xlsx:
        try:
            uploaded_file.seek(0)
            wb = load_workbook(io.BytesIO(uploaded_file.read()), data_only=True, read_only=True)
            parts = []
            for sheet in wb.worksheets:
                parts.append(f"--- Feuille : {sheet.title} ---")
                for row in sheet.iter_rows(values_only=True):
                    cells = ["" if v is None else str(v) for v in row]
                    if any(cells):
                        parts.append(" | ".join(cells))
                if len("\n".join(parts)) > MAX_CHARS:
                    break
            return "\n".join(parts)[:MAX_CHARS]
        except Exception:
            return ""

    # ---- PowerPoint ----
    is_pptx = filename_lower.endswith(".pptx") or content_type == "application/vnd.openxmlformats-officedocument.presentationml.presentation"
    if is_pptx:
        try:
            uploaded_file.seek(0)
            prs = Presentation(io.BytesIO(uploaded_file.read()))
            parts = []
            for i, slide in enumerate(prs.slides, start=1):
                parts.append(f"--- Diapositive {i} ---")
                for shape in slide.shapes:
                    if shape.has_text_frame and shape.text_frame.text.strip():
                        parts.append(shape.text_frame.text)
            return "\n".join(parts)[:MAX_CHARS]
        except Exception:
            return ""

    return ""


TEXT_EXTENSIONS = (".txt", ".md", ".csv", ".json", ".py", ".js", ".html", ".css")


def build_tree(files):
    """Liste plate ordonnée (dossiers avant fichiers) avec profondeur, pour une arborescence repliable."""
    folders = set()
    file_items = []
    for f in files:
        name = f.original_name
        if name.endswith("/.keep"):
            parts = name[:-len("/.keep")].split("/")
            for i in range(1, len(parts) + 1):
                folders.add("/".join(parts[:i]))
            continue
        parts = name.split("/")
        for i in range(1, len(parts)):
            folders.add("/".join(parts[:i]))
        file_items.append(f)

    entries = []
    for path in folders:
        parts = path.split("/")
        entries.append({
            "kind": "folder", "name": parts[-1], "path": path,
            "parent": "/".join(parts[:-1]), "indent": (len(parts) - 1) * 14,
            "_key": [(0, p) for p in parts],
        })
    for f in file_items:
        parts = f.original_name.split("/")
        lower = f.original_name.lower()
        entries.append({
            "kind": "file", "file": f, "name": parts[-1],
            "parent": "/".join(parts[:-1]), "indent": (len(parts) - 1) * 14,
            "editable": lower.endswith(TEXT_EXTENSIONS) or (f.content_type or "").startswith("text/"),
            "previewable": lower.endswith(".html"),
            "_key": [(0, p) for p in parts[:-1]] + [(1, parts[-1])],
        })
    entries.sort(key=lambda e: e["_key"])
    return entries


MAX_TEXT_FILE_BYTES = 1_000_000


class StorageQuotaError(Exception):
    pass


def _limit_bytes():
    return int(getattr(settings, "BARA_USER_STORAGE_LIMIT_MB", 50) * 1024 * 1024)


def _limit_files():
    return int(getattr(settings, "BARA_USER_FILE_LIMIT", 300))


def user_storage_used(user):
    files = WorkFile.objects.filter(workspace__user=user).aggregate(t=Sum("size_bytes"))["t"] or 0
    versions = FileVersion.objects.filter(file__workspace__user=user).aggregate(t=Sum("size_bytes"))["t"] or 0
    return files + versions


def storage_summary(user):
    used, limit = user_storage_used(user), _limit_bytes()
    return {
        "used_mb": round(used / 1048576, 1),
        "limit_mb": round(limit / 1048576),
        "percent": min(100, round(used * 100 / limit)),
    }


def check_quota(user, extra_bytes=0, new_files=0):
    if new_files and WorkFile.objects.filter(workspace__user=user).count() + new_files > _limit_files():
        raise StorageQuotaError(f"Limite de {_limit_files()} fichiers atteinte. Supprime des fichiers pour continuer.")
    if extra_bytes > 0 and user_storage_used(user) + extra_bytes > _limit_bytes():
        raise StorageQuotaError(
            f"Espace de stockage plein ({round(_limit_bytes() / 1048576)} Mo). Supprime des fichiers pour continuer."
        )