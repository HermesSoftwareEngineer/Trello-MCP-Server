"""read_attachment: le ou baixa um anexo de card, de QUALQUER tipo.

As URLs de anexos do Trello exigem autenticacao, entao um agente nao consegue
abri-las sozinho. Esta tool baixa o arquivo com as credenciais do usuario
autenticado e devolve o conteudo em blocos MCP: texto/PDF/imagem direto; zip,
docx, xlsx, pptx com listagem/texto extraido; qualquer outro tipo (mp4, audio...)
com metadados e, via mode="download", o arquivo em base64 em partes.

Nada e gravado em disco: tudo acontece em memoria, com limites. O token do
Trello nunca aparece nos retornos (a URL do anexo exige login e nem e devolvida).
"""

import base64
import hashlib
import io
import json
import mimetypes
import posixpath
import re
import xml.etree.ElementTree as ET
import zipfile
from urllib.parse import unquote, urlparse

from .common import ID_RE, ContentResult, ToolError, _match_by_name, _preview, to_datetime
from .schemas import READ_ONLY, REF

MAX_TEXT_BYTES = 60 * 1024          # texto devolvido ao modelo
MAX_PDF_BYTES = 10 * 1024 * 1024    # PDF baixado inteiro para extrair o texto
MAX_IMAGE_BYTES = 5 * 1024 * 1024   # imagem devolvida em base64
MAX_PDF_PAGES = 60

MAX_SCAN_BYTES = 64 * 1024 * 1024    # offset+parte de um download: ate onde leio o arquivo
MAX_ARCHIVE_BYTES = 30 * 1024 * 1024  # zip/docx/xlsx/pptx baixados inteiros para inspecao
DEFAULT_PART_BYTES = 96 * 1024       # tamanho de uma parte no mode=download (base64 ~128 KB)
MAX_PART_BYTES = 512 * 1024
MAX_ZIP_ENTRIES = 500                # entradas listadas
MAX_ENTRY_BYTES = 20 * 1024 * 1024   # descompactado de UMA entrada (anti zip-bomb)
MAX_XML_BYTES = 8 * 1024 * 1024      # parte XML de docx/xlsx/pptx
MAX_SHEETS, MAX_ROWS = 20, 2000
SUSPECT_RATIO = 200                  # descompactado/compactado acima disso = aviso

TEXT_EXTENSIONS = {".txt", ".md", ".markdown", ".json", ".csv"}
TEXT_MIMES = {"text/plain", "text/markdown", "text/csv", "application/json", "text/x-markdown"}

ATTACHMENT_FIELDS = "id,name,url,bytes,date,mimeType,isUpload"

