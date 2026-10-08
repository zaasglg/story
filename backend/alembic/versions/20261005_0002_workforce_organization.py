"""Add workforce organization and assignment history.

Revision ID: 20261005_0002
Revises: 20261005_0001
"""

from collections.abc import Sequence
from datetime import UTC, datetime
from uuid import uuid4

import sqlalchemy as sa

from alembic import op

revision: str = "20261005_0002"
down_revision: str | None = "20261005_0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

CITY_NAMES = ("Lipetsk", "Kursk", "Voronezh", "Belgorod", "Oryol")


def upgrade() -> None:
    op.create_table(
        "cities",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(length=128), nullable=False),
        sa.Column("timezone", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("name"),
    )
    cities = sa.table(
        "cities",
        sa.column("id", sa.Uuid()),
        sa.column("name", sa.String()),
        sa.column("timezone", sa.String()),
        sa.column("created_at", sa.DateTime(timezone=True)),
    )
    op.bulk_insert(
        cities,
        [
            {
                "id": uuid4(),
                "name": name,
                "timezone": "Europe/Moscow",
                "created_at": datetime.now(UTC),
            }
            for name in CITY_NAMES
        ],
    )
    op.add_column("employee_profiles", sa.Column("city_id", sa.Uuid(), nullable=True))
    op.add_column("employee_profiles", sa.Column("hire_date", sa.Date(), nullable=True))
    op.create_foreign_key(
        "fk_employee_profiles_city_id", "employee_profiles", "cities", ["city_id"], ["id"]
    )
    op.create_index("ix_employee_profiles_city_id", "employee_profiles", ["city_id"])
    op.execute(
        "UPDATE employee_profiles p SET city_id = c.id FROM cities c "
        "WHERE lower(p.city) = lower(c.name)"
    )

    op.create_table(
        "brigades",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(length=128), nullable=False),
        sa.Column("city_id", sa.Uuid(), nullable=False),
        sa.Column("master_user_id", sa.Uuid(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("archived_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["city_id"], ["cities.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["master_user_id"], ["users.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_brigades_city_id", "brigades", ["city_id"])
    op.create_index("ix_brigades_master_user_id", "brigades", ["master_user_id"])

    op.create_table(
        "work_objects",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("city_id", sa.Uuid(), nullable=False),
        sa.Column("address", sa.Text(), nullable=False),
        sa.Column("status", sa.String(length=9), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("archived_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "status IN ('PLANNED', 'ACTIVE', 'PAUSED', 'COMPLETED')",
            name="ck_work_objects_status",
        ),
        sa.ForeignKeyConstraint(["city_id"], ["cities.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_work_objects_city_id", "work_objects", ["city_id"])

    op.create_table(
        "employee_private_data",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("passport_encrypted", sa.Text(), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id"),
    )

    op.create_table(
        "worker_assignments",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("worker_user_id", sa.Uuid(), nullable=False),
        sa.Column("brigade_id", sa.Uuid(), nullable=False),
        sa.Column("object_id", sa.Uuid(), nullable=True),
        sa.Column("master_user_id", sa.Uuid(), nullable=False),
        sa.Column("effective_from", sa.DateTime(timezone=True), nullable=False),
        sa.Column("effective_to", sa.DateTime(timezone=True), nullable=True),
        sa.Column("assigned_by", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("archived_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["assigned_by"], ["users.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["brigade_id"], ["brigades.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["master_user_id"], ["users.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["object_id"], ["work_objects.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["worker_user_id"], ["users.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
    )
    for column in ("worker_user_id", "brigade_id", "object_id", "master_user_id"):
        op.create_index(f"ix_worker_assignments_{column}", "worker_assignments", [column])
    op.create_index(
        "uq_worker_assignments_active_worker",
        "worker_assignments",
        ["worker_user_id"],
        unique=True,
        postgresql_where=sa.text("effective_to IS NULL AND archived_at IS NULL"),
    )

    op.create_table(
        "profile_change_requests",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("worker_user_id", sa.Uuid(), nullable=False),
        sa.Column("changes", sa.JSON(), nullable=False),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("status", sa.String(length=9), nullable=False),
        sa.Column("reviewed_by", sa.Uuid(), nullable=True),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "status IN ('PENDING', 'APPROVED', 'REJECTED', 'CANCELLED')",
            name="ck_profile_change_requests_status",
        ),
        sa.ForeignKeyConstraint(["reviewed_by"], ["users.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["worker_user_id"], ["users.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_profile_change_requests_worker_user_id",
        "profile_change_requests",
        ["worker_user_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_profile_change_requests_worker_user_id", table_name="profile_change_requests")
    op.drop_table("profile_change_requests")
    op.drop_index("uq_worker_assignments_active_worker", table_name="worker_assignments")
    for column in ("master_user_id", "object_id", "brigade_id", "worker_user_id"):
        op.drop_index(f"ix_worker_assignments_{column}", table_name="worker_assignments")
    op.drop_table("worker_assignments")
    op.drop_table("employee_private_data")
    op.drop_index("ix_work_objects_city_id", table_name="work_objects")
    op.drop_table("work_objects")
    op.drop_index("ix_brigades_master_user_id", table_name="brigades")
    op.drop_index("ix_brigades_city_id", table_name="brigades")
    op.drop_table("brigades")
    op.drop_index("ix_employee_profiles_city_id", table_name="employee_profiles")
    op.drop_constraint("fk_employee_profiles_city_id", "employee_profiles", type_="foreignkey")
    op.drop_column("employee_profiles", "hire_date")
    op.drop_column("employee_profiles", "city_id")
    op.drop_table("cities")
