"""read_attachment: le o conteudo de um anexo de card (texto, PDF, imagem).

As URLs de anexos do Trello exigem autenticacao, entao um agente nao consegue
abri-las sozinho. Esta tool baixa o arquivo com as credenciais do usuario
autenticado e devolve o conteudo em blocos MCP.
"""

import base64
import io
import mimetypes
import re
from urllib.parse import unquote, urlparse

from .common import ID_RE, ContentResult, ToolError, _match_by_name, _preview, to_datetime
from .schemas import READ_ONLY, REF

MAX_TEXT_BYTES = 60 * 1024          # texto devolvido ao modelo
MAX_PDF_BYTES = 10 * 1024 * 1024    # PDF baixado inteiro para extrair o texto
MAX_IMAGE_BYTES = 5 * 1024 * 1024   # imagem devolvida em base64
MAX_PDF_PAGES = 60

TEXT_EXTENSIONS = {".txt", ".md", ".markdown", ".json", ".csv"}
TEXT_MIMES = {"text/plain", "text/markdown", "text/csv", "application/json", "text/x-markdown"}

ATTACHMENT_FIELDS = "id,name,url,bytes,date,mimeType,isUpload"

READ_ATTACHMENT_TOOL = {
    "name": "read_attachment",
    "description": (
        "Le o CONTEUDO de um arquivo anexado a um card (as URLs de anexo do Trello exigem "
        "login, entao nao da para abri-las por fora). Informe o card e o anexo (id ou nome; "
        "use manage_attachments/list para ver os anexos).\n\n"
        "Devolve: texto para .txt/.md/.json/.csv e PDFs (texto extraido, ate ~60 KB, "
        "indicando se foi truncado); para PNG/JPG, a propria imagem (ate 5 MB). Outros tipos "
        "e tamanhos acima do limite voltam com erro claro. Anexos que sao so link externo "
        "nao sao baixados. Somente leitura."
    ),
    "annotations": {"title": "Ler anexo de card", **READ_ONLY},
    "inputSchema": {
        "type": "object",
        "properties": {
            "card": {**REF, "description": "Card dono do anexo (id, shortLink ou nome)."},
            "attachment": {
                "type": "string",
                "description": (
                    "Id do anexo ou nome (ex.: 'roteiro-v1.pdf'). Com varios anexos de mesmo "
                    "nome, vale o mais recente."
                ),
            },
            "board": {**REF, "description": "Board de contexto para resolver o nome do card."},
        },
        "required": ["card", "attachment"],
        "additionalProperties": False,
    },
}


def _filename(att: dict) -> str:
    path = unquote(urlparse(att.get("url") or "").path)
    return path.rsplit("/", 1)[-1] if "/" in path else ""


def _find_attachment(attachments: list, ref: str) -> dict:
    ref = ref.strip()
    if not attachments:
        raise ToolError("Esse card nao tem anexos.")

    if ID_RE.match(ref):
        for att in attachments:
            if att.get("id") == ref:
                return att
        raise ToolError(
            f"O anexo {ref} nao pertence a este card. Anexos do card: "
            + _preview([f"{a.get('name')} ({a.get('id')})" for a in attachments])
        )

    needle = ref.lower()
    exact = [a for a in attachments
             if needle in (str(a.get("name") or "").lower(), _filename(a).lower())]
    if exact:
        # Mesmo nome varias vezes (versoes reenviadas): o mais recente.
        return max(exact, key=lambda a: str(a.get("date") or ""))

    # Prefixo/substring; aponta ambiguidade listando os candidatos.
    match = _match_by_name(attachments, ref, ("name",))
    if match is None:
        raise ToolError(
            f"Anexo {ref!r} nao encontrado neste card. Anexos: "
            + _preview([f"{a.get('name')} ({a.get('id')})" for a in attachments])
        )
    return match


