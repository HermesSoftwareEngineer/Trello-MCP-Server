"""Wrapper fino sobre a REST API do Trello.

Cada instancia carrega as credenciais de UM usuario. Os metodos de alto
nivel (buscar cards, criar board, etc.) serao adicionados junto com as
tools do MCP.
"""

import time
from urllib.parse import urljoin, urlparse

import requests

from .config import Config

# Limites da API do Trello: 300 req/10s por API key e 100 req/10s por token.
# Um lote grande de operacoes pode estourar isso, entao 429 vira espera e
# nova tentativa em vez de erro. A janela e de 10s -- por isso o teto de espera.
RATE_LIMIT_RETRIES = 3
RATE_LIMIT_MAX_WAIT = 11.0

# Timeouts (conexao, leitura) em segundos. Upload de arquivo grande precisa de
# mais folga na leitura (o Trello processa o arquivo antes de responder).
TIMEOUT_DEFAULT = (10, 15)
TIMEOUT_UPLOAD = (10, 90)
TIMEOUT_DOWNLOAD = (10, 30)
DOWNLOAD_DEADLINE = 45.0  # teto total de um download, em segundos
DOWNLOAD_MAX_REDIRECTS = 4
DOWNLOAD_CHUNK = 64 * 1024

# Hosts que recebem as credenciais do usuario ao baixar um anexo.
TRELLO_HOST_SUFFIXES = ("trello.com",)


def is_trello_host(url: str) -> bool:
    host = (urlparse(url).hostname or "").lower()
    return any(host == s or host.endswith("." + s) for s in TRELLO_HOST_SUFFIXES)


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
        files: dict | None = None,
    ):
        url = f"{self._base_url}{path}"
        merged_params = {"key": self._api_key, "token": self._token, **(params or {})}

        for attempt in range(RATE_LIMIT_RETRIES + 1):
            response = self._send(method, url, merged_params, json, files)

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

    def _redact(self, text: str) -> str:
        """Mensagens de erro do requests embutem a URL com key/token na query."""
        for secret in (self._api_key, self._token):
            if secret:
                text = text.replace(secret, "***")
        return text

    def _send(self, method, url, params, json, files):
        """Uma chamada HTTP. Leituras (GET) sao idempotentes e ganham uma
        segunda tentativa em falha transitoria de rede; escritas nao, para
        nao duplicar efeitos."""
        tries = 2 if method.upper() == "GET" else 1
        for n in range(tries):
            try:
                return requests.request(
                    method, url, params=params, json=json, files=files,
                    timeout=TIMEOUT_UPLOAD if files else TIMEOUT_DEFAULT,
                )
            except (requests.ConnectionError, requests.Timeout) as exc:
                if n + 1 < tries:
                    time.sleep(0.5)
                    continue
                error = exc
            except requests.RequestException as exc:
                error = exc
            hint = ""
            if files:
                hint = (" O anexo pode ter sido criado mesmo assim: confira com "
                        "'list' antes de repetir o envio.")
            raise TrelloApiError(
                f"Falha de rede ao chamar o Trello: {self._redact(str(error))}.{hint}"
            ) from None

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

    def download(self, url: str, limit: int, offset: int = 0) -> tuple[bytes, str, bool]:
        """Baixa um arquivo anexado (URL do Trello) com as credenciais do usuario.

        As URLs de anexos do Trello exigem o header Authorization (OAuth) e
        nao aceitam key/token na query. As credenciais so vao para hosts do
        Trello: se houver redirect para outro host (storage assinado), o
        header e descartado. Pula os primeiros `offset` bytes (sem guarda-los
        em memoria), le no maximo `limit` bytes e devolve
        (conteudo, content_type, truncado) -- truncado=True se havia mais.
        """
        if not url.lower().startswith("https://") or not is_trello_host(url):
            raise TrelloApiError("URL de anexo fora do Trello; nao baixo.")
        auth = f'OAuth oauth_consumer_key="{self._api_key}", oauth_token="{self._token}"'
        deadline = time.monotonic() + DOWNLOAD_DEADLINE
        current = url

        for _ in range(DOWNLOAD_MAX_REDIRECTS + 1):
            headers = {"Authorization": auth} if is_trello_host(current) else {}
            try:
                response = requests.get(
                    current, headers=headers, stream=True, allow_redirects=False,
                    timeout=TIMEOUT_DOWNLOAD,
                )
            except requests.RequestException as exc:
                raise TrelloApiError(
                    f"Falha de rede ao baixar o anexo: {self._redact(str(exc))}"
                ) from None

            with response:
                if response.status_code in (301, 302, 303, 307, 308):
                    current = urljoin(current, response.headers.get("Location", ""))
                    if not current.lower().startswith("https://"):
                        raise TrelloApiError("Redirect de anexo para URL nao-HTTPS; recusado.")
                    continue
                if response.status_code in (401, 403):
                    raise TrelloAuthError(
                        f"Trello recusou o download do anexo ({response.status_code})."
                    )
                if response.status_code == 404:
                    raise TrelloApiError("Anexo nao encontrado no Trello (404).", status_code=404)
                if not response.ok:
                    raise TrelloApiError(
                        f"Download do anexo falhou: Trello retornou {response.status_code}.",
                        status_code=response.status_code,
                    )

                chunks, size, pos, truncated = [], 0, 0, False
                try:
                    for chunk in response.iter_content(DOWNLOAD_CHUNK):
                        if not chunk:
                            continue
                        piece = chunk[max(offset - pos, 0):]
                        pos += len(chunk)
                        if piece:
                            room = limit - size
                            if len(piece) > room:
                                if room > 0:
                                    chunks.append(piece[:room])
                                    size += room
                                truncated = True
                                break
                            chunks.append(piece)
                            size += len(piece)
                        if time.monotonic() > deadline:
                            raise TrelloApiError("Download do anexo excedeu o tempo limite.")
                except requests.RequestException as exc:
                    raise TrelloApiError(
                        f"Falha de rede ao baixar o anexo: {self._redact(str(exc))}"
                    ) from None
                return b"".join(chunks), response.headers.get("Content-Type", ""), truncated

        raise TrelloApiError("Muitos redirects ao baixar o anexo.")
