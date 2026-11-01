from datetime import date, datetime, time
from decimal import Decimal
from uuid import UUID

from pydantic import BaseModel, Field, model_validator

from backend.core.time import ensure_utc
from backend.operations.models import AttendanceStatus, WorkDayStatus


class UpdateModel(BaseModel):
    @model_validator(mode="after")
    def reject_null_required_fields(self):
        optional_fields = {"check_in", "check_out", "comment"}
        for field in self.model_fields_set - optional_fields:
            if getattr(self, field) is None:
                raise ValueError(f"{field} cannot be null")
        return self


class TariffCreate(BaseModel):
    object_id: UUID
    rate_per_m3: Decimal = Field(gt=0, max_digits=12, decimal_places=2)
    currency: str = Field(default="RUB", pattern=r"^[A-Z]{3}$")
    effective_from: date


class TariffRead(TariffCreate):
    id: UUID
    object_name: str
    effective_to: date | None


class ScheduleCreate(BaseModel):
    approved: bool = True
    is_working_day: bool = True

    worker_user_id: UUID
    brigade_id: UUID
    object_id: UUID
    work_date: date
    shift_start: time
    shift_end: time

    @model_validator(mode="after")
    def validate_shift(self) -> "ScheduleCreate":
        if self.shift_start == self.shift_end:
            raise ValueError("Shift start and end must differ")
        return self


class ScheduleUpdate(UpdateModel):
    approved: bool | None = None
    is_working_day: bool | None = None

    brigade_id: UUID | None = None
    object_id: UUID | None = None
    work_date: date | None = None
    shift_start: time | None = None
    shift_end: time | None = None


class ScheduleRead(ScheduleCreate):
    id: UUID
    worker_name: str
    brigade_name: str
    object_name: str


class AttendanceCreate(BaseModel):
    worker_user_id: UUID
    work_date: date
    check_in: datetime | None = None
    check_out: datetime | None = None
    status: AttendanceStatus
    comment: str | None = Field(default=None, max_length=1000)

    @model_validator(mode="after")
    def validate_times(self) -> "AttendanceCreate":
        if (
            self.check_in
            and self.check_out
            and ensure_utc(self.check_out) <= ensure_utc(self.check_in)
        ):
            raise ValueError("Check-out must be after check-in")
        return self


class AttendanceUpdate(UpdateModel):
    check_in: datetime | None = None
    check_out: datetime | None = None
    status: AttendanceStatus | None = None
    comment: str | None = Field(default=None, max_length=1000)


class AttendanceRead(AttendanceCreate):
    id: UUID
    worker_name: str


class WorkDayCreate(BaseModel):
    worker_user_id: UUID
    object_id: UUID
    brigade_id: UUID
    work_date: date
    check_in: datetime | None = None
    check_out: datetime | None = None
    hours: Decimal = Field(ge=0, le=24, max_digits=8, decimal_places=2)
    m3: Decimal = Field(ge=0, max_digits=12, decimal_places=2)
    status: WorkDayStatus = WorkDayStatus.DRAFT

    @model_validator(mode="after")
    def validate_times(self) -> "WorkDayCreate":
        if (
            self.check_in
            and self.check_out
            and ensure_utc(self.check_out) <= ensure_utc(self.check_in)
        ):
            raise ValueError("Check-out must be after check-in")
        return self


class WorkDayUpdate(UpdateModel):
    check_in: datetime | None = None
    check_out: datetime | None = None
    hours: Decimal | None = Field(default=None, ge=0, le=24, max_digits=8, decimal_places=2)
    m3: Decimal | None = Field(default=None, ge=0, max_digits=12, decimal_places=2)
    status: WorkDayStatus | None = None


class WorkDayTeamRead(BaseModel):
    id: UUID
    worker_user_id: UUID
    worker_name: str
    object_id: UUID
    object_name: str
    brigade_id: UUID
    brigade_name: str
    work_date: date
    check_in: datetime | None
    check_out: datetime | None
    hours: Decimal
    m3: Decimal
    status: WorkDayStatus


class WorkDayPrivateRead(WorkDayTeamRead):
    rate_per_m3: Decimal
    daily_earnings: Decimal
    currency: str
