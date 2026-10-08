from datetime import date, datetime
from uuid import UUID

from pydantic import BaseModel, Field, model_validator

from backend.identity.models import AccountStatus, UserRole
from backend.workforce.models import ChangeRequestStatus, ObjectStatus


class CityRead(BaseModel):
    id: UUID
    name: str
    timezone: str


class AssignmentRead(BaseModel):
    brigade_id: UUID
    brigade_name: str
    object_id: UUID | None
    object_name: str | None
    master_user_id: UUID
    effective_from: datetime


class WorkerPublicRead(BaseModel):
    id: UUID
    full_name: str
    role: UserRole
    photo_url: str | None
    city: str | None
    brigade_name: str | None


class WorkerTeamRead(WorkerPublicRead):
    employee_id: str
    assignment: AssignmentRead | None


class WorkerPrivateRead(WorkerTeamRead):
    phone: str
    status: AccountStatus
    hire_date: date | None
    passport_number: str | None


class WorkerCreate(BaseModel):
    phone: str
    full_name: str = Field(min_length=1, max_length=255)
    employee_id: str = Field(min_length=1, max_length=64)
    city_id: UUID | None = None
    photo_url: str | None = Field(default=None, max_length=2048)
    hire_date: date | None = None
    passport_number: str | None = Field(default=None, min_length=3, max_length=64)


class WorkerUpdate(BaseModel):
    full_name: str | None = Field(default=None, min_length=1, max_length=255)
    employee_id: str | None = Field(default=None, min_length=1, max_length=64)
    city_id: UUID | None = None
    photo_url: str | None = Field(default=None, max_length=2048)
    hire_date: date | None = None
    passport_number: str | None = Field(default=None, min_length=3, max_length=64)
    status: AccountStatus | None = None


class AssignmentCreate(BaseModel):
    brigade_id: UUID
    object_id: UUID | None = None


class BrigadeCreate(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    city_id: UUID
    master_user_id: UUID | None = None


class BrigadeRead(BrigadeCreate):
    id: UUID
    city_name: str
    archived_at: datetime | None


class BrigadeUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=128)
    city_id: UUID | None = None
    master_user_id: UUID | None = None


class WorkObjectCreate(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    city_id: UUID
    address: str = Field(min_length=1, max_length=2000)
    status: ObjectStatus = ObjectStatus.PLANNED


class WorkObjectRead(WorkObjectCreate):
    id: UUID
    city_name: str
    archived_at: datetime | None


class WorkObjectUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=255)
    city_id: UUID | None = None
    address: str | None = Field(default=None, min_length=1, max_length=2000)
    status: ObjectStatus | None = None


class ChangeRequestCreate(BaseModel):
    full_name: str | None = Field(default=None, min_length=1, max_length=255)
    city_id: UUID | None = None
    photo_url: str | None = Field(default=None, max_length=2048)
    passport_number: str | None = Field(default=None, min_length=3, max_length=64)
    reason: str | None = Field(default=None, max_length=1000)

    @model_validator(mode="after")
    def require_change(self) -> "ChangeRequestCreate":
        if not self.model_dump(exclude={"reason"}, exclude_none=True):
            raise ValueError("At least one profile change is required")
        return self


class ChangeRequestRead(BaseModel):
    id: UUID
    worker_user_id: UUID
    changes: dict[str, object]
    reason: str | None
    status: ChangeRequestStatus
    reviewed_by: UUID | None
    reviewed_at: datetime | None
    created_at: datetime


class ChangeRequestDecision(BaseModel):
    approve: bool
