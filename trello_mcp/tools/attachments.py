"""manage_attachments: anexar arquivos/links a cards, listar e definir capa."""

import base64
import binascii
import mimetypes

from .common import ToolError, as_bool_param, run_batch
from .schemas import REF, WRITE

# Limite de upload do Trello: 10 MB no plano gratuito, 250 MB nos pagos.
# O arquivo chega codificado no JSON da chamada, entao o teto aqui e conservador.
MAX_UPLOAD_BYTES = 10 * 1024 * 1024

ATTACHMENT_FIELDS = "id,name,url,bytes,date,mimeType,isUpload,idMember"

MANAGE_ATTACHMENTS_TOOL = {
    "name": "manage_attachments",
    "description": (
        "Gerencia anexos de cards -- varias operacoes numa unica chamada, cada uma "
        "independente.\n\n"
        "'add' anexa um LINK (url) ou um ARQUIVO (content_base64 + filename; ate 10 MB). "
        "Com 'set_cover' o anexo vira a capa do card. 'list' lista os anexos de um card. "
        "'set_cover' / 'remove_cover' controlam a capa de um anexo ja existente.\n\n"
        "Para APAGAR um anexo use a tool 'delete_items' (delete_attachment)."
    ),
    "annotations": {"title": "Anexos de cards", **WRITE},
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
                            "enum": ["add", "list", "set_cover", "remove_cover"],
                            "description": (
                                "add = anexa link ou arquivo; list = lista anexos do card; "
                                "set_cover = usa um anexo como capa; remove_cover = tira a capa."
                            ),
                        },
                        "board": {**REF, "description": "Board de contexto para resolver nomes."},
                        "card": {**REF, "description": "Card alvo."},
                        "url": {"type": "string", "description": "Em 'add': URL a anexar como link."},
                        "content_base64": {
                            "type": "string",
                            "description": "Em 'add': conteudo do arquivo em base64 (alternativa a 'url').",
                        },
                        "filename": {
                            "type": "string",
                            "description": "Em 'add' com arquivo: nome do arquivo, com extensao.",
                        },
                        "mime_type": {
                            "type": "string",
                            "description": "Em 'add' com arquivo: tipo MIME. Se omitido, deduz da extensao.",
                        },
                        "name": {"type": "string", "description": "Nome exibido do anexo."},
                        "set_cover": {
                            "type": "boolean",
                            "description": "Em 'add': define o anexo como capa (so faz sentido para imagens).",
                        },
                        "attachment": {
                            "type": "string",
                            "description": "Em 'set_cover': id do anexo.",
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


def _card(ctx, operation):
    board_hint = ctx.resolve_board(operation["board"])["id"] if operation.get("board") else None
    return ctx.resolve_card(operation.get("card"), board_hint)


def _render(att: dict) -> dict:
    return {
        "id": att.get("id"),
        "name": att.get("name"),
        "url": att.get("url"),
        "bytes": att.get("bytes"),
        "mime_type": att.get("mimeType"),
        "is_upload": att.get("isUpload"),
        "date": att.get("date"),
    }


def _decode(operation) -> tuple[str, bytes, str]:
    filename = (operation.get("filename") or "").strip()
    if not filename:
        raise ToolError("Anexar arquivo exige 'filename' (com extensao).")
    raw = operation["content_base64"]
    if raw.lstrip().startswith("data:") and "," in raw:
        raw = raw.split(",", 1)[1]  # aceita data URI
    try:
        content = base64.b64decode(raw)
    except (binascii.Error, ValueError) as exc:
        raise ToolError(f"'content_base64' invalido: {exc}") from exc
    if not content:
        raise ToolError("O arquivo esta vazio.")
    if len(content) > MAX_UPLOAD_BYTES:
        raise ToolError(
            f"Arquivo de {len(content)} bytes excede o limite de {MAX_UPLOAD_BYTES} bytes."
        )
    mime = operation.get("mime_type") or mimetypes.guess_type(filename)[0] or "application/octet-stream"
    return filename, content, mime


def _add(ctx, operation):
    card = _card(ctx, operation)
    has_url = bool(operation.get("url"))
    has_file = bool(operation.get("content_base64"))
    if has_url == has_file:
        raise ToolError("'add' exige exatamente um entre 'url' e 'content_base64'.")

    params = {}
    if operation.get("name"):
        params["name"] = operation["name"]
    if operation.get("set_cover") is not None:
        params["setCover"] = as_bool_param(operation["set_cover"])

    if has_url:
        params["url"] = operation["url"]
        att = ctx.client.request("POST", f"/cards/{card['id']}/attachments", params=params)
    else:
        filename, content, mime = _decode(operation)
        att = ctx.client.request(
            "POST", f"/cards/{card['id']}/attachments", params=params,
            files={"file": (filename, content, mime)},
        )
    return {"card": {"id": card["id"], "name": card.get("name")}, "attachment": _render(att or {})}


def _list(ctx, operation):
    card = _card(ctx, operation)
    atts = ctx.client.request(
        "GET", f"/cards/{card['id']}/attachments", params={"fields": ATTACHMENT_FIELDS}
    ) or []
    return {
        "card": {"id": card["id"], "name": card.get("name")},
        "count": len(atts),
        "attachments": [_render(a) for a in atts],
    }


def _set_cover(ctx, operation):
    card = _card(ctx, operation)
    attachment = str(operation.get("attachment") or "").strip()
    if not attachment:
        raise ToolError("'set_cover' exige 'attachment' (id do anexo).")
    ctx.client.request("PUT", f"/cards/{card['id']}", params={"idAttachmentCover": attachment})
    return {"card": {"id": card["id"], "name": card.get("name")}, "cover": attachment}


def _remove_cover(ctx, operation):
    card = _card(ctx, operation)
    ctx.client.request("PUT", f"/cards/{card['id']}", params={"idAttachmentCover": ""})
    return {"card": {"id": card["id"], "name": card.get("name")}, "cover": None}


ACTIONS = {
    "add": _add,
    "list": _list,
    "set_cover": _set_cover,
    "remove_cover": _remove_cover,
}


def manage_attachments(ctx, args):
    return run_batch(ctx, args.get("operations"), ACTIONS)


TOOLS = [(MANAGE_ATTACHMENTS_TOOL, manage_attachments)]
