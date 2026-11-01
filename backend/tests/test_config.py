import pytest
from pydantic import ValidationError

from backend.core.config import Settings


def test_database_url_converts_render_postgres_url_for_asyncpg() -> None:
    settings = Settings(
        database_url=(
            "postgres://user:pass@host.example/db"
            "?sslmode=require&channel_binding=require"
        )
    )

    assert settings.database_url == (
        "postgresql+asyncpg://user:pass@host.example/db?ssl=require"
    )


def test_database_url_preserves_non_postgres_urls() -> None:
    settings = Settings(database_url="sqlite+aiosqlite:///test.db")

    assert settings.database_url == "sqlite+aiosqlite:///test.db"


def test_production_rejects_development_security_settings() -> None:
    with pytest.raises(ValidationError):
        Settings(environment="production")


def test_production_accepts_password_auth_without_sms() -> None:
    settings = Settings(
        environment="production",
        jwt_secret="jwt-secret-value-with-more-than-32-characters",
        otp_pepper="otp-pepper-value-with-more-than-32-characters",
        pii_encryption_key="5c06-YuAnZHqiKSNbXRlDSFRD89WTTVaWiAf7cdHL6U=",
    )
    assert settings.environment == "production"
