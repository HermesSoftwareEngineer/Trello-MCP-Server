"""Busca global no Trello, com filtros pos-processados."""

from .common import (
    CARD_FIELDS,
    ToolError,
    as_bool_param,
    as_list,
    card_created_at,
    filter_cards,
    join_ids,
    truncate,
)
from .schemas import CARD_FILTERS, OUTPUT_OPTIONS, READ_ONLY

SEARCH_TOOL = {
    "name": "search",
    "description": (
        "Busca cards, boards, membros e organizacoes por texto livre. Use quando nao "
        "souber em qual board algo esta, ou para consultas amplas entre boards.\n\n"
        "'query' aceita a sintaxe de busca do Trello, que ja resolve muita coisa sem "
        "filtros extras: '@me' (atribuidos a voce), 'due:day' / 'due:week' / 'due:overdue', "
        "'created:week', 'list:\"Em andamento\"', 'label:red', 'board:Nome', 'is:archived', "
        "'has:attachments', '-termo' para excluir. Combine livremente: "
        "'@me due:week -label:red'.\n\n"
        "Os filtros estruturados em 'filters' sao aplicados depois, sobre o resultado, "
        "e permitem cortes que a sintaxe do Trello nao cobre (intervalos de data exatos, "
        "label_match='all', etc)."
    ),
    "annotations": {"title": "Buscar no Trello", **READ_ONLY},
    "inputSchema": {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "Texto e/ou operadores da busca do Trello. Obrigatorio.",
            },
            "scope": {
                "type": "array",
                "items": {"type": "string", "enum": ["cards", "boards", "members", "organizations"]},
                "default": ["cards"],
                "description": "O que procurar.",
            },
            "boards": {
                "type": "array", "items": {"type": "string"},
                "description": "Restringe a busca a estes boards (ids ou nomes).",
            },
            "archived_only": {
                "type": "boolean", "default": False,
                "description": (
                    "Busca APENAS itens arquivados (equivale a 'is:archived' na query). "
                    "A busca do Trello nao consegue misturar ativos e arquivados no mesmo "
                    "resultado -- para os dois, faca duas chamadas."
                ),
            },
            "partial_match": {
                "type": "boolean", "default": True,
                "description": "true casa palavras parciais ('proj' acha 'projeto').",
            },
            "filters": CARD_FILTERS,
            "limit": {"type": "integer", "default": 50, "description": "Maximo de cards (ate 1000)."},
            **OUTPUT_OPTIONS,
        },
        "required": ["query"],
        "additionalProperties": False,
    },
}


def search(ctx, args):
    query = (args.get("query") or "").strip()
    if not query:
        raise ToolError("Informe 'query'.")

    scope = as_list(args.get("scope")) or ["cards"]
    limit = min(int(args.get("limit") or 50), 1000)
    board_ids = ctx.resolve_board_ids(args.get("boards"))

    params = {
        "query": query,
        "modelTypes": join_ids(scope),
        "partial": as_bool_param(args.get("partial_match", True)),
        "cards_limit": limit,
        "boards_limit": min(limit, 100),
        "card_fields": CARD_FIELDS,
        "card_board": "true",
        "card_list": "true",
        "card_members": "true",
        "board_fields": "id,name,closed,shortUrl,dateLastActivity",
        "member_fields": "id,username,fullName",
    }
    if board_ids:
        params["idBoards"] = join_ids(board_ids)
    if args.get("archived_only") and "is:archived" not in query:
        params["query"] = f"{query} is:archived"

    raw = ctx.client.request("GET", "/search", params=params) or {}
    response = {"query": query}

    if "cards" in scope:
        cards = raw.get("cards") or []
        filters = args.get("filters") or {}

        needs_board_ctx = bool(filters.get("lists") or filters.get("labels"))
        filter_board = board_ids[0] if len(board_ids) == 1 else None
        if needs_board_ctx and not filter_board:
            raise ToolError(
                "Os filtros 'lists' e 'labels' precisam de um board de referencia. "
                "Informe exatamente um board em 'boards', ou use os operadores "
                "'list:' / 'label:' dentro de 'query'."
            )

        found = len(cards)
        cards = filter_cards(cards, filters, ctx, filter_board)

        response["cards"] = {
            "found_by_query": found,
            "after_filters": len(cards),
            "items": [_render_search_card(card, args) for card in cards],
        }

    if "boards" in scope:
        response["boards"] = [
            {
                "id": board.get("id"),
                "name": board.get("name"),
                "closed": board.get("closed"),
                "url": board.get("shortUrl"),
                "last_activity": board.get("dateLastActivity"),
            }
            for board in (raw.get("boards") or [])
        ]

    if "members" in scope:
        response["members"] = [
            {"id": m.get("id"), "username": m.get("username"), "name": m.get("fullName")}
            for m in (raw.get("members") or [])
        ]

    if "organizations" in scope:
        response["organizations"] = [
            {"id": o.get("id"), "name": o.get("displayName") or o.get("name")}
            for o in (raw.get("organizations") or [])
        ]

    return response


def _render_search_card(card: dict, args: dict) -> dict:
    """A busca ja devolve board/list/members aninhados -- aproveita isso."""
    if args.get("include_raw"):
        return card

    desc_max = args.get("desc_max_chars", 500)
    desc_max = None if desc_max == 0 else desc_max
    badges = card.get("badges") or {}
    checkitems = badges.get("checkItems") or 0

    entry = {
        "id": card.get("id"),
        "name": card.get("name"),
        "board": (card.get("board") or {}).get("name"),
        "board_id": card.get("idBoard"),
        "list": (card.get("list") or {}).get("name"),
        "list_id": card.get("idList"),
        "members": [m.get("username") for m in (card.get("members") or [])]
                   or card.get("idMembers") or [],
        "label_ids": card.get("idLabels") or [],
        "due": card.get("due"),
        "due_complete": card.get("dueComplete"),
        "closed": card.get("closed"),
        "url": card.get("shortUrl"),
        "created_at": card_created_at(card.get("id", "")),
        "last_activity": card.get("dateLastActivity"),
        "counts": {
            "comments": badges.get("comments", 0),
            "attachments": badges.get("attachments", 0),
            "checkitems": f"{badges.get('checkItemsChecked', 0)}/{checkitems}" if checkitems else None,
        },
    }
    if args.get("include_desc", True):
        entry["desc"] = truncate(card.get("desc"), desc_max)
    return entry


TOOLS = [(SEARCH_TOOL, search)]
