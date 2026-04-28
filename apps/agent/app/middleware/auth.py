"""Auth middleware.

Verifies the Clerk JWT passed by the Next.js BFF via Authorization: Bearer <token>.
Extracts user_id from the verified claims and writes it to request.state.user_id.

Falls back to "anonymous" in development when no token is present and APP_ENV != production.
Returns 401 in production if the token is missing or invalid.
"""
import logging
from functools import lru_cache

import httpx
from jose import JWTError, jwt
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from app.config.settings import get_settings

logger = logging.getLogger(__name__)


@lru_cache(maxsize=1)
def _jwks() -> dict:
    """Fetch Clerk's JWKS once and cache. Restart service to rotate keys."""
    settings = get_settings()
    url = f"https://{settings.clerk_frontend_api}/.well-known/jwks.json"
    resp = httpx.get(url, timeout=10)
    resp.raise_for_status()
    return resp.json()


def _verify_token(token: str) -> str:
    """Return user_id (sub claim) from a valid Clerk JWT. Raises JWTError on failure."""
    jwks = _jwks()
    header = jwt.get_unverified_header(token)
    key = next((k for k in jwks["keys"] if k["kid"] == header["kid"]), None)
    if key is None:
        raise JWTError("No matching key in JWKS")
    payload = jwt.decode(token, key, algorithms=["RS256"])
    return payload["sub"]


class AuthMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next) -> Response:
        settings = get_settings()
        token = request.headers.get("Authorization", "").removeprefix("Bearer ").strip()

        if token:
            try:
                request.state.user_id = _verify_token(token)
            except JWTError as exc:
                logger.warning("Invalid JWT: %s", exc)
                return JSONResponse({"detail": "Unauthorized"}, status_code=401)
        elif settings.app_env == "production":
            return JSONResponse({"detail": "Unauthorized"}, status_code=401)
        else:
            # Dev fallback — trust X-User-Id or use anonymous
            request.state.user_id = request.headers.get("X-User-Id", "anonymous")

        return await call_next(request)
