"""Registro das tools do MCP.

Cada modulo expoe TOOLS = [(definicao_json_schema, handler), ...].
O handler recebe (TrelloContext, arguments) e devolve um objeto serializavel.
"""

from . import activity, boards, cards, checklists, deletion, members, search, structure
from .common import ToolError, TrelloContext

_MODULES = (boards, search, cards, checklists, structure, members, activity, deletion)

TOOL_DEFINITIONS: dict[str, dict] = {}
_HANDLERS: dict[str, callable] = {}

for _module in _MODULES:
    for _definition, _handler in _module.TOOLS:
        TOOL_DEFINITIONS[_definition["name"]] = _definition
        _HANDLERS[_definition["name"]] = _handler


def list_tools() -> list[dict]:
    return list(TOOL_DEFINITIONS.values())


def has_tool(name: str) -> bool:
    return name in _HANDLERS


def call_tool(client, name: str, arguments: dict | None):
    """Executa uma tool com um contexto novo (caches valem por chamada)."""
    if name not in _HANDLERS:
        raise ToolError(f"Tool {name!r} nao existe.")
    return _HANDLERS[name](TrelloContext(client), arguments or {})


__all__ = ["TOOL_DEFINITIONS", "ToolError", "call_tool", "has_tool", "list_tools"]
