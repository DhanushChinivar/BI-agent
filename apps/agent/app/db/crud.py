"""CRUD helpers for connector credentials."""
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import UserConnectorCredential


async def get_credentials(
    session: AsyncSession, user_id: str, connector: str
) -> dict | None:
    row = await session.scalar(
        select(UserConnectorCredential).where(
            UserConnectorCredential.user_id == user_id,
            UserConnectorCredential.connector == connector,
        )
    )
    return row.get_credentials() if row else None


async def upsert_credentials(
    session: AsyncSession, user_id: str, connector: str, data: dict
) -> None:
    row = await session.scalar(
        select(UserConnectorCredential).where(
            UserConnectorCredential.user_id == user_id,
            UserConnectorCredential.connector == connector,
        )
    )
    if row is None:
        row = UserConnectorCredential(user_id=user_id, connector=connector)
        session.add(row)
    row.set_credentials(data)
    await session.commit()
