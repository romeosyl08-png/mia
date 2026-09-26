import json

from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from chat.services import ProviderError, estimate_cost, credit_multiplier
from api.models import AIModel
from ai_user.models import InsufficientCreditsError

from .models import Workspace, WorkFile, WorkMessage
from .services import extract_text, get_workspace_context, call_llm_with_tools
from .tools.registry import get_tools
from .tools.executor import execute_tool
from .tools import definitions  # noqa: F401 — déclenche l'enregistrement des outils via les décorateurs
from django.http import FileResponse
from chat.services import is_local_provider


MAX_AGENT_ITERATIONS = 5


@login_required
def workspace_home(request):
    workspaces = Workspace.objects.filter(user=request.user)
    return render(request, "bara/workspace.html", {"workspaces": workspaces})


@login_required
@require_POST
def create_workspace(request):
    title = (request.POST.get("title") or "").strip() or "Nouvel espace de travail"
    workspace = Workspace.objects.create(user=request.user, title=title)
    return redirect("bara:detail", workspace_id=workspace.id)


@login_required
def workspace_detail(request, workspace_id):
    workspace = get_object_or_404(Workspace, id=workspace_id, user=request.user)
    messages = workspace.messages.all()
    files = workspace.files.all()

    return render(request, "bara/workspace.html", {
    "workspace": workspace,
    "messages": messages,
    "files": files,
    "credits": request.user.credit_account.balance,
    "available_models": AIModel.objects.filter(is_active=True, provider__is_active=True).select_related('provider'),
    })


@login_required
@require_POST
def upload_file(request, workspace_id):
    workspace = get_object_or_404(Workspace, id=workspace_id, user=request.user)
    uploaded = request.FILES.get("file")
    if not uploaded:
        return JsonResponse({"error": "Aucun fichier reçu."}, status=400)

    MAX_SIZE = 10 * 1024 * 1024
    if uploaded.size > MAX_SIZE:
        return JsonResponse({"error": "Fichier trop volumineux (10 Mo maximum)."}, status=400)

    content_type = uploaded.content_type or ""
    extracted_text = extract_text(uploaded, content_type)
    uploaded.seek(0)

    work_file = WorkFile.objects.create(
        workspace=workspace,
        file=uploaded,
        original_name=uploaded.name,
        content_type=content_type,
        size_bytes=uploaded.size,
        extracted_text=extracted_text,
    )

    return JsonResponse({
        "id": work_file.id,
        "name": work_file.original_name,
        "size": work_file.size_bytes,
        "has_text": bool(extracted_text),
    })


def _tools_schema():
    return [
        {
            "type": "function",
            "function": {
                "name": t["name"],
                "description": t["description"],
                "parameters": t["parameters"],
            },
        }
        for t in get_tools()
    ]


from django.http import StreamingHttpResponse
from .services import call_llm_with_tools_stream


def _sse(event_type, data):
    return f"data: {json.dumps({'type': event_type, **data}, ensure_ascii=False)}\n\n"


