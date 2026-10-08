from functools import lru_cache
from typing import Literal

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy.engine import make_url


def normalize_database_url(value: str) -> str:
    """Convert provider-style PostgreSQL URLs for SQLAlchemy's asyncpg dialect."""
    url = make_url(value)
    if url.get_backend_name() not in {"postgres", "postgresql"}:
        return value

    url = url.set(drivername="postgresql+asyncpg")
    query = dict(url.query)
    sslmode = query.pop("sslmode", None)
    if sslmode is not None and "ssl" not in query:
        query["ssl"] = sslmode
    return url.set(query=query).render_as_string(hide_password=False)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_prefix="M1917_",
        extra="ignore",
        case_sensitive=False,
    )

    app_name: str = "ALASH M-1917 API"
    environment: Literal["development", "test", "production"] = "development"
    api_prefix: str = "/api/v1"
    database_url: str = "postgresql+asyncpg://m1917:m1917@postgres:5432/m1917"
    redis_url: str = "redis://redis:6379/0"

    jwt_secret: SecretStr = SecretStr("development-only-change-this-secret")
    jwt_issuer: str = "alash-m1917"
    jwt_audience: str = "alash-m1917-clients"
    access_token_minutes: int = Field(default=15, ge=5, le=60)
    refresh_token_days: int = Field(default=30, ge=1, le=90)

    otp_provider: Literal["console", "test", "sms"] = "console"
    sms_gateway_url: str | None = None
    sms_api_token: SecretStr | None = None
    sms_sender_name: str = "ALASH"
    otp_pepper: SecretStr = SecretStr("development-only-otp-pepper")
    pii_encryption_key: SecretStr = SecretStr("0HzTSHdTqMQboIFWW1d8PuI2OhVrPYJBZ9ZCrVnz1kk=")
    otp_ttl_seconds: int = Field(default=300, ge=60, le=600)
    otp_resend_seconds: int = Field(default=60, ge=10, le=300)
    otp_max_attempts: int = Field(default=5, ge=3, le=10)
    otp_phone_hour_limit: int = Field(default=6, ge=1, le=20)
    otp_ip_hour_limit: int = Field(default=30, ge=1, le=200)

    bootstrap_users_json: str = "[]"
    cors_origins: list[str] = ["http://localhost:3000", "http://localhost:8080"]

    @field_validator("cors_origins", mode="before")
    @classmethod
    def parse_cors_origins(cls, value: object) -> object:
        if isinstance(value, str) and not value.startswith("["):
            return [item.strip() for item in value.split(",") if item.strip()]
        return value

    @field_validator("database_url", mode="before")
    @classmethod
    def parse_database_url(cls, value: object) -> object:
        if isinstance(value, str):
            return normalize_database_url(value)
        return value

    @model_validator(mode="after")
    def validate_production_secrets(self) -> "Settings":
        if self.environment == "production":
            if self.otp_provider != "sms":
                raise ValueError("M1917_OTP_PROVIDER must be 'sms' in production")
            if not self.sms_gateway_url or self.sms_api_token is None:
                raise ValueError("SMS gateway URL and token are required in production")
            if not self.sms_gateway_url.startswith("https://"):
                raise ValueError("M1917_SMS_GATEWAY_URL must use HTTPS in production")
            jwt_secret = self.jwt_secret.get_secret_value()
            otp_pepper = self.otp_pepper.get_secret_value()
            pii_key = self.pii_encryption_key.get_secret_value()
            if len(jwt_secret) < 32 or jwt_secret.startswith("development-"):
                raise ValueError("M1917_JWT_SECRET must be a unique 32+ character secret")
            if len(otp_pepper) < 32 or otp_pepper.startswith("development-"):
                raise ValueError("M1917_OTP_PEPPER must be a unique 32+ character secret")
            if jwt_secret == otp_pepper:
                raise ValueError("JWT and OTP secrets must be independent")
            if pii_key == "0HzTSHdTqMQboIFWW1d8PuI2OhVrPYJBZ9ZCrVnz1kk=":
                raise ValueError("M1917_PII_ENCRYPTION_KEY must be unique in production")
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()
