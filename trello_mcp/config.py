import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

BASE_DIR = Path(__file__).resolve().parent.parent


def _resolve_db_path(raw: str) -> str:
    path = Path(raw)
    if not path.is_absolute():
        path = BASE_DIR / path
    return str(path)


class Config:
    HOST = os.getenv("HOST", "0.0.0.0")
    PORT = int(os.getenv("PORT", "8000"))
    DEBUG = os.getenv("FLASK_DEBUG", "0") == "1"

    APP_SECRET_KEY = os.getenv("APP_SECRET_KEY", "")
    SECRET_KEY = APP_SECRET_KEY or "dev-insecure-key"

    DATABASE_PATH = _resolve_db_path(os.getenv("DATABASE_PATH", "instance/trello_mcp.sqlite3"))
    PUBLIC_BASE_URL = os.getenv("PUBLIC_BASE_URL", "http://localhost:8000").rstrip("/")

    TRELLO_API_BASE_URL = "https://api.trello.com/1"

    # OAuth 2.0 -- o proprio servidor e o authorization server. O issuer e a
    # URL publica; os endpoints ficam abaixo dela. Ver trello_mcp/oauth.py.
    OAUTH_ISSUER = PUBLIC_BASE_URL
    OAUTH_CODE_TTL = int(os.getenv("OAUTH_CODE_TTL", "600"))  # 10 min
    OAUTH_ACCESS_TOKEN_TTL = int(os.getenv("OAUTH_ACCESS_TOKEN_TTL", str(30 * 24 * 3600)))
    OAUTH_REFRESH_TOKEN_TTL = int(os.getenv("OAUTH_REFRESH_TOKEN_TTL", str(180 * 24 * 3600)))
