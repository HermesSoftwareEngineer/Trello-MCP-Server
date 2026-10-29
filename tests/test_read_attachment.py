"""read_attachment, endurecimento de anexos e velocidade do handshake."""
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

import base64
import json
import time

import requests

from fake_trello import FakeTrello
from trello_mcp import create_app, db, mcp_server, trello_client
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


def make_pdf(lines):
    """PDF simples como o da fabrica: Helvetica, WinAnsi, texto sem compressao, `(txt) Tj`."""
    content = b"BT /F1 12 Tf 72 720 Td 14 TL\n"
    for line in lines:
        esc = line.encode("cp1252").replace(b"\\", b"\\\\").replace(b"(", b"\\(").replace(b")", b"\\)")
        content += b"(" + esc + b") Tj T*\n"
    content += b"ET"
    objs = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R "
        b"/Resources << /Font << /F1 5 0 R >> >> >>",
        b"<< /Length %d >>\nstream\n" % len(content) + content + b"\nendstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>",
    ]
    out, offsets = b"%PDF-1.4\n", []
    for i, body in enumerate(objs, 1):
        offsets.append(len(out))
        out += b"%d 0 obj\n" % i + body + b"\nendobj\n"
    xref = len(out)
    out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objs) + 1)
    for off in offsets:
        out += b"%010d 00000 n \n" % off
    out += b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF" % (len(objs) + 1, xref)
    return out


PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 40
JPG = b"\xff\xd8\xff\xe0" + b"\x00" * 40


def attach(fake, card, filename, content, mime=None):
    out = call_tool(fake, "manage_attachments", {"operations": [{
        "action": "add", "card": card, "filename": filename,
        "content_base64": base64.b64encode(content).decode(), **({"mime_type": mime} if mime else {}),
    }]})
    assert out["results"][0]["status"] == "ok", out
    return out["results"][0]["result"]["attachment"]


def read(fake, **args):
    return call_tool(fake, "read_attachment", args)


def text_of(result):
    return result.content[0]["text"]


fake = FakeTrello()
card = fake.card1["id"] if isinstance(fake.card1, dict) else fake.card1
other_card = fake.card2["id"] if isinstance(fake.card2, dict) else fake.card2

# --------------------------------------------------------------------------
section("Texto")
md = attach(fake, card, "notas.md", "# Título\nação rápida ç".encode("utf-8"))
r = read(fake, card=card, attachment="notas.md")
check("md lido por nome", "ação rápida ç" in text_of(r) and "notas.md" in text_of(r), text_of(r)[:120])
r = read(fake, card=card, attachment=md["id"])
check("md lido por id", "# Título" in text_of(r))
r = read(fake, card=card, attachment="NOTAS")
check("nome por prefixo/caixa", "# Título" in text_of(r))
attach(fake, card, "dados.csv", b"a,b\n1,2\n")
attach(fake, card, "x.json", b'{"k": 1}')
check("csv e json", "a,b" in text_of(read(fake, card=card, attachment="dados.csv"))
      and '"k"' in text_of(read(fake, card=card, attachment="x.json")))
attach(fake, card, "grande.txt", b"A" * (200 * 1024))
r = read(fake, card=card, attachment="grande.txt")
body = text_of(r)
check("texto grande truncado em ~60 KB com aviso", "TRUNCADO" in body and 60 * 1024 <= len(body) < 62 * 1024, str(len(body)))
attach(fake, card, "bin.txt", b"abc\x00\x01\x02def")
try:
    read(fake, card=card, attachment="bin.txt"); ok = False
except ToolError as e:
    ok = "binario" in str(e)
check("texto com NUL recusado", ok)

