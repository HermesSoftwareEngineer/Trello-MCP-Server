"""manage_powerup_checklists: checklists do Power-Up (com inicio, fim e responsavel).

Seguranca: a chave do Power-Up acessa todos os quadros. Por isso o card e o
board sao SEMPRE resolvidos com o token do usuario (que so enxerga o que tem
acesso), e checklists, itens e padroes so sao localizados dentro deles --
nenhum id informado pelo usuario e repassado direto ao Power-Up.
"""

import re
from datetime import datetime, timedelta, timezone

from .. import powerup_client
from .common import ToolError, as_list, name_maps, parse_date, run_batch
from .schemas import DESTRUCTIVE, REF

# Datas do Power-Up sao dias, no horario de Brasilia (sem horario de verao).
BRASILIA = timezone(timedelta(hours=-3))
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
CLEAR = {"", "null", "none", "clear", "remove"}

ITEM_OBJECT = (
    "Cada item pode ser uma string (so o texto) ou {text, start, end, member, checked}."
)

MANAGE_POWERUP_TOOL = {
    "name": "manage_powerup_checklists",
    "description": (
        "Gerencia as checklists do Power-Up 'Checklists' (secao propria no verso do card, "
        "diferente das checklists nativas do Trello): varias checklists por card, itens com "
        "data de INICIO, data de FIM e responsavel, e checklists PADRAO por board. Varias "
        "operacoes numa unica chamada; falhas nao interrompem as demais.\n\n"
        "Use esta tool quando o usuario falar das checklists do Power-Up, de data de inicio "
        "de item ou de checklists padrao do board. Para as checklists nativas, use "
        "'manage_checklists_and_comments'.\n\n"
        "Datas: 'AAAA-MM-DD', 'today', 'tomorrow' ou offsets como '+3d'; '' limpa. "
        "Responsavel: nome, username ou 'me'; '' remove. Padroes do board sao modelos de "
        "checklist (nome + textos dos itens); 'apply_template' cria uma copia no card. "
        "As acoes delete_* apagam de verdade."
    ),
    "annotations": {"title": "Checklists do Power-Up", **DESTRUCTIVE},
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
                            "enum": [
                                "list", "add_checklist", "update_checklist", "delete_checklist",
                                "add_items", "update_item", "check_items", "delete_item",
                                "list_templates", "create_template", "update_template",
                                "delete_template", "apply_template",
                            ],
                            "description": (
                                "list = le as checklists do card; add_checklist = nova "
                                "checklist (com 'items' opcionais); update_checklist = renomeia; "
                                "add_items / update_item / check_items / delete_item = itens; "
                                "list_templates / create_template / update_template / "
                                "delete_template = padroes do board; apply_template = cria no "
                                "card uma checklist a partir de um padrao."
                            ),
                        },
                        "board": {**REF, "description": "Board de contexto (resolve o card por nome e os padroes)."},
                        "card": {**REF, "description": "Card alvo. Obrigatorio nas acoes de checklist e item."},
                        "checklist": {**REF, "description": "Checklist por id ou nome. Opcional se o card tiver so uma."},
                        "name": {"type": "string", "description": "Nome da checklist ou do padrao."},
                        "items": {
                            "type": "array",
                            "items": {"type": ["string", "object"]},
                            "description": "Itens a criar. " + ITEM_OBJECT
                            + " Em create/update_template: lista de textos.",
                        },
                        "item": {**REF, "description": "Item alvo (id ou texto) em update_item/delete_item."},
                        "item_names": {
                            "type": "array", "items": {"type": "string"},
                            "description": "Em 'check_items': textos (ou ids) dos itens.",
                        },
                        "text": {"type": "string", "description": "Novo texto do item em update_item."},
                        "start": {"type": "string", "description": "Data de inicio do item. '' limpa."},
                        "end": {"type": "string", "description": "Data de fim do item. '' limpa."},
                        "member": {**REF, "description": "Responsavel do item. Aceita 'me'; '' remove."},
                        "checked": {"type": "boolean", "description": "Estado do item."},
                        "template": {**REF, "description": "Padrao do board por id ou nome."},
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

def _api():
    return powerup_client.get_client()


