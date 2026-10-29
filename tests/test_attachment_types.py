"""read_attachment 0.3.0: qualquer tipo (info, download em partes, zip, office, midia)."""
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

import base64
import hashlib
import io
import json
import zipfile

from fake_trello import FakeTrello
from trello_mcp import create_app, db, mcp_server
from trello_mcp.auth import generate_connector_token
from trello_mcp.crypto import encrypt, hash_token
from trello_mcp.tools import call_tool
from trello_mcp.tools.common import ToolError

PASS, FAIL = [], []


def check(label, cond, detail=""):
    (PASS if cond else FAIL).append(label)
    print(f"  [{'OK  ' if cond else 'FALHOU'}] {label}" + (f"  -> {detail}" if detail and not cond else ""))


def section(title):
    print(f"\n=== {title} ===")


fake = FakeTrello()
card = fake.card1["id"] if isinstance(fake.card1, dict) else fake.card1


def attach(filename, content, mime=None):
    out = call_tool(fake, "manage_attachments", {"operations": [{
        "action": "add", "card": card, "filename": filename,
        "content_base64": base64.b64encode(content).decode(), **({"mime_type": mime} if mime else {}),
    }]})
    assert out["results"][0]["status"] == "ok", out


def read(**args):
    return call_tool(fake, "read_attachment", args)


def text_of(r):
    return r.content[0]["text"]


def jbody(r):
    return json.loads(r.content[-1]["text"])


def raises(fn, needle=None):
    try:
        fn()
    except ToolError as e:
        return needle is None or needle.lower() in str(e).lower()
    return False


def mkzip(files):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for n, c in files.items():
            z.writestr(n, c)
    return buf.getvalue()


# --------------------------------------------------------------------------
section("Midia e tipos desconhecidos: metadados + download em partes")
mp4 = b"\x00\x00\x00\x18ftypmp42" + bytes(range(256)) * 3000  # ~750 KB
attach("003-ssh-previa-cena2.mp4", mp4, "video/mp4")
attach("notas.md", "# Título\nação".encode())
j = jbody(read(card=card, attachment="003-ssh-previa-cena2.mp4"))
check("mp4 (auto): metadados e como baixar, sem erro",
      j["tipo"] == "video" and j["bytes"] == len(mp4) and "download" in j["como_baixar"], str(j)[:200])
check("mp4 (auto): sem URL no retorno", "trello.com" not in json.dumps(j))
check("mp4 (auto) nao baixou o arquivo", not fake.downloads)
j = jbody(read(card=card, attachment="previa", mode="info"))
check("mode=info", j["name"] == "003-ssh-previa-cena2.mp4" and j["size"].endswith("KB"))

got, off, n = b"", 0, 0
while True:
    j = jbody(read(card=card, attachment="previa", mode="download", offset=off, length=100_000))
    chunk = base64.b64decode(j["data"])
    if n == 0:
        check("sha256 da parte confere", hashlib.sha256(chunk).hexdigest() == j["sha256_da_parte"])
    got += chunk
    n += 1
    if j["done"]:
        break
    off = j["next_offset"]
check("download em partes remonta o arquivo identico", got == mp4 and n == 8, f"{n} partes")
j = jbody(read(card=card, attachment="previa", mode="download", part=2, length=100_000))
check("part=2 equivale a offset=100000", j["offset"] == 100_000 and base64.b64decode(j["data"]) == mp4[100_000:200_000])
j = jbody(read(card=card, attachment="previa", mode="download", length=10 ** 9))
check("length enorme e limitado a 512 KB", j["length"] == 512 * 1024 and not j["done"], str(j["length"]))
for bad in (dict(offset=len(mp4) + 5), dict(length=0), dict(offset=1, part=1), dict(offset=-1),
            dict(offset=70 * 1024 * 1024)):
    check(f"download recusa parametros ruins {bad}",
          raises(lambda b=bad: read(card=card, attachment="previa", mode="download", **b)))
