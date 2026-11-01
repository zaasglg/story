from datetime import date, datetime
from decimal import Decimal
from uuid import UUID, uuid4

from sqlalchemy import (
    BigInteger,
    Date,
    DateTime,
    ForeignKey,
    Identity,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from backend.identity.models import Base


class WorkAuthorization(Base):
    __tablename__ = "work_authorizations"
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    worker_user_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), index=True
    )
    valid_from: Mapped[date] = mapped_column(Date, nullable=False)
    valid_until: Mapped[date] = mapped_column(Date, nullable=False)
    reference: Mapped[str] = mapped_column(String(255), nullable=False)
    created_by: Mapped[UUID] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class PassToken(Base):
    __tablename__ = "pass_tokens"
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    worker_user_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), index=True
    )
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    consumed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class RatingEvent(Base):
    rating_before: Mapped[Decimal | None] = mapped_column(Numeric(4, 2))
    rating_after: Mapped[Decimal | None] = mapped_column(Numeric(4, 2))
    source: Mapped[str] = mapped_column(String(16), default="MANUAL", server_default="LEGACY")

    __tablename__ = "rating_events"
    __table_args__ = (UniqueConstraint("worker_user_id", "source_key"),)
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    worker_user_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), index=True
    )
    event_date: Mapped[date] = mapped_column(Date, nullable=False)
    event_type: Mapped[str] = mapped_column(String(32), nullable=False)
    delta: Mapped[Decimal] = mapped_column(Numeric(4, 2), nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    photo_url: Mapped[str | None] = mapped_column(Text)
    source_key: Mapped[str] = mapped_column(String(128), nullable=False)
    created_by: Mapped[UUID | None] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class ShiftScanEvent(Base):
    event_sequence: Mapped[int] = mapped_column(BigInteger, Identity(), unique=True)

    """Immutable scan context, preserved when assignments and names change."""

    __tablename__ = "shift_scan_events"
    master_user_id: Mapped[UUID | None] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"))
    __table_args__ = (UniqueConstraint("attendance_id", "action"),)

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    attendance_id: Mapped[UUID] = mapped_column(
        ForeignKey("attendance_records.id", ondelete="RESTRICT"), index=True
    )
    worker_user_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), index=True
    )
    actor_user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"))
    brigade_id: Mapped[UUID] = mapped_column(ForeignKey("brigades.id", ondelete="RESTRICT"))
    object_id: Mapped[UUID] = mapped_column(ForeignKey("work_objects.id", ondelete="RESTRICT"))
    city_id: Mapped[UUID] = mapped_column(ForeignKey("cities.id", ondelete="RESTRICT"))
    worker_name: Mapped[str] = mapped_column(String(255))
    actor_name: Mapped[str] = mapped_column(String(255))
    brigade_name: Mapped[str] = mapped_column(String(128))
    object_name: Mapped[str] = mapped_column(String(255))
    timezone: Mapped[str] = mapped_column(String(64))
    action: Mapped[str] = mapped_column(String(16))
    work_date: Mapped[date] = mapped_column(Date, index=True)
    recorded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