def _card_of(ctx, operation) -> dict:
    board_hint = ctx.resolve_board(operation["board"])["id"] if operation.get("board") else None
    card = ctx.resolve_card(operation.get("card"), board_hint)
    if not card.get("idBoard"):
        raise ToolError("Nao consegui identificar o board desse card.")
    return card


def _board_id(ctx, operation) -> str:
    if operation.get("board"):
        return ctx.resolve_board(operation["board"])["id"]
    if operation.get("card"):
        return _card_of(ctx, operation)["idBoard"]
    raise ToolError("Informe 'board' (ou 'card') para trabalhar com os padroes.")


def _date(value, field: str):
    raw = str(value).strip()
    if raw.lower() in CLEAR:
        return None
    if DATE_RE.match(raw):
        return raw
    parsed = parse_date(raw, field=field)
    moment = datetime.strptime(parsed, "%Y-%m-%dT%H:%M:%S.000Z").replace(tzinfo=timezone.utc)
    return moment.astimezone(BRASILIA).strftime("%Y-%m-%d")


def _member_id(ctx, value, board_id):
    if value is None or str(value).strip() == "":
        return None
    return ctx.resolve_member(value, board_id)["id"]


def _checklists(card_id: str) -> list:
    return _api().request("GET", f"/cards/{card_id}/checklists")["checklists"]


def _match(entries, ref, field, label, available):
    ref = str(ref).strip()
    lowered = ref.lower()
    for entry in entries:
        if entry["id"] == ref or (entry.get(field) or "").lower() == lowered:
            return entry
    partial = [e for e in entries if lowered in (e.get(field) or "").lower()]
    if len(partial) == 1:
        return partial[0]
    if len(partial) > 1:
        raise ToolError(f"{label} {ref!r} e ambiguo: " + ", ".join(e.get(field) for e in partial))
    raise ToolError(f"{label} {ref!r} nao encontrado. {available}: "
                    + (", ".join(e.get(field) or e["id"] for e in entries) or "(nenhum)"))


def _resolve_checklist(card_id: str, ref) -> dict:
    checklists = _checklists(card_id)
    if not ref:
        if len(checklists) == 1:
            return checklists[0]
        if not checklists:
            raise ToolError("Esse card nao tem checklists do Power-Up. Crie uma com 'add_checklist'.")
        raise ToolError("O card tem varias checklists -- informe 'checklist'. Disponiveis: "
                        + ", ".join(c["nome"] for c in checklists))
    return _match(checklists, ref, "nome", "Checklist", "Checklists do card")


def _resolve_item(card_id: str, checklist_ref, item_ref) -> dict:
    if checklist_ref:
        items = _resolve_checklist(card_id, checklist_ref)["itens"]
    else:
        items = [i for c in _checklists(card_id) for i in c["itens"]]
    return _match(items, item_ref, "texto", "Item", "Itens")


def _resolve_template(board_id: str, ref) -> dict:
    if not ref:
        raise ToolError("Informe 'template' (id ou nome do padrao).")
    templates = _api().request("GET", f"/boards/{board_id}/modelos")["modelos"]
    return _match(templates, ref, "nome", "Padrao", "Padroes do board")


def _item_body(ctx, raw, board_id) -> tuple[dict, bool]:
    if isinstance(raw, str):
        raw = {"text": raw}
    if not isinstance(raw, dict):
        raise ToolError("Cada item precisa ser uma string ou um objeto. " + ITEM_OBJECT)
    text = raw.get("text") or raw.get("name")
    if not text:
        raise ToolError("Item sem texto. " + ITEM_OBJECT)
    body = {"texto": text}
    if raw.get("start") is not None:
        body["dataInicio"] = _date(raw["start"], "start do item")
    end = raw.get("end", raw.get("due"))
    if end is not None:
        body["dataFim"] = _date(end, "end do item")
    if raw.get("member"):
        body["responsavelId"] = _member_id(ctx, raw["member"], board_id)
    return body, bool(raw.get("checked"))


def _render_item(item, members) -> dict:
    member_id = item.get("responsavelId")
    return {
        "id": item["id"],
        "text": item["texto"],
        "checked": item["feito"],
        "start": item.get("dataInicio"),
        "end": item.get("dataFim"),
        "member": (members.get(member_id) or member_id) if member_id else None,
    }


