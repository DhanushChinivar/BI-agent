"""create scheduled_reports table

Postgres becomes the source of truth for recurring reports. Previously the
action node tried to hold the schedule in n8n, which had nowhere to put a
per-user cron: both workflow JSONs read the question and schedule from
instance-level `$env`/`$vars`, so a deployment had one global schedule.

Revision ID: 0004
Revises: 0003
Create Date: 2026-08-10
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0004"
down_revision: Union[str, None] = "0003"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "scheduled_reports",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("user_id", sa.String(length=128), nullable=False),
        sa.Column("question", sa.Text(), nullable=False),
        sa.Column("cron", sa.String(length=64), nullable=False),
        sa.Column(
            "action_type",
            sa.String(length=32),
            nullable=False,
            server_default="schedule_report",
        ),
        sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("next_run_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_run_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_status", sa.String(length=16), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_scheduled_reports_user_id", "scheduled_reports", ["user_id"])
    # The due query is the only hot read and is exactly this predicate.
    op.create_index(
        "ix_scheduled_reports_due", "scheduled_reports", ["active", "next_run_at"]
    )


def downgrade() -> None:
    op.drop_index("ix_scheduled_reports_due", table_name="scheduled_reports")
    op.drop_index("ix_scheduled_reports_user_id", table_name="scheduled_reports")
    op.drop_table("scheduled_reports")
