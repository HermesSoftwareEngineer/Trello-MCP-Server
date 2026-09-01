"""Valida as tools contra a API REAL do Trello.

Opcional: so roda se TRELLO_API_KEY e TRELLO_TOKEN estiverem no ambiente.
As demais suites usam um fake e nao precisam de credenciais.

    set TRELLO_API_KEY=...
    set TRELLO_TOKEN=...
    .venv\\Scripts\\python.exe tests/test_real_api.py

A parte de leitura toca apenas um board existente, sem alterar nada.
A parte de escrita cria um board descartavel ("ZZ TESTE MCP (apagar)"),
exercita tudo dentro dele e o apaga no final -- nenhum board existente
e modificado. Se a limpeza falhar, o id do board e impresso para remocao
manual.
"""

import os
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from trello_mcp.tools import call_tool
from trello_mcp.trello_client import TrelloClient

KEY = os.getenv("TRELLO_API_KEY", "")
TOKEN = os.getenv("TRELLO_TOKEN", "")

if not KEY or not TOKEN:
    print("PULADO: defina TRELLO_API_KEY e TRELLO_TOKEN para rodar contra a API real.")
    sys.exit(0)

BOARD_NAME = "ZZ TESTE MCP (apagar)"
client = TrelloClient(KEY, TOKEN)
OK, BAD = [], []


def call(tool, args):
    return call_tool(client, tool, args)


def check(label, cond, detail=""):
    (OK if cond else BAD).append(label if cond else f"{label}: {detail}")
    print(f"  [{'OK  ' if cond else 'FALHA'}] {label}" + (f"\n         {detail}" if detail and not cond else ""))


def probe(label, fn):
    try:
        result = fn()
        OK.append(label)
        print(f"  [OK  ] {label}")
        return result
    except Exception as exc:
        BAD.append(f"{label}: {type(exc).__name__}: {exc}")
        print(f"  [FALHA] {label}\n         {type(exc).__name__}: {exc}")
        return None


def batch(label, tool, operations):
    out = call(tool, {"operations": operations})
    failed = [r for r in out["results"] if r["status"] != "ok"]
    check(label + f"  ({out['summary']['succeeded']}/{out['summary']['total']})", not failed,
          "; ".join(f"op[{e['index']}] {e.get('action')}: {e.get('error')}" for e in failed))
    return out


# ==========================================================================
# LEITURA (nao altera nada)
# ==========================================================================
print("### LEITURA ###\n")
print("=== list_boards ===")
boards = probe("list_boards", lambda: call("list_boards", {"limit": 50, "include": ["lists"]}))
target = next((b for b in (boards or {}).get("boards", []) if b.get("lists")), None)
if target is None:
    print("Nenhum board com listas na conta -- pulando a parte de leitura.")
else:
    print(f">>> board de leitura: '{target['name']}'\n")
    print("=== get_board_snapshot ===")
    probe("depth=lists", lambda: call("get_board_snapshot", {"board": target["id"], "depth": "lists"}))
    snap = probe("depth=cards", lambda: call("get_board_snapshot", {"board": target["id"]}))
    probe("depth=full", lambda: call("get_board_snapshot",
                                     {"board": target["id"], "depth": "full", "max_cards": 20}))
    probe("resolve board por nome",
          lambda: call("get_board_snapshot", {"board": target["name"], "depth": "lists"}))
    probe("filtro members=me",
          lambda: call("get_board_snapshot", {"board": target["id"], "filters": {"members": ["me"]}}))
    probe("filtro overdue",
          lambda: call("get_board_snapshot", {"board": target["id"], "filters": {"overdue": True}}))
    probe("filtro updated_after=-30d",
          lambda: call("get_board_snapshot", {"board": target["id"],
                                              "filters": {"updated_after": "-30d"}}))
    probe("card_status=all",
          lambda: call("get_board_snapshot", {"board": target["id"], "card_status": "all",
                                              "max_cards": 5}))

    print("\n=== search ===")
    # A busca do Trello ignora queries de 1 caractere -- use termos reais.
    probe("search @me", lambda: call("search", {"query": "@me", "limit": 10}))
    probe("search due:week", lambda: call("search", {"query": "due:week", "limit": 10}))
    probe("search escopo multiplo",
          lambda: call("search", {"query": "a b", "scope": ["cards", "boards", "members"], "limit": 5}))
    probe("search restrito a board",
          lambda: call("search", {"query": "e", "boards": [target["id"]], "limit": 10}))

    print("\n=== get_activity ===")
    probe("activity do board", lambda: call("get_activity", {"board": target["id"], "limit": 10}))
    probe("activity filtrada", lambda: call("get_activity", {"board": target["id"],
                                                             "action_types": ["commentCard"],
                                                             "since": "-30d", "limit": 5}))
    probe("activity de membro", lambda: call("get_activity", {"member": "me", "limit": 5}))