def _render_checklist(checklist, members) -> dict:
    items = checklist["itens"]
    return {
        "id": checklist["id"],
        "name": checklist["nome"],
        "progress": f"{sum(1 for i in items if i['feito'])}/{len(items)}",
        "items": [_render_item(i, members) for i in items],
    }


def _members(ctx, board_id) -> dict:
    try:
        return name_maps(ctx, board_id)[2]
    except Exception:  # sem nomes, mostra os ids
        return {}


# --------------------------------------------------------------------------
# Acoes
# --------------------------------------------------------------------------

def _list(ctx, operation):
    card = _card_of(ctx, operation)
    members = _members(ctx, card["idBoard"])
    return {
        "card": {"id": card["id"], "name": card.get("name")},
        "checklists": [_render_checklist(c, members) for c in _checklists(card["id"])],
    }


def _create_items(ctx, checklist_id, raw_items, board_id, members) -> list:
    created = []
    for raw in as_list(raw_items):
        body, checked = _item_body(ctx, raw, board_id)
        item = _api().request("POST", f"/checklists/{checklist_id}/items", body)
        if checked:
            item = _api().request("PATCH", f"/items/{item['id']}", {"feito": True})
        created.append(_render_item(item, members))
    return created


def _add_checklist(ctx, operation):
    card = _card_of(ctx, operation)
    board_id = card["idBoard"]
    members = _members(ctx, board_id)
    body = {"boardId": board_id}
    if operation.get("name"):
        body["nome"] = operation["name"]

    # Itens validados antes de criar qualquer coisa: entrada ruim nao deixa checklist pela metade.
    parsed = [_item_body(ctx, raw, board_id) for raw in as_list(operation.get("items"))]
    body["itens"] = [item for item, _ in parsed]
    checklist = _api().request("POST", f"/cards/{card['id']}/checklists", body)
    for created, (_, checked) in zip(checklist["itens"], parsed):
        if checked:
            created.update(_api().request("PATCH", f"/items/{created['id']}", {"feito": True}))
    return {"card": {"id": card["id"], "name": card.get("name")},
            "checklist": _render_checklist(checklist, members)}


def _update_checklist(ctx, operation):
    card = _card_of(ctx, operation)
    checklist = _resolve_checklist(card["id"], operation.get("checklist"))
    if not operation.get("name"):
        raise ToolError("Informe o novo 'name' da checklist.")
    updated = _api().request("PATCH", f"/checklists/{checklist['id']}", {"nome": operation["name"]})
    return {"checklist": {"id": updated["id"], "name": updated["nome"]}}


def _delete_checklist(ctx, operation):
    card = _card_of(ctx, operation)
    if not operation.get("checklist"):
        raise ToolError("Informe 'checklist' para apagar (por seguranca nao e inferida).")
    checklist = _resolve_checklist(card["id"], operation["checklist"])
    _api().request("DELETE", f"/checklists/{checklist['id']}")
    return {"deleted_checklist": {"id": checklist["id"], "name": checklist["nome"],
                                  "items_deleted": len(checklist["itens"])}}


def _add_items(ctx, operation):
    card = _card_of(ctx, operation)
    if not operation.get("items"):
        raise ToolError("'add_items' exige 'items'.")
    checklist = _resolve_checklist(card["id"], operation.get("checklist"))
    members = _members(ctx, card["idBoard"])
    return {
        "checklist": {"id": checklist["id"], "name": checklist["nome"]},
        "items_created": _create_items(ctx, checklist["id"], operation["items"],
                                       card["idBoard"], members),
    }


def _update_item(ctx, operation):
    card = _card_of(ctx, operation)
    if not operation.get("item"):
        raise ToolError("Informe 'item' (texto ou id).")
    item = _resolve_item(card["id"], operation.get("checklist"), operation["item"])

    body = {}
    if operation.get("text"):
        body["texto"] = operation["text"]
    if "start" in operation:
        body["dataInicio"] = _date(operation["start"], "start")
    if "end" in operation:
        body["dataFim"] = _date(operation["end"], "end")
    if "member" in operation:
        body["responsavelId"] = _member_id(ctx, operation["member"], card["idBoard"])
    if operation.get("checked") is not None:
        body["feito"] = bool(operation["checked"])
    if not body:
        raise ToolError("Nenhum campo para atualizar (text, start, end, member, checked).")

    updated = _api().request("PATCH", f"/items/{item['id']}", body)
    return {"item": _render_item(updated, _members(ctx, card["idBoard"]))}