def _kind(att: dict, head: bytes = b"") -> str | None:
    """Classifica pelas assinaturas do arquivo quando da; senao por mime/extensao."""
    name = (att.get("name") or _filename(att) or "").lower()
    mime = (att.get("mimeType") or mimetypes.guess_type(name)[0] or "").lower()
    ext = "." + name.rsplit(".", 1)[-1] if "." in name else ""
    if head.startswith(b"%PDF"):
        return "pdf"
    if head.startswith(b"\x89PNG\r\n\x1a\n"):
        return "png"
    if head.startswith(b"\xff\xd8\xff"):
        return "jpeg"
    if mime == "application/pdf" or ext == ".pdf":
        return "pdf"
    if mime == "image/png" or ext == ".png":
        return "png"
    if mime == "image/jpeg" or ext in (".jpg", ".jpeg"):
        return "jpeg"
    if mime in TEXT_MIMES or ext in TEXT_EXTENSIONS:
        return "text"
    return None


_TJ_RE = re.compile(rb"\(((?:\\.|[^\\()])*)\)\s*Tj|\[((?:\\.|[^\]])*)\]\s*TJ", re.S)
_STR_RE = re.compile(rb"\(((?:\\.|[^\\()])*)\)", re.S)
_ESC = {b"n": b"\n", b"r": b"\r", b"t": b"\t", b"b": b"\b", b"f": b"\f",
        b"(": b"(", b")": b")", b"\\": b"\\"}


def _unescape_pdf(raw: bytes) -> str:
    out, i = bytearray(), 0
    while i < len(raw):
        ch = raw[i:i + 1]
        if ch == b"\\" and i + 1 < len(raw):
            nxt = raw[i + 1:i + 2]
            if nxt in _ESC:
                out += _ESC[nxt]
                i += 2
                continue
            octal = re.match(rb"[0-7]{1,3}", raw[i + 1:i + 4])
            if octal:
                out.append(int(octal.group(), 8) & 0xFF)
                i += 1 + len(octal.group())
                continue
            i += 1
            continue
        out += ch
        i += 1
    return bytes(out).decode("cp1252", errors="replace")


def _pdf_fallback_text(data: bytes) -> str:
    """Extracao minima para PDFs simples (texto nao comprimido, `(txt) Tj`)."""
    lines = []
    for m in _TJ_RE.finditer(data):
        if m.group(1) is not None:
            lines.append(_unescape_pdf(m.group(1)))
        else:
            lines.append("".join(_unescape_pdf(s) for s in _STR_RE.findall(m.group(2))))
    return "\n".join(lines)


def extract_pdf_text(data: bytes) -> tuple[str, int, bool]:
    """(texto, total_de_paginas, leu_so_parte). pypdf primeiro; se falhar ou
    vier vazio, tenta o extrator minimo."""
    text, pages, cut = "", 0, False
    try:
        from pypdf import PdfReader

        reader = PdfReader(io.BytesIO(data))
        pages = len(reader.pages)
        cut = pages > MAX_PDF_PAGES
        parts = [(page.extract_text() or "") for page in reader.pages[:MAX_PDF_PAGES]]
        text = "\n\n".join(p.strip() for p in parts if p.strip())
    except Exception:  # noqa: BLE001 - PDF malformado: cai no extrator minimo
        text = ""
    if not text.strip():
        text = _pdf_fallback_text(data)
    return text.strip(), pages, cut


def _clip(text: str) -> tuple[str, bool]:
    raw = text.encode("utf-8")
    if len(raw) <= MAX_TEXT_BYTES:
        return text, False
    return raw[:MAX_TEXT_BYTES].decode("utf-8", errors="ignore"), True


def _header(att: dict, extra: str = "") -> str:
    return (f"[anexo: {att.get('name')} | id {att.get('id')} | "
            f"{att.get('mimeType') or 'tipo desconhecido'} | {att.get('bytes')} bytes{extra}]")


