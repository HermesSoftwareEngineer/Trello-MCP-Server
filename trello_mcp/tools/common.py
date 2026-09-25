"""Fundacao compartilhada pelas tools: resolvers, filtros e batch runner.

O objetivo e que qualquer parametro que identifique algo no Trello aceite
tanto o id quanto o nome -- a IA raramente sabe ids de antemao. Toda
resolucao passa por cache dentro da mesma chamada de tool.
"""

import re
from datetime import datetime, timedelta, timezone

ID_RE = re.compile(r"^[0-9a-fA-F]{24}$")

BOARD_FIELDS = "id,name,desc,closed,starred,url,shortUrl,idOrganization,dateLastActivity,prefs"
LIST_FIELDS = "id,name,closed,pos,idBoard"
LABEL_FIELDS = "id,name,color,idBoard"
MEMBER_FIELDS = "id,username,fullName,initials"
CARD_FIELDS = (
    "id,name,desc,idList,idBoard,idMembers,idLabels,due,dueComplete,start,"
    "closed,pos,shortUrl,dateLastActivity,idChecklists,badges"
)

LABEL_COLORS = {
    "green", "yellow", "orange", "red", "purple", "blue",
    "sky", "lime", "pink", "black", "null",
}


class ToolError(RuntimeError):
    """Erro de uso da tool -- vira mensagem de erro legivel para a IA."""


# --------------------------------------------------------------------------
# Coercao de valores
# --------------------------------------------------------------------------

def as_bool_param(value) -> str:
    return "true" if value else "false"


def as_list(value) -> list:
    """Aceita lista ou valor unico; normaliza para lista, ignorando vazios."""
    if value is None:
        return []
    if isinstance(value, (list, tuple, set)):
        return [v for v in value if v not in (None, "")]
    return [value] if value != "" else []


def join_ids(ids) -> str:
    return ",".join(ids)


_RELATIVE_RE = re.compile(r"^([+-])\s*(\d+)\s*([hdwm])$", re.I)
_CLEAR_VALUES = {"", "null", "none", "clear", "remove"}


def parse_date(value, *, field: str = "data"):
    """Converte data para ISO 8601 UTC.

    Aceita ISO ("2026-03-01", "2026-03-01T14:00:00Z"), palavras
    ("today", "tomorrow", "yesterday", "now") e offsets relativos
    ("+3d", "+2w", "-1d", "+6h", "+1m" = meses). String vazia / "null"
    limpa o campo no Trello.
    """
    if value is None:
        return None

    raw = str(value).strip()
    lowered = raw.lower()

    if lowered in _CLEAR_VALUES:
        return ""

    now = datetime.now(timezone.utc)

    if lowered == "now":
        return _iso(now)
    if lowered == "today":
        return _iso(now.replace(hour=12, minute=0, second=0, microsecond=0))
    if lowered == "tomorrow":
        return _iso((now + timedelta(days=1)).replace(hour=12, minute=0, second=0, microsecond=0))
    if lowered == "yesterday":
        return _iso((now - timedelta(days=1)).replace(hour=12, minute=0, second=0, microsecond=0))

    match = _RELATIVE_RE.match(lowered)
    if match:
        sign, amount, unit = match.groups()
        amount = int(amount) * (-1 if sign == "-" else 1)
        delta = {
            "h": timedelta(hours=amount),
            "d": timedelta(days=amount),
            "w": timedelta(weeks=amount),
            "m": timedelta(days=30 * amount),
        }[unit.lower()]
        return _iso(now + delta)

    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ToolError(
            f"Nao entendi o valor de {field}: {value!r}. Use ISO 8601 "
            "(2026-03-01T14:00:00Z), 'today', 'tomorrow', ou offsets como '+3d'."
        ) from exc

    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return _iso(parsed)


def _iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")


def to_datetime(value) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def card_created_at(card_id: str) -> str | None:
    """O id do card carrega o timestamp de criacao nos 8 primeiros hex."""
    try:
        return _iso(datetime.fromtimestamp(int(card_id[:8], 16), timezone.utc))
    except (ValueError, TypeError, OSError):
        return None


def truncate(text: str | None, max_chars: int | None) -> str | None:
    if text is None or max_chars is None or len(text) <= max_chars:
        return text
    return text[:max_chars] + f"... [truncado, {len(text)} chars no total]"


