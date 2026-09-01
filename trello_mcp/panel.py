"""Painel web onde o usuario conecta a conta do Trello."""

from flask import Blueprint, current_app, render_template, request

from . import db
from .auth import AuthError, connect_account
from .crypto import CryptoError
from .trello_client import TrelloApiError, TrelloAuthError

panel_bp = Blueprint("panel", __name__)


def _mcp_url() -> str:
    return f"{current_app.config['PUBLIC_BASE_URL']}/mcp"


@panel_bp.get("/")
@panel_bp.get("/panel")
def index():
    return render_template("connect.html", mcp_url=_mcp_url())


@panel_bp.post("/panel/connect")
def connect():
    api_key = request.form.get("api_key", "")
    token = request.form.get("token", "")

    try:
        user, connector_token = connect_account(api_key, token)
    except TrelloAuthError:
        error = "O Trello recusou essas credenciais. Confira a API Key e o Token."
    except (AuthError, CryptoError, TrelloApiError) as exc:
        error = str(exc)
    else:
        return render_template(
            "connected.html",
            user=user,
            connector_token=connector_token,
            mcp_url=_mcp_url(),
        )

    return render_template("connect.html", mcp_url=_mcp_url(), error=error), 400


@panel_bp.post("/panel/disconnect")
def disconnect():
    """Revoga o acesso. Exige as credenciais do Trello como prova de posse."""
    api_key = request.form.get("api_key", "")
    token = request.form.get("token", "")

    try:
        user, _ = connect_account(api_key, token)
    except TrelloAuthError:
        error = "O Trello recusou essas credenciais. Confira a API Key e o Token."
    except (AuthError, CryptoError, TrelloApiError) as exc:
        error = str(exc)
    else:
        db.delete_user_by_member_id(user["trello_member_id"])
        return render_template(
            "connect.html",
            mcp_url=_mcp_url(),
            notice="Conta desconectada. As credenciais foram apagadas do servidor.",
        )

    return render_template("connect.html", mcp_url=_mcp_url(), error=error), 400
