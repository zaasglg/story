from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from backend.identity.models import AccountStatus, UserRole


class OtpRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    phone: str = Field(min_length=7, max_length=32)


class OtpRequestResponse(BaseModel):
    status: str = "accepted"
    retry_after_seconds: int


class OtpVerifyRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    phone: str = Field(min_length=7, max_length=32)
    code: str = Field(pattern=r"^\d{6}$")
    device_name: str = Field(default="Unknown device", min_length=1, max_length=255)


class RefreshRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    refresh_token: str = Field(min_length=40, max_length=512)


class LogoutRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    refresh_token: str | None = Field(default=None, min_length=40, max_length=512)


class UserResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    phone: str
    role: UserRole
    status: AccountStatus
    full_name: str | None = None
    employee_id: str | None = None
    city: str | None = None


class TokenResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    expires_in: int
    user: UserResponse


class SessionResponse(BaseModel):
    id: UUID
    device_name: str
    created_at: datetime
    last_used_at: datetime
    expires_at: datetime
    current: bool


class MessageResponse(BaseModel):
    status: str
