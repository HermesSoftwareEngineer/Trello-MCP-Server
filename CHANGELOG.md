# Changelog

## 0.5.0 (2026-11-12)

Retrocompatível: nenhuma tool existente mudou.

- **Nova tool `manage_powerup_checklists`**: checklists do Power-Up "Checklists" (projeto trello-power) — várias por card, itens com data de início, data de fim e responsável, e checklists padrão por board (`list_templates`, `create_template`, `update_template`, `delete_template`, `apply_template`). Ações em lote, como as demais.
- **Segurança**: a chave do Power-Up vale para todos os quadros, então card e board são sempre resolvidos com o token do usuário, e checklists/itens/padrões só são localizados dentro deles.
- Configuração nova: `POWERUP_API_URL` e `POWERUP_API_KEY`. Sem elas, a tool responde que não está configurada.
- Testes: suíte nova `test_powerup.py` (fakes do Trello e do Power-Up).

## 0.4.0 (2026-11-01)

Retrocompatível: nenhuma ação ou parâmetro existente mudou.

- **Copiar board**: `manage_board_structure` / `create_board` aceita `source_board` (copia listas e labels, via `idBoardSource`) e `keep_cards` (copia também os cards).
- **Mover lista entre boards**: `update_list` aceita `target_board` (junto com `name`/`position`).
- Erro "Lista ... nao encontrada" agora indica como criar a lista (`create_list`).

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
