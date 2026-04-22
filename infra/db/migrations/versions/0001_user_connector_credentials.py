"""create user_connector_credentials table

Revision ID: 0001
Revises:
Create Date: 2026-04-22
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "user_connector_credentials",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("user_id", sa.String(length=128), nullable=False),
        sa.Column("connector", sa.String(length=64), nullable=False),
        sa.Column("credentials_enc", sa.Text(), nullable=False),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_user_connector_credentials_user_id", "user_connector_credentials", ["user_id"])


def downgrade() -> None:
    op.drop_index("ix_user_connector_credentials_user_id", table_name="user_connector_credentials")
    op.drop_table("user_connector_credentials")