j = jbody(read(card=card, attachment="notas.md", mode="download"))
check("download tambem serve tipos que o auto le", base64.b64decode(j["data"]).startswith(b"# T") and j["done"])
check("mode invalido -> erro", raises(lambda: read(card=card, attachment="notas.md", mode="xyz")))
attach("musica.mp3", b"ID3" + b"\x00" * 50, "audio/mpeg")
check("audio: metadados", jbody(read(card=card, attachment="musica.mp3"))["tipo"] == "audio")
attach("x.bin", b"\x01\x02", "application/octet-stream")
check("tipo desconhecido: metadados", jbody(read(card=card, attachment="x.bin"))["tipo"] == "outro")

# --------------------------------------------------------------------------
section("Zip: listar sem extrair, ler entrada, protecoes")
z = mkzip({"cena2/roteiro.txt": "linha um\nação", "cena2/img.bin": b"\x00\x01" * 100, "../evil.txt": "x"})
attach("003-ssh-material-de-apoio.zip", z, "application/zip")
j = jbody(read(card=card, attachment="material"))
names = {e["name"]: e for e in j["arquivos"]}
check("zip: lista o conteudo", j["tipo"] == "zip" and "cena2/roteiro.txt" in names and j["entradas"] == 3)
check("zip: path traversal sinalizado", names["../evil.txt"].get("unsafe_path") and "avisos" in j)
check("zip entry texto", "ação" in text_of(read(card=card, attachment="material", entry="cena2/roteiro.txt")))
j = jbody(read(card=card, attachment="material", entry="cena2/img.bin"))
check("zip entry binario -> orienta download", "download" in j["como_baixar"])
j = jbody(read(card=card, attachment="material", entry="cena2/img.bin", mode="download"))
check("zip entry binario via download", base64.b64decode(j["data"]) == b"\x00\x01" * 100 and j["done"])
wz = mkzip({"demo\README.md": "windows ok"})
attach("win.zip", wz, "application/zip")
check("zip entry aceita / para nomes gravados com barra invertida",
      "windows ok" in text_of(read(card=card, attachment="win.zip", entry="demo/README.md")))
j = jbody(read(card=card, attachment="previa", mode="download", part="2", length="100000", offset=None))
check("numeros como texto sao aceitos", j["offset"] == 100_000 and j["length"] == 100_000)
PNG_SIG = bytes([0x89]) + b"PNG" + bytes([13, 10, 26, 10])
png = PNG_SIG + bytes(range(256)) * 600
attach("img.zip", mkzip({"a/b.png": png}), "application/zip")
r = read(card=card, attachment="img.zip", entry="a/b.png")
check("zip entry PNG grande vira bloco image", r.content[1]["type"] == "image" and base64.b64decode(r.content[1]["data"]) == png)
check("zip entry inexistente -> erro", raises(lambda: read(card=card, attachment="material", entry="nao/existe"), "nao existe"))
check("entry em nao-zip -> erro", raises(lambda: read(card=card, attachment="notas.md", entry="x")))
bomb = mkzip({"zeros.bin": b"\x00" * (60 * 1024 * 1024)})
attach("bomba.zip", bomb, "application/zip")
j = jbody(read(card=card, attachment="bomba.zip"))
check(f"zip-bomb: lista com aviso, nada extraido ({len(bomb)} bytes)", "zip-bomb" in " ".join(j.get("avisos", [])))
check("zip-bomb: entrada gigante recusada", raises(lambda: read(card=card, attachment="bomba.zip", entry="zeros.bin"), "limite"))
attach("quebrado.zip", b"PK\x03\x04lixo", "application/zip")
check("zip corrompido -> erro claro", raises(lambda: read(card=card, attachment="quebrado.zip"), "valido"))

# --------------------------------------------------------------------------
section("docx / xlsx / pptx")
W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
docx = mkzip({"word/document.xml": f'<w:document xmlns:w="{W}"><w:body><w:p><w:r><w:t>Titulo do roteiro</w:t></w:r></w:p>'
              '<w:p><w:r><w:t>Cena </w:t></w:r><w:r><w:t>ação 1</w:t></w:r></w:p></w:body></w:document>'})
