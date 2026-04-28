"""CRUD helpers for user plan and query usage."""
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import UserPlan

_FREE_DAILY_LIMIT = 3


async def get_or_create_plan(session: AsyncSession, user_id: str) -> UserPlan:
    row = await session.scalar(select(UserPlan).where(UserPlan.user_id == user_id))
    if row is None:
        row = UserPlan(user_id=user_id, plan="free", queries_today=0)
        session.add(row)
        await session.commit()
        await session.refresh(row)
    return row


async def check_and_increment(session: AsyncSession, user_id: str) -> tuple[bool, str]:
    """Returns (allowed, reason). Increments counter if allowed."""
    row = await get_or_create_plan(session, user_id)

    # Reset counter if it's a new day
    now = datetime.now(timezone.utc)
    if row.reset_at.date() < now.date():
        row.queries_today = 0
        row.reset_at = now

    if row.plan == "pro":
        row.queries_today += 1
        await session.commit()
        return True, ""

    if row.queries_today >= _FREE_DAILY_LIMIT:
        return False, f"Free plan limit of {_FREE_DAILY_LIMIT} queries/day reached. Upgrade to Pro."

    row.queries_today += 1
    await session.commit()
    return True, ""


async def set_plan(
    session: AsyncSession,
    user_id: str,
    plan: str,
    stripe_customer_id: str | None = None,
    stripe_subscription_id: str | None = None,
) -> None:
    row = await get_or_create_plan(session, user_id)
    row.plan = plan
    if stripe_customer_id:
        row.stripe_customer_id = stripe_customer_id
    if stripe_subscription_id:
        row.stripe_subscription_id = stripe_subscription_id
    await session.commit()
