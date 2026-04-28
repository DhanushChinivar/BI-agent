"""GET /v1/plan/status — returns current user's plan and usage."""
from fastapi import APIRouter, Request

from app.db.engine import get_session_factory
from app.db.plan_crud import get_or_create_plan

router = APIRouter(prefix="/v1", tags=["billing"])


@router.get("/plan/status")
async def plan_status(request: Request) -> dict:
    user_id = request.state.user_id
    factory = get_session_factory()
    async with factory() as session:
        row = await get_or_create_plan(session, user_id)
    return {
        "user_id": user_id,
        "plan": row.plan,
        "queries_today": row.queries_today,
        "stripe_customer_id": row.stripe_customer_id,
    }
