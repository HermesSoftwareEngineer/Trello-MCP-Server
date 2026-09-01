import json
import logging

from flask import Blueprint, g, jsonify, request

from .auth import AuthError, client_for_user, extract_bearer_token, resolve_bearer
from .config import Config
from .crypto import CryptoError
from .tools import ToolError, call_tool, has_tool, list_tools
from .trello_client import TrelloApiError, TrelloAuthError

logger = logging.getLogger(__name__)

mcp_bp = Blueprint("mcp", __name__)

PROTOCOL_VERSION = "2025-06-18"

SERVER_INFO = {
    "name": "trello-mcp-server",
    "version": "0.1.0",
}

JSONRPC_PARSE_ERROR = -32700
JSONRPC_INVALID_REQUEST = -32600
JSONRPC_METHOD_NOT_FOUND = -32601
JSONRPC_INTERNAL_ERROR = -32603
JSONRPC_UNAUTHORIZED = -32001


def _result(request_id, result):
    return {"jsonrpc": "2.0", "id": request_id, "result": result}


def _error(request_id, code, message):
    return {"jsonrpc": "2.0", "id": request_id, "error": {"code": code, "message": message}}


def _tool_content(payload, *, is_error: bool = False):
    """Resultado de tool no formato do MCP: texto JSON legivel pelo modelo."""
    text = payload if isinstance(payload, str) else json.dumps(
        payload, ensure_ascii=False, indent=2, default=str
    )
    return {"content": [{"type": "text", "text": text}], "isError": is_error}


@mcp_bp.post("/mcp")
def mcp_endpoint():
    payload = request.get_json(silent=True)
    if payload is None:
        return jsonify(_error(None, JSONRPC_PARSE_ERROR, "Parse error")), 400

    method = payload.get("method")
    request_id = payload.get("id")
    params = payload.get("params") or {}

    if not method:
        return jsonify(_error(request_id, JSONRPC_INVALID_REQUEST, "Missing 'method'")), 400

    # Toda requisicao MCP e autenticada: o Bearer (connector token manual ou
    # access token OAuth) identifica de qual usuario sao as credenciais do
    # Trello usadas na chamada.
    try:
        g.user = resolve_bearer(extract_bearer_token(request.headers.get("Authorization")))
    except AuthError as exc:
        response = jsonify(_error(request_id, JSONRPC_UNAUTHORIZED, str(exc)))
        response.headers["WWW-Authenticate"] = (
            'Bearer realm="trello-mcp", '
            f'resource_metadata="{Config.PUBLIC_BASE_URL}/.well-known/oauth-protected-resource"'
        )
        return response, 401

    if method == "initialize":
        return jsonify(
            _result(
                request_id,
                {
                    "protocolVersion": PROTOCOL_VERSION,
                    "capabilities": {"tools": {"listChanged": False}},
                    "serverInfo": SERVER_INFO,
                },
            )
        )

    if method == "notifications/initialized":
        return "", 204

    if method == "ping":
        return jsonify(_result(request_id, {}))

    if method == "tools/list":
        return jsonify(_result(request_id, {"tools": list_tools()}))

    if method == "tools/call":
        return _handle_tools_call(request_id, params)

    return jsonify(_error(request_id, JSONRPC_METHOD_NOT_FOUND, f"Method '{method}' not found")), 404


def _handle_tools_call(request_id, params):
    tool_name = params.get("name")
    arguments = params.get("arguments") or {}

    if not has_tool(tool_name):
        return jsonify(
            _error(request_id, JSONRPC_METHOD_NOT_FOUND, f"Tool '{tool_name}' not found")
        ), 404

    try:
        client = client_for_user(g.user)
    except CryptoError as exc:
        return jsonify(_error(request_id, JSONRPC_UNAUTHORIZED, str(exc))), 401

    # Erros de tool voltam como resultado com isError, e nao como erro de
    # protocolo: assim o modelo enxerga a mensagem e pode se corrigir.
    try:
        result = call_tool(client, tool_name, arguments)
    except ToolError as exc:
        return jsonify(_result(request_id, _tool_content(f"Erro de uso: {exc}", is_error=True)))
    except TrelloAuthError as exc:
        return jsonify(_result(request_id, _tool_content(
            f"O Trello recusou as credenciais desta conta. Reconecte no painel. Detalhe: {exc}",
            is_error=True,
        )))
    except TrelloApiError as exc:
        return jsonify(_result(request_id, _tool_content(
            f"Erro na API do Trello: {exc}", is_error=True
        )))
    except Exception as exc:  # noqa: BLE001 - qualquer falha vira feedback para o modelo
        logger.exception("Falha inesperada na tool %s", tool_name)
        return jsonify(_result(request_id, _tool_content(
            f"Falha inesperada em '{tool_name}': {type(exc).__name__}: {exc}", is_error=True
        )))

    return jsonify(_result(request_id, _tool_content(result)))
