import asyncio
import ipaddress
import json
import socket
from contextlib import asynccontextmanager
from urllib.parse import urlparse

import httpx
from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client


MAX_TOOL_RESULT_CHARS = 20_000
MAX_MCP_TOOLS = 50
MAX_TOOL_SCHEMA_CHARS = 12_000


def validate_mcp_endpoint(endpoint):
    try:
        parsed = urlparse(endpoint)
        port = parsed.port
    except (TypeError, ValueError):
        return "URL MCP invalide."
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
        return "Le serveur MCP doit utiliser une URL HTTPS valide, sans identifiant intégré ni fragment."
    if port not in (None, 443):
        return "Seul le port HTTPS standard (443) est autorisé."
    try:
        addresses = socket.getaddrinfo(parsed.hostname, 443, type=socket.SOCK_STREAM)
    except socket.gaierror:
        return "Le nom de domaine du serveur MCP est introuvable."
    if not addresses:
        return "Le nom de domaine du serveur MCP ne possède aucune adresse valide."
    for address in addresses:
        ip = ipaddress.ip_address(address[4][0].split("%")[0])
        if ip.version == 6 and ip.ipv4_mapped:
            ip = ip.ipv4_mapped
        if not ip.is_global:
            return "Les serveurs MCP hébergés sur un réseau privé ou réservé ne sont pas autorisés."
    return None


def _http_client_factory(headers=None, timeout=None, auth=None):
    return httpx.AsyncClient(
        headers=headers,
        timeout=timeout or httpx.Timeout(20.0, read=30.0),
        auth=auth,
        follow_redirects=False,
        trust_env=False,
    )


@asynccontextmanager
async def _open_session(endpoint, bearer_token):
    headers = {"Authorization": f"Bearer {bearer_token}"} if bearer_token else None
    async with streamablehttp_client(
        endpoint,
        headers=headers,
        timeout=20,
        sse_read_timeout=30,
        httpx_client_factory=_http_client_factory,
    ) as (read_stream, write_stream, _):
        async with ClientSession(read_stream, write_stream) as session:
            await session.initialize()
            yield session


async def _list_tools(endpoint, bearer_token):
    async with _open_session(endpoint, bearer_token) as session:
        result = await session.list_tools()
        tools = []
        for tool in result.tools[:MAX_MCP_TOOLS]:
            schema = tool.inputSchema
            if not isinstance(schema, dict) or schema.get("type") != "object":
                schema = {"type": "object", "properties": {}}
            if len(json.dumps(schema, ensure_ascii=False)) > MAX_TOOL_SCHEMA_CHARS:
                continue
            tools.append({
                "name": tool.name[:100],
                "description": (tool.description or "")[:1000],
                "inputSchema": schema,
            })
        return tools


async def _call_tool(endpoint, bearer_token, tool_name, arguments):
    async with _open_session(endpoint, bearer_token) as session:
        result = await session.call_tool(tool_name, arguments)
        content = []
        for item in result.content[:20]:
            if hasattr(item, "text"):
                content.append({"type": "text", "text": item.text[:5000]})
            else:
                content.append({"type": getattr(item, "type", "resource")})
        payload = {"is_error": result.isError, "content": content}
        text = str(payload)
        if len(text) > MAX_TOOL_RESULT_CHARS:
            payload = {"is_error": result.isError, "content": text[:MAX_TOOL_RESULT_CHARS], "truncated": True}
        return payload


def list_mcp_tools(server):
    error = validate_mcp_endpoint(server.endpoint)
    if error:
        raise ValueError(error)
    return asyncio.run(_list_tools(server.endpoint, server.get_bearer_token()))


def call_mcp_tool(server, tool_name, arguments):
    error = validate_mcp_endpoint(server.endpoint)
    if error:
        return {"error": error}
    try:
        return asyncio.run(_call_tool(server.endpoint, server.get_bearer_token(), tool_name, arguments))
    except Exception as exc:
        return {"error": f"Erreur MCP ({server.name}): {str(exc)[:500]}"}