def read_attachment(ctx, args):
    card_ref, att_ref = args.get("card"), args.get("attachment")
    if not card_ref:
        raise ToolError("Informe o card (id ou nome).")
    if not att_ref or not str(att_ref).strip():
        raise ToolError("Informe o anexo (id ou nome). Use manage_attachments/list para ver os anexos.")

    board_hint = ctx.resolve_board(args["board"])["id"] if args.get("board") else None
    # O card e resolvido com as credenciais do usuario: sem acesso, o Trello recusa aqui.
    card = ctx.resolve_card(card_ref, board_hint)
    attachments = ctx.client.request(
        "GET", f"/cards/{card['id']}/attachments", params={"fields": ATTACHMENT_FIELDS}
    ) or []
    # So os anexos DESTE card entram na busca: id de outro card nao e aceito.
    att = _find_attachment(attachments, str(att_ref))

    if not att.get("isUpload"):
        raise ToolError(
            f"O anexo {att.get('name')!r} e um link externo ({att.get('url')}), nao um arquivo "
            "enviado ao Trello; so leio arquivos enviados."
        )

    kind = _kind(att)
    if kind is None:
        raise ToolError(
            f"Tipo nao suportado: {att.get('name')!r} ({att.get('mimeType') or 'sem tipo'}). "
            "Suportados: .txt .md .json .csv, PDF, PNG e JPG."
        )
    size = att.get("bytes")
    if isinstance(size, int):
        if kind == "pdf" and size > MAX_PDF_BYTES:
            raise ToolError(f"PDF de {size} bytes excede o limite de {MAX_PDF_BYTES} bytes.")
        if kind in ("png", "jpeg") and size > MAX_IMAGE_BYTES:
            raise ToolError(f"Imagem de {size} bytes excede o limite de {MAX_IMAGE_BYTES} bytes.")

    limit = {"text": MAX_TEXT_BYTES + 4, "pdf": MAX_PDF_BYTES,
             "png": MAX_IMAGE_BYTES, "jpeg": MAX_IMAGE_BYTES}[kind]
    data, _content_type, truncated = ctx.client.download(att["url"], limit)

    if not data:
        raise ToolError(f"O anexo {att.get('name')!r} esta vazio.")
    kind = _kind(att, data[:16]) or kind  # confia na assinatura do arquivo

    if kind in ("pdf", "png", "jpeg") and truncated:
        raise ToolError(
            f"O anexo {att.get('name')!r} excede o limite de leitura ({limit} bytes) "
            "e nao foi lido."
        )

    if kind in ("png", "jpeg"):
        mime = "image/png" if kind == "png" else "image/jpeg"
        return ContentResult([
            {"type": "text", "text": _header(att, " | imagem")},
            {"type": "image", "data": base64.b64encode(data).decode("ascii"), "mimeType": mime},
        ])

    if kind == "pdf":
        text, pages, cut = extract_pdf_text(data)
        if not text:
            raise ToolError(
                f"Nao consegui extrair texto do PDF {att.get('name')!r} (pode ser imagem "
                "escaneada ou PDF protegido)."
            )
        text, clipped = _clip(text)
        notes = f" | {pages} paginas" if pages else ""
        if cut:
            notes += f" (so as primeiras {MAX_PDF_PAGES} foram lidas)"
        if clipped:
            notes += f" | TRUNCADO em {MAX_TEXT_BYTES} bytes"
        return ContentResult([{"type": "text", "text": f"{_header(att, notes)}\n\n{text}"}])

    if b"\x00" in data[:4096]:
        raise ToolError(f"O anexo {att.get('name')!r} parece binario, nao texto.")
    text, clipped = _clip(data.decode("utf-8-sig", errors="replace"))
    notes = f" | TRUNCADO em {MAX_TEXT_BYTES} bytes (o arquivo e maior)" if (clipped or truncated) else ""
    return ContentResult([{"type": "text", "text": f"{_header(att, notes)}\n\n{text}"}])


TOOLS = [(READ_ATTACHMENT_TOOL, read_attachment)]