# --------------------------------------------------------------------------
section("PDF")
pdf = make_pdf(["Roteiro v1 - O que é um webhook", "Cena 1: abertura (rápida)", "Fim: ação"])
attach(fake, card, "roteiro-v1.pdf", pdf)
r = read(fake, card=card, attachment="roteiro-v1.pdf")
body = text_of(r)
check("PDF simples: texto extraido", "O que é um webhook" in body and "Cena 1" in body and "ação" in body, body[:200])
check("PDF: parenteses escapados", "(rápida)" in body, body[:200])
fb = __import__("trello_mcp.tools.read_attachment", fromlist=["x"])._pdf_fallback_text(pdf)
check("extrator minimo (sem pypdf) tambem le", "O que é um webhook" in fb and "(rápida)" in fb, fb[:150])
attach(fake, card, "vazio.pdf", make_pdf([""]))
try:
    read(fake, card=card, attachment="vazio.pdf"); ok = False
except ToolError as e:
    ok = "extrair" in str(e)
check("PDF sem texto -> erro claro", ok)
attach(fake, card, "quebrado.pdf", b"%PDF-1.4 lixo sem estrutura")
try:
    read(fake, card=card, attachment="quebrado.pdf"); ok = False
except ToolError as e:
    ok = True
check("PDF malformado -> erro de uso, nao excecao", ok)

# --------------------------------------------------------------------------
section("Imagem")
attach(fake, card, "slide.png", PNG)
attach(fake, card, "foto.jpg", JPG)
r = read(fake, card=card, attachment="slide.png")
img = r.content[1]
check("PNG vira bloco image", img["type"] == "image" and img["mimeType"] == "image/png"
      and base64.b64decode(img["data"]) == PNG)
r = read(fake, card=card, attachment="foto.jpg")
check("JPG vira bloco image", r.content[1]["mimeType"] == "image/jpeg")
attach(fake, card, "enorme.png", PNG + b"\x00" * (5 * 1024 * 1024))
try:
    read(fake, card=card, attachment="enorme.png"); ok = False
except ToolError as e:
    ok = "excede" in str(e)
check("imagem > 5 MB recusada", ok)

# --------------------------------------------------------------------------
section("Erros e autorizacao")
attach(fake, card, "planilha.xlsx", b"PK\x03\x04....")
try:
    read(fake, card=card, attachment="planilha.xlsx"); ok = False
except ToolError as e:
    ok = "valido" in str(e).lower() and "download" in str(e)
check("xlsx corrompido -> erro claro apontando mode=download", ok)
call_tool(fake, "manage_attachments", {"operations": [
    {"action": "add", "card": card, "url": "https://example.com/doc", "name": "Link"}]})
try:
    read(fake, card=card, attachment="Link"); ok = False
except ToolError as e:
    ok = "link externo" in str(e)
check("link externo nao e baixado", ok)
n_before = len(fake.downloads)
try:
    read(fake, card=other_card, attachment=md["id"]); ok = False
except ToolError as e:
    ok = "nao pertence" in str(e) or "nao tem anexos" in str(e)
check("anexo de OUTRO card e recusado (por id)", ok)
try:
    read(fake, card=other_card, attachment="notas.md"); ok = False
except ToolError as e:
    ok = True
check("anexo de outro card recusado (por nome)", ok and len(fake.downloads) == n_before)
try:
    read(fake, card=card, attachment="inexistente.pdf"); ok = False
except ToolError as e:
    ok = "roteiro-v1.pdf" in str(e)
check("anexo inexistente lista os disponiveis", ok)
try:
    read(fake, card=card, attachment=""); ok = False
except ToolError as e:
    ok = True
check("anexo vazio -> erro", ok)
# mesmo nome duas vezes: vale o mais recente
a1 = attach(fake, card, "v.txt", b"antigo")
fake.attachments[card][-1]["date"] = "2026-01-01T00:00:00.000Z"
a2 = attach(fake, card, "v.txt", b"novo")
fake.attachments[card][-1]["date"] = "2026-06-01T00:00:00.000Z"
check("nome repetido: o mais recente", "novo" in text_of(read(fake, card=card, attachment="v.txt")))

