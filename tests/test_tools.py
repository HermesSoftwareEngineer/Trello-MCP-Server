"""Exercita as 9 tools contra o fake da API do Trello."""
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

import json
import sys

from fake_trello import FakeTrello
from trello_mcp.tools import call_tool, list_tools

PASS, FAIL = [], []


def check(label, condition, detail=""):
    (PASS if condition else FAIL).append(label)
    mark = "OK  " if condition else "FALHOU"
    print(f"  [{mark}] {label}" + (f"  -> {detail}" if detail and not condition else ""))


def run(fake, tool, args):
    return call_tool(fake, tool, args)


def section(title):
    print(f"\n=== {title} ===")


# --------------------------------------------------------------------------
section("Definicoes das tools")
tools = list_tools()
print(f"  {len(tools)} tools: {', '.join(t['name'] for t in tools)}")
check("9 tools registradas", len(tools) == 9, str(len(tools)))
for tool in tools:
    ann = tool.get("annotations", {})
    check(f"{tool['name']}: schema + annotations",
          "inputSchema" in tool and "readOnlyHint" in ann and len(tool["description"]) > 80)
check("delete_items marcada como destrutiva",
      next(t for t in tools if t["name"] == "delete_items")["annotations"]["destructiveHint"] is True)
check("manage_cards NAO destrutiva",
      next(t for t in tools if t["name"] == "manage_cards")["annotations"]["destructiveHint"] is False)

# --------------------------------------------------------------------------
section("list_boards")
fake = FakeTrello()
out = run(fake, "list_boards", {})
check("so boards abertos por padrao", out["count"] == 1 and out["boards"][0]["name"] == "Projeto Alpha", str(out))

out = run(fake, "list_boards", {"filter": "all", "include": ["lists", "labels", "members"]})
check("filter=all traz os 2 boards", out["count"] == 2)
alpha = next(b for b in out["boards"] if b["name"] == "Projeto Alpha")
check("include traz listas/labels/membros",
      len(alpha["lists"]) == 3 and len(alpha["labels"]) == 2 and len(alpha["members"]) == 1)

out = run(fake, "list_boards", {"name_contains": "alpha"})
check("name_contains filtra", out["count"] == 1)

# --------------------------------------------------------------------------
section("get_board_snapshot")
fake = FakeTrello()
snap = run(fake, "get_board_snapshot", {"board": "Projeto Alpha"})
check("resolve board por nome", snap["board"]["name"] == "Projeto Alpha")
check("agrupa cards por lista", all("cards" in l for l in snap["lists"]))
todo = next(l for l in snap["lists"] if l["name"] == "A Fazer")
check("2 cards em 'A Fazer'", len(todo["cards"]) == 2, str(todo))
check("labels viram nomes", todo["cards"][0]["labels"] == ["Urgente"], str(todo["cards"][0]))
check("membros viram usernames", todo["cards"][0]["members"] == ["hermes"])
check("created_at derivado do id", todo["cards"][0].get("created_at") is not None)

snap = run(fake, "get_board_snapshot", {"board": "Projeto Alpha", "depth": "lists"})
check("depth=lists nao traz cards", "cards" not in str(snap.get("lists", [{}])[0].keys()))

snap = run(fake, "get_board_snapshot", {"board": "Projeto Alpha", "depth": "full"})
alpha_todo = next(l for l in snap["lists"] if l["name"] == "A Fazer")
card = next(c for c in alpha_todo["cards"] if c["name"] == "Corrigir login")
check("depth=full traz checklists", len(card["checklists"]) == 1 and len(card["checklists"][0]["items"]) == 2)
check("depth=full traz comentarios", len(card["comments"]) == 1 and card["comments"][0]["text"] == "Prioridade alta")

snap = run(fake, "get_board_snapshot", {"board": "Projeto Alpha", "filters": {"labels": ["Urgente"]}})
check("filtro por label (nome)", snap["cards_summary"]["matched_filters"] == 1)

snap = run(fake, "get_board_snapshot", {"board": "Projeto Alpha", "filters": {"labels": ["red"]}})
check("filtro por label (cor)", snap["cards_summary"]["matched_filters"] == 1)

snap = run(fake, "get_board_snapshot", {"board": "Projeto Alpha", "filters": {"members": ["me"]}})
check("filtro members=me", snap["cards_summary"]["matched_filters"] == 1)

snap = run(fake, "get_board_snapshot", {"board": "Projeto Alpha", "filters": {"overdue": True}})
check("filtro overdue (1 card vencido)", snap["cards_summary"]["matched_filters"] == 1, str(snap["cards_summary"]))

