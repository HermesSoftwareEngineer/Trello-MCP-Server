"""Persistencia dos usuarios conectados (SQLite)."""

import os
import sqlite3
from datetime import datetime, timezone

from .config import Config

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id                   INTEGER PRIMARY KEY AUTOINCREMENT,
    trello_member_id     TEXT NOT NULL UNIQUE,
    trello_username      TEXT,
    trello_full_name     TEXT,
    trello_key_enc       TEXT NOT NULL,
    trello_token_enc     TEXT NOT NULL,
    connector_token_hash TEXT NOT NULL UNIQUE,
    created_at           TEXT NOT NULL,
    updated_at           TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_users_connector_token_hash
    ON users (connector_token_hash);

-- OAuth 2.0: clientes registrados via Dynamic Client Registration (RFC 7591).
CREATE TABLE IF NOT EXISTS oauth_clients (
    client_id                  TEXT PRIMARY KEY,
    client_secret_hash         TEXT,
    client_name                TEXT,
    redirect_uris              TEXT NOT NULL,   -- JSON array
    grant_types                TEXT,            -- JSON array
    token_endpoint_auth_method TEXT NOT NULL DEFAULT 'none',
    created_at                 TEXT NOT NULL
);

-- Authorization codes: efemeros, uso unico, presos ao PKCE do cliente.
CREATE TABLE IF NOT EXISTS oauth_auth_codes (
    code_hash             TEXT PRIMARY KEY,
    client_id             TEXT NOT NULL,
    user_id               INTEGER NOT NULL,
    redirect_uri          TEXT NOT NULL,
    code_challenge        TEXT NOT NULL,
    code_challenge_method TEXT NOT NULL,
    scope                 TEXT,
    resource              TEXT,
    expires_at            TEXT NOT NULL,
    created_at            TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_oauth_codes_expires ON oauth_auth_codes (expires_at);

-- Access/refresh tokens emitidos. So o hash e persistido.
CREATE TABLE IF NOT EXISTS oauth_tokens (
    id                 INTEGER PRIMARY KEY AUTOINCREMENT,
    access_token_hash  TEXT NOT NULL UNIQUE,
    refresh_token_hash TEXT UNIQUE,
    client_id          TEXT NOT NULL,
    user_id            INTEGER NOT NULL,
    scope              TEXT,
    resource           TEXT,
    access_expires_at  TEXT NOT NULL,
    refresh_expires_at TEXT,
    created_at         TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_oauth_tokens_access  ON oauth_tokens (access_token_hash);
CREATE INDEX IF NOT EXISTS idx_oauth_tokens_refresh ON oauth_tokens (refresh_token_hash);
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def get_connection() -> sqlite3.Connection:
    os.makedirs(os.path.dirname(Config.DATABASE_PATH), exist_ok=True)
    # timeout: em producao varios workers do gunicorn compartilham o arquivo,
    # entao vale esperar um lock em vez de falhar de imediato.
    conn = sqlite3.connect(Config.DATABASE_PATH, timeout=10)
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    with get_connection() as conn:
        # WAL permite leituras concorrentes com uma escrita -- necessario com
        # mais de um worker. E persistente: basta configurar uma vez.
        conn.execute("PRAGMA journal_mode=WAL")
        conn.executescript(SCHEMA)


def upsert_user(
    *,
    trello_member_id: str,
    trello_username: str | None,
    trello_full_name: str | None,
    trello_key_enc: str,
    trello_token_enc: str,
    connector_token_hash: str,
) -> dict:
    """Cria ou atualiza o usuario pelo trello_member_id.

    Reconectar a mesma conta Trello rotaciona o connector token: o token
    antigo para de funcionar imediatamente.
    """
    now = _now()
    with get_connection() as conn:
        conn.execute(
            """
            INSERT INTO users (
                trello_member_id, trello_username, trello_full_name,
                trello_key_enc, trello_token_enc, connector_token_hash,
                created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT (trello_member_id) DO UPDATE SET
                trello_username      = excluded.trello_username,
                trello_full_name     = excluded.trello_full_name,
                trello_key_enc       = excluded.trello_key_enc,
                trello_token_enc     = excluded.trello_token_enc,
                connector_token_hash = excluded.connector_token_hash,
                updated_at           = excluded.updated_at
            """,
            (
                trello_member_id,
                trello_username,
                trello_full_name,
                trello_key_enc,
                trello_token_enc,
                connector_token_hash,
                now,
                now,
            ),
        )
        row = conn.execute(
            "SELECT * FROM users WHERE trello_member_id = ?", (trello_member_id,)
        ).fetchone()
    return dict(row)


def find_user_by_connector_token_hash(token_hash: str) -> dict | None:
    with get_connection() as conn:
        row = conn.execute(
            "SELECT * FROM users WHERE connector_token_hash = ?", (token_hash,)
        ).fetchone()
    return dict(row) if row else None


def delete_user_by_member_id(trello_member_id: str) -> bool:
    """Apaga o usuario e, junto, todo access/refresh token e auth code dele."""
    with get_connection() as conn:
        row = conn.execute(
            "SELECT id FROM users WHERE trello_member_id = ?", (trello_member_id,)
        ).fetchone()
        if row is None:
            return False
        user_id = row["id"]
        conn.execute("DELETE FROM oauth_tokens WHERE user_id = ?", (user_id,))
        conn.execute("DELETE FROM oauth_auth_codes WHERE user_id = ?", (user_id,))
        conn.execute("DELETE FROM users WHERE id = ?", (user_id,))
    return True


def upsert_trello_credentials(
    *,
    trello_member_id: str,
    trello_username: str | None,
    trello_full_name: str | None,
    trello_key_enc: str,
    trello_token_enc: str,
    fallback_connector_token_hash: str,
) -> dict:
    """Salva/atualiza as credenciais do Trello sem tocar no connector token.

    Usado pelo fluxo OAuth: a identidade ali vem do access token, nao do
    connector token. Em um INSERT novo a coluna NOT NULL
    `connector_token_hash` recebe `fallback_connector_token_hash` (um hash
    aleatorio inutilizavel); em um UPDATE ela e preservada, entao um token
    manual ja emitido para essa conta continua valendo.
    """
    now = _now()
    with get_connection() as conn:
        conn.execute(
            """
            INSERT INTO users (
                trello_member_id, trello_username, trello_full_name,
                trello_key_enc, trello_token_enc, connector_token_hash,
                created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT (trello_member_id) DO UPDATE SET
                trello_username  = excluded.trello_username,
                trello_full_name = excluded.trello_full_name,
                trello_key_enc   = excluded.trello_key_enc,
                trello_token_enc = excluded.trello_token_enc,
                updated_at       = excluded.updated_at
            """,
            (
                trello_member_id,
                trello_username,
                trello_full_name,
                trello_key_enc,
                trello_token_enc,
                fallback_connector_token_hash,
                now,
                now,
            ),
        )
        row = conn.execute(
            "SELECT * FROM users WHERE trello_member_id = ?", (trello_member_id,)
        ).fetchone()
    return dict(row)


# --------------------------------------------------------------------------
# OAuth 2.0
# --------------------------------------------------------------------------

def create_oauth_client(
    *,
    client_id: str,
    client_secret_hash: str | None,
    client_name: str | None,
    redirect_uris: str,
    grant_types: str,
    token_endpoint_auth_method: str,
) -> None:
    with get_connection() as conn:
        conn.execute(
            """
            INSERT INTO oauth_clients (
                client_id, client_secret_hash, client_name, redirect_uris,
                grant_types, token_endpoint_auth_method, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                client_id,
                client_secret_hash,
                client_name,
                redirect_uris,
                grant_types,
                token_endpoint_auth_method,
                _now(),
            ),
        )


def get_oauth_client(client_id: str) -> dict | None:
    with get_connection() as conn:
        row = conn.execute(
            "SELECT * FROM oauth_clients WHERE client_id = ?", (client_id,)
        ).fetchone()
    return dict(row) if row else None


def create_auth_code(
    *,
    code_hash: str,
    client_id: str,
    user_id: int,
    redirect_uri: str,
    code_challenge: str,
    code_challenge_method: str,
    scope: str | None,
    resource: str | None,
    expires_at: str,
) -> None:
    with get_connection() as conn:
        conn.execute("DELETE FROM oauth_auth_codes WHERE expires_at < ?", (_now(),))
        conn.execute(
            """
            INSERT INTO oauth_auth_codes (
                code_hash, client_id, user_id, redirect_uri, code_challenge,
                code_challenge_method, scope, resource, expires_at, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                code_hash,
                client_id,
                user_id,
                redirect_uri,
                code_challenge,
                code_challenge_method,
                scope,
                resource,
                expires_at,
                _now(),
            ),
        )


def pop_auth_code(code_hash: str) -> dict | None:
    """Le e apaga o code na mesma transacao -- garante uso unico."""
    with get_connection() as conn:
        row = conn.execute(
            "SELECT * FROM oauth_auth_codes WHERE code_hash = ?", (code_hash,)
        ).fetchone()
        if row is not None:
            conn.execute("DELETE FROM oauth_auth_codes WHERE code_hash = ?", (code_hash,))
    return dict(row) if row else None


def store_oauth_token(
    *,
    access_token_hash: str,
    refresh_token_hash: str | None,
    client_id: str,
    user_id: int,
    scope: str | None,
    resource: str | None,
    access_expires_at: str,
    refresh_expires_at: str | None,
) -> None:
    with get_connection() as conn:
        conn.execute(
            """
            INSERT INTO oauth_tokens (
                access_token_hash, refresh_token_hash, client_id, user_id,
                scope, resource, access_expires_at, refresh_expires_at, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                access_token_hash,
                refresh_token_hash,
                client_id,
                user_id,
                scope,
                resource,
                access_expires_at,
                refresh_expires_at,
                _now(),
            ),
        )


def find_user_by_access_token_hash(token_hash: str) -> dict | None:
    with get_connection() as conn:
        row = conn.execute(
            """
            SELECT u.* FROM users u
            JOIN oauth_tokens t ON t.user_id = u.id
            WHERE t.access_token_hash = ? AND t.access_expires_at > ?
            """,
            (token_hash, _now()),
        ).fetchone()
    return dict(row) if row else None


def get_token_by_refresh_hash(refresh_token_hash: str) -> dict | None:
    with get_connection() as conn:
        row = conn.execute(
            "SELECT * FROM oauth_tokens WHERE refresh_token_hash = ?",
            (refresh_token_hash,),
        ).fetchone()
    return dict(row) if row else None


def rotate_oauth_token(
    *,
    token_id: int,
    access_token_hash: str,
    refresh_token_hash: str,
    access_expires_at: str,
    refresh_expires_at: str,
) -> None:
    """Troca access e refresh da mesma linha -- o par antigo morre na hora."""
    with get_connection() as conn:
        conn.execute(
            """
            UPDATE oauth_tokens SET
                access_token_hash  = ?,
                refresh_token_hash = ?,
                access_expires_at  = ?,
                refresh_expires_at = ?
            WHERE id = ?
            """,
            (
                access_token_hash,
                refresh_token_hash,
                access_expires_at,
                refresh_expires_at,
                token_id,
            ),
        )
