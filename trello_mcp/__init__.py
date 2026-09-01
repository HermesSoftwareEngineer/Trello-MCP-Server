from flask import Flask

from . import db
from .config import Config
from .mcp_server import mcp_bp
from .oauth_routes import oauth_bp
from .panel import panel_bp

# Endpoints que clientes MCP no navegador (claude.ai) chamam cross-origin.
_CORS_PATHS = ("/mcp", "/oauth/", "/.well-known/")


def create_app(config_class: type[Config] = Config) -> Flask:
    app = Flask(__name__)
    app.config.from_object(config_class)

    db.init_db()

    app.register_blueprint(panel_bp)
    app.register_blueprint(oauth_bp)
    app.register_blueprint(mcp_bp)

    @app.get("/health")
    def health():
        return {"status": "ok"}

    @app.after_request
    def _cors(response):
        if request_path_needs_cors():
            response.headers.setdefault("Access-Control-Allow-Origin", "*")
            response.headers.setdefault("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
            response.headers.setdefault(
                "Access-Control-Allow-Headers",
                "Authorization, Content-Type, MCP-Protocol-Version",
            )
            response.headers.setdefault("Access-Control-Expose-Headers", "WWW-Authenticate")
            response.headers.setdefault("Access-Control-Max-Age", "3600")
        return response

    return app


def request_path_needs_cors() -> bool:
    from flask import request

    path = request.path
    return any(path == p or path.startswith(p) for p in _CORS_PATHS)
