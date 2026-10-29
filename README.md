# Trello MCP Server

Servidor MCP (Model Context Protocol) para o Trello, em Flask. Multiusuário:
cada pessoa conecta a própria conta do Trello por um painel web e recebe um
connector token para configurar no Claude.

## Setup

```bash
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
copy .env.example .env
```

Gere a chave mestra e coloque em `APP_SECRET_KEY` no `.env`:

```bash
python -c "import secrets; print(secrets.token_urlsafe(48))"
```

> Trocar a `APP_SECRET_KEY` depois torna as credenciais já salvas ilegíveis —
> os usuários precisarão reconectar a conta.

Suba o servidor:

```bash
python run.py
```

## Conectar uma conta

Há dois caminhos. Ambos provam a posse da conta do mesmo jeito: você
apresenta um par **API Key + Token** válido do Trello.

### A) OAuth (Claude Desktop / claude.ai)

Adicione um **conector customizado** apontando só para a URL:

```
https://trello-mcp.olimpo-services.com.br/mcp
```

O cliente descobre o restante sozinho (metadata, registro dinâmico), abre
`/oauth/authorize` no navegador, você cola API Key + Token do Trello e
autoriza. O cliente recebe um access token renovável — sem header manual.

### B) Connector token manual (Claude Code / CLI)

