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

1. Abra `http://localhost:8000/panel`.
2. Cole a **API Key** e o **Token** gerados em [trello.com/app-key](https://trello.com/app-key).
3. O servidor valida as credenciais direto no Trello — isso prova que a conta é sua.
4. Guarde o **connector token** exibido (só aparece uma vez) e configure no Claude:

```bash
claude mcp add --transport http trello http://localhost:8000/mcp --header "Authorization: Bearer SEU_TOKEN"
```

Reconectar a mesma conta gera um token novo e revoga o anterior.

## Tools

9 tools, desenhadas para **poucas chamadas e muito poder por chamada**.

| Tool | Tipo | O que faz |
|---|---|---|
| `list_boards` | leitura | Lista boards; `include` já traz listas/labels/membros junto |
| `get_board_snapshot` | leitura | Board inteiro numa chamada: listas, labels, membros, cards, checklists e comentários |
| `search` | leitura | Busca global; aceita a sintaxe do Trello (`@me`, `due:week`, `label:red`) + filtros estruturados |
| `get_activity` | leitura | Histórico de board/card/membro, com resumo em texto por ação |
| `manage_cards` | escrita | create, update, move, archive, unarchive, duplicate, get — em lote |
| `manage_checklists_and_comments` | escrita | Checklists, itens e comentários — em lote |
| `manage_board_structure` | escrita | Boards, listas e labels; `create_board` monta o board inteiro de uma vez |
| `manage_members` | escrita | Membros de board e atribuição em cards (produto membros × cards) |
| `delete_items` | **destrutiva** | Remoções permanentes, com `dry_run` |

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
| `trello_mcp/auth.py` | Conectar conta, resolver connector token → credenciais |
| `trello_mcp/panel.py` + `templates/` | Painel web (`/panel`) |
| `trello_mcp/mcp_server.py` | Endpoint JSON-RPC `/mcp` e dispatch das tools |
| `trello_mcp/trello_client.py` | Wrapper HTTP da API do Trello |
| `trello_mcp/tools/common.py` | Resolvers com cache, filtros de card, batch runner |
| `trello_mcp/tools/*.py` | Uma tool (ou grupo) por arquivo |

### Modelo de segurança

- Key e Token do Trello são criptografados (Fernet) antes de ir para o banco.
- Do connector token só o hash SHA-256 é persistido.
- Todo `POST /mcp` exige `Authorization: Bearer <connector_token>`.
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

**`--timeout 120` no gunicorn.** Um lote grande de operações encadeia várias chamadas à API do Trello (cada uma com até 15s de timeout); o default de 30s mataria requisições legítimas.

## Testes

Suíte sem dependências externas, rodando contra um fake da API do Trello:

```bash
.venv\Scripts\python.exe tests/run_all.py
```

- `tests/test_auth.py` — banco, criptografia, rotação de token, `/mcp` sem auth
- `tests/test_tools.py` — as 9 tools, filtros, lotes parciais e mensagens de erro
- `tests/test_mcp.py` — handshake MCP, `tools/list`, `tools/call`, tratamento de erros

## Status

- [x] Infraestrutura Flask + endpoint MCP JSON-RPC
- [x] Multiusuário com token por sessão + painel web
- [x] 9 tools do Trello + suíte de testes
- [ ] Validado contra a API real do Trello (só testado contra o fake)
