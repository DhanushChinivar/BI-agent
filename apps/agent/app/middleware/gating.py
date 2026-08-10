"""Gating middleware — enforces per-plan query limits on pipeline endpoints."""
import logging

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from app.db.engine import get_session_factory
from app.db.plan_crud import check_and_increment, refund_query

logger = logging.getLogger(__name__)

# Routes where the caller's identity is on `request.state`, having been verified
# from a Clerk JWT. `/v1/webhooks/n8n` runs the same pipeline and must also be
# gated, but it is auth-exempt — at this point its `user_id` is the placeholder
# "system", and charging that would meter every scheduled report in the
# deployment against one shared counter. It charges the payload's real user from
# inside its handler instead.
_GATED_PATHS = {"/v1/query", "/v1/query/stream"}


class GatingMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next) -> Response:
        if not (request.method == "POST" and request.url.path in _GATED_PATHS):
            return await call_next(request)

        user_id = getattr(request.state, "user_id", "anonymous")
        factory = get_session_factory()
        async with factory() as session:
            allowed, reason = await check_and_increment(session, user_id)
        if not allowed:
            return JSONResponse({"detail": reason}, status_code=402)

        # The quota is spent up front because the check is what gates the work.
        # Anything the user never got an answer to is refunded below.
        request.state.quota_charged_for = user_id

        try:
            response = await call_next(request)
        except Exception:
            await refund(user_id, "unhandled exception")
            raise

        # Only refunds what this middleware charged, and only for errors that are
        # ours. A 4xx is the caller's mistake and still consumed a slot's worth
        # of validation, while a 5xx means the pipeline owed an answer and failed.
        #
        # The streaming endpoint cannot be judged here: it returns 200 as soon as
        # the SSE response opens, long before the pipeline can fail. That path
        # refunds itself from inside the generator.
        if response.status_code >= 500:
            await refund(user_id, f"status {response.status_code}")

        return response


async def refund(user_id: str, reason: str) -> None:
    """Return one query to `user_id`. Never raises — a failed refund must not
    turn a recoverable error into a second one."""
    try:
        factory = get_session_factory()
        async with factory() as session:
            await refund_query(session, user_id)
        logger.info("Refunded one query to user=%s (%s)", user_id, reason)
    except Exception as exc:
        logger.error("Quota refund failed for user=%s: %s", user_id, exc)
