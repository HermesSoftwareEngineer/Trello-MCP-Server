"""Tools de leitura de boards: listagem e snapshot completo."""

from .common import (
    CARD_FIELDS,
    as_list,
    compact_card,
    custom_field_schema,
    custom_field_values,
    filter_cards,
    name_maps,
    truncate,
)
from .schemas import CARD_FILTERS, OUTPUT_OPTIONS, READ_ONLY, REF

# --------------------------------------------------------------------------
# list_boards
# --------------------------------------------------------------------------

LIST_BOARDS_TOOL = {
    "name": "list_boards",
    "description": (
        "Lista os boards do usuario. Use como ponto de partida quando nao souber "
        "quais boards existem ou precisar do id de um board. "
        "Pode ja trazer listas, labels e membros de cada board em uma unica chamada "
        "(parametro 'include'), evitando idas e voltas."
    ),
    "annotations": {"title": "Listar boards", **READ_ONLY},
    "inputSchema": {
        "type": "object",
        "properties": {
            "filter": {
                "type": "string",
                "enum": ["open", "closed", "starred", "all"],
                "default": "open",
                "description": "open = ativos; closed = arquivados; starred = favoritos.",
            },
            "name_contains": {"type": "string", "description": "Filtra por substring no nome."},
            "include": {
                "type": "array",
                "items": {"type": "string", "enum": ["lists", "labels", "members"]},
                "description": "Dados extras por board. Cada item custa uma chamada por board.",
            },
            "limit": {"type": "integer", "default": 50},
        },
        "additionalProperties": False,
    },
}


def list_boards(ctx, args):
    wanted = (args.get("filter") or "open").lower()
    boards = ctx.my_boards()

    if wanted == "open":
        boards = [b for b in boards if not b.get("closed")]
    elif wanted == "closed":
        boards = [b for b in boards if b.get("closed")]
    elif wanted == "starred":
        boards = [b for b in boards if b.get("starred")]

    needle = (args.get("name_contains") or "").lower()
    if needle:
        boards = [b for b in boards if needle in (b.get("name") or "").lower()]

    limit = args.get("limit") or 50
    truncated = len(boards) > limit
    boards = boards[:limit]

    include = set(as_list(args.get("include")))
    payload = []
    for board in boards:
        entry = {
            "id": board["id"],
            "name": board.get("name"),
            "url": board.get("shortUrl") or board.get("url"),
            "closed": board.get("closed"),
            "starred": board.get("starred"),
            "last_activity": board.get("dateLastActivity"),
        }
        if board.get("desc"):
            entry["desc"] = truncate(board["desc"], 300)
        if "lists" in include:
            entry["lists"] = [
                {"id": item["id"], "name": item["name"], "closed": item.get("closed")}
                for item in ctx.board_lists(board["id"])
            ]
        if "labels" in include:
            entry["labels"] = [
                {"id": lb["id"], "name": lb.get("name"), "color": lb.get("color")}
                for lb in ctx.board_labels(board["id"])
            ]
        if "members" in include:
            entry["members"] = [
                {"id": m["id"], "username": m.get("username"), "name": m.get("fullName")}
                for m in ctx.board_members(board["id"])
            ]
        payload.append(entry)

    return {
        "count": len(payload),
        "truncated": truncated,
        "boards": payload,
    }


# --------------------------------------------------------------------------
# get_board_snapshot
# --------------------------------------------------------------------------