# --------------------------------------------------------------------------
# Contexto com cache + resolvers
# --------------------------------------------------------------------------

class TrelloContext:
    """Envolve o TrelloClient com caches de resolucao por chamada de tool."""

    def __init__(self, client):
        self.client = client
        self._me = None
        self._boards = None
        self._lists: dict[str, list] = {}
        self._labels: dict[str, list] = {}
        self._members: dict[str, list] = {}

    # -- entidades base ----------------------------------------------------

    def me(self) -> dict:
        if self._me is None:
            self._me = self.client.get_me()
        return self._me

    def my_boards(self) -> list:
        if self._boards is None:
            self._boards = self.client.request(
                "GET", "/members/me/boards", params={"filter": "all", "fields": BOARD_FIELDS}
            ) or []
        return self._boards

    def board_lists(self, board_id: str, include_closed: bool = True) -> list:
        if board_id not in self._lists:
            self._lists[board_id] = self.client.request(
                "GET", f"/boards/{board_id}/lists",
                params={"filter": "all", "fields": LIST_FIELDS},
            ) or []
        lists = self._lists[board_id]
        return lists if include_closed else [item for item in lists if not item.get("closed")]

    def board_labels(self, board_id: str) -> list:
        if board_id not in self._labels:
            self._labels[board_id] = self.client.request(
                "GET", f"/boards/{board_id}/labels",
                params={"fields": LABEL_FIELDS, "limit": 1000},
            ) or []
        return self._labels[board_id]

    def board_members(self, board_id: str) -> list:
        if board_id not in self._members:
            members = self.client.request(
                "GET", f"/boards/{board_id}/members", params={"fields": MEMBER_FIELDS}
            ) or []
            # /members nao traz o papel de cada um no board -- so /memberships tem isso.
            memberships = self.client.request(
                "GET", f"/boards/{board_id}/memberships",
                params={"member": "false", "fields": "idMember,memberType,unconfirmed,deactivated"},
            ) or []
            roles = {m.get("idMember"): m for m in memberships}
            for member in members:
                info = roles.get(member.get("id")) or {}
                member["role"] = info.get("memberType")
                member["unconfirmed"] = info.get("unconfirmed")
                member["deactivated"] = info.get("deactivated")
            self._members[board_id] = members
        return self._members[board_id]

    def invalidate(self, board_id: str | None = None) -> None:
        """Chamado apos escritas para que leituras seguintes vejam o novo estado."""
        self._boards = None
        if board_id is None:
            self._lists.clear()
            self._labels.clear()
            self._members.clear()
        else:
            self._lists.pop(board_id, None)
            self._labels.pop(board_id, None)
            self._members.pop(board_id, None)

    # -- resolvers ---------------------------------------------------------

    def resolve_board(self, ref, *, required: bool = True) -> dict | None:
        if not ref:
            if required:
                raise ToolError("Informe o board (id ou nome).")
            return None
        if isinstance(ref, dict):
            return ref
        ref = str(ref).strip()

        if ID_RE.match(ref):
            for board in self.my_boards():
                if board["id"] == ref:
                    return board
            return self.client.request("GET", f"/boards/{ref}", params={"fields": BOARD_FIELDS})

        match = _match_by_name(self.my_boards(), ref, ("name",))
        if match is None:
            raise ToolError(
                f"Board {ref!r} nao encontrado. Boards disponiveis: "
                + _preview([b["name"] for b in self.my_boards()])
            )
        return match

    def resolve_list(self, ref, board_id: str, *, required: bool = True) -> dict | None:
        if not ref:
            if required:
                raise ToolError("Informe a lista (id ou nome).")
            return None
        ref = str(ref).strip()

        if ID_RE.match(ref):
            return self.client.request("GET", f"/lists/{ref}", params={"fields": LIST_FIELDS})

        lists = self.board_lists(board_id)
        match = _match_by_name(lists, ref, ("name",))
        if match is None:
            raise ToolError(
                f"Lista {ref!r} nao encontrada nesse board. Listas: "
                + _preview([item["name"] for item in lists])
            )
        return match

    def resolve_label(self, ref, board_id: str) -> dict:
        ref = str(ref).strip()
        labels = self.board_labels(board_id)

        if ID_RE.match(ref):
            for label in labels:
                if label["id"] == ref:
                    return label
            return {"id": ref, "name": None, "color": None}

        match = _match_by_name(labels, ref, ("name",))
        if match is None and ref.lower() in LABEL_COLORS:
            colored = [label for label in labels if label.get("color") == ref.lower()]
            if len(colored) == 1:
                return colored[0]
            if len(colored) > 1:
                raise ToolError(
                    f"Ha {len(colored)} labels com a cor {ref!r} nesse board. "
                    "Use o nome ou o id da label."
                )
        if match is None:
            raise ToolError(
                f"Label {ref!r} nao encontrada nesse board. Labels: "
                + _preview([f"{lb.get('name') or '(sem nome)'}/{lb.get('color')}" for lb in labels])
            )
        return match

    def resolve_member(self, ref, board_id: str | None = None) -> dict:
        ref = str(ref).strip().lstrip("@")

        if ref.lower() in ("me", "eu", "self"):
            return self.me()
        if ID_RE.match(ref):
            return self.client.request("GET", f"/members/{ref}", params={"fields": MEMBER_FIELDS})

        if board_id:
            match = _match_by_name(self.board_members(board_id), ref, ("username", "fullName"))
            if match:
                return match

        try:
            return self.client.request("GET", f"/members/{ref}", params={"fields": MEMBER_FIELDS})
        except Exception as exc:
            hint = ""
            if board_id:
                hint = " Membros do board: " + _preview(
                    [m.get("username") for m in self.board_members(board_id)]
                )
            raise ToolError(f"Membro {ref!r} nao encontrado.{hint}") from exc

    def resolve_card(self, ref, board_id: str | None = None) -> dict:
        if not ref:
            raise ToolError("Informe o card (id ou nome).")
        ref = str(ref).strip()

        if ID_RE.match(ref) or re.match(r"^[a-zA-Z0-9]{8}$", ref):
            return self.client.request("GET", f"/cards/{ref}", params={"fields": CARD_FIELDS})

        if board_id:
            cards = self.client.request(
                "GET", f"/boards/{board_id}/cards",
                params={"filter": "all", "fields": CARD_FIELDS},
            ) or []
            match = _match_by_name(cards, ref, ("name",))
            if match:
                return match
            raise ToolError(
                f"Card {ref!r} nao encontrado nesse board. Use 'search' para localiza-lo."
            )

        found = self.client.request(
            "GET", "/search",
            params={"query": ref, "modelTypes": "cards", "cards_limit": 10,
                    "card_fields": CARD_FIELDS, "partial": "true"},
        ) or {}
        cards = found.get("cards") or []
        match = _match_by_name(cards, ref, ("name",))
        if match:
            return match
        if len(cards) == 1:
            return cards[0]
        raise ToolError(
            f"Card {ref!r} nao encontrado ou ambiguo. Informe 'board' para desambiguar, "
            "ou passe o id do card."
        )

    # -- resolucao em lote (para filtros) ---------------------------------

    def resolve_list_ids(self, refs, board_id: str) -> list[str]:
        return [self.resolve_list(ref, board_id)["id"] for ref in as_list(refs)]

    def resolve_label_ids(self, refs, board_id: str) -> list[str]:
        return [self.resolve_label(ref, board_id)["id"] for ref in as_list(refs)]

    def resolve_member_ids(self, refs, board_id: str | None = None) -> list[str]:
        return [self.resolve_member(ref, board_id)["id"] for ref in as_list(refs)]

    def resolve_board_ids(self, refs) -> list[str]:
        return [self.resolve_board(ref)["id"] for ref in as_list(refs)]