attach("roteiro.docx", docx)
t = text_of(read(card=card, attachment="roteiro.docx"))
check("docx: texto extraido", "Titulo do roteiro" in t and "Cena ação 1" in t, t[:200])
NS = ('xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
      'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"')
xlsx = mkzip({
    "xl/workbook.xml": f'<workbook {NS}><sheets><sheet name="Plano" sheetId="1" r:id="rId1"/></sheets></workbook>',
    "xl/_rels/workbook.xml.rels": '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                                  '<Relationship Id="rId1" Target="worksheets/sheet1.xml"/></Relationships>',
    "xl/sharedStrings.xml": f"<sst {NS}><si><t>Cena</t></si><si><t>Duração</t></si></sst>",
    "xl/worksheets/sheet1.xml": f'<worksheet {NS}><sheetData><row r="1"><c r="A1" t="s"><v>0</v></c><c r="C1" t="s"><v>1</v></c></row>'
                                '<row r="2"><c r="A2"><v>1</v></c><c r="B2"><v>3.5</v></c></row></sheetData></worksheet>'})
attach("plano.xlsx", xlsx)
t = text_of(read(card=card, attachment="plano.xlsx"))
check("xlsx: planilha, linhas e colunas", "Plano" in t and "Cena\t\tDuração" in t and "1\t3.5" in t, t[:300])
pptx = mkzip({"ppt/slides/slide1.xml": '<p:sld xmlns:p="x" xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main">'
              "<a:p><a:r><a:t>Abertura</a:t></a:r></a:p></p:sld>"})
attach("deck.pptx", pptx)
check("pptx: texto dos slides", "Abertura" in text_of(read(card=card, attachment="deck.pptx")))
evil = mkzip({"word/document.xml": f'<?xml version="1.0"?><!DOCTYPE x [<!ENTITY a "aaaa">]><w:document xmlns:w="{W}"><w:p><w:t>&a;</w:t></w:p></w:document>'})
attach("evil.docx", evil)
check("docx com DTD/entidades recusado", raises(lambda: read(card=card, attachment="evil.docx"), "DTD"))

# --------------------------------------------------------------------------
section("Via /mcp e vazamento de segredos")
mcp_server.client_for_user = lambda user: fake
http = create_app().test_client()
token = generate_connector_token()
db.upsert_user(trello_member_id="at-member", trello_username="h", trello_full_name="H",
               trello_key_enc=encrypt("k"), trello_token_enc=encrypt("t"),
               connector_token_hash=hash_token(token))
auth = {"Authorization": f"Bearer {token}"}
res = http.post("/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {
    "name": "read_attachment", "arguments": {"card": card, "attachment": "previa", "mode": "download", "length": 1000}}},
    headers=auth).get_json()["result"]
payload = json.loads(res["content"][-1]["text"])
check("via /mcp: JSON do download", res["isError"] is False and payload["length"] == 1000)
tool = next(t for t in http.post("/mcp", json={"jsonrpc": "2.0", "id": 2, "method": "tools/list"}, headers=auth)
            .get_json()["result"]["tools"] if t["name"] == "read_attachment")
props = tool["inputSchema"]["properties"]
check("schema: mode/offset/part/length/entry; so card+attachment obrigatorios",
      {"mode", "offset", "part", "length", "entry"} <= set(props)
      and set(tool["inputSchema"]["required"]) == {"card", "attachment"} and tool["annotations"]["readOnlyHint"] is True)
leak = json.dumps(res) + text_of(read(card=card, attachment="material")) + json.dumps(jbody(read(card=card, attachment="previa")))
check("retornos nao trazem URL do Trello nem credenciais", "trello.com" not in leak and "oauth" not in leak.lower())

db.delete_user_by_member_id("at-member")
print("\n" + "=" * 60)
print(f"PASSOU: {len(PASS)}   FALHOU: {len(FAIL)}")
for name in FAIL:
    print(f"  - {name}")
sys.exit(1 if FAIL else 0)