@login_required
@require_POST
def send_message(request, workspace_id):
    workspace = get_object_or_404(Workspace, id=workspace_id, user=request.user)
    content = (request.POST.get("content") or "").strip()
    if not content:
        return JsonResponse({"error": "Message vide."}, status=400)

    active_model = workspace.active_model or AIModel.objects.filter(is_active=True, provider__is_active=True).first()
    if not active_model:
        return JsonResponse({"error": "Aucun modèle actif n'est configuré."}, status=503)

    api_key_obj = active_model.provider.keys.filter(is_active=True).order_by("id").first()
    if not api_key_obj and not is_local_provider(active_model.provider):
        return JsonResponse({"error": "Aucune clé API active pour ce fournisseur."}, status=503)
    api_key = api_key_obj.get_key() if api_key_obj else ""

    past_messages = list(workspace.messages.filter(role__in=("user", "assistant")).order_by("created_at").values("role", "content"))
    messages_payload = list(past_messages)
    messages_payload.append({"role": "user", "content": content})

    workspace_context = get_workspace_context(workspace)
    system_prompt = (
    "Tu es un assistant intégré à un espace de travail nommé Bara. Tu as accès à ces outils : "
    "`list_files`, `read_file`, `write_file`, `create_folder`, `web_search`. "
    "Les dossiers sont virtuels (un fichier nommé 'dossier/fichier.txt' vit dans le dossier 'dossier'). "
    "Tu peux chercher des informations actuelles sur le web avec `web_search` quand c'est pertinent. "
    "Ne décris jamais une fonctionnalité qui n'existe pas dans cette liste d'outils — si tu ne peux pas faire "
    "quelque chose avec ces outils, dis-le clairement plutôt que d'inventer une procédure."
    )
    
    if workspace_context:
        system_prompt += "\n\n" + workspace_context

    reserve, estimated_input = estimate_cost(content, active_model, system_prompt, past_messages)
    reserve *= MAX_AGENT_ITERATIONS
    account = request.user.credit_account
    try:
        account.debit(reserve, reason='usage', note=f'Bara agent — {active_model.model_id}')
    except InsufficientCreditsError:
        return JsonResponse({"error": {"message": "Crédits insuffisants pour ce message.", "type": "insufficient_credits"}}, status=402)

    WorkMessage.objects.create(workspace=workspace, role="user", content=content)

    def event_stream():
        iteration = 0
        final_text_parts = []
        total_prompt_tokens = 0
        total_completion_tokens = 0

        try:
            while iteration < MAX_AGENT_ITERATIONS:
                iteration += 1
                pending_tool_calls = {}  # index -> {"id":.., "name":.., "arguments": ""}
                content_this_turn = []

                for event in call_llm_with_tools_stream(
                    provider=active_model.provider,
                    api_key=api_key,
                    model_id=active_model.model_id,
                    messages=messages_payload,
                    system_prompt=system_prompt,
                    tools=_tools_schema(),
                ):
                    if event["type"] == "delta":
                        content_this_turn.append(event["content"])
                        final_text_parts.append(event["content"])
                        yield _sse("token", {"text": event["content"]})

                    elif event["type"] == "tool_call_delta":
                        idx = event["index"]
                        entry = pending_tool_calls.setdefault(idx, {"id": None, "name": "", "arguments": ""})
                        if event.get("id"):
                            entry["id"] = event["id"]
                        if event.get("name"):
                            entry["name"] = event["name"]
                        entry["arguments"] += event.get("arguments_delta") or ""

                    elif event["type"] == "done":
                        usage = event.get("usage") or {}
                        total_prompt_tokens += usage.get("prompt_tokens", 0)
                        total_completion_tokens += usage.get("completion_tokens", 0)

                assistant_text = "".join(content_this_turn)
                tool_calls_list = [
                    {"id": v["id"], "type": "function", "function": {"name": v["name"], "arguments": v["arguments"]}}
                    for v in pending_tool_calls.values()
                ]

                assistant_msg = {"role": "assistant", "content": assistant_text or None}
                if tool_calls_list:
                    assistant_msg["tool_calls"] = tool_calls_list
                messages_payload.append(assistant_msg)

                if not tool_calls_list:
                    break  # réponse finale donnée, pas d'outil demandé

                for tc in tool_calls_list:
                    tool_name = tc["function"]["name"]
                    try:
                        tool_args = json.loads(tc["function"]["arguments"] or "{}")
                    except (TypeError, ValueError):
                        tool_args = {}

                    yield _sse("step", {"status": "calling_tool", "tool": tool_name})
                    tool_result = execute_tool(tool_name, workspace, tool_args)
                    yield _sse("step", {"status": "tool_done", "tool": tool_name})

                    messages_payload.append({
                        "role": "tool",
                        "tool_call_id": tc["id"],
                        "name": tool_name,
                        "content": json.dumps(tool_result, ensure_ascii=False),
                    })
            else:
                yield _sse("token", {"text": "\n\n(Limite d'étapes atteinte.)"})
                final_text_parts.append("\n\n(Limite d'étapes atteinte.)")

        except ProviderError as error:
            account.credit(reserve, reason='adjustment', note='Remboursement — erreur agent')
            yield _sse("error", {"message": str(error)})
            return

        final_reply_text = "".join(final_text_parts)
        actual_cost = max(1, (total_prompt_tokens + total_completion_tokens)) * credit_multiplier(active_model)
        if actual_cost < reserve:
            account.credit(reserve - actual_cost, reason='adjustment', note=f'Ajustement Bara {active_model.model_id}')

        WorkMessage.objects.create(workspace=workspace, role="assistant", content=final_reply_text)

        yield _sse("done", {"remaining_balance": account.balance, "iterations_used": iteration})

    response = StreamingHttpResponse(event_stream(), content_type="text/event-stream")
    response["Cache-Control"] = "no-cache"
    response["X-Accel-Buffering"] = "no"  # évite le buffering si jamais tu passes derrière nginx plus tard
    return response


@login_required
@require_POST
def update_model(request, workspace_id):
    workspace = get_object_or_404(Workspace, id=workspace_id, user=request.user)
    model_id = request.POST.get("active_model_id")
    workspace.active_model = AIModel.objects.filter(id=model_id, is_active=True).first() if model_id else None
    workspace.save(update_fields=["active_model", "updated_at"])
    return redirect("bara:detail", workspace_id=workspace.id)

@login_required
def download_file(request, workspace_id, file_id):
    workspace = get_object_or_404(Workspace, id=workspace_id, user=request.user)
    work_file = get_object_or_404(WorkFile, id=file_id, workspace=workspace)
    return FileResponse(work_file.file.open('rb'), as_attachment=True, filename=work_file.original_name)