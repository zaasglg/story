"""Work authorizations, short-lived pass tokens, immutable rating events."""

import sqlalchemy as sa

from alembic import op

revision = "20261005_0008"
down_revision = "20261005_0007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "work_authorizations",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "worker_user_id",
            sa.Uuid(),
            sa.ForeignKey("users.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("valid_from", sa.Date(), nullable=False),
        sa.Column("valid_until", sa.Date(), nullable=False),
        sa.Column("reference", sa.String(255), nullable=False),
        sa.Column(
            "created_by", sa.Uuid(), sa.ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True)),
        sa.CheckConstraint("valid_until >= valid_from", name="ck_authorization_dates"),
    )
    op.create_index(
        "ix_work_authorizations_worker_user_id", "work_authorizations", ["worker_user_id"]
    )
    op.create_table(
        "pass_tokens",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "worker_user_id",
            sa.Uuid(),
            sa.ForeignKey("users.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("token_hash", sa.String(64), nullable=False, unique=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("consumed_at", sa.DateTime(timezone=True)),
    )
    op.create_index("ix_pass_tokens_worker_user_id", "pass_tokens", ["worker_user_id"])
    op.create_table(
        "rating_events",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "worker_user_id",
            sa.Uuid(),
            sa.ForeignKey("users.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("event_date", sa.Date(), nullable=False),
        sa.Column("event_type", sa.String(32), nullable=False),
        sa.Column("delta", sa.Numeric(4, 2), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("photo_url", sa.Text()),
        sa.Column("source_key", sa.String(128), nullable=False),
        sa.Column(
            "created_by", sa.Uuid(), sa.ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("worker_user_id", "source_key"),
    )
    op.create_index("ix_rating_events_worker_user_id", "rating_events", ["worker_user_id"])
    op.execute(
        "CREATE TRIGGER rating_events_append_only BEFORE UPDATE OR DELETE "
        "ON rating_events FOR EACH ROW EXECUTE FUNCTION protect_payroll_ledger()"
    )


def downgrade() -> None:
    op.drop_table("rating_events")
    op.drop_table("pass_tokens")
    op.drop_table("work_authorizations")
