"""manage_board_structure: boards, listas e labels em lote."""

from .common import (
    ToolError,
    as_bool_param,
    as_list,
    run_batch,
)
from .schemas import REF, WRITE

LABEL_COLOR_ENUM = [
    "green", "yellow", "orange", "red", "purple", "blue",
    "sky", "lime", "pink", "black", "null",
]

MANAGE_STRUCTURE_TOOL = {
    "name": "manage_board_structure",
    "description": (
        "Cria e altera a estrutura dos boards: o proprio board, suas listas e suas labels "
        "-- varias operacoes numa unica chamada, com resultado por operacao.\n\n"
        "'create_board' aceita 'lists' e 'labels' para montar um board inteiro (com colunas "
        "e etiquetas) de uma so vez. 'move_all_cards' e 'archive_all_cards' operam sobre a "
        "lista toda, evitando uma chamada por card.\n\n"
        "Listas nao podem ser apagadas no Trello -- use 'archive_list'. Para apagar boards "
        "ou labels definitivamente use a tool 'delete_items'."
    ),
    "annotations": {"title": "Estrutura de boards", **WRITE},
    "inputSchema": {
        "type": "object",
        "properties": {
            "operations": {
                "type": "array",
                "minItems": 1,
                "items": {
                    "type": "object",
                    "properties": {
                        "action": {
                            "type": "string",
                            "enum": ["create_board", "update_board", "close_board", "reopen_board",
                                     "create_list", "update_list", "archive_list", "unarchive_list",
                                     "move_all_cards", "archive_all_cards",
                                     "create_label", "update_label"],
                        },
                        "board": {**REF, "description": "Board alvo ou de contexto."},
                        "list": {**REF, "description": "Lista alvo (id ou nome)."},
                        "label": {**REF, "description": "Label alvo (id, nome ou cor)."},
                        "name": {"type": "string", "description": "Nome do board, lista ou label."},
                        "desc": {"type": "string", "description": "Descricao do board."},
                        "color": {
                            "type": "string", "enum": LABEL_COLOR_ENUM,
                            "description": "Cor da label.",
                        },
                        "position": {"type": ["string", "number"], "description": "'top', 'bottom' ou numero."},
                        "target_list": {**REF, "description": "Lista destino em 'move_all_cards'."},
                        "target_board": {**REF, "description": "Board destino em 'move_all_cards' entre boards."},
                        "lists": {
                            "type": "array", "items": {"type": "string"},
                            "description": "Em 'create_board': nomes das listas a criar, em ordem.",
                        },
                        "labels": {
                            "type": "array",
                            "items": {"type": ["string", "object"]},
                            "description": (
                                "Em 'create_board': labels a criar. String = nome (cor automatica) "
                                "ou objeto {name, color}."
                            ),
                        },
                        "default_lists": {
                            "type": "boolean", "default": False,
                            "description": "Em 'create_board': criar as listas padrao do Trello (To Do/Doing/Done).",
                        },
                        "organization": {**REF, "description": "Workspace do novo board (id ou nome)."},
                    },
                    "required": ["action"],
                    "additionalProperties": False,
                },
            },
        },
        "required": ["operations"],
        "additionalProperties": False,
    },
}


# --------------------------------------------------------------------------
# Boards
# --------------------------------------------------------------------------

def _create_board(ctx, operation):
    if not operation.get("name"):
        raise ToolError("'create_board' exige 'name'.")

    params = {
        "name": operation["name"],
        "defaultLists": as_bool_param(operation.get("default_lists", False)),
    }
    if operation.get("desc"):
        params["desc"] = operation["desc"]
    if operation.get("organization"):
        params["idOrganization"] = operation["organization"]

    board = ctx.client.request("POST", "/boards/", params=params)
    ctx.invalidate()

    result = {"board": {"id": board["id"], "name": board.get("name"),
                        "url": board.get("shortUrl") or board.get("url")}}

    created_lists = []
    for name in as_list(operation.get("lists")):
        item = ctx.client.request(
            "POST", "/lists", params={"name": name, "idBoard": board["id"], "pos": "bottom"}
        )
        created_lists.append({"id": item["id"], "name": item.get("name")})
    if created_lists:
        result["lists_created"] = created_lists

    created_labels = []
    for raw in as_list(operation.get("labels")):
        spec = {"name": raw} if isinstance(raw, str) else dict(raw)
        label = ctx.client.request(
            "POST", "/labels",
            params={"name": spec.get("name", ""), "color": spec.get("color") or "green",
                    "idBoard": board["id"]},
        )
        created_labels.append({"id": label["id"], "name": label.get("name"),
                               "color": label.get("color")})
    if created_labels:
        result["labels_created"] = created_labels

    ctx.invalidate(board["id"])
    return result


def _update_board(ctx, operation):
    board = ctx.resolve_board(operation.get("board"))
    params = {}
    if "name" in operation:
        params["name"] = operation["name"]
    if "desc" in operation:
        params["desc"] = operation["desc"]
    if not params:
        raise ToolError("Informe 'name' e/ou 'desc' para atualizar o board.")

    updated = ctx.client.request("PUT", f"/boards/{board['id']}", params=params)
    ctx.invalidate(board["id"])
    return {"board": {"id": updated["id"], "name": updated.get("name")}}