def _match_by_name(items: list, ref: str, fields: tuple) -> dict | None:
    """Casa por igualdade exata, depois prefixo, depois substring."""
    needle = ref.lower()

    def values(item):
        return [str(item.get(field) or "").lower() for field in fields]

    for item in items:
        if needle in values(item):
            return item
    prefixed = [item for item in items if any(v.startswith(needle) for v in values(item))]
    if len(prefixed) == 1:
        return prefixed[0]
    partial = [item for item in items if any(needle in v for v in values(item) if v)]
    if len(partial) == 1:
        return partial[0]
    if len(partial) > 1:
        names = _preview([str(item.get(fields[0])) for item in partial])
        raise ToolError(f"{ref!r} e ambiguo. Candidatos: {names}")
    return None


def _preview(names, limit: int = 25) -> str:
    names = [str(name) for name in names if name]
    shown = ", ".join(names[:limit])
    if len(names) > limit:
        shown += f", ... (+{len(names) - limit})"
    return shown or "(nenhum)"


# --------------------------------------------------------------------------
# Filtro de cards (compartilhado entre snapshot e search)
# --------------------------------------------------------------------------

def filter_cards(cards: list, filters: dict | None, ctx: TrelloContext, board_id: str | None) -> list:
    """Aplica os filtros de card em memoria. Vazio/ausente = sem filtro."""
    if not filters:
        return cards

    list_ids = set(ctx.resolve_list_ids(filters.get("lists"), board_id)) if (
        filters.get("lists") and board_id
    ) else None
    label_ids = set(ctx.resolve_label_ids(filters.get("labels"), board_id)) if (
        filters.get("labels") and board_id
    ) else None
    member_ids = set(ctx.resolve_member_ids(filters.get("members"), board_id)) if (
        filters.get("members")
    ) else None

    label_match = (filters.get("label_match") or "any").lower()
    member_match = (filters.get("member_match") or "any").lower()

    name_contains = (filters.get("name_contains") or "").lower()
    desc_contains = (filters.get("desc_contains") or "").lower()

    due_before = to_datetime(parse_date(filters.get("due_before"), field="due_before"))
    due_after = to_datetime(parse_date(filters.get("due_after"), field="due_after"))
    created_before = to_datetime(parse_date(filters.get("created_before"), field="created_before"))
    created_after = to_datetime(parse_date(filters.get("created_after"), field="created_after"))
    updated_before = to_datetime(parse_date(filters.get("updated_before"), field="updated_before"))
    updated_after = to_datetime(parse_date(filters.get("updated_after"), field="updated_after"))

    has_due = filters.get("has_due")
    overdue = filters.get("overdue")
    due_complete = filters.get("due_complete")
    now = datetime.now(timezone.utc)

    result = []
    for card in cards:
        if list_ids is not None and card.get("idList") not in list_ids:
            continue

        if label_ids is not None:
            card_labels = set(card.get("idLabels") or [])
            if label_match == "all" and not label_ids <= card_labels:
                continue
            if label_match == "none" and card_labels & label_ids:
                continue
            if label_match == "any" and not card_labels & label_ids:
                continue

        if member_ids is not None:
            card_members = set(card.get("idMembers") or [])
            if member_match == "all" and not member_ids <= card_members:
                continue
            if member_match == "none" and card_members & member_ids:
                continue
            if member_match == "any" and not card_members & member_ids:
                continue

        if name_contains and name_contains not in (card.get("name") or "").lower():
            continue
        if desc_contains and desc_contains not in (card.get("desc") or "").lower():
            continue

        due = to_datetime(card.get("due"))
        if has_due is True and due is None:
            continue
        if has_due is False and due is not None:
            continue
        if due_before and (due is None or due >= due_before):
            continue
        if due_after and (due is None or due <= due_after):
            continue
        if overdue is True and not (due and due < now and not card.get("dueComplete")):
            continue
        if overdue is False and (due and due < now and not card.get("dueComplete")):
            continue
        if due_complete is not None and bool(card.get("dueComplete")) is not bool(due_complete):
            continue

        created = to_datetime(card_created_at(card.get("id", "")))
        if created_before and (created is None or created >= created_before):
            continue
        if created_after and (created is None or created <= created_after):
            continue

        updated = to_datetime(card.get("dateLastActivity"))
        if updated_before and (updated is None or updated >= updated_before):
            continue
        if updated_after and (updated is None or updated <= updated_after):
            continue

        result.append(card)
    return result


