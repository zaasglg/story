from datetime import date, datetime
from decimal import Decimal
from uuid import UUID, uuid4

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from backend.identity.models import Base


class Expense(Base):
    recipient: Mapped[str | None] = mapped_column(String(255))

    revision: Mapped[int] = mapped_column(default=0, server_default="0", nullable=False)

    __tablename__ = "expenses"
    payroll_entry_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("ledger_entries.id", ondelete="RESTRICT")
    )
    payroll_source_key: Mapped[str | None] = mapped_column(String(128), unique=True)
    __table_args__ = (
        CheckConstraint(
            "amount > 0 OR payroll_entry_id IS NOT NULL", name="ck_expense_positive_amount"
        ),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    object_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("work_objects.id", ondelete="RESTRICT"), index=True
    )
    city_id: Mapped[UUID] = mapped_column(ForeignKey("cities.id", ondelete="RESTRICT"), index=True)
    expense_date: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    amount: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    category: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    comment: Mapped[str | None] = mapped_column(Text)
    receipt_url: Mapped[str | None] = mapped_column(Text)
    created_by: Mapped[UUID] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"))
    updated_by: Mapped[UUID] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String(16), default="DRAFT", nullable=False)


class Revenue(Base):
    __tablename__ = "revenues"
    __table_args__ = (CheckConstraint("amount > 0", name="ck_revenue_positive_amount"),)
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    object_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("work_objects.id", ondelete="RESTRICT"), index=True
    )
    city_id: Mapped[UUID] = mapped_column(ForeignKey("cities.id", ondelete="RESTRICT"), index=True)
    revenue_date: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    amount: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    category: Mapped[str] = mapped_column(String(128), nullable=False)
    comment: Mapped[str | None] = mapped_column(Text)
    attachment_url: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(16), default="DRAFT", nullable=False)
    created_by: Mapped[UUID] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"))
    updated_by: Mapped[UUID] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class CompanyBankAccount(Base):
    __tablename__ = "company_bank_accounts"
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    iban_encrypted: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class FinancialRequest(Base):
    __tablename__ = "financial_requests"
    __table_args__ = (
        UniqueConstraint("created_by", "idempotency_key"),
        Index(
            "uq_pending_salary_change",
            "ledger_entry_id",
            unique=True,
            postgresql_where=text("type = 'SALARY_CHANGE' AND status = 'PENDING_BOSS'"),
            sqlite_where=text("type = 'SALARY_CHANGE' AND status = 'PENDING_BOSS'"),
        ),
        CheckConstraint(
            "type != 'SALARY_CHANGE' OR (ledger_entry_id IS NOT NULL AND "
            "expected_amount IS NOT NULL AND expected_amount >= 0 AND "
            "expected_revision IS NOT NULL AND expected_revision >= 0)",
            name="ck_salary_change_snapshot",
        ),
        CheckConstraint(
            "(type = 'SALARY_CHANGE' AND amount >= 0) OR (type != 'SALARY_CHANGE' AND amount > 0)",
            name="ck_financial_request_amount",
        ),
        CheckConstraint(
            "type IN ('SALARY','BONUS','PENALTY','ADVANCE','PAYMENT','SALARY_CHANGE')",
            name="ck_financial_request_type",
        ),
        CheckConstraint(
            "status IN ('PENDING_BOSS','APPROVED','REJECTED','CANCELLED')",
            name="ck_financial_request_status",
        ),
    )
    ledger_entry_id: Mapped[UUID | None] = mapped_column(
        ForeignKey(
            "ledger_entries.id",
            ondelete="RESTRICT",
            use_alter=True,
            name="fk_salary_change_ledger_entry",
        )
    )
    expected_amount: Mapped[Decimal | None] = mapped_column(Numeric(14, 2))
    expected_revision: Mapped[int | None] = mapped_column()
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    worker_user_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), index=True
    )
    type: Mapped[str] = mapped_column(String(16), nullable=False)
    amount: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    period: Mapped[date] = mapped_column(Date, nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="PENDING_BOSS")
    idempotency_key: Mapped[str] = mapped_column(String(128), nullable=False)
    created_by: Mapped[UUID] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"))
    reviewed_by: Mapped[UUID | None] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"))
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class LedgerEntry(Base):
    __tablename__ = "ledger_entries"
    cost_integrated: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default="false", nullable=False
    )
    __table_args__ = (
        UniqueConstraint("request_id"),
        Index(
            "uq_ledger_salary_period",
            "worker_user_id",
            "period",
            "currency",
            unique=True,
            postgresql_where=text("type = 'SALARY'"),
            sqlite_where=text("type = 'SALARY'"),
        ),
    )
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    request_id: Mapped[UUID] = mapped_column(
        ForeignKey("financial_requests.id", ondelete="RESTRICT")
    )
    worker_user_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), index=True
    )
    type: Mapped[str] = mapped_column(String(16), nullable=False)
    amount: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    period: Mapped[date] = mapped_column(Date, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class LedgerCorrection(Base):
    __tablename__ = "ledger_corrections"
    __table_args__ = (
        UniqueConstraint("ledger_entry_id", "revision"),
        UniqueConstraint("actor_user_id", "idempotency_key"),
        CheckConstraint("old_amount >= 0 AND new_amount >= 0", name="ck_correction_amounts"),
    )
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    ledger_entry_id: Mapped[UUID] = mapped_column(
        ForeignKey("ledger_entries.id", ondelete="RESTRICT"), index=True
    )
    revision: Mapped[int] = mapped_column(nullable=False)
    old_amount: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)
    new_amount: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    actor_user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"))
    idempotency_key: Mapped[str] = mapped_column(String(128), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class PersonalBankAccount(Base):
    __tablename__ = "personal_bank_accounts"
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    owner_user_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    details_encrypted: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
