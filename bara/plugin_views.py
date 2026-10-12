import base64
import hashlib
import json
import secrets
import time
from datetime import timedelta
from pathlib import Path
from urllib.parse import urlencode

from django.conf import settings
from django.http import FileResponse, HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_GET, require_POST
from django.contrib.auth.decorators import login_required

from .models import Workspace, WorkspacePluginConnection
from .plugin_services import (
    BLENDER_RESULT_LIMIT,
    PluginError,
    blender_token_digest,
    claim_blender_task,
    complete_blender_task,
    email_account_address,
    email_oauth_config,
    email_oauth_is_configured,
    exchange_email_oauth_code,
)


OAUTH_SESSION_KEY = "bara_plugin_oauth_states"
OAUTH_STATE_MAX_AGE = 600
BLENDER_ADDON_PATH = Path(settings.BASE_DIR) / "static" / "app" / "plugins" / "blender_2ai_addon.py"


def _workspace_url(workspace_id, query=None):
    conversation_id = (
        Workspace.objects.filter(id=workspace_id)
        .values_list("conversations__id", flat=True)
        .first()
    )
    url = (
        reverse("bara:conversation", args=[workspace_id, conversation_id])
        if conversation_id
        else reverse("bara:detail", args=[workspace_id])
    )
    return f"{url}?{urlencode(query)}" if query else url


@login_required
@require_GET
def connect_email(request, workspace_id, plugin):
    workspace = get_object_or_404(Workspace, id=workspace_id, user=request.user)
    if plugin not in {"google_mail", "microsoft_mail", "google_drive", "google_calendar"}:
        return HttpResponse("Fournisseur e-mail inconnu.", status=404)
    if not email_oauth_is_configured(plugin):
        return redirect(_workspace_url(workspace.id, {"automation_error": "oauth_not_configured"}))

    config = email_oauth_config(plugin)
    state = secrets.token_urlsafe(32)
    code_verifier = secrets.token_urlsafe(48)
    code_challenge = base64.urlsafe_b64encode(hashlib.sha256(code_verifier.encode()).digest()).decode().rstrip("=")
    states = request.session.get(OAUTH_SESSION_KEY, {})
    if not isinstance(states, dict):
        states = {}
    now = int(time.time())
    states = {
        key: value
        for key, value in states.items()
        if isinstance(key, str)
        and isinstance(value, dict)
        and isinstance(value.get("created_at"), int)
        and now - value["created_at"] < OAUTH_STATE_MAX_AGE
    }
    states[state] = {
        "workspace_id": workspace.id,
        "user_id": request.user.id,
        "plugin": plugin,
        "code_verifier": code_verifier,
        "created_at": now,
    }
    request.session[OAUTH_SESSION_KEY] = dict(list(states.items())[-5:])
    redirect_uri = request.build_absolute_uri(
        reverse("bara:email-oauth-callback", args=[plugin])
    )
    params = {
        "client_id": config["client_id"],
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": " ".join(config["scopes"]),
        "state": state,
        "code_challenge": code_challenge,
        "code_challenge_method": "S256",
    }
    if plugin.startswith("google_"):
        params.update({"access_type": "offline", "prompt": "consent"})
    return redirect(f"{config['authorize_url']}?{urlencode(params)}")


@login_required
@require_GET
def email_oauth_callback(request, plugin):
    if plugin not in {"google_mail", "microsoft_mail", "google_drive", "google_calendar"}:
        return HttpResponse("Fournisseur e-mail inconnu.", status=404)
    state = request.GET.get("state", "")
    states = request.session.get(OAUTH_SESSION_KEY, {})
    if not isinstance(states, dict):
        states = {}
    state_data = states.pop(state, None) if state else None
    request.session[OAUTH_SESSION_KEY] = states
    created_at = state_data.get("created_at") if isinstance(state_data, dict) else None
    if (
        not isinstance(state_data, dict)
        or not isinstance(state_data.get("workspace_id"), int)
        or state_data.get("user_id") != request.user.id
        or state_data.get("plugin") != plugin
        or isinstance(created_at, bool)
        or not isinstance(created_at, int)
        or int(time.time()) - created_at >= OAUTH_STATE_MAX_AGE
        or int(time.time()) < created_at
        or not isinstance(state_data.get("code_verifier"), str)
    ):
        return HttpResponse("État OAuth invalide ou expiré.", status=400)

    workspace = get_object_or_404(Workspace, id=state_data["workspace_id"], user=request.user)
    if request.GET.get("error"):
        return redirect(_workspace_url(workspace.id, {"automation_error": "oauth_cancelled"}))
    code = request.GET.get("code", "")
    if not code or len(code) > 4096:
        return HttpResponse("Code OAuth manquant ou invalide.", status=400)

    redirect_uri = request.build_absolute_uri(
        reverse("bara:email-oauth-callback", args=[plugin])
    )
    try:
        token_data = exchange_email_oauth_code(
            plugin,
            code,
            redirect_uri,
            state_data["code_verifier"],
        )
        access_token = token_data["access_token"]
        address = email_account_address(plugin, access_token)
    except (PluginError, KeyError):
        return redirect(_workspace_url(workspace.id, {"automation_error": "oauth_failed"}))

    try:
        expires_in = max(60, int(token_data.get("expires_in", 3600)))
    except (TypeError, ValueError):
        return redirect(_workspace_url(workspace.id, {"automation_error": "oauth_failed"}))
    connection, _ = WorkspacePluginConnection.objects.get_or_create(
        workspace=workspace,
        plugin=plugin,
    )
    connection.account_email = address
    connection.set_oauth_tokens(access_token, token_data.get("refresh_token"))
    connection.access_token_expires_at = timezone.now() + timedelta(seconds=expires_in)
    connection.is_enabled = True
    connection.save()
    return redirect(_workspace_url(workspace.id, {"automation_status": f"{plugin}_connected"}))


