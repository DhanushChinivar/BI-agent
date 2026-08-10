"""enable pgvector; create indexed_resources and document_chunks

The RAG layer. Vectors live in the same Postgres as everything else rather than
in a separate vector service — one fewer thing to run, and a chunk can be joined
to its owner without a second store to keep consistent.

Requires the `pgvector/pgvector:pg16` image; `postgres:16-alpine` has no
`vector` extension to enable.

Revision ID: 0005
Revises: 0004
Create Date: 2026-08-10
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from pgvector.sqlalchemy import Vector

revision: str = "0005"
down_revision: Union[str, None] = "0004"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_DIMS = 512  # voyage-3-lite


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")

    op.create_table(
        "indexed_resources",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("user_id", sa.String(length=128), nullable=False),
        sa.Column("connector", sa.String(length=64), nullable=False),
        sa.Column("resource_id", sa.String(length=256), nullable=False),
        sa.Column("title", sa.String(length=512), nullable=False, server_default=""),
        sa.Column("revision", sa.String(length=128), nullable=True),
        sa.Column("chunk_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "embedding_model", sa.String(length=64), nullable=False, server_default=""
        ),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="ok"),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column(
            "indexed_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        # One row per resource: the resync path upserts on this.
        sa.UniqueConstraint(
            "user_id", "connector", "resource_id", name="uq_indexed_resource"
        ),
    )

    op.create_table(
        "document_chunks",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("user_id", sa.String(length=128), nullable=False),
        sa.Column("connector", sa.String(length=64), nullable=False),
        sa.Column("resource_id", sa.String(length=256), nullable=False),
        sa.Column(
            "resource_title", sa.String(length=512), nullable=False, server_default=""
        ),
        sa.Column("chunk_index", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("embedding", Vector(_DIMS), nullable=False),
        sa.Column("embedding_model", sa.String(length=64), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_document_chunks_scope", "document_chunks", ["user_id", "connector"]
    )
    op.create_index(
        "ix_document_chunks_resource",
        "document_chunks",
        ["user_id", "connector", "resource_id"],
    )

    # HNSW rather than IVFFlat: IVFFlat needs a populated table to build a
    # meaningful list assignment, so building it here — on an empty table —
    # would produce a bad index that only a later REINDEX fixes. HNSW builds
    # incrementally and needs no training data.
    #
    # vector_cosine_ops matches the `<=>` operator the search uses. An index
    # built for a different operator class is silently ignored by the planner,
    # which looks like "pgvector is slow" rather than like a mistake.
    op.execute(
        "CREATE INDEX ix_document_chunks_embedding ON document_chunks "
        "USING hnsw (embedding vector_cosine_ops)"
    )


def downgrade() -> None:
    op.drop_index("ix_document_chunks_embedding", table_name="document_chunks")
    op.drop_index("ix_document_chunks_resource", table_name="document_chunks")
    op.drop_index("ix_document_chunks_scope", table_name="document_chunks")
    op.drop_table("document_chunks")
    op.drop_table("indexed_resources")
    # The extension is deliberately left installed: another table may use it,
    # and dropping it would cascade away their columns.
