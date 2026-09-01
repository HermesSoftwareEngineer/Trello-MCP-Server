from flask import Flask

from . import db
from .config import Config
from .mcp_server import mcp_bp
from .panel import panel_bp


def create_app(config_class: type[Config] = Config) -> Flask:
    app = Flask(__name__)
    app.config.from_object(config_class)

    db.init_db()

    app.register_blueprint(panel_bp)
    app.register_blueprint(mcp_bp)

    @app.get("/health")
    def health():
        return {"status": "ok"}

    return app
