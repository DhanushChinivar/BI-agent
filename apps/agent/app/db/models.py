"""SQLAlchemy ORM models."""
import base64
from datetime import datetime, timezone

from cryptography.fernet import Fernet
from sqlalchemy import Boolean, DateTime, ForeignKey, Index, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.config.settings import get_settings
from app.db.engine import Base


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
        onupdate=lambda: datetime.now(timezone.utc),
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
        onupdate=lambda: datetime.now(timezone.utc),
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
        onupdate=lambda: datetime.now(timezone.utc),
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
