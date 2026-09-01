"""Smoke test do fluxo de identidade, sem chamar a API real do Trello."""
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

import json

from trello_mcp import create_app, db
from trello_mcp.auth import generate_connector_token, resolve_connector_token, client_for_user
from trello_mcp.crypto import encrypt, hash_token

app = create_app()
client = app.test_client()

def show(label, resp):
    body = resp.get_data(as_text=True)
    if resp.content_type and "json" in resp.content_type:
        body = json.dumps(resp.get_json(), ensure_ascii=False)
    print(f"[{resp.status_code}] {label}: {body[:160]}")

# 1. health
show("GET /health", client.get("/health"))

# 2. painel renderiza
r = client.get("/panel")
print(f"[{r.status_code}] GET /panel: contem formulario =", "panel/connect" in r.get_data(as_text=True))

# 3. /mcp sem header -> 401
show("POST /mcp sem auth", client.post("/mcp", json={"jsonrpc":"2.0","id":1,"method":"initialize"}))

# 4. /mcp com token invalido -> 401
show("POST /mcp token invalido", client.post("/mcp",
     json={"jsonrpc":"2.0","id":1,"method":"initialize"},
     headers={"Authorization": "Bearer nao-existe"}))

# 5. cria usuario ficticio direto no banco
tok = generate_connector_token()
user = db.upsert_user(
    trello_member_id="fake-member-123",
    trello_username="fulano",
    trello_full_name="Fulano de Tal",
    trello_key_enc=encrypt("fake-key"),
    trello_token_enc=encrypt("fake-token"),
    connector_token_hash=hash_token(tok),
)
print("usuario criado:", user["trello_username"], "| id:", user["trello_member_id"])

auth = {"Authorization": f"Bearer {tok}"}
show("POST /mcp initialize", client.post("/mcp", json={"jsonrpc":"2.0","id":1,"method":"initialize"}, headers=auth))
show("POST /mcp tools/list", client.post("/mcp", json={"jsonrpc":"2.0","id":2,"method":"tools/list"}, headers=auth))
show("POST /mcp ping", client.post("/mcp", json={"jsonrpc":"2.0","id":3,"method":"ping"}, headers=auth))

# 6. resolve token -> credenciais descriptografadas corretas
resolved = resolve_connector_token(tok)
c = client_for_user(resolved)
print("credenciais decifradas ok:", c._api_key == "fake-key" and c._token == "fake-token")

# 7. reconectar rotaciona o token: token antigo deve morrer
tok2 = generate_connector_token()
db.upsert_user(
    trello_member_id="fake-member-123",
    trello_username="fulano",
    trello_full_name="Fulano de Tal",
    trello_key_enc=encrypt("fake-key"),
    trello_token_enc=encrypt("fake-token"),
    connector_token_hash=hash_token(tok2),
)
show("POST /mcp com token antigo (revogado)", client.post("/mcp",
     json={"jsonrpc":"2.0","id":4,"method":"initialize"}, headers=auth))
show("POST /mcp com token novo", client.post("/mcp",
     json={"jsonrpc":"2.0","id":5,"method":"initialize"},
     headers={"Authorization": f"Bearer {tok2}"}))

# 8. limpeza
print("removido:", db.delete_user_by_member_id("fake-member-123"))