snap = run(fake, "get_board_snapshot", {"board": "Projeto Alpha", "filters": {"has_due": False}})
check("filtro has_due=false", snap["cards_summary"]["matched_filters"] == 1)

snap = run(fake, "get_board_snapshot", {"board": "Projeto Alpha", "filters": {"lists": ["Em Andamento"]},
                                        "group_by": "none"})
check("filtro por lista + group_by=none", len(snap["cards"]) == 1 and snap["cards"][0]["name"] == "Refatorar API")

snap = run(fake, "get_board_snapshot", {"board": "Projeto Alpha", "max_cards": 1})
check("max_cards trunca e avisa", snap["cards_summary"]["truncated"] is True and snap["cards_summary"]["returned"] == 1)

# --------------------------------------------------------------------------
section("search")
fake = FakeTrello()
out = run(fake, "search", {"query": "login"})
check("acha card por texto", out["cards"]["after_filters"] == 1, str(out["cards"]))
check("resultado traz board e lista", out["cards"]["items"][0]["board"] == "Projeto Alpha"
      and out["cards"]["items"][0]["list"] == "A Fazer")

out = run(fake, "search", {"query": "a", "scope": ["cards", "boards", "members"]})
check("scope multiplo", "boards" in out and "members" in out)

out = run(fake, "search", {"query": "a", "filters": {"has_due": True}})
check("filtros pos-busca", out["cards"]["after_filters"] < out["cards"]["found_by_query"])

err = None
try:
    run(fake, "search", {"query": "a", "filters": {"labels": ["Urgente"]}})
except Exception as exc:
    err = str(exc)
check("erro claro ao filtrar label sem board", err is not None and "board de referencia" in err, str(err))

# --------------------------------------------------------------------------
section("manage_cards")
fake = FakeTrello()
out = run(fake, "manage_cards", {"operations": [
    {"action": "create", "board": "Projeto Alpha", "list": "A Fazer", "name": "Novo card",
     "desc": "Descricao", "due": "+3d", "labels": ["Urgente"], "members": ["me"],
     "comment": "criado pela IA"},
]})
check("create com labels/membros/due/comment", out["summary"]["succeeded"] == 1, json.dumps(out, ensure_ascii=False)[:400])
created = out["results"][0]["result"]["created"]
check("card criado com label resolvida", created["labels"] == ["Urgente"], str(created))
check("comentario acompanhou o create", "comment" in out["results"][0]["result"])

out = run(fake, "manage_cards", {"operations": [
    {"action": "update", "board": "Projeto Alpha", "card": "Corrigir login",
     "labels": {"add": ["Bug"]}, "members": {"remove": ["me"]}, "name": "Corrigir login (v2)"},
]})
updated = out["results"][0]["result"]["updated"]
check("update incremental de labels", sorted(updated["labels"]) == ["Bug", "Urgente"], str(updated))
check("update remove membro", updated["members"] == [], str(updated))
check("update renomeia", updated["name"] == "Corrigir login (v2)")

out = run(fake, "manage_cards", {"operations": [
    {"action": "move", "board": "Projeto Alpha", "card": "Atualizar README",
     "list": "Em Andamento", "position": "top"},
]})
check("move troca a lista", out["results"][0]["result"]["updated"]["list"] == "Em Andamento", str(out["results"][0]))

out = run(fake, "manage_cards", {"operations": [
    {"action": "archive", "board": "Projeto Alpha", "card": "Refatorar API"},
]})
check("archive fecha o card", out["results"][0]["result"]["closed"] is True)

out = run(fake, "manage_cards", {"operations": [
    {"action": "get", "board": "Projeto Alpha", "card": "Corrigir login"},
]})
detail = out["results"][0]["result"]
check("get traz checklists", len(detail["checklists"]) == 1 and len(detail["checklists"][0]["items"]) == 2)

# batch parcial: 1 ok, 1 erro, 1 ok -- nao pode abortar
out = run(fake, "manage_cards", {"operations": [
    {"action": "create", "board": "Projeto Alpha", "list": "A Fazer", "name": "Card A"},
    {"action": "update", "board": "Projeto Alpha", "card": "NAO EXISTE", "name": "x"},
    {"action": "create", "board": "Projeto Alpha", "list": "A Fazer", "name": "Card B"},
    {"action": "acao_invalida"},
]})
check("batch continua apos erro", out["summary"] == {"total": 4, "succeeded": 2, "failed": 2}, str(out["summary"]))
check("erro reporta o indice certo", out["results"][1]["status"] == "error" and out["results"][1]["index"] == 1)
check("acao invalida lista as validas", "Disponiveis" in out["results"][3]["error"])