SNAPSHOT_TOOL = {
    "name": "get_board_snapshot",
    "description": (
        "Retrato completo de um board em UMA chamada: listas, labels, membros e cards "
        "(opcionalmente com checklists e comentarios). Prefira esta tool a varias chamadas "
        "encadeadas quando precisar entender o estado de um board.\n"
        "Controle o tamanho da resposta com 'depth' (lists < cards < full), 'filters' e "
        "'max_cards'. Com depth='full' o servidor busca checklists e comentarios do board "
        "inteiro de uma vez, sem uma chamada por card.\n"
        "Por padrao traz os campos personalizados (custom fields): a lista de definicoes "
        "do board em 'custom_fields' e os valores de cada card em card['custom_fields']. "
        "Desligue com include_custom_fields=false."
    ),
    "annotations": {"title": "Snapshot de board", **READ_ONLY},
    "inputSchema": {
        "type": "object",
        "properties": {
            "board": {**REF, "description": "Board por id ou nome. Obrigatorio."},
            "depth": {
                "type": "string",
                "enum": ["lists", "cards", "full"],
                "default": "cards",
                "description": (
                    "lists = board + listas + labels + membros; "
                    "cards = tambem os cards; "
                    "full = tambem checklists e comentarios de cada card."
                ),
            },
            "card_status": {
                "type": "string", "enum": ["open", "archived", "all"], "default": "open",
                "description": "Quais cards considerar antes dos filtros.",
            },
            "include_archived_lists": {"type": "boolean", "default": False},
            "include_custom_fields": {
                "type": "boolean", "default": True,
                "description": (
                    "Traz os campos personalizados (custom fields) de cada card e a "
                    "lista de definicoes do board. Custa uma chamada extra. "
                    "Desligue se o board nao usa custom fields."
                ),
            },
            "filters": CARD_FILTERS,
            "group_by": {
                "type": "string", "enum": ["list", "none"], "default": "list",
                "description": "list = cards aninhados sob cada lista; none = array plano.",
            },
            "max_cards": {"type": "integer", "default": 200},
            "max_comments_per_card": {"type": "integer", "default": 10},
            **OUTPUT_OPTIONS,
        },
        "required": ["board"],
        "additionalProperties": False,
    },
}


def get_board_snapshot(ctx, args):
    board = ctx.resolve_board(args.get("board"))
    board_id = board["id"]
    depth = (args.get("depth") or "cards").lower()
    include_raw = bool(args.get("include_raw"))

    include_closed_lists = bool(args.get("include_archived_lists"))
    lists = ctx.board_lists(board_id, include_closed=include_closed_lists)
    labels = ctx.board_labels(board_id)
    members = ctx.board_members(board_id)

    snapshot = {
        "board": {
            "id": board_id,
            "name": board.get("name"),
            "url": board.get("shortUrl") or board.get("url"),
            "closed": board.get("closed"),
            "desc": truncate(board.get("desc"), 500),
            "last_activity": board.get("dateLastActivity"),
        },
        "lists": [
            {"id": item["id"], "name": item["name"], "closed": item.get("closed"),
             "position": item.get("pos")}
            for item in lists
        ],
        "labels": [
            {"id": lb["id"], "name": lb.get("name"), "color": lb.get("color")} for lb in labels
        ],
        "members": [
            {"id": m["id"], "username": m.get("username"), "name": m.get("fullName")}
            for m in members
        ],
    }

    want_custom_fields = bool(args.get("include_custom_fields", True))
    custom_field_defs = ctx.board_custom_fields(board_id) if want_custom_fields else []
    if want_custom_fields:
        snapshot["custom_fields"] = custom_field_schema(custom_field_defs)

    if depth == "lists":
        return snapshot

    status = (args.get("card_status") or "open").lower()
    api_filter = {"open": "open", "archived": "closed", "all": "all"}.get(status, "open")

    card_params = {"filter": api_filter, "fields": CARD_FIELDS}
    if want_custom_fields:
        card_params["customFieldItems"] = "true"
    cards = ctx.client.request(
        "GET", f"/boards/{board_id}/cards", params=card_params,
    ) or []

    if not include_closed_lists:
        visible = {item["id"] for item in lists}
        cards = [card for card in cards if card.get("idList") in visible]

    total_before_filters = len(cards)
    cards = filter_cards(cards, args.get("filters"), ctx, board_id)
    matched = len(cards)

    max_cards = args.get("max_cards") or 200
    truncated = matched > max_cards
    cards = cards[:max_cards]

    checklists_by_card: dict[str, list] = {}
    comments_by_card: dict[str, list] = {}
    if depth == "full" and cards:
        checklists_by_card = _fetch_checklists(ctx, board_id)
        comments_by_card = _fetch_comments(
            ctx, board_id, args.get("max_comments_per_card") or 10
        )

    list_names, label_names, member_names = name_maps(ctx, board_id)
    desc_max = args.get("desc_max_chars", 500)
    desc_max = None if desc_max == 0 else desc_max
    include_desc = args.get("include_desc", True)

    rendered = []
    for card in cards:
        if include_raw:
            entry = dict(card)
        else:
            entry = compact_card(
                card,
                list_names=list_names,
                label_names=label_names,
                member_names=member_names,
                include_desc=include_desc,
                desc_max_chars=desc_max,
            )
        if want_custom_fields and not include_raw:
            values = custom_field_values(card.get("customFieldItems"), custom_field_defs)
            if values:
                entry["custom_fields"] = values
        if depth == "full":
            entry["checklists"] = checklists_by_card.get(card["id"], [])
            entry["comments"] = comments_by_card.get(card["id"], [])
        rendered.append(entry)

    snapshot["cards_summary"] = {
        "total_in_board": total_before_filters,
        "matched_filters": matched,
        "returned": len(rendered),
        "truncated": truncated,
    }

    if (args.get("group_by") or "list").lower() == "list":
        grouped = {item["id"]: [] for item in lists}
        orphans = []
        for entry, card in zip(rendered, cards):
            grouped.setdefault(card.get("idList"), orphans).append(entry)
        for item in snapshot["lists"]:
            item["cards"] = grouped.get(item["id"], [])
        if orphans:
            snapshot["cards_outside_listed_lists"] = orphans
    else:
        snapshot["cards"] = rendered

    return snapshot