# --------------------------------------------------------------------------
section("Via /mcp (blocos de conteudo)")
mcp_server.client_for_user = lambda user: fake
app = create_app()
http = app.test_client()
token = generate_connector_token()
db.upsert_user(trello_member_id="ra-member", trello_username="h", trello_full_name="H",
               trello_key_enc=encrypt("k"), trello_token_enc=encrypt("t"),
               connector_token_hash=hash_token(token))
auth = {"Authorization": f"Bearer {token}"}


def rpc(method, params=None):
    return http.post("/mcp", json={"jsonrpc": "2.0", "id": 1, "method": method, "params": params or {}},
                     headers=auth)


res = rpc("tools/call", {"name": "read_attachment", "arguments": {"card": card, "attachment": "slide.png"}}).get_json()["result"]
check("imagem: content[1] e image/base64", res["isError"] is False and res["content"][1]["type"] == "image"
      and res["content"][1]["data"] == base64.b64encode(PNG).decode())
res = rpc("tools/call", {"name": "read_attachment", "arguments": {"card": card, "attachment": "roteiro-v1.pdf"}}).get_json()["result"]
check("pdf: content[0] e texto", res["content"][0]["type"] == "text" and "webhook" in res["content"][0]["text"])
res = rpc("tools/call", {"name": "read_attachment", "arguments": {"card": card, "attachment": "nada"}}).get_json()["result"]
check("erro vira isError", res["isError"] is True)
tool = next(t for t in rpc("tools/list").get_json()["result"]["tools"] if t["name"] == "read_attachment")
check("read_attachment: somente leitura", tool["annotations"]["readOnlyHint"] is True
      and set(tool["inputSchema"]["required"]) == {"card", "attachment"})

# --------------------------------------------------------------------------
section("Handshake rapido, sem Trello")


class NoTrello:
    def request(self, *a, **k):
        raise AssertionError("handshake nao pode chamar o Trello")

    get_me = request


mcp_server.client_for_user = lambda user: NoTrello()
for method in ("initialize", "tools/list", "ping"):
    rpc(method)  # aquece
    t0 = time.perf_counter()
    for _ in range(20):
        r = rpc(method)
    avg_ms = (time.perf_counter() - t0) / 20 * 1000
    check(f"{method}: sem chamada ao Trello e < 50 ms (media {avg_ms:.1f} ms)", r.status_code == 200 and avg_ms < 50)
r = rpc("notifications/cancelled")
check("qualquer notifications/* -> 204", r.status_code == 204)
check("versao do servidor 0.3.0", rpc("initialize").get_json()["result"]["serverInfo"]["version"] == "0.3.0")
big = http.post("/mcp", data=b"x" * 10, content_type="application/json", headers=auth)
check("corpo invalido continua parse error", big.status_code == 400)

# --------------------------------------------------------------------------
section("manage_attachments: base64 mais robusto")
fk = FakeTrello()
c = fk.card1["id"] if isinstance(fk.card1, dict) else fk.card1


def add(**kw):
    return call_tool(fk, "manage_attachments", {"operations": [{"action": "add", "card": c, **kw}]})["results"][0]


b64 = base64.b64encode(b"conteudo de teste " * 5).decode()
check("base64 com quebras de linha", add(content_base64=b64[:20] + "\n" + b64[20:], filename="a.txt")["status"] == "ok")
check("base64 sem padding", add(content_base64=base64.b64encode(b"ab").decode().rstrip("="), filename="b.txt")["status"] == "ok")
check("data URI", add(content_base64="data:text/plain;base64," + b64, filename="c.txt")["status"] == "ok")
check("base64 url-safe", add(content_base64=base64.urlsafe_b64encode(b"\xff\xfe\xfd\xfc").decode(), filename="d.bin")["status"] == "ok")
check("lixo no base64 e recusado (antes ia corrompido)", add(content_base64="@@@@!!!!", filename="e.txt")["status"] == "error")
r = add(content_base64="A" * (14 * 1024 * 1024), filename="f.bin")
check("base64 gigante recusado antes de decodificar", r["status"] == "error" and "limite" in r["error"], str(r)[:200])
r = add(content_base64=b64, filename="../../etc/passwd.txt")
check("filename sem caminho", fk.calls[-1][2]["_files"]["file"][0] == "passwd.txt", str(fk.calls[-1][2]["_files"]["file"][0]))

