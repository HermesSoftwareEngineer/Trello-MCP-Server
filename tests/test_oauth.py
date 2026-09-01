"""Fluxo OAuth 2.0 de ponta a ponta, sem tocar na API real do Trello."""
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

import base64
import hashlib
import secrets
from urllib.parse import parse_qs, urlparse

from trello_mcp import auth, create_app, db

PASS, FAIL = [], []


def check(label, cond, detail=""):
    (PASS if cond else FAIL).append(label)
    print(f"  [{'OK  ' if cond else 'FALHOU'}] {label}" + (f"  -> {detail}" if detail and not cond else ""))


# Trello falso: qualquer credencial "vale" e devolve o mesmo membro.
class FakeTrelloClient:
    def __init__(self, api_key, token, base_url=None):
        self._bad = api_key == "ruim"

    def get_me(self):
        if self._bad:
            from trello_mcp.trello_client import TrelloAuthError
            raise TrelloAuthError("credenciais ruins")
        return {"id": "oauth-member-1", "username": "hermes", "fullName": "Hermes B"}


auth.TrelloClient = FakeTrelloClient

app = create_app()
http = app.test_client()

REDIRECT_URI = "https://claude.ai/api/mcp/auth_callback"


def pkce_pair():
    verifier = secrets.token_urlsafe(48)
    challenge = base64.urlsafe_b64encode(
        hashlib.sha256(verifier.encode("ascii")).digest()
    ).rstrip(b"=").decode("ascii")
    return verifier, challenge


print("=== Metadata ===")
r = http.get("/.well-known/oauth-authorization-server")
meta = r.get_json()
check("authorization-server 200", r.status_code == 200)
check("tem authorization_endpoint", meta.get("authorization_endpoint", "").endswith("/oauth/authorize"))
check("tem token_endpoint", meta.get("token_endpoint", "").endswith("/oauth/token"))
check("tem registration_endpoint", meta.get("registration_endpoint", "").endswith("/oauth/register"))
check("anuncia PKCE S256", meta.get("code_challenge_methods_supported") == ["S256"])
check("CORS liberado na metadata", r.headers.get("Access-Control-Allow-Origin") == "*")

r = http.get("/.well-known/oauth-protected-resource/mcp")
prm = r.get_json()
check("protected-resource no caminho /mcp responde", r.status_code == 200)
check("aponta para o authorization server", prm.get("authorization_servers") == [meta["issuer"]])

print("\n=== Dynamic Client Registration ===")
r = http.post("/oauth/register", json={
    "client_name": "Claude",
    "redirect_uris": [REDIRECT_URI],
    "grant_types": ["authorization_code", "refresh_token"],
    "token_endpoint_auth_method": "none",
})
reg = r.get_json()
check("register 201", r.status_code == 201, str(reg))
check("devolve client_id", bool(reg.get("client_id")))
check("public client (sem secret)", "client_secret" not in reg)
client_id = reg["client_id"]

r = http.post("/oauth/register", json={"redirect_uris": ["javascript:alert(1)"]})
check("register recusa redirect_uri perigoso", r.status_code == 400)

print("\n=== Authorize (GET) ===")
verifier, challenge = pkce_pair()
q = {
    "response_type": "code", "client_id": client_id, "redirect_uri": REDIRECT_URI,
    "code_challenge": challenge, "code_challenge_method": "S256",
    "state": "xyz123", "scope": "trello",
}
r = http.get("/oauth/authorize", query_string=q)
check("form de autorizacao 200", r.status_code == 200)
check("form fala do cliente", "Claude" in r.get_data(as_text=True))

r = http.get("/oauth/authorize", query_string={**q, "client_id": "nao-existe"})
check("client_id invalido -> pagina de erro, sem redirect", r.status_code == 400)

r = http.get("/oauth/authorize", query_string={**q, "code_challenge": ""})
check("sem PKCE -> redirect com error", r.status_code == 302 and "error=invalid_request" in r.headers["Location"])

print("\n=== Authorize (POST) ===")
r = http.post("/oauth/authorize", data={**q, "api_key": "ruim", "token": "t"})
check("credencial ruim -> reexibe form com erro", r.status_code == 400 and "recusou" in r.get_data(as_text=True))

