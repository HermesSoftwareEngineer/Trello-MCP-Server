"""get_activity: historico de acoes de um board, card ou membro."""

from .common import ToolError, as_list, join_ids, parse_date
from .schemas import READ_ONLY, REF

COMMON_ACTION_TYPES = [
    "createCard", "updateCard", "deleteCard", "commentCard", "copyCard",
    "addMemberToCard", "removeMemberFromCard", "addAttachmentToCard",
    "addChecklistToCard", "updateCheckItemStateOnCard",
    "createList", "updateList", "createBoard", "updateBoard",
    "addMemberToBoard", "removeMemberFromBoard", "createLabel", "updateLabel",
    "moveCardFromBoard", "moveCardToBoard",
]

ACTIVITY_TOOL = {
    "name": "get_activity",
    "description": (
        "Historico de atividade de um board, de um card ou de um membro: quem fez o que "
        "e quando. Use para 'o que mudou esta semana', auditoria, ou para reconstruir o "
        "contexto de um card.\n\n"
        "Informe exatamente um alvo: 'board', 'card' ou 'member'. Filtre por tipo de acao "
        "('action_types'), por autor ('by_members') e por periodo ('since'/'before', que "
        "aceitam ISO, 'today' ou offsets como '-7d').\n\n"
        "Cada acao vem com um resumo em texto ja pronto, alem dos dados crus."
    ),
    "annotations": {"title": "Historico de atividade", **READ_ONLY},
    "inputSchema": {
        "type": "object",
        "properties": {
            "board": {**REF, "description": "Board cujo historico sera lido."},
            "card": {**REF, "description": "Card cujo historico sera lido."},
            "member": {**REF, "description": "Membro cujo historico sera lido. Aceita 'me'."},
            "action_types": {
                "type": "array",
                "items": {"type": "string", "enum": COMMON_ACTION_TYPES},
                "description": "Tipos de acao a incluir. Omitir traz todos os tipos relevantes.",
            },
            "by_members": {
                "type": "array", "items": {"type": "string"},
                "description": "So acoes feitas por estes membros (id, username ou 'me').",
            },
            "since": {"type": "string", "description": "Inicio do periodo. ISO, 'today', '-7d'."},
            "before": {"type": "string", "description": "Fim do periodo. ISO, 'today'."},
            "limit": {"type": "integer", "default": 100, "description": "Maximo de acoes (ate 1000)."},
            "include_raw": {
                "type": "boolean", "default": False,
                "description": "Inclui o objeto 'data' cru de cada acao.",
            },
        },
        "additionalProperties": False,
    },
}


def get_activity(ctx, args):
    targets = [key for key in ("board", "card", "member") if args.get(key)]
    if len(targets) != 1:
        raise ToolError("Informe exatamente um alvo: 'board', 'card' ou 'member'.")
    target = targets[0]

    if target == "board":
        entity = ctx.resolve_board(args["board"])
        path = f"/boards/{entity['id']}/actions"
        label = {"board": entity.get("name"), "board_id": entity["id"]}
    elif target == "card":
        entity = ctx.resolve_card(args["card"])
        path = f"/cards/{entity['id']}/actions"
        label = {"card": entity.get("name"), "card_id": entity["id"]}
    else:
        entity = ctx.resolve_member(args["member"])
        path = f"/members/{entity['id']}/actions"
        label = {"member": entity.get("username"), "member_id": entity["id"]}

    # 'limit' vai ate 1000 em /boards/{id}/actions e /members/{id}/actions.
    # Em /cards/{id}/actions a doc so documenta paginacao por 'page' (50 por
    # pagina); 'limit' e aceito na pratica, mas nao conte com ele la.
    params = {
        "limit": min(int(args.get("limit") or 100), 1000),
        "memberCreator_fields": "id,username,fullName",
    }
    action_types = as_list(args.get("action_types"))
    if action_types:
        params["filter"] = join_ids(action_types)
    if args.get("since"):
        params["since"] = parse_date(args["since"], field="since")
    if args.get("before"):
        params["before"] = parse_date(args["before"], field="before")

    actions = ctx.client.request("GET", path, params=params) or []

    author_ids = None
    if args.get("by_members"):
        board_id = entity["id"] if target == "board" else entity.get("idBoard")
        author_ids = set(ctx.resolve_member_ids(args["by_members"], board_id))

    rendered = []
    for action in actions:
        author = action.get("memberCreator") or {}
        if author_ids is not None and author.get("id") not in author_ids:
            continue

        entry = {
            "id": action.get("id"),
            "type": action.get("type"),
            "date": action.get("date"),
            "author": author.get("username") or author.get("fullName"),
            "summary": _summarize(action),
        }
        if args.get("include_raw"):
            entry["data"] = action.get("data")
        rendered.append(entry)

    return {
        "target": label,
        "count": len(rendered),
        "period": {
            "since": params.get("since"),
            "before": params.get("before"),
            "oldest_returned": rendered[-1]["date"] if rendered else None,
            "newest_returned": rendered[0]["date"] if rendered else None,
        },
        "actions": rendered,
    }


