"""Wrapper fino sobre a REST API do Trello.

Cada instancia carrega as credenciais de UM usuario. Os metodos de alto
nivel (buscar cards, criar board, etc.) serao adicionados junto com as
tools do MCP.
"""

import time

import requests

from .config import Config

# Limites da API do Trello: 300 req/10s por API key e 100 req/10s por token.
# Um lote grande de operacoes pode estourar isso, entao 429 vira espera e
# nova tentativa em vez de erro. A janela e de 10s -- por isso o teto de espera.
RATE_LIMIT_RETRIES = 3
RATE_LIMIT_MAX_WAIT = 11.0


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

        for attempt in range(RATE_LIMIT_RETRIES + 1):
            try:
                response = requests.request(
                    method, url, params=merged_params, json=json, timeout=15
                )
            except requests.RequestException as exc:
                raise TrelloApiError(f"Falha de rede ao chamar o Trello: {exc}") from exc

            if response.status_code != 429 or attempt == RATE_LIMIT_RETRIES:
                break
            time.sleep(self._retry_delay(response, attempt))

        if response.status_code == 429:
            raise TrelloApiError(
                "Limite de requisicoes do Trello excedido mesmo apos "
                f"{RATE_LIMIT_RETRIES} tentativas. Divida o lote em partes menores.",
                status_code=429,
            )

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

    @staticmethod
    def _retry_delay(response, attempt: int) -> float:
        """Respeita Retry-After quando presente; senao usa backoff exponencial."""
        header = response.headers.get("Retry-After")
        if header:
            try:
                return min(float(header), RATE_LIMIT_MAX_WAIT)
            except ValueError:
                pass
        return min(2.0 ** attempt, RATE_LIMIT_MAX_WAIT)

    def get_me(self) -> dict:
        """Valida as credenciais e devolve o membro dono do token."""
        return self.request(
            "GET",
            "/members/me",
            params={"fields": "id,username,fullName,email"},
        )