# --------------------------------------------------------------------------
section("Cliente HTTP: timeout, segredos e download")
KEY, TOK = "KEYSECRET123", "TOKSECRET456"
client = trello_client.TrelloClient(KEY, TOK, base_url="https://api.trello.com/1")
orig_request, orig_get = requests.request, requests.get
calls = {"n": 0, "timeouts": []}


def flaky(method, url, **kw):
    calls["n"] += 1
    calls["timeouts"].append(kw.get("timeout"))
    if calls["n"] == 1:
        raise requests.ConnectionError(f"boom {url}?key={KEY}&token={TOK}")
    resp = requests.Response()
    resp.status_code, resp._content = 200, b'{"ok": true}'
    return resp


requests.request = flaky
try:
    check("GET tenta de novo em falha transitoria", client.request("GET", "/x") == {"ok": True} and calls["n"] == 2)
    calls["n"] = 0
    try:
        client.request("POST", "/cards/1/attachments", files={"file": ("a.txt", b"x", "text/plain")})
        msg = ""
    except trello_client.TrelloApiError as e:
        msg = str(e)
    check("POST nao repete (evita anexo duplicado) e avisa para conferir", calls["n"] == 1 and "list" in msg, msg)
    check("erro de rede nao vaza key/token", KEY not in msg and TOK not in msg, msg)
    check("upload tem timeout de leitura longo", calls["timeouts"][-1] == trello_client.TIMEOUT_UPLOAD)

    def always_down(method, url, **kw):
        raise requests.ConnectionError(f"x {url}?key={KEY}&token={TOK}")

    requests.request = always_down
    try:
        client.request("GET", "/x"); msg = ""
    except trello_client.TrelloApiError as e:
        msg = str(e)
    check("GET falho nao vaza key/token", msg and KEY not in msg and TOK not in msg, msg)
finally:
    requests.request = orig_request


class FakeResp:
    def __init__(self, status, headers=None, body=b""):
        self.status_code, self.headers, self._body = status, headers or {}, body
        self.ok = status < 400

    def iter_content(self, n):
        for i in range(0, len(self._body), n):
            yield self._body[i:i + n]

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


seen = []


def fake_get(url, headers=None, **kw):
    seen.append((url, dict(headers or {}), kw.get("allow_redirects")))
    if url.startswith("https://trello.com/"):
        return FakeResp(302, {"Location": "https://bucket.s3.amazonaws.com/signed?X=1"})
    return FakeResp(200, {"Content-Type": "text/plain"}, b"0123456789")


requests.get = fake_get
try:
    data, ctype, trunc = client.download("https://trello.com/1/cards/1/attachments/2/download/a.txt", 100)
    check("download manda Authorization OAuth ao Trello", seen[0][1].get("Authorization", "").startswith("OAuth oauth_consumer_key="))
    check("redirect para outro host NAO leva credenciais", seen[1][1] == {} and data == b"0123456789" and trunc is False)
    check("redirect nao e seguido automaticamente", seen[0][2] is False)
    data, _, trunc = client.download("https://trello.com/1/cards/1/attachments/2/download/a.txt", 4)
    check("limite de bytes marca truncado", data == b"0123" and trunc is True)
    for bad in ("http://trello.com/x", "https://evil.example.com/x", "https://trello.com.evil.com/x"):
        try:
            client.download(bad, 10); ok = False
        except trello_client.TrelloApiError:
            ok = True
        check(f"URL recusada: {bad}", ok)
finally:
    requests.get = orig_get

db.delete_user_by_member_id("ra-member")
print("\n" + "=" * 60)
print(f"PASSOU: {len(PASS)}   FALHOU: {len(FAIL)}")
for name in FAIL:
    print(f"  - {name}")
sys.exit(1 if FAIL else 0)
