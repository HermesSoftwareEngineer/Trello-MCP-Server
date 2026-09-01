"""Fragmentos de JSON Schema reaproveitados entre as tools."""

REF = {
    "type": "string",
    "description": "Id do Trello ou nome (casa exato, depois prefixo, depois substring).",
}

CARD_FILTERS = {
    "type": "object",
    "description": (
        "Filtros aplicados aos cards. Todos sao opcionais e combinam entre si (AND). "
        "Omitir um filtro significa 'nao filtrar por isso'."
    ),
    "properties": {
        "lists": {
            "type": "array", "items": {"type": "string"},
            "description": "So cards nestas listas (ids ou nomes).",
        },
        "labels": {
            "type": "array", "items": {"type": "string"},
            "description": "Labels por id, nome ou cor (ex: 'red', 'Urgente').",
        },
        "label_match": {
            "type": "string", "enum": ["any", "all", "none"], "default": "any",
            "description": "any = tem qualquer uma; all = tem todas; none = nao tem nenhuma.",
        },
        "members": {
            "type": "array", "items": {"type": "string"},
            "description": "Membros por id, username ou nome. Use 'me' para si mesmo.",
        },
        "member_match": {
            "type": "string", "enum": ["any", "all", "none"], "default": "any",
        },
        "name_contains": {"type": "string", "description": "Substring no titulo (case-insensitive)."},
        "desc_contains": {"type": "string", "description": "Substring na descricao (case-insensitive)."},
        "has_due": {"type": "boolean", "description": "true = so cards com prazo; false = so sem prazo."},
        "overdue": {"type": "boolean", "description": "true = vencidos e nao concluidos."},
        "due_complete": {"type": "boolean", "description": "Filtra pelo checkbox de prazo concluido."},
        "due_before": {"type": "string", "description": "Prazo anterior a. ISO, 'today', '+7d'."},
        "due_after": {"type": "string", "description": "Prazo posterior a. ISO, 'today', '-7d'."},
        "created_before": {"type": "string"},
        "created_after": {"type": "string"},
        "updated_before": {"type": "string"},
        "updated_after": {
            "type": "string",
            "description": "Ultima atividade depois de. Util para 'o que mudou esta semana' ('-7d').",
        },
    },
    "additionalProperties": False,
}

OUTPUT_OPTIONS = {
    "include_desc": {
        "type": "boolean", "default": True,
        "description": "Incluir a descricao dos cards. Desligue para respostas menores.",
    },
    "desc_max_chars": {
        "type": "integer", "default": 500,
        "description": "Trunca descricoes longas. Use 0 para nao truncar.",
    },
    "include_raw": {
        "type": "boolean", "default": False,
        "description": "Devolve os objetos crus do Trello em vez da versao enxuta.",
    },
}

READ_ONLY = {"readOnlyHint": True, "destructiveHint": False, "openWorldHint": True}
WRITE = {"readOnlyHint": False, "destructiveHint": False, "idempotentHint": False, "openWorldHint": True}
DESTRUCTIVE = {"readOnlyHint": False, "destructiveHint": True, "idempotentHint": False, "openWorldHint": True}
