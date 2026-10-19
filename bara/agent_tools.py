"""Shared tool availability, dispatch and journaling for Bara and chat."""
import json
from copy import deepcopy
from datetime import timedelta

from django.conf import settings
from django.utils import timezone

from .models import ToolExecution
from .tools.registry import get_tools
from .tools.executor import execute_tool
from .tool_reliability import verify_file_action
from .tools import definitions, plugins, visualizations, documents, ocr, connectors  # noqa: F401
from .tools.plugins import BLENDER_PLUGIN_TOOL_NAMES, EMAIL_PLUGIN_TOOL_NAMES, PLUGIN_TOOL_NAMES
from .tools.connectors import CONNECTOR_TOOLS
from .plugin_services import BLENDER_HEARTBEAT_SECONDS

CODE_EXECUTION_TOOLS = frozenset({'run_python', 'run_command', 'extract_document_text'})
PUBLIC_CHAT_TOOLS = frozenset({'web_search', 'fetch_url', 'visualize_chart'})


def _tools_schema(workspace=None, public_only=False):
    disabled = set(workspace.disabled_tools or []) if workspace else set()
    code_execution_enabled = settings.BARA_CODE_EXECUTION_ENABLED
    schemas = [
        {
            "type": "function",
            "function": {
                "name": t["name"],
                "description": t["description"],
                "parameters": t["parameters"],
            },
        }
        for t in get_tools()
        if t["name"] not in PLUGIN_TOOL_NAMES and t["name"] not in CONNECTOR_TOOLS
        if t["name"] not in disabled
        and (code_execution_enabled or t["name"] not in CODE_EXECUTION_TOOLS)
        and (not public_only or t['name'] in PUBLIC_CHAT_TOOLS)
    ]
    mcp_map = {}
    if workspace:
        plugin_connections = {
            connection.plugin: connection
            for connection in workspace.plugin_connections.filter(is_enabled=True)
        }
        email_plugins = [
            plugin for plugin in ("google_mail", "microsoft_mail")
            if plugin in plugin_connections
        ]
        blender_connection = plugin_connections.get("blender")
        blender_online = bool(
            blender_connection
            and blender_connection.last_seen_at
            and timezone.now() - blender_connection.last_seen_at
            <= timedelta(seconds=BLENDER_HEARTBEAT_SECONDS)
        )
        for tool in get_tools():
            name = tool["name"]
            if name in disabled:
                continue
            if name in CONNECTOR_TOOLS and CONNECTOR_TOOLS[name] in plugin_connections:
                schemas.append({'type':'function','function':{'name':name,'description':tool['description'],'parameters':tool['parameters']}})
            elif name in EMAIL_PLUGIN_TOOL_NAMES and email_plugins:
                schema = deepcopy(tool)
                schema["parameters"]["properties"]["provider"]["enum"] = email_plugins
                schemas.append({
                    "type": "function",
                    "function": {
                        "name": schema["name"],
                        "description": schema["description"],
                        "parameters": schema["parameters"],
                    },
                })
            elif name in BLENDER_PLUGIN_TOOL_NAMES and blender_online:
                schemas.append({
                    "type": "function",
                    "function": {
                        "name": name,
                        "description": tool["description"],
                        "parameters": tool["parameters"],
                    },
                })
        for server in workspace.mcp_servers.filter(is_enabled=True):
            for index, tool in enumerate(server.available_tools):
                schema = tool.get("inputSchema")
                if not isinstance(schema, dict) or schema.get("type") != "object":
                    schema = {"type": "object", "properties": {}}
                alias = f"mcp_{server.id}_{index}"
                if alias in disabled:
                    continue
                schemas.append({"type": "function", "function": {
                    "name": alias,
                    "description": f"MCP {server.name}: {tool.get('description') or tool.get('name', 'outil distant')}",
                    "parameters": schema,
                }})
                mcp_map[alias] = (server, tool["name"])
    return schemas, mcp_map


def _shrink_result(result, limit=1500):
    try:
        text = json.dumps(result, ensure_ascii=False)
    except (TypeError, ValueError):
        text = str(result)
    if len(text) <= limit:
        return result if isinstance(result, dict) else {"value": text}
    return {"preview": text[:limit], "truncated": True}


def _log_tool(workspace, name, args, result):
    if workspace is None:
        return
    if isinstance(result, dict) and result.get("status") == "pending_confirmation":
        return  # l'action en attente est déjà enregistrée par l'outil lui-même
    failed = isinstance(result, dict) and "error" in result
    if name in EMAIL_PLUGIN_TOOL_NAMES:
        args = {"provider": args.get("provider", ""), "content_redacted": True} if isinstance(args, dict) else {}
        result = (
            {"error": result["error"]}
            if failed
            else {"status": result.get("status", "completed"), "provider": result.get("provider", "")}
        )
    ToolExecution.objects.create(
        workspace=workspace,
        tool_name=name,
        arguments=args if isinstance(args, dict) else {},
        result=_shrink_result(result),
        status="error" if failed else "success",
    )


def dispatch_tool(workspace, name, arguments, mcp_map, conversation_id=None):
    if not isinstance(arguments, dict):
        return {'error': 'Les arguments de l’outil doivent être un objet JSON.'}
    arguments = dict(arguments)
    if name in {'email_send', 'blender_create_primitive'}:
        # This is a Bara conversation ID, never a chat conversation ID.
        arguments['conversation_id'] = conversation_id
    if name in mcp_map:
        server, remote_name = mcp_map[name]
        action, _ = ToolExecution.objects.get_or_create(
            workspace=workspace, tool_name='mcp_call', status='pending',
            arguments={'server_id': server.id, 'server_name': server.name, 'tool_name': remote_name, 'tool_arguments': arguments, 'conversation_id': conversation_id},
        )
        return {'status': 'pending_confirmation', 'action_id': action.id, 'description': f'Appeler {server.name} / {remote_name}\n' + json.dumps(arguments, ensure_ascii=False), 'mcp': True}
    before = None
    if name == 'edit_file':
        file_id = arguments.get('file_id')
        if not workspace or not isinstance(file_id, int) or isinstance(file_id, bool):
            return {'error': 'Identifiant de fichier invalide.'}
        before = workspace.files.filter(pk=file_id).values_list('extracted_text', flat=True).first()
    result = execute_tool(name, workspace, arguments)
    return verify_file_action(workspace, name, arguments, result, before)
