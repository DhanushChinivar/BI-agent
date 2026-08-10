"""Action node: persists the recurring reports and alerts the planner detected.

This used to talk to n8n's API directly, and it did not work. It PATCHed the
workflow with `{"active": True}` plus a copy of its own tags — the cron and the
question were never written anywhere — then POSTed to
`/api/v1/workflows/{id}/run`, an endpoint that does not exist in n8n's public
API. It returned `{"status": "scheduled"}` regardless, so the UI showed a
confirmation for a schedule that was never created.

The schedule now lives in Postgres. n8n keeps one ticker workflow that calls
`POST /v1/schedules/run-due`; see `app/api/schedules.py`.
"""
import structlog

from app.db.engine import get_session_factory
from app.db.schedule_crud import InvalidCronError, upsert_schedule
from app.graph.state import AgentState

log = structlog.get_logger()

_SUPPORTED = {"schedule_report", "data_alert"}

# What the planner emits when the user says "every week" without saying when.
_DEFAULT_CRON = "0 8 * * 1"  # Mondays, 08:00 UTC


async def action_node(state: AgentState) -> dict:
    action_type = state.get("action_type")
    bound = log.bind(
        node="action",
        conversation_id=state.get("conversation_id"),
        user_id=state.get("user_id"),
    )

    if not action_type:
        return {"schedule_result": None}

    if action_type not in _SUPPORTED:
        bound.warning("unknown_action", action_type=action_type)
        return {
            "schedule_result": {"status": "error", "reason": f"unknown action: {action_type}"}
        }

    user_id = state.get("user_id")
    if not user_id or user_id == "anonymous":
        # A schedule with no owner has nobody to deliver to and nobody to bill.
        return {
            "schedule_result": {"status": "error", "reason": "sign in to schedule a report"}
        }

    # Fall back to the question that produced this turn: "run this every Monday"
    # carries no question of its own.
    question = (state.get("action_question") or "").strip()
    if not question:
        from app.graph.message_utils import last_human_message

        question = (last_human_message(state.get("messages", [])) or "").strip()
    if not question:
        return {"schedule_result": {"status": "error", "reason": "no question to schedule"}}

    cron = (state.get("action_cron") or _DEFAULT_CRON).strip()

    try:
        factory = get_session_factory()
        async with factory() as session:
            row = await upsert_schedule(session, user_id, question, cron, action_type)
    except InvalidCronError as exc:
        # Surfaced rather than defaulted: silently rewriting a schedule the user
        # asked for is how you end up emailing someone at the wrong time
        # forever.
        bound.warning("bad_cron", cron=cron, error=str(exc))
        return {"schedule_result": {"status": "error", "reason": str(exc)}}
    except ValueError as exc:  # schedule limit
        bound.warning("schedule_rejected", error=str(exc))
        return {"schedule_result": {"status": "error", "reason": str(exc)}}
    except Exception as exc:
        bound.error("schedule_failed", error=str(exc))
        return {"schedule_result": {"status": "error", "reason": "could not save schedule"}}

    bound.info("scheduled", schedule_id=row.id, cron=row.cron, next_run=row.next_run_at)
    return {
        "schedule_result": {
            "status": "scheduled",
            "schedule_id": row.id,
            "workflow": action_type,
            "cron": row.cron,
            "next_run_at": row.next_run_at.isoformat(),
        }
    }