out = run(fake, "manage_cards", {"operations": [
    {"action": "create", "list": "A Fazer", "name": "sem board"},
]})
check("erro util quando falta board", "informe tambem 'board'" in out["results"][0]["error"], str(out["results"][0]))

# --------------------------------------------------------------------------
section("manage_checklists_and_comments")
fake = FakeTrello()
out = run(fake, "manage_checklists_and_comments", {"operations": [
    {"action": "add_checklist", "board": "Projeto Alpha", "card": "Atualizar README",
     "name": "Tarefas", "items": ["Item 1", {"name": "Item 2", "checked": True}]},
]})
check("add_checklist cria itens junto", len(out["results"][0]["result"]["items_created"]) == 2, str(out["results"][0]))

out = run(fake, "manage_checklists_and_comments", {"operations": [
    {"action": "check_items", "board": "Projeto Alpha", "card": "Corrigir login",
     "checklist": "Passos", "item_names": ["Escrever teste", "Inexistente"], "checked": True},
]})
result = out["results"][0]["result"]
check("check_items marca em lote", len(result["changed"]) == 1 and result["changed"][0]["checked"] is True)
check("check_items reporta item faltante", len(result["failed"]) == 1)

out = run(fake, "manage_checklists_and_comments", {"operations": [
    {"action": "add_comment", "board": "Projeto Alpha", "card": "Corrigir login", "text": "ok"},
    {"action": "list_checklists", "board": "Projeto Alpha", "card": "Corrigir login"},
]})
check("add_comment + list_checklists", out["summary"]["succeeded"] == 2, str(out["summary"]))

out = run(fake, "manage_checklists_and_comments", {"operations": [
    {"action": "add_items", "board": "Projeto Alpha", "card": "Corrigir login", "items": ["Novo"]},
]})
check("checklist unica e inferida", out["summary"]["succeeded"] == 1, str(out["results"][0]))

# --------------------------------------------------------------------------
section("manage_board_structure")
fake = FakeTrello()
out = run(fake, "manage_board_structure", {"operations": [
    {"action": "create_board", "name": "Board Novo",
     "lists": ["Backlog", "Sprint", "Feito"],
     "labels": ["Frontend", {"name": "Backend", "color": "blue"}]},
]})
result = out["results"][0]["result"]
check("create_board monta listas e labels",
      len(result["lists_created"]) == 3 and len(result["labels_created"]) == 2, str(result))

out = run(fake, "manage_board_structure", {"operations": [
    {"action": "create_list", "board": "Projeto Alpha", "name": "Revisao"},
    {"action": "create_label", "board": "Projeto Alpha", "name": "Tech Debt", "color": "orange"},
    {"action": "update_list", "board": "Projeto Alpha", "list": "Revisao", "name": "Em Revisao"},
    {"action": "archive_list", "board": "Projeto Alpha", "list": "Concluido"},
]})
check("estrutura em lote (4 ops)", out["summary"]["succeeded"] == 4, str(out))

out = run(fake, "manage_board_structure", {"operations": [
    {"action": "move_all_cards", "board": "Projeto Alpha", "list": "A Fazer",
     "target_list": "Em Andamento"},
]})
check("move_all_cards conta os cards", out["results"][0]["result"]["cards_moved"] == 2, str(out["results"][0]))

# --------------------------------------------------------------------------
section("manage_members")
fake = FakeTrello()
out = run(fake, "manage_members", {"operations": [
    {"action": "add_to_board", "board": "Projeto Alpha", "member": "maria", "role": "normal"},
]})
check("add_to_board", out["summary"]["succeeded"] == 1 and len(out["results"][0]["result"]["added"]) == 1, str(out))

out = run(fake, "manage_members", {"operations": [
    {"action": "assign_to_card", "board": "Projeto Alpha", "members": ["maria", "me"],
     "cards": ["Corrigir login", "Atualizar README"]},
]})
check("assign produto membros x cards (4)", len(out["results"][0]["result"]["assigned"]) == 4, str(out["results"][0]))

out = run(fake, "manage_members", {"operations": [
    {"action": "assign_to_card", "board": "Projeto Alpha", "members": ["fantasma"], "cards": ["Corrigir login"]},
]})
check("membro inexistente vira 'failed', nao excecao",
      len(out["results"][0]["result"]["failed"]) == 1 and out["results"][0]["status"] == "ok")

out = run(fake, "manage_members", {"operations": [
    {"action": "list_board_members", "board": "Projeto Alpha"},
]})
check("list_board_members", len(out["results"][0]["result"]["members"]) == 2)

