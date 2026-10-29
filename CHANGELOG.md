# Changelog

## 0.2.0 (2026-10-28)

Retrocompatível: nenhuma tool ou parâmetro existente mudou.

- **Nova tool `read_attachment`**: lê o conteúdo de anexos de card (txt/md/json/csv, PDF com texto extraído, PNG/JPG como bloco `image`), com limites e erros claros. Dependência nova: `pypdf==6.21.4`.
- **Handshake rápido**: gunicorn passa a `gthread` (3 workers x 8 threads, keep-alive), então `initialize`/`tools/list` não ficam na fila atrás de chamadas lentas ao Trello. `notifications/*` respondem 204.
- **Anexos (`manage_attachments`)**: base64 validado (antes lixo virava arquivo corrompido), aceita quebras de linha, padding ausente e url-safe; tamanho checado antes de decodificar; `filename` sem caminho; timeout de upload 90 s com aviso para conferir antes de repetir; limite de corpo de 64 MB (413 em JSON-RPC).
- **Cliente HTTP**: timeouts (conexão, leitura); GET repete uma vez em falha de rede; mensagens de erro de rede não vazam key/token.
- Testes: suíte nova `test_read_attachment.py`; corrigidos testes que dependiam da data atual e da contagem de tools.

## 0.1.0

Servidor MCP, OAuth 2.0, 10 tools (incluindo `manage_attachments`).
