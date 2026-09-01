"""manage_checklists_and_comments: checklists, itens e comentarios em lote."""

from .common import (
    ID_RE,
    ToolError,
    as_bool_param,
    as_list,
    parse_date,
    run_batch,
)
from .schemas import REF, WRITE

MANAGE_CHECKLISTS_TOOL = {
    "name": "manage_checklists_and_comments",
    "description": (
        "Gerencia checklists, itens de checklist e comentarios de cards -- varias "
        "operacoes numa unica chamada. Cada operacao e independente: falhas nao "
        "interrompem as demais.\n\n"
        "Um 'add_checklist' com 'items' cria a checklist E todos os itens de uma vez. "
        "'add_items' aceita strings simples ou objetos {name, checked, due, member}. "
        "'check_items' marca/desmarca varios itens pelo nome de uma so vez.\n\n"
        "Para APAGAR checklists, itens ou comentarios use a tool 'delete_items'."
    ),
    "annotations": {"title": "Checklists e comentarios", **WRITE},
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
                            "enum": ["add_checklist", "update_checklist", "add_items",
                                     "update_item", "check_items", "add_comment",
                                     "update_comment", "list_checklists"],
                            "description": (
                                "add_checklist = nova checklist (com 'items' opcionais); "
                                "add_items = adiciona itens a uma checklist existente; "
                                "update_item = altera um item; check_items = marca/desmarca "
                                "varios itens; add_comment / update_comment = comentarios; "
                                "list_checklists = le as checklists do card."
                            ),
                        },
                        "board": {**REF, "description": "Board de contexto para resolver nomes."},
                        "card": {**REF, "description": "Card alvo. Obrigatorio na maioria das acoes."},
                        "checklist": {**REF, "description": "Checklist por id ou nome dentro do card."},
                        "name": {"type": "string", "description": "Nome da checklist ou do item."},
                        "position": {"type": ["string", "number"], "description": "'top', 'bottom' ou numero."},
                        "items": {
                            "type": "array",
                            "description": (
                                "Itens a criar. Cada elemento pode ser uma string (so o nome) "
                                "ou {name, checked, due, member}."
                            ),
                            "items": {"type": ["string", "object"]},
                        },
                        "item": {**REF, "description": "Item alvo em 'update_item' (id ou nome)."},
                        "item_names": {
                            "type": "array", "items": {"type": "string"},
                            "description": "Em 'check_items': nomes (ou ids) dos itens a marcar/desmarcar.",
                        },
                        "checked": {"type": "boolean", "description": "Estado do item."},
                        "due": {"type": "string", "description": "Prazo do item. '' limpa."},
                        "member": {**REF, "description": "Membro atribuido ao item. Aceita 'me'."},
                        "text": {"type": "string", "description": "Texto do comentario (markdown)."},
                        "comment_id": {"type": "string", "description": "Id do comentario em 'update_comment'."},
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

def _card_of(ctx, operation) -> dict:
    board_hint = ctx.resolve_board(operation["board"])["id"] if operation.get("board") else None
    return ctx.resolve_card(operation.get("card"), board_hint)


def _card_checklists(ctx, card_id: str) -> list:
    return ctx.client.request(
        "GET", f"/cards/{card_id}/checklists",
        params={
            "fields": "id,name,pos",
            "checkItems": "all",
            "checkItem_fields": "id,name,state,pos,due,idMember",
        },
    ) or []


def _resolve_checklist(ctx, card_id: str, ref) -> dict:
    checklists = _card_checklists(ctx, card_id)
    if not ref:
        if len(checklists) == 1:
            return checklists[0]
        if not checklists:
            raise ToolError("Esse card nao tem checklists. Crie uma com 'add_checklist'.")
        raise ToolError(
            "O card tem varias checklists -- informe 'checklist'. Disponiveis: "
            + ", ".join(c.get("name") or c["id"] for c in checklists)
        )

    ref = str(ref).strip()
    for checklist in checklists:
        if checklist["id"] == ref or (checklist.get("name") or "").lower() == ref.lower():
            return checklist
    partial = [c for c in checklists if ref.lower() in (c.get("name") or "").lower()]
    if len(partial) == 1:
        return partial[0]
    raise ToolError(
        f"Checklist {ref!r} nao encontrada nesse card. Disponiveis: "
        + (", ".join(c.get("name") or c["id"] for c in checklists) or "(nenhuma)")
    )


