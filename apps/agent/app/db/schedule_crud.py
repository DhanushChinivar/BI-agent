"""CRUD helpers for scheduled reports.

Cron parsing lives here rather than in the API layer because `next_run_at` is a
derived column: nothing may write a schedule without also computing when it next
fires, or the row is invisible to the due query forever.
"""
import uuid
from datetime import UTC, datetime

from croniter import CroniterBadCronError, croniter
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import ScheduledReport

# One schedule per user per question, so asking twice updates rather than
# duplicates — an agent that re-runs the same plan should not stack up reports.
_MAX_SCHEDULES_PER_USER = 20


class InvalidCronError(ValueError):
    """The cron expression could not be parsed."""


def next_run(cron: str, after: datetime | None = None) -> datetime:
    """When `cron` next fires strictly after `after` (default: now, UTC).

    Always timezone-aware and always UTC: a naive datetime compared against a
    `timestamptz` column is a silent offset bug, and the comparison is what
    decides whether a report runs.
    """
    base = after or datetime.now(UTC)
    if base.tzinfo is None:
        base = base.replace(tzinfo=UTC)

    try:
        return croniter(cron, base).get_next(datetime).astimezone(UTC)
    except (CroniterBadCronError, ValueError, KeyError) as exc:
        raise InvalidCronError(f"Invalid cron expression {cron!r}: {exc}") from exc


def validate_cron(cron: str) -> None:
    """Raise `InvalidCronError` unless `cron` is parseable. Cheap pre-flight check."""
    next_run(cron)


async def upsert_schedule(
    session: AsyncSession,
    user_id: str,
    question: str,
    cron: str,
    action_type: str = "schedule_report",
) -> ScheduledReport:
    """Create or update the schedule for (user, question). Raises `InvalidCronError`.

    Keyed on the question rather than an id because the caller is the planner,
    which has no id to pass: asking "email me this every Monday" twice should
    leave one schedule, not two.
    """
    fires_at = next_run(cron)

    existing = await session.scalar(
        select(ScheduledReport).where(
            ScheduledReport.user_id == user_id,
            ScheduledReport.question == question,
        )
    )
    if existing is not None:
        existing.cron = cron
        existing.action_type = action_type
        existing.next_run_at = fires_at
        existing.active = True
        await session.commit()
        await session.refresh(existing)
        return existing

    count = len(
        (
            await session.scalars(
                select(ScheduledReport.id).where(ScheduledReport.user_id == user_id)
            )
        ).all()
    )
    if count >= _MAX_SCHEDULES_PER_USER:
        raise ValueError(
            f"Schedule limit of {_MAX_SCHEDULES_PER_USER} reached. Delete one first."
        )

    row = ScheduledReport(
        id=str(uuid.uuid4()),
        user_id=user_id,
        question=question,
        cron=cron,
        action_type=action_type,
        next_run_at=fires_at,
        active=True,
    )
    session.add(row)
    await session.commit()
    await session.refresh(row)
    return row


async def claim_due(
    session: AsyncSession, limit: int, now: datetime | None = None
) -> list[ScheduledReport]:
    """Take ownership of up to `limit` schedules that are due.

    `next_run_at` is advanced *before* the pipeline runs and the rows are locked
    `FOR UPDATE SKIP LOCKED`, so two ticker calls that overlap — a slow run and
    the next tick, or two agent replicas — cannot both claim the same report and
    email the user twice. The cost is that a report lost to a crash mid-run is
    skipped rather than retried, which for a recurring report is the better of
    the two failure modes.
    """
    at = now or datetime.now(UTC)

    rows = (
        await session.scalars(
            select(ScheduledReport)
            .where(ScheduledReport.active.is_(True), ScheduledReport.next_run_at <= at)
            .order_by(ScheduledReport.next_run_at)
            .limit(limit)
            .with_for_update(skip_locked=True)
        )
    ).all()

    for row in rows:
        try:
            row.next_run_at = next_run(row.cron, at)
        except InvalidCronError:
            # A row whose cron no longer parses would otherwise stay due forever
            # and be re-claimed on every single tick.
            row.active = False
            row.last_status = "error"
            row.last_error = f"Invalid cron expression: {row.cron}"

    await session.commit()
    return list(rows)


async def record_run(
    session: AsyncSession, schedule_id: str, status: str, error: str | None = None
) -> None:
    row = await session.get(ScheduledReport, schedule_id)
    if row is None:
        return
    row.last_run_at = datetime.now(UTC)
    row.last_status = status
    row.last_error = error
    await session.commit()


async def list_schedules(session: AsyncSession, user_id: str) -> list[ScheduledReport]:
    rows = await session.scalars(
        select(ScheduledReport)
        .where(ScheduledReport.user_id == user_id)
        .order_by(ScheduledReport.created_at.desc())
    )
    return list(rows.all())


async def delete_schedule(session: AsyncSession, user_id: str, schedule_id: str) -> bool:
    """Delete a schedule. Scoped by `user_id` so an id alone is not authorisation."""
    row = await session.scalar(
        select(ScheduledReport).where(
            ScheduledReport.id == schedule_id,
            ScheduledReport.user_id == user_id,
        )
    )
    if row is None:
        return False
    await session.delete(row)
    await session.commit()
    return True
