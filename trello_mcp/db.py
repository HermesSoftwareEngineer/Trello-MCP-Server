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
    with get_connection() as conn:
        cursor = conn.execute(
            "DELETE FROM users WHERE trello_member_id = ?", (trello_member_id,)
        )
    return cursor.rowcount > 0
