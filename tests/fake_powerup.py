"""Fake em memoria da API do Power-Up de checklists (projeto trello-power).

Replica so o que a tool usa, com as mesmas regras que importam: validacao de
datas, inicio <= fim e erros no formato {"erro": "..."}.
"""
import itertools
import re
from datetime import date, timedelta

from trello_mcp.tools.common import ToolError

_ids = itertools.count(1)


def _new_id():
    return f"{next(_ids):032x}"


class FakePowerUp:
    def __init__(self):
        self.calls = []
        self.checklists = {}  # id -> {id, boardId, cardId, nome}
        self.items = {}       # id -> item
        self.modelos = {}     # id -> {id, boardId, nome, itens}
        self.links = {}       # item_id -> [{id, tipo, defasagem}]
        self.history = {}     # item_id -> [ocorrencias]

    # Mesma assinatura do PowerUpClient.
    def request(self, method, path, body=None):
        self.calls.append((method, path, body))
        for pattern, handler in self._routes():
            match = re.fullmatch(pattern, path)
            if match and handler[0] == method:
                return handler[1](*match.groups(), body or {})
        raise AssertionError(f"Rota nao simulada: {method} {path}")

    def _routes(self):
        return [
            (r"/cards/(\w+)/checklists", ("GET", self._list)),
            (r"/cards/(\w+)/checklists", ("POST", self._create_checklist)),
            (r"/checklists/(\w+)", ("PATCH", self._rename)),
            (r"/checklists/(\w+)", ("DELETE", self._delete_checklist)),
            (r"/checklists/(\w+)/items", ("POST", self._create_item)),
            (r"/items/(\w+)", ("PATCH", self._update_item)),
            (r"/items/(\w+)/predecessoras", ("PUT", self._set_links)),
            (r"/items/(\w+)/ocorrencias", ("GET", self._history)),
            (r"/items/(\w+)", ("DELETE", self._delete_item)),
            (r"/boards/(\w+)/modelos", ("GET", self._list_modelos)),
            (r"/boards/(\w+)/modelos", ("POST", self._create_modelo)),
            (r"/modelos/(\w+)", ("PATCH", self._update_modelo)),
            (r"/modelos/(\w+)", ("DELETE", self._delete_modelo)),
        ]

    @staticmethod
    def _fail(message):
        raise ToolError(f"Power-Up de checklists recusou: {message}")

    def _board_of(self, item):
        return self.checklists[item["checklistId"]]["boardId"]

    def _view(self, item):
        preds = []
        for link in self.links.get(item["id"], []):
            pred = self.items[link["id"]]
            preds.append({**link, "texto": pred["texto"], "cardId": pred["cardId"], "boardId": self._board_of(pred),
                          "checklistNome": self.checklists[pred["checklistId"]]["nome"],
                          "dataInicio": pred.get("dataInicio"), "dataFim": pred.get("dataFim"),
                          "feito": pred["feito"]})
        return {**item, "predecessoras": preds}

    def _checklist_view(self, checklist):
        itens = [self._view(i) for i in self.items.values() if i["checklistId"] == checklist["id"]]
        return {**checklist, "itens": itens}

    def _set_links(self, item_id, body):
        item = self.items[item_id]
        links = body.get("predecessoras", [])
        for link in links:
            pred = self.items.get(link["id"])
            # Como a chave de servico: qualquer board (o MCP ja resolveu o card com o token do usuario).
            if link["id"] == item_id or not pred:
                self._fail("Predecessora não encontrada.")
        graph = {k: [l["id"] for l in v] for k, v in self.links.items() if k != item_id}
        stack, seen = [l["id"] for l in links], set()
        while stack:
            node = stack.pop()
            if node == item_id:
                self._fail("Essa predecessora cria um ciclo: ela já depende deste item.")
            if node not in seen:
                seen.add(node)
                stack.extend(graph.get(node, []))
        self.links[item_id] = [{"id": l["id"], "tipo": l.get("tipo", "TI"), "defasagem": l.get("defasagem", 0)}
                               for l in links]
        return self._view(item)

    def _check_dates(self, item):
        for key in ("dataInicio", "dataFim"):
            value = item.get(key)
            if value is not None and not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
                self._fail("Data inválida. Use AAAA-MM-DD.")
        if item.get("dataInicio") and item.get("dataFim") and item["dataInicio"] > item["dataFim"]:
            self._fail("A data de início não pode ser depois da data de fim.")

    STEP = {"diaria": 1, "dias_uteis": 1, "semanal": 7, "mensal": 30, "anual": 365}
    NAMES = {"diaria": "Todo dia", "dias_uteis": "Dias úteis", "semanal": "Toda semana",
             "mensal": "Todo mês", "anual": "Todo ano"}

    def _recorrencia(self, item, value):
        """Valida como o Power-Up (simplificado: mes = 30 dias, ano = 365)."""
        if not value:
            return None
        if value.get("frequencia") not in self.STEP or not 1 <= value.get("intervalo", 1) <= 99:
            self._fail("Recorrência inválida.")
        if not (item.get("dataInicio") or item.get("dataFim")):
            self._fail("Para repetir, o item precisa de uma data de início ou de fim.")
        n = value.get("intervalo", 1)
        name = self.NAMES[value["frequencia"]] if n == 1 else f"A cada {n} ({value['frequencia']})"
        return {"frequencia": value["frequencia"], "intervalo": n, "descricao": name}

    def _history(self, item_id, _body):
        return {"ocorrencias": list(reversed(self.history.get(item_id, [])))}

    def _new_item(self, checklist, body):
        item = {
            "id": _new_id(), "checklistId": checklist["id"], "cardId": checklist["cardId"],
            "texto": body["texto"], "dataInicio": body.get("dataInicio"),
            "dataFim": body.get("dataFim"), "responsavelId": body.get("responsavelId"),
            "feito": False,
        }
        self._check_dates(item)
        item["recorrencia"] = self._recorrencia(item, body.get("recorrencia"))
        self.items[item["id"]] = item
        return item

    def _list(self, card_id, _body):
        lists = [c for c in self.checklists.values() if c["cardId"] == card_id]
        return {"checklists": [self._checklist_view(c) for c in lists]}

    def _create_checklist(self, card_id, body):
        if not body.get("boardId"):
            self._fail("Quadro inválido.")
        checklist = {"id": _new_id(), "boardId": body["boardId"], "cardId": card_id,
                     "nome": body.get("nome", "Checklist")}
        if "modeloId" in body:
            modelo = self.modelos.get(body["modeloId"])
            if not modelo or modelo["boardId"] != body["boardId"]:
                self._fail("Padrão não encontrado.")
            checklist["nome"] = body.get("nome", modelo["nome"])
            itens = [{"texto": t} for t in modelo["itens"]]
        else:
            itens = body.get("itens", [])
        self.checklists[checklist["id"]] = checklist
        for raw in itens:
            self._new_item(checklist, raw)
        return self._checklist_view(checklist)

    def _rename(self, checklist_id, body):
        self.checklists[checklist_id]["nome"] = body["nome"]
        return self._checklist_view(self.checklists[checklist_id])

    def _delete_checklist(self, checklist_id, _body):
        self.checklists.pop(checklist_id)
        for item_id in [i for i, v in self.items.items() if v["checklistId"] == checklist_id]:
            self.items.pop(item_id)

    def _create_item(self, checklist_id, body):
        return self._view(self._new_item(self.checklists[checklist_id], body))

    def _update_item(self, item_id, body):
        current = self.items[item_id]
        updated = {**current, **body}
        self._check_dates(updated)
        rec = body["recorrencia"] if "recorrencia" in body else current.get("recorrencia")
        updated["recorrencia"] = self._recorrencia(updated, rec)
        done = None
        if body.get("feito") and updated["recorrencia"] and not current["feito"]:
            step = timedelta(days=self.STEP[rec["frequencia"]] * updated["recorrencia"]["intervalo"])
            done = {"dataInicio": updated.get("dataInicio"), "dataFim": updated.get("dataFim"),
                    "concluidoEm": "2026-11-20T12:00:00+00:00", "concluidoPor": "api"}
            self.history.setdefault(item_id, []).append(done)
            for key in ("dataInicio", "dataFim"):
                if updated.get(key):
                    updated[key] = (date.fromisoformat(updated[key]) + step).isoformat()
            updated["feito"] = False
        self.items[item_id] = updated
        view = self._view(updated)
        if done:
            view["ocorrenciaConcluida"] = done
        return view

    def _delete_item(self, item_id, _body):
        self.items.pop(item_id)

    def _list_modelos(self, board_id, _body):
        return {"modelos": [m for m in self.modelos.values() if m["boardId"] == board_id]}

    def _create_modelo(self, board_id, body):
        modelo = {"id": _new_id(), "boardId": board_id, "nome": body["nome"], "itens": body["itens"]}
        self.modelos[modelo["id"]] = modelo
        return modelo

    def _update_modelo(self, modelo_id, body):
        modelo = self.modelos[modelo_id]
        modelo.update({k: v for k, v in (("nome", body.get("nome")), ("itens", body.get("itens"))) if v is not None})
        return modelo

    def _delete_modelo(self, modelo_id, _body):
        self.modelos.pop(modelo_id)