def _resolve_item(checklist: dict, ref) -> dict:
    items = checklist.get("checkItems") or []
    ref = str(ref).strip()
    for item in items:
        if item["id"] == ref or (item.get("name") or "").lower() == ref.lower():
            return item
    partial = [i for i in items if ref.lower() in (i.get("name") or "").lower()]
    if len(partial) == 1:
        return partial[0]
    if len(partial) > 1:
        raise ToolError(f"Item {ref!r} e ambiguo nessa checklist.")
    raise ToolError(
        f"Item {ref!r} nao encontrado. Itens: "
        + (", ".join(i.get("name") for i in items) or "(nenhum)")
    )


def _render_checklist(checklist: dict) -> dict:
    return {
        "id": checklist.get("id"),
        "name": checklist.get("name"),
        "items": [
            {
                "id": item.get("id"),
                "name": item.get("name"),
                "checked": item.get("state") == "complete",
                "due": item.get("due"),
            }
            for item in sorted(checklist.get("checkItems") or [], key=lambda i: i.get("pos") or 0)
        ],
    }


def _create_items(ctx, checklist_id: str, items, board_id: str | None) -> list:
    created = []
    for raw in as_list(items):
        if isinstance(raw, str):
            raw = {"name": raw}
        if not isinstance(raw, dict) or not raw.get("name"):
            raise ToolError("Cada item precisa ser uma string ou um objeto com 'name'.")

        params = {"name": raw["name"]}
        if raw.get("checked") is not None:
            params["checked"] = as_bool_param(raw["checked"])
        if "due" in raw:
            params["due"] = parse_date(raw["due"], field="due do item")
        if "position" in raw:
            params["pos"] = raw["position"]
        if raw.get("member"):
            params["idMember"] = ctx.resolve_member(raw["member"], board_id)["id"]

        item = ctx.client.request("POST", f"/checklists/{checklist_id}/checkItems", params=params)
        created.append({"id": item.get("id"), "name": item.get("name")})
    return created


# --------------------------------------------------------------------------
# Acoes
# --------------------------------------------------------------------------

def _add_checklist(ctx, operation):
    card = _card_of(ctx, operation)
    params = {"idCard": card["id"], "name": operation.get("name") or "Checklist"}
    if "position" in operation:
        params["pos"] = operation["position"]

    checklist = ctx.client.request("POST", "/checklists", params=params)
    result = {"checklist": {"id": checklist["id"], "name": checklist.get("name")},
              "card": {"id": card["id"], "name": card.get("name")}}
    if operation.get("items"):
        result["items_created"] = _create_items(
            ctx, checklist["id"], operation["items"], card.get("idBoard")
        )
    return result


def _update_checklist(ctx, operation):
    card = _card_of(ctx, operation)
    checklist = _resolve_checklist(ctx, card["id"], operation.get("checklist"))

    params = {}
    if "name" in operation:
        params["name"] = operation["name"]
    if "position" in operation:
        params["pos"] = operation["position"]
    if not params:
        raise ToolError("Informe 'name' e/ou 'position' para atualizar a checklist.")

    updated = ctx.client.request("PUT", f"/checklists/{checklist['id']}", params=params)
    return {"checklist": {"id": updated["id"], "name": updated.get("name")}}


def _add_items(ctx, operation):
    card = _card_of(ctx, operation)
    checklist = _resolve_checklist(ctx, card["id"], operation.get("checklist"))
    if not operation.get("items"):
        raise ToolError("'add_items' exige 'items'.")
    return {
        "checklist": {"id": checklist["id"], "name": checklist.get("name")},
        "items_created": _create_items(
            ctx, checklist["id"], operation["items"], card.get("idBoard")
        ),
    }


