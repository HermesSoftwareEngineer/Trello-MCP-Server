from trello_mcp import create_app
from trello_mcp.config import Config

app = create_app()

if __name__ == "__main__":
    app.run(host=Config.HOST, port=Config.PORT, debug=Config.DEBUG)
