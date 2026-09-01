"""manage_cards: criar, atualizar, mover, arquivar e duplicar cards em lote."""

from .common import (
    CARD_FIELDS,
    ToolError,
    as_bool_param,
    as_list,
    card_created_at,
    compact_card,
    custom_field_values,
    join_ids,
    name_maps,
    parse_date,
    run_batch,
)
from .schemas import REF, WRITE

MANAGE_CARDS_TOOL = {
    "name": "manage_cards",
    "description": (
        "Cria, atualiza, move, arquiva, duplica e le cards -- varias operacoes numa "
        "unica chamada, via 'operations'. Cada operacao e independente: se uma falhar, "
        "as demais continuam e o resultado diz exatamente qual falhou e por que.\n\n"
        "Referencias (board, list, card, labels, members) aceitam id OU nome. "
        "Datas aceitam ISO, 'today', 'tomorrow' ou offsets como '+3d'; string vazia limpa o campo.\n\n"
        "Labels e membros aceitam tres formas: uma lista simples (substitui tudo), ou um "
        "objeto {add: [...], remove: [...], set: [...]} para alteracoes incrementais.\n\n"
        "Para APAGAR um card definitivamente use a tool 'delete_items' -- aqui 'archive' "
        "apenas arquiva (reversivel)."
    ),
    "annotations": {"title": "Gerenciar cards", **WRITE},
    "inputSchema": {
        "type": "object",
        "properties": {
            "operations": {
                "type": "array",
                "minItems": 1,
                "description": "Lista de operacoes, executadas em ordem.",
                "items": {
                    "type": "object",
                    "properties": {
                        "action": {
                            "type": "string",
                            "enum": ["create", "update", "move", "archive", "unarchive",
                                     "duplicate", "get"],
                            "description": (
                                "create = novo card; update = altera campos; "
                                "move = muda de lista/board/posicao; archive/unarchive = "
                                "arquiva ou desarquiva; duplicate = copia um card; "
                                "get = le um card com checklists, comentarios e custom fields."
                            ),
                        },
                        "board": {**REF, "description": "Board de contexto, necessario para resolver nomes."},
                        "card": {**REF, "description": "Card alvo. Obrigatorio exceto em 'create'."},
                        "list": {**REF, "description": "Lista destino (create, move, duplicate)."},
                        "name": {"type": "string"},
                        "desc": {"type": "string", "description": "Descricao em markdown."},
                        "due": {"type": "string", "description": "Prazo. '' limpa."},
                        "start": {"type": "string", "description": "Data de inicio. '' limpa."},
                        "due_complete": {"type": "boolean"},
                        "position": {
                            "description": "'top', 'bottom' ou um numero.",
                            "type": ["string", "number"],
                        },
                        "labels": {
                            "description": "Lista (substitui) ou {add/remove/set}.",
                            "type": ["array", "object"],
                        },
                        "members": {
                            "description": "Lista (substitui) ou {add/remove/set}. Aceita 'me'.",
                            "type": ["array", "object"],
                        },
                        "comment": {
                            "type": "string",
                            "description": "Atalho: publica este comentario no card apos a operacao.",
                        },
                        "keep_from_source": {
                            "type": "array", "items": {"type": "string"},
                            "description": "Em 'duplicate': attachments, checklists, comments, due, labels, members, stickers.",
                        },
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
# Helpers
# --------------------------------------------------------------------------

def _apply_set_ops(spec, current: list, resolve_fn) -> list | None:
    """Resolve {set|add|remove} (ou uma lista simples = set) para a lista final de ids."""
    if spec is None:
        return None
    if isinstance(spec, (list, tuple, str)):
        spec = {"set": spec}
    if not isinstance(spec, dict):
        raise ToolError("Use uma lista ou um objeto {add, remove, set}.")

    result = list(current)
    if "set" in spec:
        result = []
        for ref in as_list(spec["set"]):
            resolved = resolve_fn(ref)
            if resolved not in result:
                result.append(resolved)
    for ref in as_list(spec.get("add")):
        resolved = resolve_fn(ref)
        if resolved not in result:
            result.append(resolved)
    for ref in as_list(spec.get("remove")):
        resolved = resolve_fn(ref)
        if resolved in result:
            result.remove(resolved)
    return result


def _target_list(ctx, operation, fallback_board: str | None = None):
    """Resolve a lista destino e devolve (lista, board_id)."""
    list_ref = operation.get("list")
    if not list_ref:
        return None, fallback_board

    board_id = None
    if operation.get("board"):
        board_id = ctx.resolve_board(operation["board"])["id"]
    elif fallback_board:
        board_id = fallback_board

    if board_id is None:
        from .common import ID_RE
        if not ID_RE.match(str(list_ref).strip()):
            raise ToolError(
                f"Para resolver a lista {list_ref!r} pelo nome informe tambem 'board' "
                "(ou passe o id da lista)."
            )
    target = ctx.resolve_list(list_ref, board_id)
    return target, target.get("idBoard") or board_id


def _scalar_params(operation: dict) -> dict:
    params = {}
    if "name" in operation:
        params["name"] = operation["name"]
    if "desc" in operation:
        params["desc"] = operation["desc"]
    if "due" in operation:
        params["due"] = parse_date(operation["due"], field="due")
    if "start" in operation:
        params["start"] = parse_date(operation["start"], field="start")
    if "due_complete" in operation:
        params["dueComplete"] = as_bool_param(operation["due_complete"])
    if "position" in operation:
        params["pos"] = operation["position"]
    return params


def _post_comment(ctx, card_id: str, text: str) -> dict:
    action = ctx.client.request(
        "POST", f"/cards/{card_id}/actions/comments", params={"text": text}
    ) or {}
    return {"id": action.get("id"), "text": text}


def _render(ctx, card: dict, board_id: str | None) -> dict:
    if not board_id:
        return card
    lists, labels, members = name_maps(ctx, board_id)
    return compact_card(card, list_names=lists, label_names=labels, member_names=members)


# --------------------------------------------------------------------------
# Acoes
# --------------------------------------------------------------------------

def _create(ctx, operation):
    target, board_id = _target_list(ctx, operation)
    if target is None:
        raise ToolError("'create' exige 'list' (lista destino).")
    if not operation.get("name"):
        raise ToolError("'create' exige 'name'.")

    params = {"idList": target["id"], **_scalar_params(operation)}

    label_ids = _apply_set_ops(
        operation.get("labels"), [], lambda ref: ctx.resolve_label(ref, board_id)["id"]
    )
    if label_ids:
        params["idLabels"] = join_ids(label_ids)

    member_ids = _apply_set_ops(
        operation.get("members"), [], lambda ref: ctx.resolve_member(ref, board_id)["id"]
    )
    if member_ids:
        params["idMembers"] = join_ids(member_ids)

    card = ctx.client.request("POST", "/cards", params=params)
    result = {"created": _render(ctx, card, board_id)}
    if operation.get("comment"):
        result["comment"] = _post_comment(ctx, card["id"], operation["comment"])
    return result


def _update(ctx, operation, *, moving: bool = False):
    board_hint = ctx.resolve_board(operation["board"])["id"] if operation.get("board") else None
    card = ctx.resolve_card(operation.get("card"), board_hint)
    board_id = card.get("idBoard") or board_hint

    params = _scalar_params(operation)

    target, target_board = _target_list(ctx, operation, fallback_board=board_id)
    if target is not None:
        params["idList"] = target["id"]
        if target_board and target_board != board_id:
            params["idBoard"] = target_board
            board_id = target_board
    elif moving and "position" not in operation:
        raise ToolError("'move' exige 'list' e/ou 'position'.")

    label_ids = _apply_set_ops(
        operation.get("labels"), card.get("idLabels") or [],
        lambda ref: ctx.resolve_label(ref, board_id)["id"],
    )
    if label_ids is not None:
        params["idLabels"] = join_ids(label_ids)

    member_ids = _apply_set_ops(
        operation.get("members"), card.get("idMembers") or [],
        lambda ref: ctx.resolve_member(ref, board_id)["id"],
    )
    if member_ids is not None:
        params["idMembers"] = join_ids(member_ids)

    if not params and not operation.get("comment"):
        raise ToolError("Nenhum campo para atualizar nesta operacao.")

    result = {}
    if params:
        updated = ctx.client.request("PUT", f"/cards/{card['id']}", params=params)
        result["updated"] = _render(ctx, updated, board_id)
    else:
        result["updated"] = _render(ctx, card, board_id)

    if operation.get("comment"):
        result["comment"] = _post_comment(ctx, card["id"], operation["comment"])
    return result


def _move(ctx, operation):
    return _update(ctx, operation, moving=True)


def _set_closed(ctx, operation, closed: bool):
    board_hint = ctx.resolve_board(operation["board"])["id"] if operation.get("board") else None
    card = ctx.resolve_card(operation.get("card"), board_hint)
    updated = ctx.client.request(
        "PUT", f"/cards/{card['id']}", params={"closed": as_bool_param(closed)}
    )
    return {
        "card": {"id": updated["id"], "name": updated.get("name")},
        "closed": updated.get("closed"),
    }


def _archive(ctx, operation):
    return _set_closed(ctx, operation, True)


def _unarchive(ctx, operation):
    return _set_closed(ctx, operation, False)


def _duplicate(ctx, operation):
    board_hint = ctx.resolve_board(operation["board"])["id"] if operation.get("board") else None
    source = ctx.resolve_card(operation.get("card"), board_hint)
    board_id = source.get("idBoard") or board_hint

    target, target_board = _target_list(ctx, operation, fallback_board=board_id)
    params = {
        "idCardSource": source["id"],
        "idList": (target or {}).get("id") or source["idList"],
        "keepFromSource": join_ids(as_list(operation.get("keep_from_source"))) or "all",
        **_scalar_params(operation),
    }
    params.setdefault("name", f"{source.get('name')} (copia)")

    card = ctx.client.request("POST", "/cards", params=params)
    return {"created": _render(ctx, card, target_board or board_id)}


def _get(ctx, operation):
    board_hint = ctx.resolve_board(operation["board"])["id"] if operation.get("board") else None
    card = ctx.resolve_card(operation.get("card"), board_hint)

    detail = ctx.client.request(
        "GET", f"/cards/{card['id']}",
        params={
            "fields": CARD_FIELDS,
            "checklists": "all",
            "checklist_fields": "id,name,pos",
            "attachments": "true",
            "attachment_fields": "id,name,url,bytes,date",
            "actions": "commentCard",
            "actions_limit": 50,
            "customFieldItems": "true",
        },
    ) or {}
    board_id = detail.get("idBoard") or board_hint

    rendered = _render(ctx, detail, board_id)
    if board_id:
        values = custom_field_values(
            detail.get("customFieldItems"), ctx.board_custom_fields(board_id)
        )
        if values:
            rendered["custom_fields"] = values
    rendered["checklists"] = [
        {
            "id": checklist.get("id"),
            "name": checklist.get("name"),
            "items": [
                {
                    "id": item.get("id"),
                    "name": item.get("name"),
                    "checked": item.get("state") == "complete",
                    "due": item.get("due"),
                }
                for item in sorted(
                    checklist.get("checkItems") or [], key=lambda i: i.get("pos") or 0
                )
            ],
        }
        for checklist in (detail.get("checklists") or [])
    ]
    rendered["comments"] = [
        {
            "id": action.get("id"),
            "text": (action.get("data") or {}).get("text"),
            "author": (action.get("memberCreator") or {}).get("username"),
            "date": action.get("date"),
        }
        for action in (detail.get("actions") or [])
    ]
    rendered["attachments"] = [
        {"id": a.get("id"), "name": a.get("name"), "url": a.get("url"), "bytes": a.get("bytes")}
        for a in (detail.get("attachments") or [])
    ]
    rendered["created_at"] = card_created_at(detail.get("id", ""))
    return rendered


ACTIONS = {
    "create": _create,
    "update": _update,
    "move": _move,
    "archive": _archive,
    "unarchive": _unarchive,
    "duplicate": _duplicate,
    "get": _get,
}


def manage_cards(ctx, args):
    return run_batch(ctx, args.get("operations"), ACTIONS)


TOOLS = [(MANAGE_CARDS_TOOL, manage_cards)]
