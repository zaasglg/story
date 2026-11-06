import pytest

from backend.finance.company import BankWrite
from backend.finance.personal_banks import PersonalBankWrite
from backend.workforce.schemas import SalaryAccountWrite


@pytest.mark.parametrize("number", ["123", "40817810099910004312", "KZ9711155415991133323"])
@pytest.mark.parametrize("schema", [BankWrite, PersonalBankWrite, SalaryAccountWrite])
def test_account_number_does_not_require_iban_format_or_checksum(number, schema):
    payload = {"iban": number}
    if schema in (BankWrite, PersonalBankWrite):
        payload["name"] = "Основной"
    if schema in (PersonalBankWrite, SalaryAccountWrite):
        payload.update(bank_name="Банк", account_holder="Сотрудник")
    assert schema.model_validate(payload).iban == number
