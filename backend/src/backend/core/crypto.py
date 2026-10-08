from cryptography.fernet import Fernet, InvalidToken

from backend.core.errors import ApiError


class PiiCipher:
    def __init__(self, key: str) -> None:
        self._fernet = Fernet(key.encode())

    def encrypt(self, value: str) -> str:
        return self._fernet.encrypt(value.encode()).decode()

    def decrypt(self, value: str) -> str:
        try:
            return self._fernet.decrypt(value.encode()).decode()
        except InvalidToken as exc:
            raise ApiError(
                500, "pii_decryption_failed", "Protected data could not be read"
            ) from exc
