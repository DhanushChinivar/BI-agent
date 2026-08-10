"""Auth middleware.

Verifies the Clerk JWT passed by the Next.js BFF via Authorization: Bearer <token>.
Extracts user_id from the verified claims and writes it to request.state.user_id.

Falls back to "anonymous" in development when no token is present and APP_ENV != production.
Returns 401 in production if the token is missing or invalid.
"""
import asyncio
import logging
import time

import httpx
from jose import JWTError, jwt
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from app.config.settings import get_settings

logger = logging.getLogger(__name__)

# Callers that never carry a Clerk JWT because they authenticate by their own
# mechanism: OAuth callbacks are bound by the `state` parameter, the Stripe
# webhook by its signature header, the n8n webhook and both ticker routes by
# HMAC. Probes are unauthenticated by design. Without this set every one of them
# returns 401 under APP_ENV=production — see docs/DATAFLOW.md §11.
#
# `run-due` and `sync-due` are the *only* exempt paths under /v1/schedules and
# /v1/index; the per-user list, create, and delete routes stay behind the JWT.
_EXEMPT_PATHS = frozenset(
    {"/health", "/metrics", "/v1/schedules/run-due", "/v1/index/sync-due"}
)
_EXEMPT_PREFIXES = ("/v1/stripe/webhook", "/v1/webhooks/n8n")


def _is_exempt(path: str) -> bool:
    return (
        path in _EXEMPT_PATHS
        or path.startswith(_EXEMPT_PREFIXES)
        or (path.startswith("/v1/oauth/") and path.endswith("/callback"))
    )


_JWKS_TTL = 3600  # Clerk rotates signing keys rarely; an hour is well inside that.

# Guarded by `_jwks_lock` so a burst of requests on a cold cache issues one
# fetch, not one per request.
_jwks_cache: dict | None = None
_jwks_fetched_at = 0.0
_jwks_lock = asyncio.Lock()


def _issuer() -> str:
    """Clerk's `iss` claim: the Frontend API origin.

    CLERK_FRONTEND_API is documented as a bare host (`clerk.example.com`), but
    accepting a full origin costs nothing and a misconfigured `https://` prefix
    would otherwise produce `https://https://...` and reject every token.
    """
    host = get_settings().clerk_frontend_api.rstrip("/")
    return host if host.startswith("https://") else f"https://{host}"


async def _fetch_jwks() -> dict:
    """Fetch Clerk's JWKS over the running event loop.

    The previous version called `httpx.get` — a blocking socket read — from
    inside async middleware, stalling every other in-flight request for the
    duration of the round trip whenever the cache was cold or a key rotated.
    """
    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.get(f"{_issuer()}/.well-known/jwks.json")
        resp.raise_for_status()
        return resp.json()


async def _jwks(*, force: bool = False) -> dict:
    """Cached JWKS, refreshed after `_JWKS_TTL` or on demand.

    `force=True` is the key-rotation path: an unrecognised `kid` means Clerk
    signed with a key minted after our last fetch, so we refetch once instead of
    requiring a service restart.
    """
    global _jwks_cache, _jwks_fetched_at

    async with _jwks_lock:
        fresh = _jwks_cache is not None and time.monotonic() - _jwks_fetched_at < _JWKS_TTL
        if fresh and not force:
            return _jwks_cache  # type: ignore[return-value]
        _jwks_cache = await _fetch_jwks()
        _jwks_fetched_at = time.monotonic()
        return _jwks_cache


def _reset_jwks_cache() -> None:
    """Test hook — the module-level cache would otherwise leak between cases."""
    global _jwks_cache, _jwks_fetched_at
    _jwks_cache, _jwks_fetched_at = None, 0.0


def _signing_key(jwks: dict, kid: str) -> dict | None:
    return next((k for k in jwks.get("keys", []) if k.get("kid") == kid), None)


async def _verify_token(token: str) -> str:
    """Return user_id (sub claim) from a valid Clerk JWT. Raises JWTError on failure."""
    settings = get_settings()
    kid = jwt.get_unverified_header(token).get("kid")
    if not kid:
        raise JWTError("Token header has no kid")

    key = _signing_key(await _jwks(), kid)
    if key is None:
        key = _signing_key(await _jwks(force=True), kid)
    if key is None:
        raise JWTError("No matching key in JWKS")

    # Signature alone only proves *some* Clerk-signed token; without `iss` a
    # token minted by a different Clerk instance verifies here and its `sub`
    # becomes a user id in our database. Clerk session tokens carry no `aud`
    # unless a JWT template adds one, so that check stays opt-in.
    audience = settings.clerk_jwt_audience or None
    payload = jwt.decode(
        token,
        key,
        algorithms=["RS256"],
        issuer=_issuer(),
        audience=audience,
        options={"verify_aud": audience is not None},
    )

    # python-jose rejects a *mismatched* `aud` but accepts a token that has none
    # at all, so `audience=` alone leaves the check opt-out for any caller who
    # simply omits the claim. If an audience is configured, require it.
    if audience is not None and not payload.get("aud"):
        raise JWTError("Token has no aud claim")

    subject = payload.get("sub")
    if not subject:
        raise JWTError("Token has no sub claim")
    return subject


class AuthMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next) -> Response:
        settings = get_settings()

        # These routes verify their own caller; they must not be gated on a JWT
        # they will never have. `user_id` is still set so downstream code can
        # read request.state.user_id unconditionally.
        if _is_exempt(request.url.path):
            request.state.user_id = "system"
            return await call_next(request)

        token = request.headers.get("Authorization", "").removeprefix("Bearer ").strip()

        if token:
            try:
                request.state.user_id = await _verify_token(token)
            except JWTError as exc:
                logger.warning("Invalid JWT: %s", exc)
                return JSONResponse({"detail": "Unauthorized"}, status_code=401)
            except httpx.HTTPError as exc:
                # Clerk unreachable is our outage, not the caller's bad token.
                logger.error("JWKS fetch failed: %s", exc)
                return JSONResponse({"detail": "Auth backend unavailable"}, status_code=503)
        elif settings.app_env == "production":
            return JSONResponse({"detail": "Unauthorized"}, status_code=401)
        else:
            # Dev fallback — trust X-User-Id or use anonymous
            request.state.user_id = request.headers.get("X-User-Id", "anonymous")

        return await call_next(request)
