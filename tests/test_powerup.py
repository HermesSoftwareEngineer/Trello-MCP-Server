"""Exercita manage_powerup_checklists contra fakes do Trello e do Power-Up."""
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

from fake_powerup import FakePowerUp
from fake_trello import FakeTrello
from trello_mcp import powerup_client
from trello_mcp.config import Config
from trello_mcp.tools import call_tool

PASS, FAIL = [], []
TOOL = "manage_powerup_checklists"


def check(label, condition, detail=""):
    (PASS if condition else FAIL).append(label)
    mark = "OK  " if condition else "FALHOU"
    print(f"  [{mark}] {label}" + (f"  -> {detail}" if detail and not condition else ""))


def section(title):
    print(f"\n=== {title} ===")


def setup():
    trello, powerup = FakeTrello(), FakePowerUp()
    powerup_client.get_client = lambda: powerup
    return trello, powerup


def run(trello, *operations):
    return call_tool(trello, TOOL, {"operations": list(operations)})


def ok(out, index=0):
    entry = out["results"][index]
    return entry["status"] == "ok", entry


CARD = {"board": "Projeto Alpha", "card": "Corrigir login"}

# --------------------------------------------------------------------------
section("Configuracao")
original_get_client = powerup_client.get_client
saved = (Config.POWERUP_API_URL, Config.POWERUP_API_KEY)
Config.POWERUP_API_URL, Config.POWERUP_API_KEY = "", ""
out = call_tool(FakeTrello(), TOOL, {"operations": [{"action": "list", **CARD}]})
check("sem configuracao: erro claro",
      out["results"][0]["status"] == "error" and "nao esta configurado" in out["results"][0]["error"],
      str(out["results"][0]))
Config.POWERUP_API_URL, Config.POWERUP_API_KEY = saved

# --------------------------------------------------------------------------
section("Checklists e itens")
trello, powerup = setup()
maria = trello.other
board_id = trello.board_alpha["id"]
trello.board_members[board_id].append(maria["id"])

out = run(trello, {
    "action": "add_checklist", **CARD, "name": "Compras",
    "items": ["Orcar", {"text": "Comprar", "start": "2026-11-10", "end": "2026-11-20",
                        "member": "maria"}, {"text": "Feito ja", "checked": True}],
})
good, entry = ok(out)
check("add_checklist com itens", good, str(entry))
checklist = entry["result"]["checklist"]
check("nome e progresso", checklist["name"] == "Compras" and checklist["progress"] == "1/3", str(checklist))
comprar = checklist["items"][1]
check("datas e responsavel por nome",
      comprar["start"] == "2026-11-10" and comprar["end"] == "2026-11-20" and comprar["member"] == "maria",
      str(comprar))
board_sent = next(b for m, p, b in powerup.calls if m == "POST" and p.endswith("/checklists"))
check("boardId vem do card resolvido pelo token do usuario", board_sent["boardId"] == board_id, str(board_sent))

out = run(trello, {"action": "add_checklist", **CARD, "name": "Obra"},
          {"action": "add_items", **CARD, "items": ["Sem checklist"]})
check("add_items exige checklist quando ha varias",
      out["results"][1]["status"] == "error" and "varias checklists" in out["results"][1]["error"])

out = run(trello, {"action": "add_items", **CARD, "checklist": "obra",
                   "items": [{"text": "Pintar", "end": "+2d", "member": "me"}]})
good, entry = ok(out)
pintar = entry["result"]["items_created"][0] if good else {}
check("add_items por nome parcial, offset de data e 'me'",
      good and len(pintar.get("end") or "") == 10 and pintar.get("member") == "hermes", str(entry))

out = run(trello, {"action": "update_item", **CARD, "item": "Comprar", "start": "", "checked": True,
                   "text": "Comprar cadeiras"})
good, entry = ok(out)
check("update_item limpa inicio, marca e renomeia",
      good and entry["result"]["item"]["start"] is None and entry["result"]["item"]["checked"]
      and entry["result"]["item"]["text"] == "Comprar cadeiras", str(entry))

out = run(trello, {"action": "update_item", **CARD, "item": "Comprar cadeiras", "start": "2026-12-30"})
check("inicio depois do fim: erro do Power-Up repassado",
      out["results"][0]["status"] == "error" and "início" in out["results"][0]["error"],
      str(out["results"][0]))

out = run(trello, {"action": "update_item", **CARD, "item": "Comprar cadeiras", "member": ""})
check("member '' remove responsavel", ok(out)[0] and ok(out)[1]["result"]["item"]["member"] is None)