def _set_board_closed(ctx, operation, closed: bool):
    board = ctx.resolve_board(operation.get("board"))
    updated = ctx.client.request(
        "PUT", f"/boards/{board['id']}", params={"closed": as_bool_param(closed)}
    )
    ctx.invalidate(board["id"])
    return {"board": {"id": updated["id"], "name": updated.get("name"),
                      "closed": updated.get("closed")}}


# --------------------------------------------------------------------------
# Listas
# --------------------------------------------------------------------------

def _create_list(ctx, operation):
    board = ctx.resolve_board(operation.get("board"))
    if not operation.get("name"):
        raise ToolError("'create_list' exige 'name'.")

    params = {"name": operation["name"], "idBoard": board["id"],
              "pos": operation.get("position", "bottom")}
    item = ctx.client.request("POST", "/lists", params=params)
    ctx.invalidate(board["id"])
    return {"list": {"id": item["id"], "name": item.get("name"), "board_id": board["id"]}}


def _update_list(ctx, operation):
    board = ctx.resolve_board(operation.get("board"), required=False)
    board_id = board["id"] if board else None
    target = ctx.resolve_list(operation.get("list"), board_id)

    params = {}
    if "name" in operation:
        params["name"] = operation["name"]
    if "position" in operation:
        params["pos"] = operation["position"]
    if not params:
        raise ToolError("Informe 'name' e/ou 'position' para atualizar a lista.")

    updated = ctx.client.request("PUT", f"/lists/{target['id']}", params=params)
    ctx.invalidate(updated.get("idBoard") or board_id)
    return {"list": {"id": updated["id"], "name": updated.get("name")}}


def _set_list_closed(ctx, operation, closed: bool):
    board = ctx.resolve_board(operation.get("board"), required=False)
    board_id = board["id"] if board else None
    target = ctx.resolve_list(operation.get("list"), board_id)

    updated = ctx.client.request(
        "PUT", f"/lists/{target['id']}", params={"closed": as_bool_param(closed)}
    )
    ctx.invalidate(updated.get("idBoard") or board_id)
    return {"list": {"id": updated["id"], "name": updated.get("name"),
                     "closed": updated.get("closed")}}


def _move_all_cards(ctx, operation):
    board = ctx.resolve_board(operation.get("board"), required=False)
    board_id = board["id"] if board else None
    source = ctx.resolve_list(operation.get("list"), board_id)
    source_board = source.get("idBoard") or board_id

    target_board_ref = operation.get("target_board")
    target_board_id = (
        ctx.resolve_board(target_board_ref)["id"] if target_board_ref else source_board
    )
    target = ctx.resolve_list(operation.get("target_list"), target_board_id)

    moved = ctx.client.request(
        "POST", f"/lists/{source['id']}/moveAllCards",
        params={"idBoard": target_board_id, "idList": target["id"]},
    ) or []
    return {
        "from_list": {"id": source["id"], "name": source.get("name")},
        "to_list": {"id": target["id"], "name": target.get("name")},
        "cards_moved": len(moved),
    }


def _archive_all_cards(ctx, operation):
    board = ctx.resolve_board(operation.get("board"), required=False)
    board_id = board["id"] if board else None
    target = ctx.resolve_list(operation.get("list"), board_id)

    ctx.client.request("POST", f"/lists/{target['id']}/archiveAllCards")
    return {"list": {"id": target["id"], "name": target.get("name")}, "archived_all_cards": True}


# --------------------------------------------------------------------------
# Labels
# --------------------------------------------------------------------------

def _create_label(ctx, operation):
    board = ctx.resolve_board(operation.get("board"))
    color = operation.get("color") or "green"
    label = ctx.client.request(
        "POST", "/labels",
        params={"name": operation.get("name", ""), "color": color, "idBoard": board["id"]},
    )
    ctx.invalidate(board["id"])
    return {"label": {"id": label["id"], "name": label.get("name"), "color": label.get("color")}}


def _update_label(ctx, operation):
    board = ctx.resolve_board(operation.get("board"))
    label = ctx.resolve_label(operation.get("label"), board["id"])

    params = {}
    if "name" in operation:
        params["name"] = operation["name"]
    if operation.get("color"):
        params["color"] = operation["color"]
    if not params:
        raise ToolError("Informe 'name' e/ou 'color' para atualizar a label.")

    updated = ctx.client.request("PUT", f"/labels/{label['id']}", params=params)
    ctx.invalidate(board["id"])
    return {"label": {"id": updated["id"], "name": updated.get("name"),
                      "color": updated.get("color")}}


ACTIONS = {
    "create_board": _create_board,
    "update_board": _update_board,
    "close_board": lambda ctx, op: _set_board_closed(ctx, op, True),
    "reopen_board": lambda ctx, op: _set_board_closed(ctx, op, False),
    "create_list": _create_list,
    "update_list": _update_list,
    "archive_list": lambda ctx, op: _set_list_closed(ctx, op, True),
    "unarchive_list": lambda ctx, op: _set_list_closed(ctx, op, False),
    "move_all_cards": _move_all_cards,
    "archive_all_cards": _archive_all_cards,
    "create_label": _create_label,
    "update_label": _update_label,
}


def manage_board_structure(ctx, args):
    return run_batch(ctx, args.get("operations"), ACTIONS)


TOOLS = [(MANAGE_STRUCTURE_TOOL, manage_board_structure)]
