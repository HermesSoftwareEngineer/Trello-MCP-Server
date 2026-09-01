"""manage_members: membros de boards e atribuicao em cards, em lote."""

from .common import (
    ToolError,
    as_list,
    run_batch,
)
from .schemas import REF, WRITE

MANAGE_MEMBERS_TOOL = {
    "name": "manage_members",
    "description": (
        "Gerencia quem participa dos boards e quem esta atribuido aos cards -- varias "
        "operacoes numa unica chamada, com resultado por operacao.\n\n"
        "'assign_to_card' e 'unassign_from_card' aceitam varios membros E varios cards de "
        "uma vez ('members' + 'cards'), aplicando o produto entre eles: util para atribuir "
        "uma pessoa a dez cards sem dez chamadas.\n\n"
        "Membros podem ser referenciados por id, username, nome ou 'me'. Para convidar "
        "alguem que ainda nao esta no board, use 'add_to_board' com 'email'."
    ),
    "annotations": {"title": "Gerenciar membros", **WRITE},
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
                            "enum": ["list_board_members", "add_to_board", "remove_from_board",
                                     "set_board_role", "assign_to_card", "unassign_from_card"],
                        },
                        "board": {**REF, "description": "Board alvo ou de contexto."},
                        "card": {**REF, "description": "Card alvo (alternativa a 'cards')."},
                        "cards": {
                            "type": "array", "items": {"type": "string"},
                            "description": "Varios cards de uma vez (ids ou nomes).",
                        },
                        "member": {**REF, "description": "Membro alvo (alternativa a 'members')."},
                        "members": {
                            "type": "array", "items": {"type": "string"},
                            "description": "Varios membros de uma vez. Aceita 'me'.",
                        },
                        "email": {
                            "type": "string",
                            "description": "Em 'add_to_board': convida por email alguem fora do board.",
                        },
                        "role": {
                            "type": "string", "enum": ["normal", "admin", "observer"],
                            "default": "normal",
                            "description": "Papel no board.",
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


def _member_refs(operation) -> list:
    refs = as_list(operation.get("members")) or as_list(operation.get("member"))
    if not refs:
        raise ToolError("Informe 'member' ou 'members'.")
    return refs


def _card_refs(operation) -> list:
    refs = as_list(operation.get("cards")) or as_list(operation.get("card"))
    if not refs:
        raise ToolError("Informe 'card' ou 'cards'.")
    return refs


def _list_board_members(ctx, operation):
    board = ctx.resolve_board(operation.get("board"))
    return {
        "board": {"id": board["id"], "name": board.get("name")},
        "members": [
            {"id": m["id"], "username": m.get("username"), "name": m.get("fullName")}
            for m in ctx.board_members(board["id"])
        ],
    }


def _add_to_board(ctx, operation):
    board = ctx.resolve_board(operation.get("board"))
    role = operation.get("role") or "normal"

    if operation.get("email"):
        ctx.client.request(
            "PUT", f"/boards/{board['id']}/members",
            params={"email": operation["email"], "type": role},
        )
        ctx.invalidate(board["id"])
        return {"invited": operation["email"], "role": role,
                "board": {"id": board["id"], "name": board.get("name")}}

    added, failed = [], []
    for ref in _member_refs(operation):
        try:
            member = ctx.resolve_member(ref, board["id"])
            ctx.client.request(
                "PUT", f"/boards/{board['id']}/members/{member['id']}", params={"type": role}
            )
            added.append({"id": member["id"], "username": member.get("username")})
        except Exception as exc:
            failed.append({"member": ref, "error": str(exc)})

    ctx.invalidate(board["id"])
    return {"board": {"id": board["id"], "name": board.get("name")},
            "added": added, "failed": failed, "role": role}


def _remove_from_board(ctx, operation):
    board = ctx.resolve_board(operation.get("board"))
    removed, failed = [], []
    for ref in _member_refs(operation):
        try:
            member = ctx.resolve_member(ref, board["id"])
            ctx.client.request("DELETE", f"/boards/{board['id']}/members/{member['id']}")
            removed.append({"id": member["id"], "username": member.get("username")})
        except Exception as exc:
            failed.append({"member": ref, "error": str(exc)})

    ctx.invalidate(board["id"])
    return {"board": {"id": board["id"], "name": board.get("name")},
            "removed": removed, "failed": failed}


def _set_board_role(ctx, operation):
    if not operation.get("role"):
        raise ToolError("'set_board_role' exige 'role'.")
    return _add_to_board(ctx, operation)


def _card_membership(ctx, operation, *, assign: bool):
    board = ctx.resolve_board(operation["board"]) if operation.get("board") else None
    board_id = board["id"] if board else None

    member_ids = []
    failed = []
    for ref in _member_refs(operation):
        try:
            member = ctx.resolve_member(ref, board_id)
            member_ids.append((ref, member))
        except Exception as exc:
            failed.append({"member": ref, "error": str(exc)})

    changed = []
    for card_ref in _card_refs(operation):
        try:
            card = ctx.resolve_card(card_ref, board_id)
        except Exception as exc:
            failed.append({"card": card_ref, "error": str(exc)})
            continue

        for ref, member in member_ids:
            try:
                if assign:
                    ctx.client.request(
                        "POST", f"/cards/{card['id']}/idMembers", params={"value": member["id"]}
                    )
                else:
                    ctx.client.request(
                        "DELETE", f"/cards/{card['id']}/idMembers/{member['id']}"
                    )
                changed.append({
                    "card": {"id": card["id"], "name": card.get("name")},
                    "member": member.get("username") or member["id"],
                })
            except Exception as exc:
                failed.append({"card": card_ref, "member": ref, "error": str(exc)})

    return {"assigned" if assign else "unassigned": changed, "failed": failed}


ACTIONS = {
    "list_board_members": _list_board_members,
    "add_to_board": _add_to_board,
    "remove_from_board": _remove_from_board,
    "set_board_role": _set_board_role,
    "assign_to_card": lambda ctx, op: _card_membership(ctx, op, assign=True),
    "unassign_from_card": lambda ctx, op: _card_membership(ctx, op, assign=False),
}


def manage_members(ctx, args):
    return run_batch(ctx, args.get("operations"), ACTIONS)


TOOLS = [(MANAGE_MEMBERS_TOOL, manage_members)]