READ_ATTACHMENT_TOOL = {
    "name": "read_attachment",
    "description": (
        "Le ou BAIXA o conteudo de um arquivo anexado a um card, de QUALQUER tipo (as URLs de "
        "anexo do Trello exigem login, entao nao da para abri-las por fora). Informe o card e o "
        "anexo (id ou nome; use manage_attachments/list para ver os anexos).\n\n"
        "mode='auto' (padrao): texto para .txt/.md/.json/.csv e PDFs (texto extraido, ate ~60 KB); "
        "imagem PNG/JPG (ate 5 MB); .zip -> lista do conteudo SEM extrair; .docx/.xlsx/.pptx -> "
        "texto extraido; qualquer outro tipo (mp4, audio, etc.) -> metadados (nome, tipo, tamanho, "
        "id) e como baixar.\n"
        "mode='info': so metadados, sem baixar.\n"
        "mode='download': devolve o arquivo em base64, em partes (JSON com data, offset, "
        "next_offset, done, sha256 da parte). Repita com offset=next_offset (ou part=2,3...) ate "
        "done=true e junte as partes. 'length' ajusta o tamanho da parte (padrao 96 KB, max 512 KB; "
        "arquivos lidos ate 64 MB). Funciona para qualquer anexo enviado.\n"
        "entry='pasta/arquivo.ext': dentro de um .zip, le UM arquivo (texto direto; binario via "
        "mode='download'). Nada e gravado em disco. Anexos que sao so link externo nao sao "
        "baixados. Somente leitura."
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
            "mode": {
                "type": "string", "enum": ["auto", "info", "download"],
                "description": "auto (padrao): conteudo util do tipo; info: so metadados; "
                               "download: arquivo em base64 em partes.",
            },
            "offset": {"type": "integer", "minimum": 0,
                       "description": "Byte inicial da parte (download)."},
            "part": {"type": "integer", "minimum": 1,
                     "description": "Parte 1-based (atalho: offset = (part-1)*length)."},
            "length": {"type": "integer", "minimum": 1,
                       "description": f"Bytes por parte (padrao {DEFAULT_PART_BYTES}, max {MAX_PART_BYTES})."},
            "entry": {"type": "string",
                      "description": "Nome de um arquivo DENTRO de um .zip para ler/baixar."},
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


def _fmt_size(n) -> str:
    if not isinstance(n, int):
        return "tamanho desconhecido"
    for unit in ("bytes", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n} {unit}" if unit == "bytes" else f"{n:.1f} {unit}"
        n /= 1024


def _json_result(obj: dict, header: str | None = None) -> ContentResult:
    blocks = []
    if header:
        blocks.append({"type": "text", "text": header})
    blocks.append({"type": "text", "text": json.dumps(obj, ensure_ascii=False, indent=1)})
    return ContentResult(blocks)


def _meta(att: dict) -> dict:
    """Metadados seguros do anexo: sem URL (exige login) e sem credenciais."""
    name = att.get("name") or _filename(att)
    return {
        "name": name,
        "id": att.get("id"),
        "mimeType": att.get("mimeType") or mimetypes.guess_type(name or "")[0] or None,
        "bytes": att.get("bytes"),
        "size": _fmt_size(att.get("bytes")),
        "date": att.get("date"),
    }


def _container_kind(att: dict) -> str | None:
    """zip / docx / xlsx / pptx pelo nome ou mime (a assinatura confirma depois)."""
    name = (att.get("name") or _filename(att) or "").lower()
    mime = (att.get("mimeType") or "").lower()
    ext = "." + name.rsplit(".", 1)[-1] if "." in name else ""
    if ext == ".docx" or "wordprocessingml" in mime:
        return "docx"
    if ext == ".xlsx" or "spreadsheetml" in mime:
        return "xlsx"
    if ext == ".pptx" or "presentationml" in mime:
        return "pptx"
    if ext == ".zip" or mime in ("application/zip", "application/x-zip-compressed"):
        return "zip"
    return None


def _media_kind(att: dict) -> str | None:
    name = (att.get("name") or _filename(att) or "").lower()
    mime = (att.get("mimeType") or mimetypes.guess_type(name)[0] or "").lower()
    for prefix in ("video", "audio", "image"):
        if mime.startswith(prefix + "/"):
            return prefix
    return None


def _how_to_download(att: dict, size) -> dict:
    parts = None
    if isinstance(size, int):
        parts = max(1, -(-size // DEFAULT_PART_BYTES))
    return {
        "como_baixar": (
            "read_attachment com mode='download' (e offset=next_offset ou part=N) devolve o "
            "arquivo em base64 em partes; junte as partes na ordem. 'length' ajusta o tamanho "
            f"(max {MAX_PART_BYTES} bytes)."
        ),
        "partes_com_tamanho_padrao": parts,
        "limite_de_leitura_bytes": MAX_SCAN_BYTES,
    }


def _clean_name(name: str) -> str:
    return "".join(c if c.isprintable() else "?" for c in name)


def _unsafe_path(name: str) -> bool:
    norm = name.replace("\\", "/")
    return (norm.startswith("/") or re.match(r"^[A-Za-z]:", norm) is not None
            or ".." in posixpath.normpath(norm).split("/"))


def _open_zip(data: bytes, att: dict):
    try:
        return zipfile.ZipFile(io.BytesIO(data))
    except (zipfile.BadZipFile, ValueError, OSError):
        raise ToolError(
            f"{att.get('name')!r} nao e um arquivo zip valido (corrompido?). "
            "Use mode='download' para obter os bytes."
        ) from None


def _fetch_whole(ctx, att, kind_label: str) -> bytes:
    size = att.get("bytes")
    if isinstance(size, int) and size > MAX_ARCHIVE_BYTES:
        raise ToolError(
            f"{kind_label} de {size} bytes passa do limite de inspecao ({MAX_ARCHIVE_BYTES}); "
            "use mode='download' para baixar em partes."
        )
    data, _ct, truncated = ctx.client.download(att["url"], MAX_ARCHIVE_BYTES)
    if truncated:
        raise ToolError(f"{kind_label} passa do limite de inspecao ({MAX_ARCHIVE_BYTES} bytes).")
    if not data:
        raise ToolError(f"O anexo {att.get('name')!r} esta vazio.")
    return data


def _zip_listing(att, data):
    zf = _open_zip(data, att)
    infos = zf.infolist()
    total_unc = sum(i.file_size for i in infos)
    total_comp = sum(i.compress_size for i in infos) or 1
    entries, warnings = [], []
    for i in infos[:MAX_ZIP_ENTRIES]:
        e = {"name": _clean_name(i.filename), "bytes": i.file_size, "compressed": i.compress_size}
        if i.is_dir():
            e["dir"] = True
        if i.flag_bits & 1:
            e["encrypted"] = True
        if _unsafe_path(i.filename):
            e["unsafe_path"] = True
        entries.append(e)
    if len(infos) > MAX_ZIP_ENTRIES:
        warnings.append(f"Lista cortada: {len(infos)} entradas, mostrando {MAX_ZIP_ENTRIES}.")
    if total_unc / total_comp > SUSPECT_RATIO:
        warnings.append("Taxa de compressao muito alta (possivel zip-bomb); nada foi extraido.")
    if any(e.get("unsafe_path") for e in entries):
        warnings.append("Ha entradas com caminho suspeito (.. ou absoluto); nunca extraia para o disco "
                        "sem sanear os nomes.")
    out = {
        **_meta(att), "tipo": "zip", "entradas": len(infos),
        "total_descompactado_bytes": total_unc, "arquivos": entries,
        "dica": "Nada foi extraido. Para ler um arquivo de dentro: entry='<nome>' "
                "(texto direto; binario com mode='download').",
    }
    if warnings:
        out["avisos"] = warnings
    return out


def _read_entry(zf: zipfile.ZipFile, name: str, att, limit: int, skip: int = 0):
    """Le ate `limit` bytes (apos `skip`) de UMA entrada, em streaming e com teto de
    descompressao. Devolve (bytes, total_conhecido_ou_None, mais_dados)."""
    try:
        info = zf.getinfo(name)
    except KeyError:
        near = [i.filename for i in zf.infolist() if name.lower() in i.filename.lower()][:10]
        raise ToolError(
            f"Entrada {name!r} nao existe no zip."
            + (f" Parecidas: {_preview([_clean_name(n) for n in near])}" if near else
               " Use mode='auto' sem entry para ver a lista.")
        ) from None
    if info.is_dir():
        raise ToolError(f"{name!r} e uma pasta, nao um arquivo.")
    if info.flag_bits & 1:
        raise ToolError(f"{name!r} esta criptografado (com senha); nao consigo ler.")
    if info.file_size > MAX_ENTRY_BYTES:
        raise ToolError(f"{name!r} tem {info.file_size} bytes descompactado; passa do limite "
                        f"de {MAX_ENTRY_BYTES} por entrada.")
    want = skip + limit
    try:
        with zf.open(info) as fh:
            raw = fh.read(min(want, MAX_ENTRY_BYTES) + 1)
    except (zipfile.BadZipFile, NotImplementedError, RuntimeError, zlib_error) as exc:
        raise ToolError(f"Nao consegui descompactar {name!r}: {type(exc).__name__}.") from None
    more = len(raw) > want
    return raw[skip:want], info.file_size, more


try:  # zlib.error nao e subclasse de BadZipFile
    from zlib import error as zlib_error
except ImportError:  # pragma: no cover
    zlib_error = ValueError


# ---- docx / xlsx / pptx: texto simples, sem dependencias ----------------------

def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _parse_xml(zf: zipfile.ZipFile, name: str):
    try:
        info = zf.getinfo(name)
    except KeyError:
        return None
    if info.file_size > MAX_XML_BYTES:
        raise ToolError(f"A parte {name} tem {info.file_size} bytes; grande demais para extrair "
                        "o texto (use mode='download').")
    with zf.open(info) as fh:
        raw = fh.read(MAX_XML_BYTES + 1)
    if len(raw) > MAX_XML_BYTES or b"<!DOCTYPE" in raw[:4096].upper() or b"<!ENTITY" in raw.upper():
        raise ToolError(f"A parte {name} foi recusada (grande demais ou com declaracoes DTD).")
    try:
        return ET.fromstring(raw)
    except ET.ParseError:
        raise ToolError(f"A parte {name} esta malformada.") from None


def _paragraph_texts(root) -> list[str]:
    """Um texto por paragrafo (w:p / a:p), juntando w:t / a:t."""
    lines = []
    for p in root.iter():
        if _local(p.tag) != "p":
            continue
        buf = []
        for el in p.iter():
            tag = _local(el.tag)
            if tag == "t" and el.text:
                buf.append(el.text)
            elif tag == "tab":
                buf.append("\t")
            elif tag in ("br", "cr"):
                buf.append("\n")
        lines.append("".join(buf))
    return lines


def _docx_text(zf):
    root = _parse_xml(zf, "word/document.xml")
    if root is None:
        raise ToolError("docx sem word/document.xml.")
    return "\n".join(_paragraph_texts(root)).strip(), None


def _pptx_text(zf):
    slides = sorted(
        (n for n in zf.namelist() if re.match(r"ppt/slides/slide\d+\.xml$", n)),
        key=lambda n: int(re.findall(r"\d+", n)[-1]))
    out = []
    for n in slides:
        root = _parse_xml(zf, n)
        body = "\n".join(t for t in _paragraph_texts(root) if t.strip())
        out.append(f"--- Slide {len(out) + 1} ---\n{body}")
    return "\n\n".join(out).strip(), f"{len(slides)} slides"


def _col_index(ref: str) -> int:
    letters = re.match(r"[A-Za-z]+", ref or "")
    n = 0
    for ch in (letters.group().upper() if letters else "A"):
        n = n * 26 + ord(ch) - 64
    return n - 1


def _xlsx_text(zf):
    shared = []
    root = _parse_xml(zf, "xl/sharedStrings.xml")
    if root is not None:
        for si in root:
            shared.append("".join(t.text or "" for t in si.iter() if _local(t.tag) == "t"))
    names = {}
    wb = _parse_xml(zf, "xl/workbook.xml")
    rels = _parse_xml(zf, "xl/_rels/workbook.xml.rels")
    targets = {}
    if rels is not None:
        for r in rels:
            tgt = r.get("Target", "")
            targets[r.get("Id")] = tgt.lstrip("/") if tgt.startswith("/") else "xl/" + tgt
    ordered = []
    if wb is not None:
        for el in wb.iter():
            if _local(el.tag) == "sheet":
                rid = next((v for k, v in el.attrib.items() if k.endswith("}id")), None)
                ordered.append((el.get("name"), targets.get(rid)))
    if not ordered:
        ordered = [(f"Planilha{i + 1}", n) for i, n in enumerate(sorted(
            n for n in zf.namelist() if re.match(r"xl/worksheets/sheet\d+\.xml$", n)))]
    out, shown = [], ordered[:MAX_SHEETS]
    for name, path in shown:
        sheet = _parse_xml(zf, path) if path else None
        if sheet is None:
            continue
        out.append(f"--- Planilha: {name} ---")
        rows = 0
        for row in sheet.iter():
            if _local(row.tag) != "row":
                continue
            if rows >= MAX_ROWS:
                out.append(f"[cortado em {MAX_ROWS} linhas]")
                break
            cells = {}
            for c in row:
                if _local(c.tag) != "c":
                    continue
                kind = c.get("t")
                v = next((x for x in c if _local(x.tag) == "v"), None)
                if kind == "inlineStr":
                    val = "".join(t.text or "" for t in c.iter() if _local(t.tag) == "t")
                elif kind == "s" and v is not None and (v.text or "").isdigit() and int(v.text) < len(shared):
                    val = shared[int(v.text)]
                else:
                    val = (v.text or "") if v is not None else ""
                cells[_col_index(c.get("r"))] = val.replace("\t", " ").replace("\n", " ")
            if cells:
                out.append("\t".join(cells.get(i, "") for i in range(max(cells) + 1)).rstrip())
                rows += 1
    note = f"{len(ordered)} planilhas" + (f" (so as primeiras {MAX_SHEETS})" if len(ordered) > MAX_SHEETS else "")
    return "\n".join(out).strip(), note


_EXTRACTORS = {"docx": _docx_text, "xlsx": _xlsx_text, "pptx": _pptx_text}


# ---- mode=download ----------------------------------------------------------------

def _part_window(args, size) -> tuple[int, int]:
    length = args.get("length")
    length = DEFAULT_PART_BYTES if length in (None, "") else length
    if not isinstance(length, int) or isinstance(length, bool) or length < 1:
        raise ToolError("'length' deve ser um inteiro positivo.")
    length = min(length, MAX_PART_BYTES)
    offset, part = args.get("offset"), args.get("part")
    if offset not in (None, "") and part not in (None, ""):
        raise ToolError("Informe offset OU part, nao os dois.")
    if part not in (None, ""):
        if not isinstance(part, int) or isinstance(part, bool) or part < 1:
            raise ToolError("'part' deve ser um inteiro >= 1.")
        offset = (part - 1) * length
    offset = 0 if offset in (None, "") else offset
    if not isinstance(offset, int) or isinstance(offset, bool) or offset < 0:
        raise ToolError("'offset' deve ser um inteiro >= 0.")
    if isinstance(size, int) and offset >= size > 0:
        raise ToolError(f"offset {offset} esta alem do fim do arquivo ({size} bytes).")
    if offset + length > MAX_SCAN_BYTES:
        raise ToolError(
            f"offset+length ({offset + length}) passa do limite de leitura de {MAX_SCAN_BYTES} bytes "
            "por chamada; arquivos maiores nao podem ser baixados por aqui."
        )
    return offset, length


def _chunk_result(att, chunk: bytes, offset: int, total, more: bool, extra: dict | None = None):
    end = offset + len(chunk)
    done = not more
    out = {
        **_meta(att), **(extra or {}),
        "encoding": "base64",
        "offset": offset, "length": len(chunk), "next_offset": None if done else end,
        "total_bytes": total if isinstance(total, int) else None,
        "done": done,
        "sha256_da_parte": hashlib.sha256(chunk).hexdigest(),
        "data": base64.b64encode(chunk).decode("ascii"),
    }
    if not done:
        out["proximo_passo"] = f"Chame de novo com offset={end} (mesmos card/attachment/entry/length)."
    return _json_result(out)


def _download_mode(ctx, att, args, entry):
    size = att.get("bytes")
    offset, length = _part_window(args, size)
    if entry:
        zf = _open_zip(_fetch_whole(ctx, att, "zip"), att)
        chunk, total, more = _read_entry(zf, entry, att, length, offset)
        if offset and not chunk:
            raise ToolError(f"offset {offset} esta alem do fim da entrada ({total} bytes).")
        return _chunk_result(att, chunk, offset, total, more, {"entry": _clean_name(entry)})
    chunk, _ct, more = ctx.client.download(att["url"], length, offset)
    if not chunk:
        raise ToolError(f"Nada para ler a partir do offset {offset}; o anexo "
                        f"{att.get('name')!r} esta vazio ou o offset passou do fim.")
    return _chunk_result(att, chunk, offset, size, more)


# ---- tool -------------------------------------------------------------------------

def _text_result(att, text: str, notes: str = "", clipped: bool = False):
    text, cut = _clip(text)
    if cut or clipped:
        notes += f" | TRUNCADO em {MAX_TEXT_BYTES} bytes"
    return ContentResult([{"type": "text", "text": f"{_header(att, notes)}\n\n{text}"}])


def read_attachment(ctx, args):
    card_ref, att_ref = args.get("card"), args.get("attachment")
    if not card_ref:
        raise ToolError("Informe o card (id ou nome).")
    if not att_ref or not str(att_ref).strip():
        raise ToolError("Informe o anexo (id ou nome). Use manage_attachments/list para ver os anexos.")
    mode = args.get("mode") or "auto"
    if mode not in ("auto", "info", "download"):
        raise ToolError("mode deve ser auto, info ou download.")
    entry = args.get("entry")
    if entry is not None and (not isinstance(entry, str) or not entry.strip()):
        entry = None

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

    container = _container_kind(att)
    if entry and container not in ("zip", "docx", "xlsx", "pptx"):
        raise ToolError("entry so vale para anexos .zip.")

    if mode == "info":
        info = {**_meta(att), "tipo": container or _kind(att) or _media_kind(att) or "outro",
                **_how_to_download(att, att.get("bytes"))}
        return _json_result(info)
    if mode == "download":
        return _download_mode(ctx, att, args, entry)

    # ---- mode=auto ----
    if entry:  # ler um arquivo de dentro do zip
        zf = _open_zip(_fetch_whole(ctx, att, "zip"), att)
        raw, total, more = _read_entry(zf, entry, att, MAX_TEXT_BYTES + 4)
        sniff = _kind({"name": entry}, raw[:16])
        label = f"[entrada {_clean_name(entry)!r} do zip {att.get('name')!r} | {total} bytes]"
        if sniff == "text" and b"\x00" not in raw[:4096]:
            text, cut = _clip(raw.decode("utf-8-sig", errors="replace"))
            note = f" | TRUNCADO em {MAX_TEXT_BYTES} bytes" if (cut or more) else ""
            return ContentResult([{"type": "text", "text": f"{label}{note}\n\n{text}"}])
        if sniff in ("png", "jpeg") and total <= MAX_IMAGE_BYTES and not more:
            return ContentResult([
                {"type": "text", "text": label + " | imagem"},
                {"type": "image", "data": base64.b64encode(raw).decode("ascii"),
                 "mimeType": "image/png" if sniff == "png" else "image/jpeg"},
            ])
        return _json_result({
            "entry": _clean_name(entry), "bytes": total, "tipo": sniff or "binario",
            "como_baixar": "read_attachment com entry + mode='download' devolve em base64 em partes.",
        }, label)

    kind = _kind(att)
    if kind is None and container:
        data = _fetch_whole(ctx, att, container)
        if container == "zip":
            return _json_result(_zip_listing(att, data))
        try:
            zf = zipfile.ZipFile(io.BytesIO(data))
            text, note = _EXTRACTORS[container](zf)
        except zipfile.BadZipFile:
            raise ToolError(f"{att.get('name')!r} nao e um {container} valido (corrompido?). "
                            "Use mode='download'.") from None
        if not text:
            return _json_result({**_meta(att), "tipo": container,
                                 "aviso": "Nenhum texto encontrado.", **_how_to_download(att, att.get("bytes"))})
        return _text_result(att, text, f" | {container}" + (f" | {note}" if note else ""))

    if kind is None:
        return _json_result({
            **_meta(att), "tipo": _media_kind(att) or "outro",
            "aviso": "Sem leitor de conteudo para este tipo; devolvo os metadados.",
            **_how_to_download(att, att.get("bytes")),
        })

    size = att.get("bytes")
    if isinstance(size, int):
        if kind == "pdf" and size > MAX_PDF_BYTES:
            raise ToolError(f"PDF de {size} bytes excede o limite de {MAX_PDF_BYTES} bytes para "
                            "extrair texto; use mode='download' para baixar em partes.")
        if kind in ("png", "jpeg") and size > MAX_IMAGE_BYTES:
            raise ToolError(f"Imagem de {size} bytes excede o limite de {MAX_IMAGE_BYTES} bytes; "
                            "use mode='download' para baixar em partes.")

    limit = {"text": MAX_TEXT_BYTES + 4, "pdf": MAX_PDF_BYTES,
             "png": MAX_IMAGE_BYTES, "jpeg": MAX_IMAGE_BYTES}[kind]
    data, _content_type, truncated = ctx.client.download(att["url"], limit)

    if not data:
        raise ToolError(f"O anexo {att.get('name')!r} esta vazio.")
    kind = _kind(att, data[:16]) or kind  # confia na assinatura do arquivo

    if kind in ("pdf", "png", "jpeg") and truncated:
        raise ToolError(
            f"O anexo {att.get('name')!r} excede o limite de leitura ({limit} bytes) "
            "e nao foi lido; use mode='download'."
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
                "escaneada ou PDF protegido). Use mode='download' para obter o arquivo."
            )
        text, clipped = _clip(text)
        notes = f" | {pages} paginas" if pages else ""
        if cut:
            notes += f" (so as primeiras {MAX_PDF_PAGES} foram lidas)"
        if clipped:
            notes += f" | TRUNCADO em {MAX_TEXT_BYTES} bytes"
        return ContentResult([{"type": "text", "text": f"{_header(att, notes)}\n\n{text}"}])

    if b"\x00" in data[:4096]:
        raise ToolError(f"O anexo {att.get('name')!r} parece binario, nao texto; use mode='download'.")
    text, clipped = _clip(data.decode("utf-8-sig", errors="replace"))
    notes = f" | TRUNCADO em {MAX_TEXT_BYTES} bytes (o arquivo e maior)" if (clipped or truncated) else ""
    return ContentResult([{"type": "text", "text": f"{_header(att, notes)}\n\n{text}"}])


TOOLS = [(READ_ATTACHMENT_TOOL, read_attachment)]
