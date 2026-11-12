"""Cliente da API do Power-Up de checklists (projeto trello-power).

A chave da API vale para todos os quadros, entao este cliente so deve ser
usado depois que o card/board foi resolvido com o token do proprio usuario
(ver tools/powerup.py).
"""

import requests

from .config import Config
from .tools.common import ToolError

TIMEOUT = (5, 15)


class PowerUpClient:
    def __init__(self, base_url: str, api_key: str):
        self._base_url = base_url.rstrip("/")
        self._api_key = api_key

    def request(self, method: str, path: str, body: dict | None = None):
        try:
            response = requests.request(
                method,
                self._base_url + path,
                json=body,
                headers={"X-API-Key": self._api_key},
                timeout=TIMEOUT,
            )
        except requests.RequestException as exc:
            raise ToolError(
                "Nao foi possivel falar com o Power-Up de checklists. Tente de novo em instantes."
            ) from exc

        if response.status_code == 204:
            return None
        try:
            data = response.json()
        except ValueError:
            data = {}
        if not response.ok:
            message = (data or {}).get("erro") or f"HTTP {response.status_code}"
            raise ToolError(f"Power-Up de checklists recusou: {message}")
        return data


def get_client() -> PowerUpClient:
    if not Config.POWERUP_API_URL or not Config.POWERUP_API_KEY:
        raise ToolError(
            "O Power-Up de checklists nao esta configurado neste servidor "
            "(defina POWERUP_API_URL e POWERUP_API_KEY)."
        )
    return PowerUpClient(Config.POWERUP_API_URL, Config.POWERUP_API_KEY)
