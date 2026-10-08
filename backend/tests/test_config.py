import pytest
from pydantic import ValidationError

from backend.core.config import Settings


def test_production_rejects_development_security_settings() -> None:
    with pytest.raises(ValidationError):
        Settings(environment="production")


def test_production_requires_sms_credentials() -> None:
    with pytest.raises(ValidationError):
        Settings(
            environment="production",
            otp_provider="sms",
            jwt_secret="a-secure-production-jwt-secret-value",
            otp_pepper="a-separate-secure-production-otp-pepper",
        )


def test_production_accepts_independent_secrets_and_https_sms() -> None:
    settings = Settings(
        environment="production",
        otp_provider="sms",
        sms_gateway_url="https://sms.example.test/messages",
        sms_api_token="provider-secret",
        jwt_secret="jwt-secret-value-with-more-than-32-characters",
        otp_pepper="otp-pepper-value-with-more-than-32-characters",
        pii_encryption_key="5c06-YuAnZHqiKSNbXRlDSFRD89WTTVaWiAf7cdHL6U=",
    )
    assert settings.environment == "production"