@login_required
@require_POST
def disconnect_plugin(request, workspace_id, plugin):
    workspace = get_object_or_404(Workspace, id=workspace_id, user=request.user)
    if plugin not in {"google_mail", "microsoft_mail", "blender", "google_drive", "google_calendar", "github"}:
        return JsonResponse({"error": "Plugin inconnu."}, status=404)
    WorkspacePluginConnection.objects.filter(workspace=workspace, plugin=plugin).delete()
    return redirect(_workspace_url(workspace.id, {"automation_status": f"{plugin}_disconnected"}))


@login_required
@require_POST
def pair_blender(request, workspace_id):
    workspace = get_object_or_404(Workspace, id=workspace_id, user=request.user)
    if not request.is_secure() and not settings.DEBUG:
        return JsonResponse({"error": "La connexion Blender exige HTTPS en production."}, status=400)
    token = secrets.token_urlsafe(32)
    connection, _ = WorkspacePluginConnection.objects.get_or_create(
        workspace=workspace,
        plugin="blender",
    )
    connection.blender_token_hash = blender_token_digest(token)
    connection.last_seen_at = None
    connection.is_enabled = True
    connection.save()
    addon_url = request.build_absolute_uri(reverse("bara:download-blender-addon"))
    return JsonResponse({
        "token": token,
        "backend_url": request.build_absolute_uri("/").rstrip("/"),
        "addon_url": addon_url,
    })


@login_required
@require_GET
def download_blender_addon(_request):
    if not BLENDER_ADDON_PATH.is_file():
        return HttpResponse("Le module complémentaire Blender est indisponible.", status=404)
    return FileResponse(
        BLENDER_ADDON_PATH.open("rb"),
        as_attachment=True,
        filename="blender_2ai_addon.py",
        content_type="text/x-python",
    )


def _blender_connection_from_request(request):
    authorization = request.headers.get("Authorization", "")
    if not authorization.startswith("Bearer "):
        return None
    token = authorization[7:].strip()
    if not token or len(token) > 200:
        return None
    return WorkspacePluginConnection.objects.filter(
        plugin="blender",
        is_enabled=True,
        blender_token_hash=blender_token_digest(token),
    ).first()


@csrf_exempt
@require_GET
def blender_poll(request):
    connection = _blender_connection_from_request(request)
    if not connection:
        return JsonResponse({"error": "Jeton Blender invalide."}, status=401)
    connection.last_seen_at = timezone.now()
    connection.save(update_fields=["last_seen_at", "updated_at"])
    task = claim_blender_task(connection)
    if not task:
        return HttpResponse(status=204)
    return JsonResponse({"task": task})


@csrf_exempt
@require_POST
def blender_complete(request):
    connection = _blender_connection_from_request(request)
    if not connection:
        return JsonResponse({"error": "Jeton Blender invalide."}, status=401)
    if len(request.body) > BLENDER_RESULT_LIMIT:
        return JsonResponse({"error": "Le résultat Blender est trop volumineux."}, status=413)
    try:
        payload = json.loads(request.body)
    except (TypeError, ValueError):
        return JsonResponse({"error": "Corps JSON invalide."}, status=400)
    task_id = payload.get("task_id") if isinstance(payload, dict) else None
    if isinstance(task_id, bool) or not isinstance(task_id, int):
        return JsonResponse({"error": "Identifiant de tâche invalide."}, status=400)
    try:
        task = complete_blender_task(
            connection,
            task_id,
            payload.get("status"),
            payload.get("result"),
        )
    except PluginError as error:
        return JsonResponse({"error": str(error)}, status=400)
    return JsonResponse({"status": task.status})
