"""create user_plans table

Revision ID: 0002
Revises: 0001
Create Date: 2026-04-22
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0002"
down_revision: Union[str, None] = "0001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "user_plans",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("user_id", sa.String(length=128), nullable=False),
        sa.Column("plan", sa.String(length=16), nullable=False, server_default="free"),
        sa.Column("stripe_customer_id", sa.String(length=128), nullable=True),
        sa.Column("stripe_subscription_id", sa.String(length=128), nullable=True),
        sa.Column("queries_today", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("reset_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id"),
    )
    op.create_index("ix_user_plans_user_id", "user_plans", ["user_id"])


def downgrade() -> None:
    op.drop_index("ix_user_plans_user_id", table_name="user_plans")
    op.drop_table("user_plans")