def compact_card(
    card: dict,
    *,
    list_names: dict | None = None,
    label_names: dict | None = None,
    member_names: dict | None = None,
    include_desc: bool = True,
    desc_max_chars: int | None = 500,
) -> dict:
    """Versao enxuta de um card: nomes em vez de ids onde da, contadores no lugar de payloads."""
    list_names = list_names or {}
    label_names = label_names or {}
    member_names = member_names or {}
    badges = card.get("badges") or {}
    checkitems = badges.get("checkItems") or 0

    compact = {
        "id": card.get("id"),
        "name": card.get("name"),
        "list": list_names.get(card.get("idList")),
        "list_id": card.get("idList"),
        "labels": [label_names.get(i, i) for i in (card.get("idLabels") or [])],
        "members": [member_names.get(i, i) for i in (card.get("idMembers") or [])],
        "due": card.get("due"),
        "due_complete": card.get("dueComplete"),
        "start": card.get("start"),
        "closed": card.get("closed"),
        "position": card.get("pos"),
        "url": card.get("shortUrl"),
        "created_at": card_created_at(card.get("id", "")),
        "last_activity": card.get("dateLastActivity"),
        "counts": {
            "comments": badges.get("comments", 0),
            "attachments": badges.get("attachments", 0),
            "checkitems": f"{badges.get('checkItemsChecked', 0)}/{checkitems}" if checkitems else None,
        },
    }
    if include_desc:
        compact["desc"] = truncate(card.get("desc"), desc_max_chars)
    return {k: v for k, v in compact.items() if v is not None or k in ("due", "desc")}


