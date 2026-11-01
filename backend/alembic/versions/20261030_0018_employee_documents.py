"""Add private employee documents.

Revision ID: 20261030_0018
Revises: 20261030_0017
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20261030_0018"
down_revision: str | None = "20261030_0017"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "employee_documents",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("worker_user_id", sa.Uuid(), nullable=False),
        sa.Column("document_type", sa.String(32), nullable=False),
        sa.Column("title", sa.String(255), nullable=False),
        sa.Column("filename", sa.String(255), nullable=False),
        sa.Column("content_type", sa.String(64), nullable=False),
        sa.Column("content", sa.LargeBinary(), nullable=False),
        sa.Column("issue_date", sa.DateTime(timezone=True), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("uploaded_by", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("archived_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["uploaded_by"], ["users.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["worker_user_id"], ["users.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_employee_documents_worker_user_id", "employee_documents", ["worker_user_id"]
    )
    op.create_index("ix_employee_documents_expires_at", "employee_documents", ["expires_at"])


def downgrade() -> None:
    op.drop_index("ix_employee_documents_expires_at", table_name="employee_documents")
    op.drop_index("ix_employee_documents_worker_user_id", table_name="employee_documents")
    op.drop_table("employee_documents")
