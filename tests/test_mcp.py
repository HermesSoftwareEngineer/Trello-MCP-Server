"""Testa o endpoint /mcp de ponta a ponta, com o Trello falsificado."""
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

import json
import sys

from fake_trello import FakeTrello
from trello_mcp import create_app, db, mcp_server
from trello_mcp.auth import generate_connector_token
from trello_mcp.crypto import encrypt, hash_token

PASS, FAIL = [], []


def check(label, cond, detail=""):
    (PASS if cond else FAIL).append(label)
    print(f"  [{'OK  ' if cond else 'FALHOU'}] {label}" + (f"  -> {detail}" if detail and not cond else ""))


fake = FakeTrello()
mcp_server.client_for_user = lambda user: fake  # injeta o Trello falso

app = create_app()
http = app.test_client()

token = generate_connector_token()
db.upsert_user(
    trello_member_id="e2e-member", trello_username="hermes", trello_full_name="Hermes",
    trello_key_enc=encrypt("k"), trello_token_enc=encrypt("t"),
    connector_token_hash=hash_token(token),
)
auth = {"Authorization": f"Bearer {token}"}
rid = iter(range(1, 999))


def rpc(method, params=None, headers=auth):
    return http.post("/mcp", json={"jsonrpc": "2.0", "id": next(rid),
                                   "method": method, "params": params or {}}, headers=headers)


print("=== Handshake MCP ===")
r = rpc("initialize")
check("initialize 200", r.status_code == 200)
check("protocolVersion", r.get_json()["result"]["protocolVersion"] == "2025-06-18")
check("declara capability de tools", "tools" in r.get_json()["result"]["capabilities"])
check("notifications/initialized 204", rpc("notifications/initialized").status_code == 204)

r = rpc("tools/list")
tools = r.get_json()["result"]["tools"]
check("tools/list traz 9 tools", len(tools) == 9, str(len(tools)))
schema_ok = all(t["inputSchema"]["type"] == "object" for t in tools)
check("todos os inputSchema sao objetos", schema_ok)
check("descricoes ricas (>200 chars nas complexas)",
      len(next(t for t in tools if t["name"] == "search")["description"]) > 200)

print("\n=== tools/call ===")
r = rpc("tools/call", {"name": "list_boards", "arguments": {}})
body = r.get_json()["result"]
check("resposta no formato content[]", body["content"][0]["type"] == "text" and body["isError"] is False)
payload = json.loads(body["content"][0]["text"])
check("payload JSON parseavel", payload["count"] == 1, str(payload)[:200])

r = rpc("tools/call", {"name": "get_board_snapshot",
                       "arguments": {"board": "Projeto Alpha", "depth": "full"}})
payload = json.loads(r.get_json()["result"]["content"][0]["text"])
check("snapshot via MCP", payload["board"]["name"] == "Projeto Alpha")

r = rpc("tools/call", {"name": "manage_cards", "arguments": {"operations": [
    {"action": "create", "board": "Projeto Alpha", "list": "A Fazer", "name": "Via MCP"}]}})
payload = json.loads(r.get_json()["result"]["content"][0]["text"])
check("escrita via MCP", payload["summary"]["succeeded"] == 1, str(payload)[:200])

print("\n=== Erros ===")
r = rpc("tools/call", {"name": "get_board_snapshot", "arguments": {"board": "Inexistente"}})
body = r.get_json()["result"]
check("erro de uso vira isError (nao erro de protocolo)",
      r.status_code == 200 and body["isError"] is True)
check("mensagem lista os boards disponiveis", "Projeto Alpha" in body["content"][0]["text"],
      body["content"][0]["text"][:200])

r = rpc("tools/call", {"name": "tool_que_nao_existe", "arguments": {}})
check("tool inexistente -> 404 + erro JSON-RPC", r.status_code == 404 and "error" in r.get_json())

r = rpc("tools/list", headers={})
check("sem auth -> 401", r.status_code == 401)
check("manda WWW-Authenticate", r.headers.get("WWW-Authenticate", "").startswith("Bearer"))

r = rpc("metodo/desconhecido")
check("metodo desconhecido -> 404", r.status_code == 404)

r = http.post("/mcp", data="isso nao e json", content_type="application/json", headers=auth)
check("corpo invalido -> parse error", r.status_code == 400 and r.get_json()["error"]["code"] == -32700)


class ExplodingClient:
    def request(self, *a, **k):
        raise RuntimeError("boom inesperado")

    def get_me(self):
        raise RuntimeError("boom inesperado")


mcp_server.client_for_user = lambda user: ExplodingClient()
r = rpc("tools/call", {"name": "list_boards", "arguments": {}})
body = r.get_json()["result"]
check("falha inesperada nao derruba o servidor",
      r.status_code == 200 and body["isError"] is True and "boom" in body["content"][0]["text"])

db.delete_user_by_member_id("e2e-member")
print("\n" + "=" * 60)
print(f"PASSOU: {len(PASS)}   FALHOU: {len(FAIL)}")
for name in FAIL:
    print(f"  - {name}")
sys.exit(1 if FAIL else 0)
