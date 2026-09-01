"""OAuth 2.0 -- o servidor MCP e o proprio authorization server.

O que os clientes MCP (Claude Desktop, claude.ai) esperam:

  1. GET /.well-known/oauth-protected-resource  -> aponta para o issuer
  2. GET /.well-known/oauth-authorization-server -> metadata (RFC 8414)
  3. POST /oauth/register  -> Dynamic Client Registration (RFC 7591)
  4. GET  /oauth/authorize -> usuario prova a conta do Trello, volta com `code`
  5. POST /oauth/token     -> troca `code` (+ PKCE) por access/refresh token

O "login" continua sendo a conta do Trello: quem apresenta um par Key+Token
valido no /authorize prova ser dono daquela conta (mesma logica do painel).
"""

import base64
import hashlib
import json
import secrets
from datetime import datetime, timedelta, timezone
from urllib.parse import urlparse

from . import db
from .config import Config
from .crypto import hash_token

CODE_CHALLENGE_METHODS = ("S256",)
DEFAULT_SCOPE = "trello"
ACCESS_TOKEN_BYTES = 32
REFRESH_TOKEN_BYTES = 32


class OAuthError(RuntimeError):
    """Erro do fluxo OAuth. `code` e o valor de `error` do RFC 6749."""

    def __init__(self, code: str, description: str, status: int = 400):
        super().__init__(description)
        self.code = code
        self.description = description
        self.status = status


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(moment: datetime) -> str:
    return moment.isoformat()


# --------------------------------------------------------------------------
# Metadata
# --------------------------------------------------------------------------

def authorization_server_metadata() -> dict:
    base = Config.OAUTH_ISSUER
    return {
        "issuer": base,
        "authorization_endpoint": f"{base}/oauth/authorize",
        "token_endpoint": f"{base}/oauth/token",
        "registration_endpoint": f"{base}/oauth/register",
        "response_types_supported": ["code"],
        "grant_types_supported": ["authorization_code", "refresh_token"],
        "code_challenge_methods_supported": list(CODE_CHALLENGE_METHODS),
        "token_endpoint_auth_methods_supported": [
            "none",
            "client_secret_post",
            "client_secret_basic",
        ],
        "scopes_supported": [DEFAULT_SCOPE],
        "service_documentation": f"{base}/panel",
    }


def protected_resource_metadata() -> dict:
    base = Config.OAUTH_ISSUER
    return {
        "resource": f"{base}/mcp",
        "authorization_servers": [base],
        "bearer_methods_supported": ["header"],
        "resource_documentation": f"{base}/panel",
    }


# --------------------------------------------------------------------------
# PKCE
# --------------------------------------------------------------------------

def verify_pkce(code_verifier: str, code_challenge: str, method: str) -> bool:
    if not code_verifier or not code_challenge:
        return False
    if method == "S256":
        digest = hashlib.sha256(code_verifier.encode("ascii")).digest()
        expected = base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")
        return secrets.compare_digest(expected, code_challenge)
    return False


# --------------------------------------------------------------------------
# Dynamic Client Registration
# --------------------------------------------------------------------------

def acceptable_redirect_uri(uri: str) -> bool:
    """https em qualquer host; http so em loopback; esquemas nativos (app://)
    sao aceitos como estao. Barra o obvio (javascript:, data:)."""
    if not uri or len(uri) > 2000:
        return False
    parsed = urlparse(uri)
    scheme = (parsed.scheme or "").lower()
    if scheme in ("javascript", "data", "vbscript", "file"):
        return False
    if scheme == "https":
        return bool(parsed.netloc)
    if scheme == "http":
        host = (parsed.hostname or "").lower()
        return host in ("localhost", "127.0.0.1", "::1", "[::1]")
    # esquema custom de app nativo: exige "scheme://algo"
    return bool(scheme and parsed.netloc or parsed.path)


def register_client(metadata: dict) -> dict:
    redirect_uris = metadata.get("redirect_uris")
    if not isinstance(redirect_uris, list) or not redirect_uris:
        raise OAuthError("invalid_client_metadata", "redirect_uris e obrigatorio.")
    if len(redirect_uris) > 10:
        raise OAuthError("invalid_client_metadata", "redirect_uris demais.")
    for uri in redirect_uris:
        if not isinstance(uri, str) or not acceptable_redirect_uri(uri):
            raise OAuthError("invalid_redirect_uri", f"redirect_uri nao aceita: {uri!r}")

    grant_types = metadata.get("grant_types") or ["authorization_code", "refresh_token"]
    auth_method = metadata.get("token_endpoint_auth_method") or "none"
    if auth_method not in ("none", "client_secret_post", "client_secret_basic"):
        raise OAuthError(
            "invalid_client_metadata",
            f"token_endpoint_auth_method nao suportado: {auth_method}",
        )

    client_id = "mcp-" + secrets.token_urlsafe(18)
    client_secret = None
    secret_hash = None
    if auth_method in ("client_secret_post", "client_secret_basic"):
        client_secret = secrets.token_urlsafe(32)
        secret_hash = hash_token(client_secret)

    db.create_oauth_client(
        client_id=client_id,
        client_secret_hash=secret_hash,
        client_name=metadata.get("client_name"),
        redirect_uris=json.dumps(redirect_uris),
        grant_types=json.dumps(grant_types),
        token_endpoint_auth_method=auth_method,
    )

    response = {
        "client_id": client_id,
        "client_id_issued_at": int(_now().timestamp()),
        "redirect_uris": redirect_uris,
        "grant_types": grant_types,
        "response_types": ["code"],
        "token_endpoint_auth_method": auth_method,
    }
    if metadata.get("client_name"):
        response["client_name"] = metadata["client_name"]
    if client_secret:
        response["client_secret"] = client_secret
        response["client_secret_expires_at"] = 0  # nao expira
    return response