1. Abra `http://localhost:8000/panel`.
2. Cole a **API Key** e o **Token** gerados em [trello.com/app-key](https://trello.com/app-key).
3. O servidor valida as credenciais direto no Trello — isso prova que a conta é sua.
4. Guarde o **connector token** exibido (só aparece uma vez) e configure no Claude:

```bash
claude mcp add --transport http trello http://localhost:8000/mcp --header "Authorization: Bearer SEU_TOKEN"
```

Reconectar a mesma conta gera um token novo e revoga o anterior.

## OAuth 2.0

O próprio servidor é o *authorization server*. O `/mcp` aceita como `Bearer`
tanto um connector token manual quanto um access token OAuth.

| Endpoint | RFC | Papel |
|---|---|---|
| `/.well-known/oauth-protected-resource` (+ `/mcp`) | 9728 | Diz qual é o authorization server |
| `/.well-known/oauth-authorization-server` (+ `/mcp`) | 8414 | Metadata: endpoints, PKCE S256, grants |
| `POST /oauth/register` | 7591 | Dynamic Client Registration (aberto) |
| `GET/POST /oauth/authorize` | 6749 | Usuário prova a conta do Trello → `code` |
| `POST /oauth/token` | 6749 | `code`+PKCE ou `refresh_token` → access token |

- **PKCE S256 obrigatório.** `plain` é recusado.
- **Clientes públicos** (`token_endpoint_auth_method: none`) e confidenciais
  (`client_secret_post` / `client_secret_basic`) são aceitos.
- Auth code: uso único, TTL `OAUTH_CODE_TTL` (10 min). Access token:
  `OAUTH_ACCESS_TOKEN_TTL` (30 dias). Refresh: `OAUTH_REFRESH_TOKEN_TTL`
  (180 dias), rotacionado a cada uso — o par antigo morre na hora.
- Só o hash SHA-256 de codes e tokens é persistido.
- Revogar todos os acessos de uma conta: desconecte pelo `/panel` — o
  usuário e todos os seus tokens/codes são apagados juntos.
- `Access-Control-Allow-Origin: *` nos endpoints OAuth, `/mcp` e
  `/.well-known/*` para os clientes web (claude.ai).

## Tools

11 tools, desenhadas para **poucas chamadas e muito poder por chamada**.

| Tool | Tipo | O que faz |
|---|---|---|
| `list_boards` | leitura | Lista boards; `include` já traz listas/labels/membros junto |
| `get_board_snapshot` | leitura | Board inteiro numa chamada: listas, labels, membros, cards, checklists e comentários |
| `search` | leitura | Busca global; aceita a sintaxe do Trello (`@me`, `due:week`, `label:red`) + filtros estruturados |
| `get_activity` | leitura | Histórico de board/card/membro, com resumo em texto por ação |
| `manage_cards` | escrita | create, update, move, archive, unarchive, duplicate, get — em lote |
| `manage_checklists_and_comments` | escrita | Checklists, itens e comentários — em lote |
| `manage_board_structure` | escrita | Boards, listas e labels; `create_board` monta o board inteiro de uma vez |
| `manage_attachments` | escrita | Anexa link ou arquivo (base64, até 10 MB), lista anexos e controla a capa — em lote |
| `read_attachment` | leitura | Lê ou **baixa** um anexo de card de **qualquer tipo** (texto, PDF, imagem, zip, docx/xlsx/pptx, vídeo, áudio...) com as credenciais do servidor |
| `manage_members` | escrita | Membros de board e atribuição em cards (produto membros × cards) |
| `delete_items` | **destrutiva** | Remoções permanentes, com `dry_run` |

### `read_attachment`: ler arquivos anexados

As URLs de anexos do Trello exigem autenticação, então um agente não consegue abri-las sozinho. `read_attachment` baixa o arquivo com as credenciais do usuário autenticado e devolve o conteúdo:

```json
{"card": "<id, shortLink ou nome>", "attachment": "<id ou nome, ex.: roteiro-v1.pdf>", "board": "<opcional>",
 "mode": "auto | info | download", "offset": 0, "part": 1, "length": 98304, "entry": "<arquivo dentro de um .zip>"}
```

| Tipo | Retorno | Limite |
|---|---|---|
| `.txt` `.md` `.json` `.csv` | texto (UTF-8) | 60 KB; acima disso vem cortado e marcado `TRUNCADO` |
| PDF | texto extraído (`pypdf`; se vier vazio, um extrator mínimo de `(texto) Tj`) | arquivo até 10 MB, 60 páginas, 60 KB de texto |
| PNG / JPG | bloco MCP `image` (base64 + `mimeType`) | 5 MB |
| `.zip` | **lista** o conteúdo (nome, tamanho, avisos) **sem extrair** | arquivo até 30 MB, 500 entradas |
| `.docx` `.xlsx` `.pptx` | texto extraído (parágrafos; planilhas em TSV; slides) | arquivo até 30 MB, 60 KB de texto |
| vídeo, áudio e demais | metadados (nome, tipo, tamanho, id) e instruções de download | — |
| link externo | erro claro | — |

**Baixar qualquer arquivo** (`mode: "download"`): devolve JSON com `data` (base64), `offset`, `length`, `next_offset`, `done`, `total_bytes` e `sha256_da_parte`. Repita com `offset = next_offset` (ou `part` 2, 3...) até `done: true` e junte as partes. Parte padrão de 96 KB (máx. 512 KB por chamada); o arquivo é lido até 64 MB. `mode: "info"` devolve só os metadados, sem baixar. `entry` lê (ou, com `download`, baixa em partes) um arquivo de dentro de um `.zip`.

Proteções: nada é gravado em disco (tudo em memória, com limites); entradas de zip com caminho `..`/absoluto são sinalizadas (`unsafe_path`) e nunca extraídas; descompactação em streaming com teto de 20 MB por entrada e aviso de zip-bomb por taxa de compressão; docx/xlsx/pptx com DTD/entidades são recusados; os retornos nunca trazem a URL do anexo nem credenciais. Observação prática: cada parte em base64 passa pelo contexto do agente, então para arquivos grandes (vídeos) o uso realista é metadados/tamanho; baixe por partes só o que for necessário.

- O anexo é procurado **só entre os anexos do card informado**; id de outro card é recusado. O card é resolvido com as credenciais do usuário, então vale a mesma autorização das demais tools.
- Com vários anexos de mesmo nome (versões reenviadas), vale o mais recente.
- Só baixa de `https://*.trello.com`; as credenciais nunca seguem para outro host (redirects para storage assinado vão sem o header). Somente leitura (`readOnlyHint`).
- Anexos que são só link (`isUpload: false`) não são baixados.

### Princípios de design

**Nomes valem como ids.** Todo parâmetro que identifica algo (`board`, `list`, `card`, `labels`, `members`, `checklist`, `item`) aceita id ou nome — casa exato, depois prefixo, depois substring. Nomes ambíguos retornam erro listando os candidatos, então a IA se corrige sozinha.

**Lote com relatório por item.** As tools de escrita recebem `operations: [...]`. Uma falha não aborta as demais; o retorno traz `summary` e um `results[i]` com `status: ok|error` por operação.

**Datas flexíveis.** `due`/`start`/`since`/`before` aceitam ISO, `today`, `tomorrow`, `yesterday`, `now` e offsets (`+3d`, `-2w`, `+6h`). String vazia limpa o campo.

**Alterações incrementais.** `labels` e `members` aceitam uma lista (substitui tudo) ou `{add, remove, set}`.

**Resposta enxuta por padrão.** Ids viram nomes, payloads viram contadores. `include_desc`, `desc_max_chars`, `max_cards` e `include_raw` controlam o tamanho.

### Autorização por tool

As tools declaram *annotations* MCP (`readOnlyHint`, `destructiveHint`) em `tools/list`, e o cliente decide o que exige confirmação. Por isso as remoções permanentes ficam isoladas em `delete_items` (única marcada `destructiveHint: true`): dá para liberar as tools de escrita normais e exigir aprovação só nas destrutivas. `archive` (reversível) mora em `manage_cards`, não ali.

## Arquitetura

| Arquivo | Responsabilidade |
|---|---|
| `run.py` | Entrypoint |
| `trello_mcp/__init__.py` | App factory, registro de blueprints, init do banco |
| `trello_mcp/config.py` | Configuração via `.env` |
| `trello_mcp/crypto.py` | Fernet para credenciais em repouso + hash do connector token |
| `trello_mcp/db.py` | SQLite: tabela `users` |
| `trello_mcp/auth.py` | Conectar conta, resolver Bearer (connector token ou access token) → credenciais |
| `trello_mcp/oauth.py` | Lógica do OAuth: metadata, DCR, PKCE, emissão/rotação de token |
| `trello_mcp/oauth_routes.py` | Endpoints `/.well-known/*`, `/oauth/register`, `/oauth/authorize`, `/oauth/token` |
| `trello_mcp/panel.py` + `templates/` | Painel web (`/panel`) e telas do OAuth |
| `trello_mcp/mcp_server.py` | Endpoint JSON-RPC `/mcp` e dispatch das tools |
| `trello_mcp/trello_client.py` | Wrapper HTTP da API do Trello |
| `trello_mcp/tools/common.py` | Resolvers com cache, filtros de card, batch runner |
| `trello_mcp/tools/*.py` | Uma tool (ou grupo) por arquivo (`read_attachment.py`: leitura de anexos) |

### Modelo de segurança

- Key e Token do Trello são criptografados (Fernet) antes de ir para o banco.
- Do connector token e dos tokens OAuth só o hash SHA-256 é persistido.
- Todo `POST /mcp` exige `Authorization: Bearer <token>` (connector token ou access token OAuth).
- Erros de tool voltam como `isError: true` no resultado (não como erro de protocolo), para o modelo ler a mensagem e se corrigir.

## Deploy (Docker + Traefik)

Host de produção: `trello-mcp.olimpo-services.com.br`

Na VPS, dentro do diretório do projeto, crie o `.env` com a chave mestra:

```bash
echo "APP_SECRET_KEY=$(python3 -c 'import secrets; print(secrets.token_urlsafe(48))')" > .env
```

`PORT`, `DATABASE_PATH`, `PUBLIC_BASE_URL` e `FLASK_DEBUG` já vêm do `docker-compose.yml` — o `.env` precisa conter apenas a `APP_SECRET_KEY`. Suba:

```bash
docker compose up -d --build
```

Confira: `https://trello-mcp.olimpo-services.com.br/health` deve responder `{"status":"ok"}`.

### Notas de infraestrutura

**Volume nomeado, não bind mount.** O container roda como usuário não-root (uid 1000). Um bind mount `./data` seria criado pelo Docker como root e o container não conseguiria escrever o SQLite. O volume nomeado `trello-mcp-data` herda o dono de `/app/data` definido na imagem. Backup:

```bash
docker run --rm -v trello-mcp-data:/data -v $(pwd):/backup alpine tar czf /backup/trello-mcp-backup.tar.gz -C /data .
```

**A `APP_SECRET_KEY` é o dado mais crítico do deploy.** Ela descriptografa as credenciais do Trello de todos os usuários. Perdê-la ou trocá-la obriga todo mundo a reconectar a conta no painel. Faça backup dela junto com o volume.

**3 workers do gunicorn sobre um SQLite.** O banco roda em modo WAL (leituras concorrentes com uma escrita) e as conexões têm `timeout=10`. Escrita só acontece quando alguém conecta uma conta no painel — o caminho quente (`/mcp`) é só leitura no banco. Ajuste com `WEB_CONCURRENCY` se precisar.

**Workers `gthread` (3 × 8 threads).** O trabalho é espera de I/O na API do Trello; com workers `sync`, três chamadas lentas ocupavam todos os workers e o `initialize`/`tools/list` de outro cliente ficava na fila (o OpenClaw estourava o limite de 1,5 s). `initialize`, `ping` e `tools/list` não chamam o Trello nem fazem I/O além da checagem do token. Ajuste com `WEB_CONCURRENCY` e `WEB_THREADS`.

**Timeouts.** Chamadas ao Trello: conexão 10 s / leitura 15 s; upload de anexo: leitura 90 s; download de anexo: 30 s por leitura e 45 s no total. Leituras (GET) repetem uma vez em falha de rede; escritas não (evita anexo duplicado: o erro avisa para conferir com `list`). Erros de rede não exibem key/token. Corpo de requisição limitado a `MAX_REQUEST_BYTES` (64 MB).

**`--timeout 120` no gunicorn.** Um lote grande de operações encadeia várias chamadas à API do Trello (cada uma com até 15s de timeout); o default de 30s mataria requisições legítimas.

## Testes

**Suíte offline** (sem credenciais, contra um fake da API do Trello):

```bash
.venv\Scripts\python.exe tests/run_all.py
```

- `tests/test_auth.py` — banco, criptografia, rotação de token, `/mcp` sem auth
- `tests/test_tools.py` — as tools, filtros, lotes parciais e mensagens de erro
- `tests/test_mcp.py` — handshake MCP, `tools/list`, `tools/call`, tratamento de erros
- `tests/test_attachment_types.py` — `read_attachment` para qualquer tipo: info, download em partes, zip (listar/entrada/zip-bomb/path traversal), docx/xlsx/pptx, mídia
- `tests/test_read_attachment.py` — `read_attachment` (texto, PDF, imagem, limites, autorização), robustez do base64, timeouts/segredos do cliente HTTP e velocidade do handshake

Os testes precisam de `APP_SECRET_KEY` e, de preferência, de um banco descartável: `APP_SECRET_KEY=teste DATABASE_PATH=/tmp/t.sqlite3 python tests/run_all.py`.

**Suíte contra a API real** (opcional, exige credenciais no ambiente):

```bash
TRELLO_API_KEY=... TRELLO_TOKEN=... .venv/Scripts/python.exe tests/test_real_api.py
```

Sem as variáveis, ela se pula sozinha. A parte de leitura só consulta um board existente; a de escrita cria um board descartável (`ZZ TESTE MCP (apagar)`), exercita tudo dentro dele e o apaga no fim — **nenhum board existente é modificado**. Se a limpeza falhar, o id do board é impresso para remoção manual.

## Notas sobre a API do Trello

Comportamentos confirmados contra a API real, que explicam decisões do código:

**Atribuição de membro é idempotente aqui, não lá.** O Trello devolve `400 member is already on the card` ao reatribuir alguém que já está no card (e `not on the card` no caso inverso). Como o efeito desejado já vale, `manage_members` trata isso como sucesso e marca `already_applied: true` — a IA reatribui por garantia o tempo todo.

**Rate limit: 300 req/10s por API key, 100 req/10s por token.** Lotes grandes podem estourar. O cliente reage a `429` com até 3 tentativas e backoff (respeitando `Retry-After`); depois disso o erro sugere dividir o lote.

**`GET /boards/{id}/checklists` aceita `checkItems`/`checkItem_fields`** embora a documentação oficial não liste esses params para esse endpoint. É o que permite o `depth='full'` sem uma chamada por card. Se parar de funcionar, o fallback é iterar `/cards/{id}/checklists`.

**A busca ignora queries de 1 caractere.** `query: "a"` retorna zero resultados — não é bug do servidor.

**`GET /cards/{id}/actions` não documenta `limit`.** Funciona na prática, mas a paginação oficial ali é por `page` (50 por página).

## Status

- [x] Infraestrutura Flask + endpoint MCP JSON-RPC
- [x] Multiusuário com token por sessão + painel web
- [x] 11 tools do Trello + suíte de testes
- [x] `read_attachment` (v0.2.0) para qualquer tipo de arquivo (v0.3.0); handshake sem I/O e workers gthread
- [x] Deploy Docker + Traefik
- [x] Validado contra a API real do Trello (57 verificações, leitura e escrita)
