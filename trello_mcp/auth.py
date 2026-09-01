"""Identidade da sessao MCP.

O "login" do sistema e a propria conta do Trello: quem apresenta um par
Key+Token valido prova ser dono daquela conta. Em troca recebe um
connector token, que autentica cada chamada em /mcp.
"""

import secrets

from . import db
from .crypto import decrypt, encrypt, hash_token
from .trello_client import TrelloClient

CONNECTOR_TOKEN_BYTES = 32


class AuthError(RuntimeError):
    pass


def generate_connector_token() -> str:
    return secrets.token_urlsafe(CONNECTOR_TOKEN_BYTES)


def connect_account(api_key: str, token: str) -> tuple[dict, str]:
    """Valida as credenciais no Trello e registra/atualiza o usuario.

    Devolve (usuario, connector_token). O connector token so e visivel
    aqui -- o banco guarda apenas o hash.
    """
    api_key = (api_key or "").strip()
    token = (token or "").strip()
    if not api_key or not token:
        raise AuthError("Informe a API Key e o Token do Trello.")

    me = TrelloClient(api_key, token).get_me()
    member_id = me.get("id")
    if not member_id:
        raise AuthError("O Trello nao retornou o id do membro para essas credenciais.")

    connector_token = generate_connector_token()
    user = db.upsert_user(
        trello_member_id=member_id,
        trello_username=me.get("username"),
        trello_full_name=me.get("fullName"),
        trello_key_enc=encrypt(api_key),
        trello_token_enc=encrypt(token),
        connector_token_hash=hash_token(connector_token),
    )
    return user, connector_token


def resolve_connector_token(connector_token: str) -> dict:
    """Traduz o connector token no usuario dono dele."""
    if not connector_token:
        raise AuthError("Connector token ausente.")
    user = db.find_user_by_connector_token_hash(hash_token(connector_token))
    if not user:
        raise AuthError("Connector token invalido ou revogado.")
    return user


def client_for_user(user: dict) -> TrelloClient:
    return TrelloClient(decrypt(user["trello_key_enc"]), decrypt(user["trello_token_enc"]))


def extract_bearer_token(authorization_header: str | None) -> str:
    """Le o token de 'Authorization: Bearer <token>'."""
    if not authorization_header:
        return ""
    parts = authorization_header.split(None, 1)
    if len(parts) != 2 or parts[0].lower() != "bearer":
        return ""
    return parts[1].strip()