# A API real devolve 400 ao reatribuir alguem que ja esta no card. Para quem
# chama o efeito desejado ja vale, entao isso conta como sucesso.
out = run(fake, "manage_members", {"operations": [
    {"action": "assign_to_card", "board": "Projeto Alpha", "members": ["me"], "cards": ["Corrigir login"]},
]})
result = out["results"][0]["result"]
check("reatribuir membro ja atribuido e no-op bem sucedido",
      len(result["assigned"]) == 1 and result["assigned"][0].get("already_applied") is True
      and not result["failed"], str(result))

out = run(fake, "manage_members", {"operations": [
    {"action": "unassign_from_card", "board": "Projeto Alpha", "members": ["maria"], "cards": ["Corrigir login"]},
]})
result = out["results"][0]["result"]
check("desatribuir quem nao esta no card e no-op bem sucedido",
      len(result["unassigned"]) == 1 and not result["failed"], str(result))

# --------------------------------------------------------------------------
section("get_activity")
fake = FakeTrello()
out = run(fake, "get_activity", {"board": "Projeto Alpha"})
check("historico do board", out["count"] == 2, str(out["count"]))
summaries = [a["summary"] for a in out["actions"]]
check("resumo legivel de comentario",
      any("comentou em 'Corrigir login': Prioridade alta" in s for s in summaries), str(summaries))
check("resumo legivel de criacao",
      any("criou o card 'Atualizar README'" in s for s in summaries), str(summaries))

out = run(fake, "get_activity", {"board": "Projeto Alpha", "action_types": ["commentCard"],
                                 "since": "-30d"})
check("filtro por tipo e periodo passa params", out["period"]["since"] is not None)

err = None
try:
    run(fake, "get_activity", {"board": "Projeto Alpha", "card": "Corrigir login"})
except Exception as exc:
    err = str(exc)
check("exige exatamente um alvo", err is not None and "exatamente um alvo" in err)

# --------------------------------------------------------------------------
section("delete_items")
fake = FakeTrello()
before = len(fake.cards)
out = run(fake, "delete_items", {"dry_run": True, "operations": [
    {"action": "delete_card", "board": "Projeto Alpha", "card": "Atualizar README"},
    {"action": "delete_board", "board": "Projeto Alpha"},
]})
check("dry_run nao apaga nada", len(fake.cards) == before and out["dry_run"] is True)
check("dry_run descreve o alvo", out["results"][0]["result"]["would_delete"]["name"] == "Atualizar README")
check("dry_run avisa impacto do board",
      out["results"][1]["result"]["would_delete"]["cards_affected"] == 3, str(out["results"][1]))

out = run(fake, "delete_items", {"operations": [
    {"action": "delete_card", "board": "Projeto Alpha", "card": "Atualizar README"},
]})
check("delete real remove o card", len(fake.cards) == before - 1 and "deleted" in out["results"][0]["result"])

out = run(fake, "delete_items", {"operations": [
    {"action": "delete_label", "board": "Projeto Alpha", "label": "Bug"},
    {"action": "delete_checkitem", "board": "Projeto Alpha", "card": "Corrigir login",
     "checklist": "Passos", "item": "Escrever teste"},
]})
check("delete label + checkitem", out["summary"]["succeeded"] == 2, str(out))

# --------------------------------------------------------------------------
section("Datas relativas")
fake = FakeTrello()
out = run(fake, "manage_cards", {"operations": [
    {"action": "create", "board": "Projeto Alpha", "list": "A Fazer", "name": "D1", "due": "tomorrow"},
    {"action": "create", "board": "Projeto Alpha", "list": "A Fazer", "name": "D2", "due": "+2w"},
    {"action": "create", "board": "Projeto Alpha", "list": "A Fazer", "name": "D3", "due": "2026-12-25"},
    {"action": "create", "board": "Projeto Alpha", "list": "A Fazer", "name": "D4", "due": "amanha??"},
]})
check("datas relativas aceitas", out["summary"]["succeeded"] == 3 and out["summary"]["failed"] == 1, str(out["summary"]))
check("data invalida gera erro explicativo", "Nao entendi o valor de due" in out["results"][3]["error"])

out = run(fake, "manage_cards", {"operations": [
    {"action": "update", "board": "Projeto Alpha", "card": "Corrigir login", "due": ""},
]})
check("due='' limpa o campo", out["summary"]["succeeded"] == 1)

# --------------------------------------------------------------------------
print("\n" + "=" * 60)
print(f"PASSOU: {len(PASS)}   FALHOU: {len(FAIL)}")
if FAIL:
    print("Falhas:")
    for name in FAIL:
        print(f"  - {name}")
sys.exit(1 if FAIL else 0)
