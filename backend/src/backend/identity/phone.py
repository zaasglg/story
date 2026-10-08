import phonenumbers

from backend.core.errors import ApiError


def normalize_phone(value: str) -> str:
    try:
        number = phonenumbers.parse(value, None)
    except phonenumbers.NumberParseException as exc:
        raise ApiError(422, "invalid_phone", "Phone number must use international format") from exc
    if not phonenumbers.is_valid_number(number):
        raise ApiError(422, "invalid_phone", "Phone number is invalid")
    return phonenumbers.format_number(number, phonenumbers.PhoneNumberFormat.E164)
