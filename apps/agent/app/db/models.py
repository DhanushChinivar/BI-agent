"""SQLAlchemy ORM models."""
import base64
from datetime import UTC, datetime

from cryptography.fernet import Fernet
from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.config.settings import get_settings
from app.db.engine import Base

# voyage-3-lite's native width. Hard-coded rather than read from settings
# because it is baked into the column type and the index: changing it is a
# migration, not a config change.
EMBEDDING_DIMS = 512


def _fernet() -> Fernet:
    key = get_settings().credential_encryption_key
    # Key must be 32 url-safe base64 bytes; pad/derive if not set
    raw = key.encode()[:32].ljust(32, b"0")
    return Fernet(base64.urlsafe_b64encode(raw))


class UserConnectorCredential(Base):
    """Stores OAuth tokens / API keys per user per connector, encrypted at rest."""

    __tablename__ = "user_connector_credentials"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    user_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    connector: Mapped[str] = mapped_column(String(64), nullable=False)
    # JSON blob encrypted with Fernet (AES-128-CBC)
    credentials_enc: Mapped[str] = mapped_column(Text, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=lambda: datetime.now(UTC),
    )

    def set_credentials(self, data: dict) -> None:
        import json
        self.credentials_enc = _fernet().encrypt(json.dumps(data).encode()).decode()

    def get_credentials(self) -> dict:
        import json
        return json.loads(_fernet().decrypt(self.credentials_enc.encode()))


class UserPlan(Base):
    """Tracks each user's subscription plan and daily query usage."""

    __tablename__ = "user_plans"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    user_id: Mapped[str] = mapped_column(String(128), nullable=False, unique=True, index=True)
    plan: Mapped[str] = mapped_column(String(16), nullable=False, default="free")  # "free" | "pro"
    stripe_customer_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    stripe_subscription_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    queries_today: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    reset_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=lambda: datetime.now(UTC),
    )


class IndexedResource(Base):
    """Per-resource sync bookkeeping for the RAG index.

    Separate from `document_chunks` so a resync can ask "has this changed?"
    without touching the vectors. `revision` is whatever the provider gives us
    to detect change cheaply — Gmail's `historyId`, Notion's `last_edited_time`.
    Unchanged revision means skip: re-embedding an untouched mailbox on every
    tick would be the dominant cost of the whole feature.
    """

    __tablename__ = "indexed_resources"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    user_id: Mapped[str] = mapped_column(String(128), nullable=False)
    connector: Mapped[str] = mapped_column(String(64), nullable=False)
    resource_id: Mapped[str] = mapped_column(String(256), nullable=False)
    title: Mapped[str] = mapped_column(String(512), nullable=False, default="")

    revision: Mapped[str | None] = mapped_column(String(128), nullable=True)
    chunk_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    # Which model produced the stored vectors. A change here means every chunk
    # for this resource is stale even if the content is not — vectors from two
    # different models are not comparable.
    embedding_model: Mapped[str] = mapped_column(String(64), nullable=False, default="")

    status: Mapped[str] = mapped_column(String(16), nullable=False, default="ok")
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    indexed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    __table_args__ = (
        UniqueConstraint(
            "user_id", "connector", "resource_id", name="uq_indexed_resource"
        ),
    )


class DocumentChunk(Base):
    """One embedded span of text from a connector resource.

    Only unstructured sources land here. Spreadsheets stay on the SQL path:
    "what was Q4 revenue?" wants an exact sum over every row, and nearest-
    neighbour lookup over embedded rows answers a different question badly.
    """

    __tablename__ = "document_chunks"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    user_id: Mapped[str] = mapped_column(String(128), nullable=False)
    connector: Mapped[str] = mapped_column(String(64), nullable=False)
    resource_id: Mapped[str] = mapped_column(String(256), nullable=False)

    # Carried so an answer can cite where a claim came from without a second
    # round trip to the provider.
    resource_title: Mapped[str] = mapped_column(String(512), nullable=False, default="")
    chunk_index: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    content: Mapped[str] = mapped_column(Text, nullable=False)

    embedding: Mapped[list[float]] = mapped_column(Vector(EMBEDDING_DIMS), nullable=False)
    embedding_model: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    __table_args__ = (
        # Every search is "this user's chunks, optionally these connectors",
        # so the filter columns lead and the vector index sits beside them.
        Index("ix_document_chunks_scope", "user_id", "connector"),
        Index("ix_document_chunks_resource", "user_id", "connector", "resource_id"),
    )


class ScheduledReport(Base):
    """A recurring question a user asked the agent to answer on a cron.

    Postgres is the source of truth for schedules, not n8n. n8n owns one ticker
    workflow that pokes `/v1/schedules/run-due` every few minutes; everything
    about *what* runs and *when* lives here. The alternative — a workflow per
    schedule, created through n8n's API — makes the schedule list unreadable
    without calling n8n, loses every schedule if the n8n volume is recreated,
    and gives no place to record whether the last run succeeded.
    """

    __tablename__ = "scheduled_reports"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    user_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    question: Mapped[str] = mapped_column(Text, nullable=False)
    cron: Mapped[str] = mapped_column(String(64), nullable=False)
    action_type: Mapped[str] = mapped_column(String(32), nullable=False, default="schedule_report")
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    # Indexed together with `active`: the due query is the only hot read, and it
    # is exactly "active rows whose next_run_at has passed".
    next_run_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    last_run_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_status: Mapped[str | None] = mapped_column(String(16), nullable=True)  # "ok" | "error"
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    __table_args__ = (Index("ix_scheduled_reports_due", "active", "next_run_at"),)


class Conversation(Base):
    """A chat session belonging to a user."""

    __tablename__ = "conversations"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    user_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    title: Mapped[str] = mapped_column(String(256), nullable=False, default="New conversation")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=lambda: datetime.now(UTC),
    )


class ChatMessage(Base):
    """A single turn within a conversation."""

    __tablename__ = "chat_messages"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    conversation_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("conversations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    role: Mapped[str] = mapped_column(String(16), nullable=False)  # "user" | "assistant"
    content: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