def _summarize(action: dict) -> str:
    """Frase curta descrevendo a acao, para a IA nao precisar interpretar 'data'."""
    data = action.get("data") or {}
    author = (action.get("memberCreator") or {}).get("username") or "alguem"
    card = (data.get("card") or {}).get("name")
    list_after = (data.get("listAfter") or {}).get("name")
    list_before = (data.get("listBefore") or {}).get("name")
    list_name = (data.get("list") or {}).get("name")
    board = (data.get("board") or {}).get("name")
    action_type = action.get("type")

    if action_type == "createCard":
        return f"{author} criou o card '{card}'" + (f" em '{list_name}'" if list_name else "")
    if action_type == "commentCard":
        text = (data.get("text") or "").replace("\n", " ")
        preview = text[:120] + ("..." if len(text) > 120 else "")
        return f"{author} comentou em '{card}': {preview}"
    if action_type == "updateCard":
        old = data.get("old") or {}
        if list_after and list_before:
            return f"{author} moveu '{card}' de '{list_before}' para '{list_after}'"
        if "closed" in old:
            state = "arquivou" if data.get("card", {}).get("closed") else "desarquivou"
            return f"{author} {state} o card '{card}'"
        changed = ", ".join(k for k in old if k != "id") or "campos"
        return f"{author} alterou {changed} em '{card}'"
    if action_type in ("addMemberToCard", "removeMemberFromCard"):
        verb = "atribuiu" if action_type == "addMemberToCard" else "removeu"
        who = (data.get("member") or {}).get("name") or (action.get("member") or {}).get("username", "")
        return f"{author} {verb} {who} em '{card}'".replace("  ", " ")
    if action_type == "updateCheckItemStateOnCard":
        item = (data.get("checkItem") or {}).get("name")
        state = (data.get("checkItem") or {}).get("state")
        verb = "marcou" if state == "complete" else "desmarcou"
        return f"{author} {verb} '{item}' em '{card}'"
    if action_type == "addChecklistToCard":
        return f"{author} adicionou a checklist '{(data.get('checklist') or {}).get('name')}' em '{card}'"
    if action_type == "addAttachmentToCard":
        return f"{author} anexou '{(data.get('attachment') or {}).get('name')}' em '{card}'"
    if action_type in ("createList", "updateList"):
        verb = "criou" if action_type == "createList" else "alterou"
        return f"{author} {verb} a lista '{list_name}'"
    if action_type in ("addMemberToBoard", "removeMemberFromBoard"):
        verb = "adicionou" if action_type == "addMemberToBoard" else "removeu"
        who = (data.get("member") or {}).get("name", "")
        return f"{author} {verb} {who} do board '{board}'".replace("  ", " ")
    if action_type in ("moveCardToBoard", "moveCardFromBoard"):
        return f"{author} moveu '{card}' entre boards"
    if action_type == "deleteCard":
        return f"{author} apagou um card de '{list_name or board}'"

    target = card or list_name or board or ""
    return f"{author}: {action_type}" + (f" ({target})" if target else "")


TOOLS = [(ACTIVITY_TOOL, get_activity)]
