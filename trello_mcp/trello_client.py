"""Wrapper fino sobre a REST API do Trello.

Cada instancia carrega as credenciais de UM usuario. Os metodos de alto
nivel (buscar cards, criar board, etc.) serao adicionados junto com as
tools do MCP.
"""

import requests

from .config import Config


class TrelloAuthError(RuntimeError):
    """Credenciais invalidas ou sem permissao."""


class TrelloApiError(RuntimeError):
    """Erro retornado pela API do Trello."""

    def __init__(self, message: str, status_code: int | None = None):
        super().__init__(message)
        self.status_code = status_code


class TrelloClient:
    def __init__(self, api_key: str, token: str, base_url: str | None = None):
        self._api_key = api_key
        self._token = token
        self._base_url = base_url or Config.TRELLO_API_BASE_URL

    def request(
        self,
        method: str,
        path: str,
        params: dict | None = None,
        json: dict | None = None,
    ):
        url = f"{self._base_url}{path}"
        merged_params = {"key": self._api_key, "token": self._token, **(params or {})}

        try:
            response = requests.request(
                method, url, params=merged_params, json=json, timeout=15
            )
        except requests.RequestException as exc:
            raise TrelloApiError(f"Falha de rede ao chamar o Trello: {exc}") from exc

        if response.status_code in (401, 403):
            raise TrelloAuthError(
                f"Trello recusou as credenciais ({response.status_code}): {response.text[:200]}"
            )
        if not response.ok:
            raise TrelloApiError(
                f"Trello retornou {response.status_code}: {response.text[:200]}",
                status_code=response.status_code,
            )

        if not response.content:
            return None
        try:
            return response.json()
        except ValueError:
            return response.text

    def get_me(self) -> dict:
        """Valida as credenciais e devolve o membro dono do token."""
        return self.request(
            "GET",
            "/members/me",
            params={"fields": "id,username,fullName,email"},
        )
