from datetime import date, datetime
from decimal import Decimal
from typing import Literal
from uuid import UUID
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from backend.identity.models import AccountStatus, UserRole
from backend.workforce.models import ChangeRequestStatus, ObjectStatus


class CityCreate(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    name: str = Field(min_length=1, max_length=128)
    timezone: str = Field(default="Europe/Moscow", min_length=1, max_length=64)

    @field_validator("timezone")
    @classmethod
    def valid_timezone(cls, value: str) -> str:
        try:
            ZoneInfo(value)
        except (ZoneInfoNotFoundError, ValueError) as exc:
            raise ValueError("Unknown IANA timezone") from exc
        return value


class CityUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    name: str | None = Field(default=None, min_length=1, max_length=128)
    timezone: str | None = Field(default=None, min_length=1, max_length=64)

    @model_validator(mode="after")
    def valid_changes(self) -> "CityUpdate":
        if not self.model_fields_set or any(
            getattr(self, field) is None for field in self.model_fields_set
        ):
            raise ValueError("Provide a non-null name or timezone")
        if self.timezone is not None:
            CityCreate.valid_timezone(self.timezone)
        return self


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
    position: str | None = None

    id: UUID
    full_name: str
    role: UserRole
    photo_url: str | None
    city: str | None
    brigade_name: str | None


class WorkerBrigadeRead(WorkerPublicRead):
    is_self: bool


class WorkerTeamRead(WorkerPublicRead):
    employee_id: str
    assignment: AssignmentRead | None


class WorkerPrivateRead(WorkerTeamRead):
    city_id: UUID | None = None
    phone: str
    status: AccountStatus
    hire_date: date | None
    passport_number: str | None


class WorkerCreate(BaseModel):
    position: str | None = Field(default=None, max_length=128)

    password: str | None = Field(default=None, min_length=8, max_length=128, repr=False)
    phone: str
    full_name: str = Field(min_length=1, max_length=255)
    employee_id: str = Field(min_length=1, max_length=64)
    city_id: UUID | None = None
    photo_url: str | None = Field(default=None, max_length=2048)
    hire_date: date | None = None
    passport_number: str | None = Field(default=None, min_length=3, max_length=64)


class WorkerUpdate(BaseModel):
    position: str | None = Field(default=None, max_length=128)

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


class ObjectCoordinates(BaseModel):
    latitude: float | None = Field(default=None, ge=-90, le=90, allow_inf_nan=False)
    longitude: float | None = Field(default=None, ge=-180, le=180, allow_inf_nan=False)

    @model_validator(mode="after")
    def paired_coordinates(self) -> "ObjectCoordinates":
        if ("latitude" in self.model_fields_set) != ("longitude" in self.model_fields_set):
            raise ValueError("Submit both coordinates together")
        if (self.latitude is None) != (self.longitude is None):
            raise ValueError("Both coordinates must be set or cleared together")
        return self


class ObjectSchedule(ObjectCoordinates):
    planned_hours: Decimal | None = Field(
        default=None, gt=0, max_digits=14, decimal_places=2, allow_inf_nan=False
    )
    planned_volume_m3: Decimal | None = Field(
        default=None, gt=0, max_digits=14, decimal_places=2, allow_inf_nan=False
    )
    progress_unit: Literal["hours", "m3"] | None = None
    planned_quantity: Decimal | None = Field(
        default=None, gt=0, max_digits=14, decimal_places=2, allow_inf_nan=False
    )

    @model_validator(mode="after")
    def paired_progress_plan(self) -> "ObjectSchedule":
        if ("progress_unit" in self.model_fields_set) != (
            "planned_quantity" in self.model_fields_set
        ) or (self.progress_unit is None) != (self.planned_quantity is None):
            raise ValueError("Submit progress unit and planned quantity together")
        return self

    start_date: date | None = None
    estimated_end_date: date | None = None
    responsible_master_user_id: UUID | None = None

    @model_validator(mode="after")
    def ordered_dates(self) -> "ObjectSchedule":
        if (
            self.start_date
            and self.estimated_end_date
            and self.estimated_end_date < self.start_date
        ):
            raise ValueError("Estimated end date must not precede start date")
        return self


class WorkObjectCreate(ObjectSchedule):
    name: str = Field(min_length=1, max_length=255)
    city_id: UUID
    address: str = Field(min_length=1, max_length=2000)
    status: ObjectStatus = ObjectStatus.PLANNED


class WorkObjectRead(WorkObjectCreate):
    responsible_master_name: str | None = None
    photo_url: str | None = None
    photo_is_demo: bool = False
    id: UUID
    city_name: str
    archived_at: datetime | None


class WorkObjectUpdate(ObjectSchedule):
    name: str | None = Field(default=None, min_length=1, max_length=255)
    city_id: UUID | None = None
    address: str | None = Field(default=None, min_length=1, max_length=2000)
    status: ObjectStatus | None = None


class ChangeRequestCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
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
    review_note: str | None = None
    status: ChangeRequestStatus
    reviewed_by: UUID | None
    reviewed_at: datetime | None
    created_at: datetime


class ChangeRequestDecision(BaseModel):
    approve: bool
    review_note: str | None = Field(default=None, max_length=1000)


class SalaryAccountWrite(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    bank_name: str = Field(min_length=1, max_length=255)
    iban: str = Field(min_length=1, max_length=80, repr=False)
    account_holder: str = Field(min_length=1, max_length=255)

    @field_validator("iban")
    @classmethod
    def valid_iban(cls, value: str) -> str:
        value = "".join(value.split()).upper()
        if not value:
            raise ValueError("Account number is required")
        return value