out = run(trello, {"action": "check_items", **CARD, "item_names": ["Orcar", "Inexistente"]})
good, entry = ok(out)
check("check_items com resultado por item",
      good and len(entry["result"]["changed"]) == 1 and len(entry["result"]["failed"]) == 1, str(entry))

out = run(trello, {"action": "update_checklist", **CARD, "checklist": "Obra", "name": "Obra final"})
check("update_checklist renomeia", ok(out)[0] and ok(out)[1]["result"]["checklist"]["name"] == "Obra final")

out = run(trello, {"action": "list", **CARD})
good, entry = ok(out)
names = [c["name"] for c in entry["result"]["checklists"]] if good else []
check("list mostra as checklists do card", names == ["Compras", "Obra final"], str(names))

out = run(trello, {"action": "delete_item", **CARD, "item": "Pintar"},
          {"action": "delete_checklist", **CARD})
check("delete_item apaga", out["results"][0]["status"] == "ok")
check("delete_checklist exige checklist explicita", out["results"][1]["status"] == "error")
out = run(trello, {"action": "delete_checklist", **CARD, "checklist": "Obra final"})
check("delete_checklist apaga", ok(out)[0] and len(powerup.checklists) == 1, str(out["results"][0]))

out = run(trello, {"action": "add_checklist", **CARD, "items": [{"text": "x", "end": "amanha talvez"}]})
check("data invalida nao cria checklist pela metade",
      out["results"][0]["status"] == "error" and len(powerup.checklists) == 1, str(out["results"][0]))

# --------------------------------------------------------------------------
section("Itens so dentro do card autorizado")
trello, powerup = setup()
foreign = powerup.request("POST", "/cards/ffffffffffffffffffffffff/checklists",
                          {"boardId": "f" * 24, "nome": "Alheia", "itens": [{"texto": "Segredo"}]})
secret_id = foreign["itens"][0]["id"]
out = run(trello, {"action": "update_item", **CARD, "item": secret_id, "checked": True},
          {"action": "delete_checklist", **CARD, "checklist": foreign["id"]})
check("id de item de outro card nao e alcancado", out["results"][0]["status"] == "error")
check("id de checklist de outro card nao e alcancado", out["results"][1]["status"] == "error")
check("item alheio intacto", powerup.items[secret_id]["feito"] is False)

# --------------------------------------------------------------------------
section("Padroes do board")
trello, powerup = setup()
out = run(trello,
          {"action": "create_template", "board": "Projeto Alpha", "name": "Entrega",
           "items": ["Conferir", "Assinar"]},
          {"action": "list_templates", "board": "Projeto Alpha"})
check("create_template e list_templates",
      ok(out)[0] and ok(out, 1)[1]["result"]["templates"][0]["items"] == ["Conferir", "Assinar"],
      str(out["results"]))

out = run(trello, {"action": "apply_template", **CARD, "template": "entrega"})
good, entry = ok(out)
check("apply_template cria checklist com os itens",
      good and [i["text"] for i in entry["result"]["checklist"]["items"]] == ["Conferir", "Assinar"],
      str(entry))

out = run(trello, {"action": "update_template", "card": trello.card1["id"], "template": "Entrega",
                   "items": ["So um"]})
check("update_template resolvendo o board pelo card",
      ok(out)[0] and ok(out)[1]["result"]["template"]["items"] == ["So um"], str(out["results"][0]))

other_board_template = powerup.request("POST", "/boards/" + "f" * 24 + "/modelos",
                                       {"nome": "Alheio", "itens": ["x"]})
out = run(trello, {"action": "apply_template", **CARD, "template": other_board_template["id"]},
          {"action": "delete_template", "board": "Projeto Alpha", "template": other_board_template["id"]})
check("padrao de outro board nao e aplicado", out["results"][0]["status"] == "error")
check("padrao de outro board nao e apagado",
      out["results"][1]["status"] == "error" and other_board_template["id"] in powerup.modelos)

out = run(trello, {"action": "delete_template", "board": "Projeto Alpha", "template": "Entrega"})
check("delete_template apaga", ok(out)[0])

out = run(trello, {"action": "list_templates"})
check("padroes exigem board ou card", out["results"][0]["status"] == "error")

powerup_client.get_client = original_get_client

print(f"\n{len(PASS)} ok, {len(FAIL)} falhas")
if FAIL:
    print("FALHAS: " + "; ".join(FAIL))
    sys.exit(1)
