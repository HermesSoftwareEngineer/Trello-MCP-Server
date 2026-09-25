"""Fake stateful da API do Trello, suficiente para exercitar as tools.

Comportamentos aqui foram conferidos contra a API real -- inclusive os erros
400 de atribuicao duplicada de membro.
"""
import itertools
import pathlib
import re
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from trello_mcp.trello_client import TrelloApiError

_counter = itertools.count(1)


def new_id(prefix="0"):
    return f"{prefix}{next(_counter):023d}"[:24]


class FakeTrello:
    def __init__(self):
        self.calls = []
        self.me = {"id": new_id("a"), "username": "hermes", "fullName": "Hermes B"}
        self.members = {self.me["id"]: self.me}

        other = {"id": new_id("a"), "username": "maria", "fullName": "Maria Silva"}
        self.members[other["id"]] = other
        self.other = other

        self.boards = {}
        self.lists = {}
        self.labels = {}
        self.cards = {}
        self.checklists = {}
        self.checkitems = {}
        self.actions = {}
        self.board_members = {}

        self._seed()

    # ---------------- seed ----------------
    def _seed(self):
        b = self._add_board("Projeto Alpha")
        self.board_alpha = b
        self._add_board("Arquivo Antigo", closed=True)

        todo = self._add_list(b, "A Fazer")
        doing = self._add_list(b, "Em Andamento")
        self._add_list(b, "Concluido")
        self.list_todo, self.list_doing = todo, doing

        urgente = self._add_label(b, "Urgente", "red")
        bug = self._add_label(b, "Bug", "purple")
        self.label_urgente, self.label_bug = urgente, bug

        c1 = self._add_card(todo, "Corrigir login", desc="Erro 500 no login",
                            labels=[urgente], members=[self.me["id"]],
                            due="2026-09-10T12:00:00.000Z")
        c2 = self._add_card(todo, "Atualizar README")
        c3 = self._add_card(doing, "Refatorar API", labels=[bug],
                            due="2026-08-01T12:00:00.000Z")
        self.card1, self.card2, self.card3 = c1, c2, c3

        cl = self._add_checklist(c1, "Passos")
        self._add_checkitem(cl, "Reproduzir bug", state="complete")
        self._add_checkitem(cl, "Escrever teste")
        self.checklist1 = cl

        self._add_action(b, "commentCard", {"card": {"id": c1["id"], "name": c1["name"]},
                                            "text": "Prioridade alta"})
        self._add_action(b, "createCard", {"card": {"id": c2["id"], "name": c2["name"]},
                                           "list": {"name": "A Fazer"}})

    def _add_board(self, name, closed=False):
        board = {"id": new_id("b"), "name": name, "desc": "", "closed": closed,
                 "starred": False, "url": f"https://trello.com/b/{name}",
                 "shortUrl": "https://trello.com/b/x", "idOrganization": None,
                 "dateLastActivity": "2026-08-30T10:00:00.000Z", "prefs": {}}
        self.boards[board["id"]] = board
        self.board_members[board["id"]] = [self.me["id"]]
        return board

    def _add_list(self, board, name):
        item = {"id": new_id("c"), "name": name, "closed": False,
                "pos": len(self.lists) * 100 + 100, "idBoard": board["id"]}
        self.lists[item["id"]] = item
        return item

    def _add_label(self, board, name, color):
        label = {"id": new_id("d"), "name": name, "color": color, "idBoard": board["id"]}
        self.labels[label["id"]] = label
        return label

    def _add_card(self, lst, name, desc="", labels=None, members=None, due=None):
        card = {
            "id": new_id("e"), "name": name, "desc": desc, "idList": lst["id"],
            "idBoard": lst["idBoard"], "idMembers": list(members or []),
            "idLabels": [lb["id"] for lb in (labels or [])],
            "due": due, "dueComplete": False, "start": None, "closed": False,
            "pos": len(self.cards) * 100 + 100,
            "shortUrl": f"https://trello.com/c/{new_id('f')[:8]}",
            "dateLastActivity": "2026-08-30T10:00:00.000Z",
            "idChecklists": [],
            "badges": {"comments": 0, "attachments": 0, "checkItems": 0, "checkItemsChecked": 0},
        }
        self.cards[card["id"]] = card
        return card

    def _add_checklist(self, card, name):
        cl = {"id": new_id("7"), "name": name, "idCard": card["id"], "pos": 100}
        self.checklists[cl["id"]] = cl
        self.checkitems[cl["id"]] = []
        card["idChecklists"].append(cl["id"])
        return cl

    def _add_checkitem(self, checklist, name, state="incomplete"):
        item = {"id": new_id("8"), "name": name, "state": state,
                "pos": len(self.checkitems[checklist["id"]]) * 100 + 100,
                "due": None, "idMember": None}
        self.checkitems[checklist["id"]].append(item)
        return item

    def _add_action(self, board, type_, data):
        action = {"id": new_id("9"), "type": type_, "date": "2026-08-30T11:00:00.000Z",
                  "data": {**data, "board": {"id": board["id"], "name": board["name"]}},
                  "memberCreator": self.me}
        self.actions.setdefault(board["id"], []).append(action)
        return action

    # ---------------- roteamento ----------------
    def request(self, method, path, params=None, json=None):
        params = params or {}
        self.calls.append((method, path, params))
        handler = self._route(method, path)
        if handler is None:
            raise AssertionError(f"Rota nao implementada no fake: {method} {path}")
        return handler(params)

    def get_me(self):
        return self.me

    def _route(self, method, path):
        routes = [
            ("GET", r"^/members/me$", lambda m: lambda p: self.me),
            ("GET", r"^/members/me/boards$", lambda m: self._my_boards),
            ("GET", r"^/members/([^/]+)/actions$", lambda m: lambda p: self._member_actions(p)),
            ("GET", r"^/members/([^/]+)$", lambda m: lambda p: self._get_member(m.group(1))),
            ("GET", r"^/boards/([^/]+)/lists$", lambda m: lambda p: [
                l for l in self.lists.values() if l["idBoard"] == m.group(1)]),
            ("GET", r"^/boards/([^/]+)/labels$", lambda m: lambda p: [
                l for l in self.labels.values() if l["idBoard"] == m.group(1)]),
            ("GET", r"^/boards/([^/]+)/members$", lambda m: lambda p: [
                self.members[i] for i in self.board_members.get(m.group(1), [])]),
            ("GET", r"^/boards/([^/]+)/memberships$", lambda m: lambda p: self._board_memberships(m.group(1))),
            ("GET", r"^/boards/([^/]+)/cards$", lambda m: lambda p: self._board_cards(m.group(1), p)),
            ("GET", r"^/boards/([^/]+)/checklists$", lambda m: lambda p: self._board_checklists(m.group(1))),
            ("GET", r"^/boards/([^/]+)/actions$", lambda m: lambda p: self.actions.get(m.group(1), [])),
            ("GET", r"^/boards/([^/]+)$", lambda m: lambda p: self.boards[m.group(1)]),
            ("GET", r"^/lists/([^/]+)$", lambda m: lambda p: self.lists[m.group(1)]),
            ("GET", r"^/cards/([^/]+)/checklists$", lambda m: lambda p: self._card_checklists(m.group(1))),
            ("GET", r"^/cards/([^/]+)/actions$", lambda m: lambda p: []),
            ("GET", r"^/cards/([^/]+)$", lambda m: lambda p: self._get_card(m.group(1), p)),
            ("GET", r"^/search$", lambda m: self._search),

            ("POST", r"^/cards/([^/]+)/actions/comments$", lambda m: lambda p: self._comment(m.group(1), p)),
            ("POST", r"^/cards/([^/]+)/idMembers$", lambda m: lambda p: self._card_add_member(m.group(1), p)),
            ("POST", r"^/cards$", lambda m: self._create_card),
            ("POST", r"^/checklists/([^/]+)/checkItems$", lambda m: lambda p: self._create_checkitem(m.group(1), p)),
            ("POST", r"^/checklists$", lambda m: self._create_checklist),
            ("POST", r"^/lists/([^/]+)/moveAllCards$", lambda m: lambda p: self._move_all(m.group(1), p)),
            ("POST", r"^/lists/([^/]+)/archiveAllCards$", lambda m: lambda p: self._archive_all(m.group(1))),
            ("POST", r"^/lists$", lambda m: self._create_list),
            ("POST", r"^/boards/?$", lambda m: self._create_board),
            ("POST", r"^/labels$", lambda m: self._create_label),

            ("PUT", r"^/cards/([^/]+)/checkItem/([^/]+)$", lambda m: lambda p: self._update_checkitem(m.group(2), p)),
            ("PUT", r"^/cards/([^/]+)/actions/([^/]+)/comments$", lambda m: lambda p: {"id": m.group(2), "text": p.get("text")}),
            ("PUT", r"^/cards/([^/]+)$", lambda m: lambda p: self._update_card(m.group(1), p)),
            ("PUT", r"^/lists/([^/]+)$", lambda m: lambda p: self._update_list(m.group(1), p)),
            ("PUT", r"^/boards/([^/]+)/members/([^/]+)$", lambda m: lambda p: self._board_add_member(m.group(1), m.group(2))),
            ("PUT", r"^/boards/([^/]+)/members$", lambda m: lambda p: {"invited": p.get("email")}),
            ("PUT", r"^/boards/([^/]+)$", lambda m: lambda p: self._update_board(m.group(1), p)),
            ("PUT", r"^/checklists/([^/]+)$", lambda m: lambda p: self._update_checklist(m.group(1), p)),
            ("PUT", r"^/labels/([^/]+)$", lambda m: lambda p: self._update_label(m.group(1), p)),

            ("DELETE", r"^/cards/([^/]+)/idMembers/([^/]+)$", lambda m: lambda p: self._card_del_member(m.group(1), m.group(2))),
            ("DELETE", r"^/cards/([^/]+)$", lambda m: lambda p: self.cards.pop(m.group(1), None) and None),
            ("DELETE", r"^/checklists/([^/]+)/checkItems/([^/]+)$", lambda m: lambda p: None),
            ("DELETE", r"^/checklists/([^/]+)$", lambda m: lambda p: self.checklists.pop(m.group(1), None) and None),
            ("DELETE", r"^/labels/([^/]+)$", lambda m: lambda p: self.labels.pop(m.group(1), None) and None),
            ("DELETE", r"^/boards/([^/]+)/members/([^/]+)$", lambda m: lambda p: None),
            ("DELETE", r"^/boards/([^/]+)$", lambda m: lambda p: self.boards.pop(m.group(1), None) and None),
            ("DELETE", r"^/cards/([^/]+)/actions/([^/]+)/comments$", lambda m: lambda p: None),
        ]
        for verb, pattern, build in routes:
            if verb != method:
                continue
            match = re.match(pattern, path)
            if match:
                return build(match)
        return None

    # ---------------- handlers ----------------
    def _my_boards(self, params):
        return list(self.boards.values())

    def _get_member(self, ref):
        for member in self.members.values():
            if member["id"] == ref or member["username"] == ref:
                return member
        raise AssertionError(f"membro {ref} nao existe")

    def _member_actions(self, params):
        return [a for actions in self.actions.values() for a in actions]

    def _board_cards(self, board_id, params):
        wanted = params.get("filter", "open")
        cards = [c for c in self.cards.values() if c["idBoard"] == board_id]
        if wanted == "open":
            cards = [c for c in cards if not c["closed"]]
        elif wanted == "closed":
            cards = [c for c in cards if c["closed"]]
        return cards

    def _board_memberships(self, board_id):
        return [
            {"idMember": mid, "memberType": "admin" if mid == self.me["id"] else "normal",
             "unconfirmed": False, "deactivated": False}
            for mid in self.board_members.get(board_id, [])
        ]

    def _board_checklists(self, board_id):
        out = []
        for cl in self.checklists.values():
            card = self.cards.get(cl["idCard"])
            if card and card["idBoard"] == board_id:
                out.append({**cl, "checkItems": self.checkitems.get(cl["id"], [])})
        return out

    def _card_checklists(self, card_id):
        return [{**cl, "checkItems": self.checkitems.get(cl["id"], [])}
                for cl in self.checklists.values() if cl["idCard"] == card_id]

    def _get_card(self, card_id, params):
        card = dict(self.cards[card_id])
        if params.get("checklists") == "all":
            card["checklists"] = self._card_checklists(card_id)
        if params.get("attachments") == "true":
            card["attachments"] = []
        if params.get("actions"):
            card["actions"] = []
        return card

    def _search(self, params):
        query = (params.get("query") or "").lower()
        terms = [t for t in query.split() if not t.startswith(("is:", "due:", "@"))]
        result = {}
        cards = []
        for card in self.cards.values():
            haystack = f"{card['name']} {card['desc']}".lower()
            if not terms or any(t in haystack for t in terms):
                enriched = dict(card)
                enriched["board"] = self.boards[card["idBoard"]]
                enriched["list"] = self.lists[card["idList"]]
                enriched["members"] = [self.members[i] for i in card["idMembers"]]
                cards.append(enriched)
        if "cards" in (params.get("modelTypes") or ""):
            result["cards"] = cards
        if "boards" in (params.get("modelTypes") or ""):
            result["boards"] = list(self.boards.values())
        if "members" in (params.get("modelTypes") or ""):
            result["members"] = list(self.members.values())
        return result

    def _create_card(self, params):
        lst = self.lists[params["idList"]]
        card = self._add_card(lst, params.get("name", "sem nome"), desc=params.get("desc", ""))
        if params.get("idLabels"):
            card["idLabels"] = params["idLabels"].split(",")
        if params.get("idMembers"):
            card["idMembers"] = params["idMembers"].split(",")
        if params.get("due"):
            card["due"] = params["due"]
        return card

    def _update_card(self, card_id, params):
        card = self.cards[card_id]
        mapping = {"name": "name", "desc": "desc", "due": "due", "start": "start",
                   "idList": "idList", "idBoard": "idBoard", "pos": "pos"}
        for key, field in mapping.items():
            if key in params:
                card[field] = params[key]
        if "closed" in params:
            card["closed"] = params["closed"] == "true"
        if "dueComplete" in params:
            card["dueComplete"] = params["dueComplete"] == "true"
        if "idLabels" in params:
            card["idLabels"] = [i for i in params["idLabels"].split(",") if i]
        if "idMembers" in params:
            card["idMembers"] = [i for i in params["idMembers"].split(",") if i]
        return card

    def _comment(self, card_id, params):
        return {"id": new_id("9"), "text": params.get("text"), "date": "2026-09-01T10:00:00.000Z"}

    def _card_add_member(self, card_id, params):
        # A API real devolve 400 "member is already on the card" nesse caso.
        card = self.cards[card_id]
        if params["value"] in card["idMembers"]:
            raise TrelloApiError("Trello retornou 400: member is already on the card",
                                 status_code=400)
        card["idMembers"].append(params["value"])
        return card["idMembers"]

    def _card_del_member(self, card_id, member_id):
        card = self.cards[card_id]
        if member_id not in card["idMembers"]:
            raise TrelloApiError("Trello retornou 400: member is not on the card",
                                 status_code=400)
        card["idMembers"] = [i for i in card["idMembers"] if i != member_id]
        return card["idMembers"]

    def _create_checklist(self, params):
        card = self.cards[params["idCard"]]
        return self._add_checklist(card, params.get("name", "Checklist"))

    def _update_checklist(self, checklist_id, params):
        cl = self.checklists[checklist_id]
        if "name" in params:
            cl["name"] = params["name"]
        return cl

    def _create_checkitem(self, checklist_id, params):
        cl = self.checklists[checklist_id]
        return self._add_checkitem(
            cl, params.get("name"),
            state="complete" if params.get("checked") == "true" else "incomplete")

    def _update_checkitem(self, item_id, params):
        for items in self.checkitems.values():
            for item in items:
                if item["id"] == item_id:
                    if "name" in params:
                        item["name"] = params["name"]
                    if "state" in params:
                        item["state"] = params["state"]
                    return item
        raise AssertionError("checkitem nao existe")

    def _create_list(self, params):
        board = self.boards[params["idBoard"]]
        return self._add_list(board, params["name"])

    def _update_list(self, list_id, params):
        item = self.lists[list_id]
        if "name" in params:
            item["name"] = params["name"]
        if "closed" in params:
            item["closed"] = params["closed"] == "true"
        return item

    def _move_all(self, list_id, params):
        moved = [c for c in self.cards.values() if c["idList"] == list_id]
        for card in moved:
            card["idList"] = params["idList"]
        return moved

    def _archive_all(self, list_id):
        for card in self.cards.values():
            if card["idList"] == list_id:
                card["closed"] = True
        return []

    def _create_board(self, params):
        board = self._add_board(params["name"])
        if params.get("desc"):
            board["desc"] = params["desc"]
        return board

    def _update_board(self, board_id, params):
        board = self.boards[board_id]
        if "name" in params:
            board["name"] = params["name"]
        if "desc" in params:
            board["desc"] = params["desc"]
        if "closed" in params:
            board["closed"] = params["closed"] == "true"
        return board

    def _create_label(self, params):
        board = self.boards[params["idBoard"]]
        return self._add_label(board, params.get("name", ""), params.get("color", "green"))

    def _update_label(self, label_id, params):
        label = self.labels[label_id]
        if "name" in params:
            label["name"] = params["name"]
        if "color" in params:
            label["color"] = params["color"]
        return label

    def _board_add_member(self, board_id, member_id):
        members = self.board_members.setdefault(board_id, [])
        if member_id not in members:
            members.append(member_id)
        return members