# --------------------------------------------------------------------------
# Authorization code
# --------------------------------------------------------------------------

def load_client_redirect(client_id: str, redirect_uri: str) -> dict:
    """Valida client + redirect_uri (match exato). Erros aqui NAO devem
    redirecionar -- o chamador mostra uma pagina de erro."""
    client = db.get_oauth_client(client_id or "")
    if not client:
        raise OAuthError("invalid_client", "client_id desconhecido. Registre o cliente.", 400)
    registered = json.loads(client["redirect_uris"])
    if redirect_uri not in registered:
        raise OAuthError("invalid_request", "redirect_uri nao confere com o registrado.", 400)
    return client


def issue_auth_code(
    *,
    client_id: str,
    user_id: int,
    redirect_uri: str,
    code_challenge: str,
    code_challenge_method: str,
    scope: str | None,
    resource: str | None,
) -> str:
    code = secrets.token_urlsafe(32)
    db.create_auth_code(
        code_hash=hash_token(code),
        client_id=client_id,
        user_id=user_id,
        redirect_uri=redirect_uri,
        code_challenge=code_challenge,
        code_challenge_method=code_challenge_method,
        scope=scope,
        resource=resource,
        expires_at=_iso(_now() + timedelta(seconds=Config.OAUTH_CODE_TTL)),
    )
    return code


# --------------------------------------------------------------------------
# Token endpoint
# --------------------------------------------------------------------------

def _authenticate_client(client: dict, provided_secret: str | None) -> None:
    method = client["token_endpoint_auth_method"]
    if method == "none":
        return
    if not provided_secret or not client["client_secret_hash"]:
        raise OAuthError("invalid_client", "client_secret ausente.", 401)
    if not secrets.compare_digest(hash_token(provided_secret), client["client_secret_hash"]):
        raise OAuthError("invalid_client", "client_secret invalido.", 401)


def _token_body(access: str, refresh: str, scope: str | None) -> dict:
    return {
        "access_token": access,
        "token_type": "Bearer",
        "expires_in": Config.OAUTH_ACCESS_TOKEN_TTL,
        "refresh_token": refresh,
        "scope": scope or DEFAULT_SCOPE,
    }


def exchange_authorization_code(form: dict, client_secret: str | None) -> dict:
    client = db.get_oauth_client(form.get("client_id") or "")
    if not client:
        raise OAuthError("invalid_client", "client_id desconhecido.", 401)
    _authenticate_client(client, client_secret)

    code = form.get("code") or ""
    entry = db.pop_auth_code(hash_token(code)) if code else None
    if not entry:
        raise OAuthError("invalid_grant", "code invalido ou ja usado.")
    if datetime.fromisoformat(entry["expires_at"]) < _now():
        raise OAuthError("invalid_grant", "code expirado.")
    if entry["client_id"] != client["client_id"]:
        raise OAuthError("invalid_grant", "code emitido para outro cliente.")
    if entry["redirect_uri"] != (form.get("redirect_uri") or ""):
        raise OAuthError("invalid_grant", "redirect_uri diferente da autorizacao.")
    if not verify_pkce(
        form.get("code_verifier") or "",
        entry["code_challenge"],
        entry["code_challenge_method"],
    ):
        raise OAuthError("invalid_grant", "PKCE nao confere (code_verifier invalido).")

    access = secrets.token_urlsafe(ACCESS_TOKEN_BYTES)
    refresh = secrets.token_urlsafe(REFRESH_TOKEN_BYTES)
    now = _now()
    db.store_oauth_token(
        access_token_hash=hash_token(access),
        refresh_token_hash=hash_token(refresh),
        client_id=client["client_id"],
        user_id=entry["user_id"],
        scope=entry["scope"],
        resource=entry["resource"],
        access_expires_at=_iso(now + timedelta(seconds=Config.OAUTH_ACCESS_TOKEN_TTL)),
        refresh_expires_at=_iso(now + timedelta(seconds=Config.OAUTH_REFRESH_TOKEN_TTL)),
    )
    return _token_body(access, refresh, entry["scope"])


def refresh_access_token(form: dict, client_secret: str | None) -> dict:
    client = db.get_oauth_client(form.get("client_id") or "")
    if not client:
        raise OAuthError("invalid_client", "client_id desconhecido.", 401)
    _authenticate_client(client, client_secret)

    refresh_token = form.get("refresh_token") or ""
    entry = db.get_token_by_refresh_hash(hash_token(refresh_token)) if refresh_token else None
    if not entry:
        raise OAuthError("invalid_grant", "refresh_token invalido ou revogado.")
    if entry["client_id"] != client["client_id"]:
        raise OAuthError("invalid_grant", "refresh_token emitido para outro cliente.")
    if entry["refresh_expires_at"] and datetime.fromisoformat(entry["refresh_expires_at"]) < _now():
        raise OAuthError("invalid_grant", "refresh_token expirado. Refaca a autorizacao.")

    access = secrets.token_urlsafe(ACCESS_TOKEN_BYTES)
    refresh = secrets.token_urlsafe(REFRESH_TOKEN_BYTES)
    now = _now()
    db.rotate_oauth_token(
        token_id=entry["id"],
        access_token_hash=hash_token(access),
        refresh_token_hash=hash_token(refresh),
        access_expires_at=_iso(now + timedelta(seconds=Config.OAUTH_ACCESS_TOKEN_TTL)),
        refresh_expires_at=_iso(now + timedelta(seconds=Config.OAUTH_REFRESH_TOKEN_TTL)),
    )
    return _token_body(access, refresh, entry["scope"])