# ==========================================================================
# ESCRITA (board descartavel)
# ==========================================================================
print("\n### ESCRITA (board temporario) ###\n")
board_id = None
try:
    print("=== manage_board_structure ===")
    out = batch("create_board com listas e labels", "manage_board_structure", [
        {"action": "create_board", "name": BOARD_NAME, "desc": "Board temporario de validacao",
         "lists": ["Backlog", "Fazendo", "Feito"],
         "labels": ["Urgente", {"name": "Bug", "color": "purple"}]},
    ])
    result = out["results"][0].get("result", {})
    board_id = result.get("board", {}).get("id")
    check("board criado", bool(board_id), str(result)[:200])
    check("3 listas + 2 labels",
          len(result.get("lists_created", [])) == 3 and len(result.get("labels_created", [])) == 2)
    if not board_id:
        raise SystemExit("sem board de teste, abortando escrita")

    print("\n=== manage_cards ===")
    out = batch("create (labels, membros, due, comentario)", "manage_cards", [
        {"action": "create", "board": board_id, "list": "Backlog", "name": "Card Alpha",
         "desc": "desc", "due": "+3d", "labels": ["Urgente"], "members": ["me"],
         "comment": "comentario na criacao"},
        {"action": "create", "board": board_id, "list": "Backlog", "name": "Card Beta"},
        {"action": "create", "board": board_id, "list": "Fazendo", "name": "Card Gamma",
         "labels": ["Bug"], "due": "tomorrow"},
    ])
    alpha = out["results"][0]["result"]["created"]
    alpha_id = alpha["id"]
    check("labels por nome", alpha["labels"] == ["Urgente"], str(alpha.get("labels")))
    check("membro 'me'", len(alpha["members"]) == 1)
    check("due relativo", alpha["due"] is not None)

    out = batch("update incremental de label", "manage_cards", [
        {"action": "update", "board": board_id, "card": "Card Alpha",
         "labels": {"add": ["Bug"]}, "name": "Card Alpha v2"},
    ])
    check("label somada a existente",
          sorted(out["results"][0]["result"]["updated"]["labels"]) == ["Bug", "Urgente"])

    out = batch("move de lista", "manage_cards", [
        {"action": "move", "board": board_id, "card": "Card Beta", "list": "Fazendo",
         "position": "top"},
    ])
    check("lista alterada", out["results"][0]["result"]["updated"]["list"] == "Fazendo")

    out = batch("duplicate", "manage_cards", [
        {"action": "duplicate", "board": board_id, "card": alpha_id, "list": "Feito"},
    ])
    dup_id = out["results"][0].get("result", {}).get("created", {}).get("id")
    check("card duplicado", bool(dup_id) and dup_id != alpha_id)

    batch("archive + unarchive", "manage_cards", [
        {"action": "archive", "board": board_id, "card": "Card Gamma"},
        {"action": "unarchive", "board": board_id, "card": "Card Gamma"},
    ])

    out = batch("due='' limpa", "manage_cards", [
        {"action": "update", "board": board_id, "card": alpha_id, "due": ""},
    ])
    check("due limpo", out["results"][0]["result"]["updated"].get("due") is None)

    print("\n=== manage_checklists_and_comments ===")
    out = batch("add_checklist com itens", "manage_checklists_and_comments", [
        {"action": "add_checklist", "board": board_id, "card": alpha_id, "name": "Passos",
         "items": ["Passo um", {"name": "Passo dois", "checked": True},
                   {"name": "Passo tres", "due": "+2d"}]},
    ])
    check("3 itens criados", len(out["results"][0]["result"].get("items_created", [])) == 3)

    batch("add_items", "manage_checklists_and_comments", [
        {"action": "add_items", "board": board_id, "card": alpha_id, "checklist": "Passos",
         "items": ["Passo quatro"]},
    ])
    out = batch("update_item", "manage_checklists_and_comments", [
        {"action": "update_item", "board": board_id, "card": alpha_id, "checklist": "Passos",
         "item": "Passo um", "name": "Passo um (revisado)", "checked": True},
    ])
    item = out["results"][0].get("result", {}).get("item", {})
    check("item renomeado e marcado", item.get("checked") is True and "revisado" in (item.get("name") or ""))

    out = batch("check_items em lote", "manage_checklists_and_comments", [
        {"action": "check_items", "board": board_id, "card": alpha_id, "checklist": "Passos",
         "item_names": ["Passo tres", "Passo quatro"], "checked": True},
    ])
    check("2 itens marcados", len(out["results"][0]["result"]["changed"]) == 2)

    out = batch("add_comment", "manage_checklists_and_comments", [
        {"action": "add_comment", "board": board_id, "card": alpha_id, "text": "primeiro comentario"},
    ])
    comment_id = out["results"][0]["result"]["comment"]["id"]
    batch("update_comment", "manage_checklists_and_comments", [
        {"action": "update_comment", "board": board_id, "card": alpha_id,
         "comment_id": comment_id, "text": "comentario editado"},
    ])
    out = batch("list_checklists", "manage_checklists_and_comments", [
        {"action": "list_checklists", "board": board_id, "card": alpha_id},
    ])
    items = out["results"][0]["result"]["checklists"][0]["items"]
    check("estado dos itens persistiu", sum(1 for i in items if i["checked"]) == 4,
          f"{sum(1 for i in items if i['checked'])} marcados de {len(items)}")

    print("\n=== manage_members ===")
    batch("list_board_members", "manage_members", [
        {"action": "list_board_members", "board": board_id}])
    out = batch("assign membros x cards", "manage_members", [
        {"action": "assign_to_card", "board": board_id, "members": ["me"],
         "cards": [alpha_id, "Card Beta"]}])
    check("2 atribuicoes", len(out["results"][0]["result"]["assigned"]) == 2)
    batch("unassign", "manage_members", [
        {"action": "unassign_from_card", "board": board_id, "members": ["me"], "cards": [alpha_id]}])
    batch("set_board_role", "manage_members", [
        {"action": "set_board_role", "board": board_id, "member": "me", "role": "admin"}])

    print("\n=== estrutura: listas e labels ===")
    batch("create/update lista e label", "manage_board_structure", [
        {"action": "create_list", "board": board_id, "name": "Revisao"},
        {"action": "update_list", "board": board_id, "list": "Revisao", "name": "Em Revisao"},
        {"action": "create_label", "board": board_id, "name": "Tech Debt", "color": "orange"},
        {"action": "update_label", "board": board_id, "label": "Tech Debt", "color": "sky"},
        {"action": "update_board", "board": board_id, "desc": "descricao atualizada"},
    ])
    out = batch("move_all_cards", "manage_board_structure", [
        {"action": "move_all_cards", "board": board_id, "list": "Fazendo",
         "target_list": "Em Revisao"}])
    check("move_all_cards conta cards", out["results"][0].get("result", {}).get("cards_moved", 0) >= 2)
    batch("archive_all_cards + archive_list", "manage_board_structure", [
        {"action": "archive_all_cards", "board": board_id, "list": "Em Revisao"},
        {"action": "archive_list", "board": board_id, "list": "Em Revisao"},
    ])

    print("\n=== delete_items ===")
    out = call("delete_items", {"dry_run": True, "operations": [
        {"action": "delete_card", "board": board_id, "card": alpha_id}]})
    check("dry_run nao apaga", out["dry_run"] is True
          and out["results"][0]["result"].get("would_delete") is not None)
    check("card sobreviveu ao dry_run",
          client.request("GET", f"/cards/{alpha_id}", params={"fields": "id"}).get("id") == alpha_id)

    batch("delete comentario, checkitem, checklist", "delete_items", [
        {"action": "delete_comment", "board": board_id, "card": alpha_id, "comment_id": comment_id},
        {"action": "delete_checkitem", "board": board_id, "card": alpha_id,
         "checklist": "Passos", "item": "Passo dois"},
        {"action": "delete_checklist", "board": board_id, "card": alpha_id, "checklist": "Passos"},
    ])
    batch("delete label e card", "delete_items", [
        {"action": "delete_label", "board": board_id, "label": "Tech Debt"},
        {"action": "delete_card", "board": board_id, "card": dup_id},
    ])

except Exception as exc:
    import traceback
    traceback.print_exc()
    BAD.append(f"excecao: {exc}")

finally:
    print("\n=== limpeza ===")
    if board_id:
        try:
            out = call("delete_items", {"operations": [
                {"action": "delete_board", "board": board_id}]})
            if out["results"][0]["status"] == "ok":
                OK.append("delete_board")
                print("  [OK  ] board de teste apagado")
            else:
                BAD.append(f"delete_board: {out['results'][0].get('error')}")
                print(f"  [FALHA] limpeza -- APAGUE MANUALMENTE O BOARD {board_id}")
        except Exception as exc:
            BAD.append(f"limpeza: {exc}")
            print(f"  [FALHA] limpeza ({exc}) -- APAGUE MANUALMENTE O BOARD {board_id}")

    print("\n" + "=" * 62)
    print(f"OK: {len(OK)}   FALHAS: {len(BAD)}")
    for item in BAD:
        print(f"  - {item}")

sys.exit(1 if BAD else 0)
