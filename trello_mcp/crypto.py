"""Criptografia das credenciais do Trello em repouso.

A chave do Fernet e derivada de APP_SECRET_KEY. Trocar essa variavel
torna tudo que ja foi salvo ilegivel -- nesse caso os usuarios precisam
reconectar a conta no painel.
"""

import base64
import hashlib

from cryptography.fernet import Fernet, InvalidToken

from .config import Config


class CryptoError(RuntimeError):
    pass


def _fernet() -> Fernet:
    secret = Config.APP_SECRET_KEY
    if not secret:
        raise CryptoError(
            "APP_SECRET_KEY nao configurada. Gere uma com: "
            'python -c "import secrets; print(secrets.token_urlsafe(48))"'
        )
    derived = base64.urlsafe_b64encode(hashlib.sha256(secret.encode()).digest())
    return Fernet(derived)


def encrypt(plaintext: str) -> str:
    return _fernet().encrypt(plaintext.encode()).decode()


def decrypt(ciphertext: str) -> str:
    try:
        return _fernet().decrypt(ciphertext.encode()).decode()
    except InvalidToken as exc:
        raise CryptoError(
            "Nao foi possivel descriptografar a credencial. A APP_SECRET_KEY "
            "provavelmente mudou -- reconecte a conta no painel."
        ) from exc


def hash_token(token: str) -> str:
    """Hash do connector token. So o hash e persistido."""
    return hashlib.sha256(token.encode()).hexdigest()
