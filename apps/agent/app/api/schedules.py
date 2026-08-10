"""Scheduled-report endpoints.

`/v1/schedules/run-due` is the one n8n calls; the rest are for the UI. n8n owns
a single ticker workflow that pokes this every few minutes and knows nothing
about which reports exist — the alternative, a workflow per schedule created
through n8n's API, puts the schedule list somewhere the app cannot read and
loses every schedule if the n8n volume is recreated.
"""
import hashlib
import hmac
import logging
import uuid

from fastapi import APIRouter, Header, HTTPException, Request
from pydantic import BaseModel, Field

from app.config.settings import get_settings
from app.db.engine import get_session_factory
from app.db.plan_crud import check_and_increment
from app.db.schedule_crud import (
    InvalidCronError,
    claim_due,
    delete_schedule,
    list_schedules,
    record_run,
    upsert_schedule,
    validate_cron,
)
from app.graph.builder import graph
from app.graph.state import AgentState
from app.middleware.gating import refund

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/v1", tags=["schedules"])

# A tick that claims more than this defers the rest to the next tick rather than
# holding one HTTP request open for an unbounded number of pipeline runs.
_MAX_PER_TICK = 10


class ScheduleCreate(BaseModel):
    question: str = Field(min_length=1, max_length=2000)
    cron: str = Field(min_length=1, max_length=64)
    action_type: str = "schedule_report"


def _as_dict(row) -> dict:
    return {
        "id": row.id,
        "question": row.question,
        "cron": row.cron,
        "action_type": row.action_type,
        "active": row.active,
        "next_run_at": row.next_run_at.isoformat() if row.next_run_at else None,
        "last_run_at": row.last_run_at.isoformat() if row.last_run_at else None,
        "last_status": row.last_status,
        "last_error": row.last_error,
    }


# ── user-facing ───────────────────────────────────────────────────────────────

@router.get("/schedules")
async def get_schedules(request: Request) -> dict:
    factory = get_session_factory()
    async with factory() as session:
        rows = await list_schedules(session, request.state.user_id)
    return {"schedules": [_as_dict(r) for r in rows]}


@router.post("/schedules", status_code=201)
async def create_schedule(body: ScheduleCreate, request: Request) -> dict:
    try:
        validate_cron(body.cron)
    except InvalidCronError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    factory = get_session_factory()
    async with factory() as session:
        try:
            row = await upsert_schedule(
                session,
                request.state.user_id,
                body.question,
                body.cron,
                body.action_type,
            )
        except ValueError as exc:  # schedule limit
            raise HTTPException(status_code=409, detail=str(exc)) from exc
    return _as_dict(row)


@router.delete("/schedules/{schedule_id}")
async def remove_schedule(schedule_id: str, request: Request) -> dict:
    factory = get_session_factory()
    async with factory() as session:
        # Scoped by the verified user: an id alone is not authorisation.
        deleted = await delete_schedule(session, request.state.user_id, schedule_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Schedule not found")
    return {"id": schedule_id, "deleted": True}


# ── the ticker ────────────────────────────────────────────────────────────────

def _verify_signature(body: bytes, signature: str, secret: str) -> bool:
    if not signature.startswith("sha256="):
        return False
    expected = "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature)


async def _run_one(row) -> dict:
    """Run one due schedule through the pipeline, charging its owner.

    Quota is charged here for the same reason `/v1/webhooks/n8n` charges in its
    handler: this route is auth-exempt, so `request.state.user_id` is the
    placeholder "system" and metering it would bill every user's reports to one
    shared counter.
    """
    factory = get_session_factory()
    async with factory() as session:
        allowed, reason = await check_and_increment(session, row.user_id)

    if not allowed:
        async with factory() as session:
            await record_run(session, row.id, "error", reason)
        return {"id": row.id, "status": "skipped", "reason": reason}

    try:
        state: AgentState = {
            "messages": [{"role": "human", "content": row.question}],
            "user_id": row.user_id,
            "conversation_id": str(uuid.uuid4()),
        }
        final_state = await graph.ainvoke(state)
    except Exception as exc:
        logger.exception("Scheduled report %s failed", row.id)
        await refund(row.user_id, "scheduled report failed")
        async with factory() as session:
            await record_run(session, row.id, "error", str(exc)[:500])
        return {"id": row.id, "status": "error"}

    async with factory() as session:
        await record_run(session, row.id, "ok", None)

    return {
        "id": row.id,
        "status": "ok",
        "answer": final_state.get("final_answer", ""),
        "user_id": row.user_id,
        "question": row.question,
    }


@router.post("/schedules/run-due")
async def run_due(
    request: Request,
    x_hub_signature_256: str = Header(alias="x-hub-signature-256", default=""),
) -> dict:
    """Run every schedule that is due. Called by the n8n ticker workflow.

    Authenticated by the same HMAC as the n8n webhook — it is unauthenticated as
    far as Clerk is concerned, and it runs LLM work on behalf of arbitrary users,
    so an unsigned caller could drain every account's quota.
    """
    settings = get_settings()
    body = await request.body()
    if not _verify_signature(body, x_hub_signature_256, settings.webhook_secret):
        raise HTTPException(status_code=401, detail="Invalid webhook signature")

    factory = get_session_factory()
    async with factory() as session:
        # Claiming advances next_run_at and locks the rows, so an overlapping
        # tick picks up nothing rather than sending a second copy of the report.
        due = await claim_due(session, _MAX_PER_TICK)

    if not due:
        return {"ran": 0, "results": []}

    logger.info("Running %d due scheduled report(s)", len(due))
    results = [await _run_one(row) for row in due]

    return {"ran": len(results), "results": results}
