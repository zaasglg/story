"""Protected profile-request storage; plaintext exists only in authorized projections."""

from backend.core.crypto import PiiCipher
from backend.workforce.schemas import ChangeRequestRead

PROTECTED_PASSPORT = "_passport_number_encrypted"


def protect_changes(changes: dict, cipher: PiiCipher) -> dict:
    protected = dict(changes)
    passport = protected.pop("passport_number", None)
    if passport is not None:
        protected[PROTECTED_PASSPORT] = cipher.encrypt(str(passport))
    return protected


def reveal_changes(changes: dict, cipher: PiiCipher) -> dict:
    revealed = dict(changes)
    encrypted = revealed.pop(PROTECTED_PASSPORT, None)
    if encrypted is not None:
        revealed["passport_number"] = cipher.decrypt(encrypted)
    return revealed


def request_read(item, cipher: PiiCipher) -> ChangeRequestRead:
    return ChangeRequestRead.model_validate(item, from_attributes=True).model_copy(
        update={"changes": reveal_changes(item.changes, cipher)}
    )
