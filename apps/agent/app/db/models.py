"""SQLAlchemy ORM models."""
import base64
from datetime import datetime, timezone

from cryptography.fernet import Fernet
from sqlalchemy import DateTime, Integer, String, Text, func
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