def _check_items(ctx, operation):
    card = _card_of(ctx, operation)
    refs = as_list(operation.get("item_names"))
    if not refs:
        raise ToolError("'check_items' exige 'item_names'.")
    checked = bool(operation.get("checked", True))
    changed, failed = [], []
    for ref in refs:
        try:
            item = _resolve_item(card["id"], operation.get("checklist"), ref)
            updated = _api().request("PATCH", f"/items/{item['id']}", {"feito": checked})
            changed.append({"id": updated["id"], "text": updated["texto"], "checked": updated["feito"]})
        except Exception as exc:
            failed.append({"item": ref, "error": str(exc)})
    return {"changed": changed, "failed": failed}


def _delete_item(ctx, operation):
    card = _card_of(ctx, operation)
    if not operation.get("item"):
        raise ToolError("Informe 'item' (texto ou id).")
    item = _resolve_item(card["id"], operation.get("checklist"), operation["item"])
    _api().request("DELETE", f"/items/{item['id']}")
    return {"deleted_item": {"id": item["id"], "text": item["texto"]}}


def _render_template(template) -> dict:
    return {"id": template["id"], "name": template["nome"], "items": template["itens"]}


def _template_texts(operation) -> list:
    texts = []
    for raw in as_list(operation.get("items")):
        text = raw.get("text") or raw.get("name") if isinstance(raw, dict) else raw
        if not isinstance(text, str) or not text.strip():
            raise ToolError("Itens de padrao sao textos (strings).")
        texts.append(text)
    return texts


def _list_templates(ctx, operation):
    board_id = _board_id(ctx, operation)
    templates = _api().request("GET", f"/boards/{board_id}/modelos")["modelos"]
    return {"board_id": board_id, "templates": [_render_template(t) for t in templates]}


def _create_template(ctx, operation):
    board_id = _board_id(ctx, operation)
    if not operation.get("name"):
        raise ToolError("Informe 'name' do padrao.")
    created = _api().request("POST", f"/boards/{board_id}/modelos",
                             {"nome": operation["name"], "itens": _template_texts(operation)})
    return {"template": _render_template(created)}


def _update_template(ctx, operation):
    board_id = _board_id(ctx, operation)
    template = _resolve_template(board_id, operation.get("template"))
    body = {}
    if operation.get("name"):
        body["nome"] = operation["name"]
    if "items" in operation:
        body["itens"] = _template_texts(operation)
    if not body:
        raise ToolError("Informe 'name' e/ou 'items' para atualizar o padrao.")
    updated = _api().request("PATCH", f"/modelos/{template['id']}", body)
    return {"template": _render_template(updated)}


def _delete_template(ctx, operation):
    board_id = _board_id(ctx, operation)
    template = _resolve_template(board_id, operation.get("template"))
    _api().request("DELETE", f"/modelos/{template['id']}")
    return {"deleted_template": {"id": template["id"], "name": template["nome"]}}


def _apply_template(ctx, operation):
    card = _card_of(ctx, operation)
    template = _resolve_template(card["idBoard"], operation.get("template"))
    body = {"boardId": card["idBoard"], "modeloId": template["id"]}
    if operation.get("name"):
        body["nome"] = operation["name"]
    checklist = _api().request("POST", f"/cards/{card['id']}/checklists", body)
    return {"card": {"id": card["id"], "name": card.get("name")},
            "checklist": _render_checklist(checklist, _members(ctx, card["idBoard"]))}


ACTIONS = {
    "list": _list,
    "add_checklist": _add_checklist,
    "update_checklist": _update_checklist,
    "delete_checklist": _delete_checklist,
    "add_items": _add_items,
    "update_item": _update_item,
    "check_items": _check_items,
    "delete_item": _delete_item,
    "list_templates": _list_templates,
    "create_template": _create_template,
    "update_template": _update_template,
    "delete_template": _delete_template,
    "apply_template": _apply_template,
}


def manage_powerup_checklists(ctx, args):
    return run_batch(ctx, args.get("operations"), ACTIONS)


TOOLS = [(MANAGE_POWERUP_TOOL, manage_powerup_checklists)]