def render_checklist(checklist: dict, member_names: dict | None = None) -> dict:
    """Checklist completa: nome, prazo/lembrete/responsavel da checklist e de cada item.

    Trello permite atribuir prazo e responsavel tanto na checklist quanto em cada
    item individualmente -- os dois niveis sao expostos aqui para que a IA veja o
    estado completo sem ter que adivinhar em qual nivel a informacao esta.
    """
    member_names = member_names or {}
    checklist_member = checklist.get("idMember")
    return {
        "id": checklist.get("id"),
        "name": checklist.get("name"),
        "due": checklist.get("due"),
        "due_reminder": checklist.get("dueReminder"),
        "assignee": member_names.get(checklist_member, checklist_member) if checklist_member else None,
        "items": [
            {
                "id": item.get("id"),
                "name": item.get("name"),
                "checked": item.get("state") == "complete",
                "due": item.get("due"),
                "member": (
                    member_names.get(item.get("idMember"), item.get("idMember"))
                    if item.get("idMember") else None
                ),
            }
            for item in sorted(checklist.get("checkItems") or [], key=lambda i: i.get("pos") or 0)
        ],
    }


def name_maps(ctx: TrelloContext, board_id: str) -> tuple[dict, dict, dict]:
    lists = {item["id"]: item["name"] for item in ctx.board_lists(board_id)}
    labels = {
        label["id"]: (label.get("name") or f"({label.get('color')})")
        for label in ctx.board_labels(board_id)
    }
    members = {
        member["id"]: member.get("username") or member.get("fullName")
        for member in ctx.board_members(board_id)
    }
    return lists, labels, members


# --------------------------------------------------------------------------
# Batch runner
# --------------------------------------------------------------------------

def run_batch(ctx: TrelloContext, operations, handlers: dict) -> dict:
    """Executa operacoes em sequencia SEM abortar no primeiro erro.

    Cada item vira uma entrada de resultado com status ok/error, para que a
    IA veja exatamente o que passou e o que falhou.
    """
    operations = as_list(operations)
    if not operations:
        raise ToolError("Envie ao menos uma operacao em 'operations'.")

    results = []
    for index, operation in enumerate(operations):
        if not isinstance(operation, dict):
            results.append({
                "index": index, "status": "error",
                "error": "Cada operacao deve ser um objeto.",
            })
            continue

        action = operation.get("action")
        entry = {"index": index, "action": action}
        handler = handlers.get(action)

        if handler is None:
            entry.update({
                "status": "error",
                "error": f"Acao {action!r} desconhecida. Disponiveis: {', '.join(sorted(handlers))}.",
            })
        else:
            try:
                entry.update({"status": "ok", "result": handler(ctx, operation)})
            except Exception as exc:  # reporta e segue para a proxima operacao
                entry.update({"status": "error", "error": f"{type(exc).__name__}: {exc}"})
        results.append(entry)

    succeeded = sum(1 for entry in results if entry["status"] == "ok")
    return {
        "summary": {
            "total": len(results),
            "succeeded": succeeded,
            "failed": len(results) - succeeded,
        },
        "results": results,
    }


def board_context(ctx: TrelloContext, operation: dict, *, from_card: dict | None = None) -> str | None:
    """Descobre o board de uma operacao: explicito, ou herdado do card."""
    if operation.get("board"):
        return ctx.resolve_board(operation["board"])["id"]
    if from_card:
        return from_card.get("idBoard")
    return None