r = http.post("/oauth/authorize", data={**q, "api_key": "boa", "token": "t"})
check("autorizacao ok -> 302 para o redirect_uri", r.status_code == 302)
loc = urlparse(r.headers["Location"])
params = parse_qs(loc.query)
check("redirect traz o state", params.get("state") == ["xyz123"])
code = params.get("code", [None])[0]
check("redirect traz o code", bool(code))

print("\n=== Token (authorization_code) ===")
r = http.post("/oauth/token", data={
    "grant_type": "authorization_code", "code": code, "redirect_uri": REDIRECT_URI,
    "client_id": client_id, "code_verifier": verifier,
})
tok = r.get_json()
check("token 200", r.status_code == 200, str(tok))
check("devolve access_token", bool(tok.get("access_token")))
check("devolve refresh_token", bool(tok.get("refresh_token")))
check("token_type Bearer", tok.get("token_type") == "Bearer")
check("Cache-Control no-store", r.headers.get("Cache-Control") == "no-store")
access, refresh = tok["access_token"], tok["refresh_token"]

r = http.post("/oauth/token", data={
    "grant_type": "authorization_code", "code": code, "redirect_uri": REDIRECT_URI,
    "client_id": client_id, "code_verifier": verifier,
})
check("code e uso unico", r.status_code == 400 and r.get_json()["error"] == "invalid_grant")

print("\n=== Access token no /mcp ===")
r = http.post("/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "initialize"},
              headers={"Authorization": f"Bearer {access}"})
check("initialize com access token OAuth", r.status_code == 200 and "result" in r.get_json())

r = http.post("/mcp", json={"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
              headers={"Authorization": f"Bearer {access}"})
check("tools/list com access token OAuth", r.status_code == 200 and len(r.get_json()["result"]["tools"]) == 9)

r = http.post("/mcp", json={"jsonrpc": "2.0", "id": 3, "method": "initialize"},
              headers={"Authorization": "Bearer lixo"})
check("token invalido -> 401", r.status_code == 401)
check("401 aponta a resource_metadata",
      "resource_metadata=" in r.headers.get("WWW-Authenticate", ""))

print("\n=== PKCE errado ===")
verifier2, challenge2 = pkce_pair()
r = http.post("/oauth/authorize", data={
    "response_type": "code", "client_id": client_id, "redirect_uri": REDIRECT_URI,
    "code_challenge": challenge2, "code_challenge_method": "S256",
    "api_key": "boa", "token": "t",
})
code2 = parse_qs(urlparse(r.headers["Location"]).query)["code"][0]
r = http.post("/oauth/token", data={
    "grant_type": "authorization_code", "code": code2, "redirect_uri": REDIRECT_URI,
    "client_id": client_id, "code_verifier": "verifier-errado",
})
check("code_verifier errado -> invalid_grant", r.status_code == 400 and r.get_json()["error"] == "invalid_grant")

print("\n=== Refresh token ===")
r = http.post("/oauth/token", data={
    "grant_type": "refresh_token", "refresh_token": refresh, "client_id": client_id,
})
ref = r.get_json()
check("refresh 200 + novo access_token", r.status_code == 200 and ref.get("access_token") not in (None, access))
new_access = ref["access_token"]
check("access antigo morre apos rotacao",
      http.post("/mcp", json={"jsonrpc": "2.0", "id": 9, "method": "ping"},
                headers={"Authorization": f"Bearer {access}"}).status_code == 401)
check("access novo funciona",
      http.post("/mcp", json={"jsonrpc": "2.0", "id": 10, "method": "ping"},
                headers={"Authorization": f"Bearer {new_access}"}).status_code == 200)

r = http.post("/oauth/token", data={
    "grant_type": "refresh_token", "refresh_token": refresh, "client_id": client_id,
})
check("refresh antigo morre apos rotacao", r.status_code == 400)

print("\n=== Grant nao suportado ===")
r = http.post("/oauth/token", data={"grant_type": "password", "username": "x"})
check("grant_type desconhecido -> unsupported_grant_type",
      r.status_code == 400 and r.get_json()["error"] == "unsupported_grant_type")

# limpeza
db.delete_user_by_member_id("oauth-member-1")

print("\n" + "=" * 60)
print(f"PASSOU: {len(PASS)}   FALHOU: {len(FAIL)}")
for name in FAIL:
    print(f"  - {name}")
sys.exit(1 if FAIL else 0)