def _update_item(ctx, operation):
    card = _card_of(ctx, operation)
    checklist = _resolve_checklist(ctx, card["id"], operation.get("checklist"))
    item = _resolve_item(checklist, operation.get("item"))

    params = {}
    if "name" in operation:
        params["name"] = operation["name"]
    if operation.get("checked") is not None:
        params["state"] = "complete" if operation["checked"] else "incomplete"
    if "position" in operation:
        params["pos"] = operation["position"]
    if "due" in operation:
        params["due"] = parse_date(operation["due"], field="due do item")
    if operation.get("member"):
        params["idMember"] = ctx.resolve_member(operation["member"], card.get("idBoard"))["id"]
    if not params:
        raise ToolError("Nenhum campo para atualizar no item.")

    updated = ctx.client.request(
        "PUT", f"/cards/{card['id']}/checkItem/{item['id']}", params=params
    )
    return {
        "item": {
            "id": updated.get("id"),
            "name": updated.get("name"),
            "checked": updated.get("state") == "complete",
        }
    }


def _check_items(ctx, operation):
    """Marca/desmarca varios itens de uma vez, com resultado por item."""
    card = _card_of(ctx, operation)
    checklist = _resolve_checklist(ctx, card["id"], operation.get("checklist"))
    names = as_list(operation.get("item_names"))
    if not names:
        raise ToolError("'check_items' exige 'item_names'.")

    state = "complete" if operation.get("checked", True) else "incomplete"
    changed, failed = [], []
    for ref in names:
        try:
            item = _resolve_item(checklist, ref)
            updated = ctx.client.request(
                "PUT", f"/cards/{card['id']}/checkItem/{item['id']}", params={"state": state}
            )
            changed.append({"id": updated.get("id"), "name": updated.get("name"),
                            "checked": updated.get("state") == "complete"})
        except Exception as exc:
            failed.append({"item": ref, "error": str(exc)})

    return {
        "checklist": {"id": checklist["id"], "name": checklist.get("name")},
        "changed": changed,
        "failed": failed,
    }


def _add_comment(ctx, operation):
    card = _card_of(ctx, operation)
    if not operation.get("text"):
        raise ToolError("'add_comment' exige 'text'.")
    action = ctx.client.request(
        "POST", f"/cards/{card['id']}/actions/comments", params={"text": operation["text"]}
    ) or {}
    return {
        "comment": {"id": action.get("id"), "text": operation["text"], "date": action.get("date")},
        "card": {"id": card["id"], "name": card.get("name")},
    }


def _update_comment(ctx, operation):
    card = _card_of(ctx, operation)
    comment_id = operation.get("comment_id")
    if not comment_id or not ID_RE.match(str(comment_id).strip()):
        raise ToolError("'update_comment' exige 'comment_id' (id do comentario).")
    if not operation.get("text"):
        raise ToolError("'update_comment' exige 'text'.")

    action = ctx.client.request(
        "PUT", f"/cards/{card['id']}/actions/{comment_id}/comments",
        params={"text": operation["text"]},
    ) or {}
    return {"comment": {"id": action.get("id"), "text": operation["text"]}}


def _list_checklists(ctx, operation):
    card = _card_of(ctx, operation)
    return {
        "card": {"id": card["id"], "name": card.get("name")},
        "checklists": [_render_checklist(c) for c in _card_checklists(ctx, card["id"])],
    }


ACTIONS = {
    "add_checklist": _add_checklist,
    "update_checklist": _update_checklist,
    "add_items": _add_items,
    "update_item": _update_item,
    "check_items": _check_items,
    "add_comment": _add_comment,
    "update_comment": _update_comment,
    "list_checklists": _list_checklists,
}


def manage_checklists_and_comments(ctx, args):
    return run_batch(ctx, args.get("operations"), ACTIONS)


TOOLS = [(MANAGE_CHECKLISTS_TOOL, manage_checklists_and_comments)]
