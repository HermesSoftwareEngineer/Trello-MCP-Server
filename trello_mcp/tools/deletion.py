"""delete_items: remocoes permanentes, isoladas numa tool para facilitar o controle de permissao."""

from .common import ID_RE, ToolError, run_batch
from .checklists import _resolve_checklist, _resolve_item
from .schemas import DESTRUCTIVE, REF

DELETE_TOOL = {
    "name": "delete_items",
    "description": (
        "APAGA itens do Trello PERMANENTEMENTE -- cards, checklists, itens de checklist, "
        "comentarios, labels, anexos e boards. Nao ha desfazer.\n\n"
        "Esta tool existe separada das tools de escrita justamente para poder ser "
        "autorizada a parte. Para remocoes reversiveis prefira 'archive' em 'manage_cards' "
        "ou 'archive_list' / 'close_board' em 'manage_board_structure'.\n\n"
        "Use 'dry_run': true para ver exatamente o que seria apagado sem apagar nada. "
        "Apagar uma label a remove de todos os cards do board."
    ),
    "annotations": {"title": "Apagar permanentemente", **DESTRUCTIVE},
    "inputSchema": {
        "type": "object",
        "properties": {
            "dry_run": {
                "type": "boolean", "default": False,
                "description": "Resolve os alvos e informa o que seria apagado, sem apagar.",
            },
            "operations": {
                "type": "array",
                "minItems": 1,
                "items": {
                    "type": "object",
                    "properties": {
                        "action": {
                            "type": "string",
                            "enum": ["delete_card", "delete_checklist", "delete_checkitem",
                                     "delete_comment", "delete_label", "delete_attachment",
                                     "delete_board"],
                        },
                        "board": {**REF, "description": "Board alvo ou de contexto."},
                        "card": {**REF, "description": "Card alvo ou de contexto."},
                        "checklist": {**REF, "description": "Checklist alvo ou de contexto."},
                        "item": {**REF, "description": "Item de checklist alvo."},
                        "label": {**REF, "description": "Label alvo (id, nome ou cor)."},
                        "comment_id": {"type": "string", "description": "Id do comentario."},
                        "attachment_id": {"type": "string", "description": "Id do anexo."},
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


def _context_card(ctx, operation):
    board_hint = ctx.resolve_board(operation["board"])["id"] if operation.get("board") else None
    return ctx.resolve_card(operation.get("card"), board_hint)


def _delete_card(ctx, operation, dry_run: bool):
    card = _context_card(ctx, operation)
    target = {"type": "card", "id": card["id"], "name": card.get("name")}
    if not dry_run:
        ctx.client.request("DELETE", f"/cards/{card['id']}")
    return target


def _delete_checklist(ctx, operation, dry_run: bool):
    card = _context_card(ctx, operation)
    checklist = _resolve_checklist(ctx, card["id"], operation.get("checklist"))
    target = {
        "type": "checklist", "id": checklist["id"], "name": checklist.get("name"),
        "card": card.get("name"),
        "items_affected": len(checklist.get("checkItems") or []),
    }
    if not dry_run:
        ctx.client.request("DELETE", f"/checklists/{checklist['id']}")
    return target


def _delete_checkitem(ctx, operation, dry_run: bool):
    card = _context_card(ctx, operation)
    checklist = _resolve_checklist(ctx, card["id"], operation.get("checklist"))
    item = _resolve_item(checklist, operation.get("item"))
    target = {
        "type": "checkitem", "id": item["id"], "name": item.get("name"),
        "checklist": checklist.get("name"), "card": card.get("name"),
    }
    if not dry_run:
        ctx.client.request("DELETE", f"/checklists/{checklist['id']}/checkItems/{item['id']}")
    return target


def _delete_comment(ctx, operation, dry_run: bool):
    card = _context_card(ctx, operation)
    comment_id = str(operation.get("comment_id") or "").strip()
    if not ID_RE.match(comment_id):
        raise ToolError("'delete_comment' exige 'comment_id' (id do comentario).")

    target = {"type": "comment", "id": comment_id, "card": card.get("name")}
    if not dry_run:
        ctx.client.request("DELETE", f"/cards/{card['id']}/actions/{comment_id}/comments")
    return target


def _delete_label(ctx, operation, dry_run: bool):
    board = ctx.resolve_board(operation.get("board"))
    label = ctx.resolve_label(operation.get("label"), board["id"])
    target = {
        "type": "label", "id": label["id"],
        "name": label.get("name"), "color": label.get("color"),
        "board": board.get("name"),
        "warning": "Sera removida de todos os cards do board.",
    }
    if not dry_run:
        ctx.client.request("DELETE", f"/labels/{label['id']}")
        ctx.invalidate(board["id"])
    return target


def _delete_attachment(ctx, operation, dry_run: bool):
    card = _context_card(ctx, operation)
    attachment_id = str(operation.get("attachment_id") or "").strip()
    if not ID_RE.match(attachment_id):
        raise ToolError("'delete_attachment' exige 'attachment_id'.")

    target = {"type": "attachment", "id": attachment_id, "card": card.get("name")}
    if not dry_run:
        ctx.client.request("DELETE", f"/cards/{card['id']}/attachments/{attachment_id}")
    return target


def _delete_board(ctx, operation, dry_run: bool):
    board = ctx.resolve_board(operation.get("board"))
    cards = ctx.client.request(
        "GET", f"/boards/{board['id']}/cards", params={"filter": "all", "fields": "id"}
    ) or []
    target = {
        "type": "board", "id": board["id"], "name": board.get("name"),
        "cards_affected": len(cards),
        "warning": "Apaga o board inteiro e todos os seus cards.",
    }
    if not dry_run:
        ctx.client.request("DELETE", f"/boards/{board['id']}")
        ctx.invalidate()
    return target


HANDLERS = {
    "delete_card": _delete_card,
    "delete_checklist": _delete_checklist,
    "delete_checkitem": _delete_checkitem,
    "delete_comment": _delete_comment,
    "delete_label": _delete_label,
    "delete_attachment": _delete_attachment,
    "delete_board": _delete_board,
}


def delete_items(ctx, args):
    dry_run = bool(args.get("dry_run"))

    actions = {
        name: (lambda ctx_, op, _handler=handler: {
            "deleted" if not dry_run else "would_delete": _handler(ctx_, op, dry_run)
        })
        for name, handler in HANDLERS.items()
    }

    outcome = run_batch(ctx, args.get("operations"), actions)
    outcome["dry_run"] = dry_run
    if dry_run:
        outcome["note"] = "Nada foi apagado. Repita sem 'dry_run' para efetivar."
    return outcome


TOOLS = [(DELETE_TOOL, delete_items)]