def _fetch_checklists(ctx, board_id: str) -> dict[str, list]:
    """Todos os checklists do board em uma chamada, indexados por card.

    Os query params abaixo nao estao na doc oficial deste endpoint (so em
    /cards/{id}/checklists), mas funcionam e sao o que permite montar o
    depth='full' sem uma chamada por card. Se um dia pararem de funcionar,
    o fallback e iterar os cards chamando /cards/{id}/checklists.
    """
    raw = ctx.client.request(
        "GET", f"/boards/{board_id}/checklists",
        params={
            "fields": "id,name,idCard,pos",
            "checkItems": "all",
            "checkItem_fields": "id,name,state,pos,due,idMember",
        },
    ) or []

    by_card: dict[str, list] = {}
    for checklist in raw:
        items = sorted(checklist.get("checkItems") or [], key=lambda i: i.get("pos") or 0)
        by_card.setdefault(checklist.get("idCard"), []).append({
            "id": checklist.get("id"),
            "name": checklist.get("name"),
            "items": [
                {
                    "id": item.get("id"),
                    "name": item.get("name"),
                    "checked": item.get("state") == "complete",
                    "due": item.get("due"),
                }
                for item in items
            ],
        })
    return by_card


def _fetch_comments(ctx, board_id: str, per_card: int) -> dict[str, list]:
    """Comentarios do board inteiro em uma chamada, indexados por card."""
    raw = ctx.client.request(
        "GET", f"/boards/{board_id}/actions",
        params={"filter": "commentCard", "limit": 1000, "memberCreator_fields": "username"},
    ) or []

    by_card: dict[str, list] = {}
    for action in raw:
        data = action.get("data") or {}
        card_id = (data.get("card") or {}).get("id")
        if not card_id:
            continue
        bucket = by_card.setdefault(card_id, [])
        if len(bucket) >= per_card:
            continue
        bucket.append({
            "id": action.get("id"),
            "text": data.get("text"),
            "author": (action.get("memberCreator") or {}).get("username"),
            "date": action.get("date"),
        })
    return by_card


TOOLS = [
    (LIST_BOARDS_TOOL, list_boards),
    (SNAPSHOT_TOOL, get_board_snapshot),
]
