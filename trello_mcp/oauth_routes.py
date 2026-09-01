"""Endpoints HTTP do fluxo OAuth 2.0. A logica fica em trello_mcp/oauth.py."""

import base64
import binascii
from urllib.parse import urlencode

from flask import Blueprint, jsonify, redirect, render_template, request

from . import db, oauth
from .auth import AuthError, upsert_trello_user
from .crypto import CryptoError
from .oauth import OAuthError
from .trello_client import TrelloApiError, TrelloAuthError

oauth_bp = Blueprint("oauth", __name__)

_NO_STORE = {"Cache-Control": "no-store", "Pragma": "no-cache"}

# Campos do OAuth que atravessam o /authorize (GET -> form -> POST).
_AUTHORIZE_FIELDS = (
    "client_id",
    "redirect_uri",
    "response_type",
    "state",
    "scope",
    "resource",
    "code_challenge",
    "code_challenge_method",
)


# --------------------------------------------------------------------------
# Metadata (RFC 8414 / RFC 9728). Servidas na raiz e no caminho /mcp porque
# clientes diferentes montam a URL de descoberta de um jeito ou de outro.
# --------------------------------------------------------------------------

@oauth_bp.get("/.well-known/oauth-authorization-server")
@oauth_bp.get("/.well-known/oauth-authorization-server/mcp")
def authorization_server_metadata():
    return jsonify(oauth.authorization_server_metadata())


@oauth_bp.get("/.well-known/oauth-protected-resource")
@oauth_bp.get("/.well-known/oauth-protected-resource/mcp")
def protected_resource_metadata():
    return jsonify(oauth.protected_resource_metadata())


# --------------------------------------------------------------------------
# Dynamic Client Registration (RFC 7591)
# --------------------------------------------------------------------------

@oauth_bp.post("/oauth/register")
def register():
    metadata = request.get_json(silent=True)
    if not isinstance(metadata, dict):
        return _oauth_error(OAuthError("invalid_client_metadata", "Corpo JSON obrigatorio."))
    try:
        body = oauth.register_client(metadata)
    except OAuthError as exc:
        return _oauth_error(exc)
    return jsonify(body), 201, _NO_STORE


# --------------------------------------------------------------------------
# Authorization endpoint
# --------------------------------------------------------------------------

@oauth_bp.get("/oauth/authorize")
def authorize_form():
    params = {field: request.args.get(field, "") for field in _AUTHORIZE_FIELDS}
    if not params["code_challenge_method"]:
        params["code_challenge_method"] = "S256"

    # client_id / redirect_uri invalidos: mostrar erro, nunca redirecionar.
    try:
        oauth.load_client_redirect(params["client_id"], params["redirect_uri"])
    except OAuthError as exc:
        return render_template("oauth_error.html", message=exc.description), 400

    # Demais erros de parametro: redirecionar de volta com ?error=.
    problem = _validate_authorize_params(params)
    if problem:
        return _redirect_error(params["redirect_uri"], params["state"], *problem)

    return render_template(
        "authorize.html",
        params=params,
        client_name=_client_label(params["client_id"]),
    )


@oauth_bp.post("/oauth/authorize")
def authorize_submit():
    params = {field: request.form.get(field, "") for field in _AUTHORIZE_FIELDS}
    if not params["code_challenge_method"]:
        params["code_challenge_method"] = "S256"

    try:
        oauth.load_client_redirect(params["client_id"], params["redirect_uri"])
    except OAuthError as exc:
        return render_template("oauth_error.html", message=exc.description), 400

    problem = _validate_authorize_params(params)
    if problem:
        return _redirect_error(params["redirect_uri"], params["state"], *problem)

    api_key = request.form.get("api_key", "")
    token = request.form.get("token", "")
    try:
        user = upsert_trello_user(api_key, token)
    except TrelloAuthError:
        return _render_authorize(params, "O Trello recusou essas credenciais. Confira a API Key e o Token."), 400
    except (AuthError, CryptoError, TrelloApiError) as exc:
        return _render_authorize(params, str(exc)), 400

    code = oauth.issue_auth_code(
        client_id=params["client_id"],
        user_id=user["id"],
        redirect_uri=params["redirect_uri"],
        code_challenge=params["code_challenge"],
        code_challenge_method=params["code_challenge_method"],
        scope=params["scope"] or None,
        resource=params["resource"] or None,
    )
    query = {"code": code}
    if params["state"]:
        query["state"] = params["state"]
    return redirect(_append_query(params["redirect_uri"], query), code=302)


# --------------------------------------------------------------------------
# Token endpoint
# --------------------------------------------------------------------------

@oauth_bp.post("/oauth/token")
def token():
    form = request.form.to_dict()
    grant_type = form.get("grant_type")
    basic_id, basic_secret = _basic_auth()
    if basic_id and not form.get("client_id"):
        form["client_id"] = basic_id
    client_secret = form.get("client_secret") or basic_secret

    try:
        if grant_type == "authorization_code":
            body = oauth.exchange_authorization_code(form, client_secret)
        elif grant_type == "refresh_token":
            body = oauth.refresh_access_token(form, client_secret)
        else:
            raise OAuthError("unsupported_grant_type", f"grant_type nao suportado: {grant_type!r}")
    except OAuthError as exc:
        return _oauth_error(exc)

    return jsonify(body), 200, _NO_STORE


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------

def _validate_authorize_params(params: dict):
    """Devolve (error, description) ou None."""
    if params["response_type"] != "code":
        return ("unsupported_response_type", "Apenas response_type=code e suportado.")
    if not params["code_challenge"]:
        return ("invalid_request", "PKCE obrigatorio: envie code_challenge.")
    if params["code_challenge_method"] not in oauth.CODE_CHALLENGE_METHODS:
        return ("invalid_request", "code_challenge_method deve ser S256.")
    return None


def _render_authorize(params: dict, error: str):
    return render_template(
        "authorize.html",
        params=params,
        client_name=_client_label(params["client_id"]),
        error=error,
    )


def _client_label(client_id: str) -> str:
    client = db.get_oauth_client(client_id or "")
    if client and client.get("client_name"):
        return client["client_name"]
    return "um cliente MCP"


def _append_query(url: str, extra: dict) -> str:
    sep = "&" if "?" in url else "?"
    return f"{url}{sep}{urlencode(extra)}"


def _redirect_error(redirect_uri: str, state: str, error: str, description: str):
    query = {"error": error, "error_description": description}
    if state:
        query["state"] = state
    return redirect(_append_query(redirect_uri, query), code=302)


def _oauth_error(exc: OAuthError):
    payload = {"error": exc.code, "error_description": exc.description}
    response = jsonify(payload)
    response.status_code = exc.status
    for key, value in _NO_STORE.items():
        response.headers[key] = value
    if exc.status == 401:
        response.headers["WWW-Authenticate"] = 'Bearer error="invalid_client"'
    return response


def _basic_auth() -> tuple[str | None, str | None]:
    """Extrai (client_id, client_secret) de um header client_secret_basic."""
    header = request.headers.get("Authorization", "")
    if not header.lower().startswith("basic "):
        return None, None
    try:
        decoded = base64.b64decode(header.split(None, 1)[1]).decode("utf-8")
    except (binascii.Error, ValueError, UnicodeDecodeError, IndexError):
        return None, None
    if ":" not in decoded:
        return None, None
    client_id, _, secret = decoded.partition(":")
    return (client_id or None), (secret or None)
