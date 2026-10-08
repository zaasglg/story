"""Equipment inventory, movement history, and expenses."""

import sqlalchemy as sa

from alembic import op

revision = "20261005_0004"
down_revision = "20261005_0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "equipment_items",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("serial_number", sa.String(128), unique=True),
        sa.Column("available_quantity", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("archived_at", sa.DateTime(timezone=True)),
        sa.CheckConstraint("available_quantity >= 0", name="ck_equipment_available"),
    )
    op.create_table(
        "equipment_issues",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "equipment_id",
            sa.Uuid(),
            sa.ForeignKey("equipment_items.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "worker_user_id",
            sa.Uuid(),
            sa.ForeignKey("users.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("quantity", sa.Integer(), nullable=False),
        sa.Column("issue_date", sa.Date(), nullable=False),
        sa.Column("return_date", sa.Date()),
        sa.Column(
            "created_by", sa.Uuid(), sa.ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
        ),
        sa.Column("returned_by", sa.Uuid(), sa.ForeignKey("users.id", ondelete="RESTRICT")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("quantity > 0", name="ck_equipment_issue_quantity"),
    )
    for column in ("equipment_id", "worker_user_id"):
        op.create_index(f"ix_equipment_issues_{column}", "equipment_issues", [column])
    op.create_table(
        "stock_movements",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "equipment_id",
            sa.Uuid(),
            sa.ForeignKey("equipment_items.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("delta", sa.Integer(), nullable=False),
        sa.Column("reason", sa.String(255), nullable=False),
        sa.Column(
            "actor_user_id",
            sa.Uuid(),
            sa.ForeignKey("users.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_stock_movements_equipment_id", "stock_movements", ["equipment_id"])
    op.create_table(
        "expenses",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("object_id", sa.Uuid(), sa.ForeignKey("work_objects.id", ondelete="RESTRICT")),
        sa.Column(
            "city_id", sa.Uuid(), sa.ForeignKey("cities.id", ondelete="RESTRICT"), nullable=False
        ),
        sa.Column("expense_date", sa.Date(), nullable=False),
        sa.Column("amount", sa.Numeric(14, 2), nullable=False),
        sa.Column("currency", sa.String(3), nullable=False),
        sa.Column("category", sa.String(32), nullable=False),
        sa.Column("comment", sa.Text()),
        sa.Column("receipt_url", sa.Text()),
        sa.Column(
            "created_by", sa.Uuid(), sa.ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
        ),
        sa.Column(
            "updated_by", sa.Uuid(), sa.ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("archived_at", sa.DateTime(timezone=True)),
        sa.CheckConstraint("amount > 0", name="ck_expense_positive_amount"),
    )
    for column in ("object_id", "city_id", "expense_date", "category"):
        op.create_index(f"ix_expenses_{column}", "expenses", [column])


def downgrade() -> None:
    op.drop_table("expenses")
    op.drop_table("stock_movements")
    op.drop_table("equipment_issues")
    op.drop_table("equipment_items")
