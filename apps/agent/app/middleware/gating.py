"""Gating middleware — enforces per-plan query limits on pipeline endpoints."""
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from app.db.engine import get_session_factory
from app.db.plan_crud import check_and_increment

_GATED_PATHS = {"/v1/query", "/v1/query/stream"}


class GatingMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next) -> Response:
        if request.method == "POST" and request.url.path in _GATED_PATHS:
            user_id = getattr(request.state, "user_id", "anonymous")
            factory = get_session_factory()
            async with factory() as session:
                allowed, reason = await check_and_increment(session, user_id)
            if not allowed:
                return JSONResponse({"detail": reason}, status_code=402)

        return await call_next(request)
