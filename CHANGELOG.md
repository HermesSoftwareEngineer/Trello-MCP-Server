# Changelog

## 0.3.0 (2026-10-29)

Retrocompatível: texto, PDF e imagem continuam com o mesmo retorno; `card` e `attachment` seguem sendo os únicos parâmetros obrigatórios.

- **`read_attachment` aceita qualquer tipo** (antes: "Tipo nao suportado" para zip, mp4 etc.). Novos parâmetros opcionais: `mode` (`auto`/`info`/`download`), `offset`, `part`, `length`, `entry`.
- **Download em partes** (`mode: "download"`): base64 em JSON com `offset`/`next_offset`/`done`/`sha256_da_parte`; 96 KB por parte (máx. 512 KB), arquivo lido até 64 MB; cabe no tempo de chamada do cliente (~60 s).
- **Zip**: lista o conteúdo sem extrair; `entry` lê um arquivo de dentro (texto/imagem direto, binário via download). Proteções: nada em disco, caminhos `..`/absolutos sinalizados, teto de 20 MB por entrada, aviso de zip-bomb, entradas com senha recusadas.
- **docx/xlsx/pptx**: texto extraído sem dependências novas (XML com DTD/entidades é recusado).
- **Vídeo, áudio e outros**: metadados (nome, tipo, tamanho, id) e instruções de download. Retornos nunca incluem a URL do anexo nem credenciais.
- `TrelloClient.download` ganhou `offset` (pula bytes sem guardá-los em memória).
- Testes: nova suíte `test_attachment_types.py`.

## 0.2.0 (2026-10-28)

Retrocompatível: nenhuma tool ou parâmetro existente mudou.

- **Nova tool `read_attachment`**: lê o conteúdo de anexos de card (txt/md/json/csv, PDF com texto extraído, PNG/JPG como bloco `image`), com limites e erros claros. Dependência nova: `pypdf==6.21.4`.
- **Handshake rápido**: gunicorn passa a `gthread` (3 workers x 8 threads, keep-alive), então `initialize`/`tools/list` não ficam na fila atrás de chamadas lentas ao Trello. `notifications/*` respondem 204.
- **Anexos (`manage_attachments`)**: base64 validado (antes lixo virava arquivo corrompido), aceita quebras de linha, padding ausente e url-safe; tamanho checado antes de decodificar; `filename` sem caminho; timeout de upload 90 s com aviso para conferir antes de repetir; limite de corpo de 64 MB (413 em JSON-RPC).
- **Cliente HTTP**: timeouts (conexão, leitura); GET repete uma vez em falha de rede; mensagens de erro de rede não vazam key/token.
- Testes: suíte nova `test_read_attachment.py`; corrigidos testes que dependiam da data atual e da contagem de tools.

## 0.1.0

Servidor MCP, OAuth 2.0, 10 tools (incluindo `manage_attachments`).
