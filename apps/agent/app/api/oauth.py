"""OAuth2 initiation and callback endpoints for Google Sheets, Gmail, and Notion."""
import asyncio
import base64
import logging
import secrets

import httpx
from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import RedirectResponse
from google_auth_oauthlib.flow import Flow
from redis.exceptions import RedisError

from app.cache import oauth_state_put, oauth_state_take
from app.config.settings import get_settings
from app.db.crud import upsert_credentials
from app.db.engine import get_session_factory

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/v1/oauth", tags=["oauth"])

_GOOGLE_SHEETS_SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets.readonly",
    "https://www.googleapis.com/auth/drive.readonly",
    "openid",
    "https://www.googleapis.com/auth/userinfo.email",
]
_GOOGLE_GMAIL_SCOPES = [
    "https://www.googleapis.com/auth/gmail.readonly",
    "openid",
    "https://www.googleapis.com/auth/userinfo.email",
]

async def _take_state(state: str, connector: str) -> dict:
    """Redeem a pending OAuth state, or 400.

    The connector is re-checked because each callback rebuilds a `Flow` with its
    own scopes and redirect URI: a state minted by /gmail/start must not be
    redeemable at /google-sheets/callback, which would store Gmail's tokens
    under the Sheets connector.
    """
    try:
        meta = await oauth_state_take(state)
    except RedisError as exc:
        # Fail closed and say so. Swallowing this would turn a cache outage into
        # "invalid state", sending the user round the authorise loop forever.
        logger.error("OAuth state store unavailable: %s", exc)
        raise HTTPException(status_code=503, detail="Authorization store unavailable")

    if not meta or meta.get("connector") != connector:
        raise HTTPException(status_code=400, detail="Invalid or expired OAuth state")
    return meta


def _google_flow(redirect_uri: str, scopes: list[str]) -> Flow:
    settings = get_settings()
    return Flow.from_client_config(
        {
            "web": {
                "client_id": settings.google_client_id,
                "client_secret": settings.google_client_secret,
                "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                "token_uri": "https://oauth2.googleapis.com/token",
                "redirect_uris": [redirect_uri],
            }
        },
        scopes=scopes,
        redirect_uri=redirect_uri,
    )


# ── Google Sheets ──────────────────────────────────────────────────────────────

@router.get("/google-sheets/start")
async def google_sheets_start(request: Request):
    # Bind to the identity AuthMiddleware verified from the Clerk JWT — never a
    # query param, which the caller controls and could set to a victim's id.
    user_id = request.state.user_id
    settings = get_settings()
    flow = _google_flow(settings.google_sheets_redirect_uri, _GOOGLE_SHEETS_SCOPES)
    state = secrets.token_urlsafe(16)
    auth_url, _ = flow.authorization_url(
        access_type="offline", include_granted_scopes="true", state=state, prompt="consent"
    )
    await oauth_state_put(state, {"user_id": user_id, "connector": "google_sheets"})
    return RedirectResponse(auth_url)


@router.get("/google-sheets/callback")
async def google_sheets_callback(code: str = Query(...), state: str = Query(...)):
    meta = await _take_state(state, "google_sheets")

    settings = get_settings()
    # Rebuilt rather than carried across the redirect: a Flow holds a live
    # session and is not serialisable, and it is fully determined by the
    # redirect URI and scopes, which are config.
    flow = _google_flow(settings.google_sheets_redirect_uri, _GOOGLE_SHEETS_SCOPES)
    # google-auth-oauthlib exchanges the code over a blocking socket; run it off
    # the event loop so one slow token endpoint does not stall every other request.
    await asyncio.to_thread(flow.fetch_token, code=code)
    creds = flow.credentials

    factory = get_session_factory()
    async with factory() as session:
        await upsert_credentials(session, meta["user_id"], "google_sheets", {
            "access_token": creds.token,
            "refresh_token": creds.refresh_token,
            "client_id": settings.google_client_id,
            "client_secret": settings.google_client_secret,
        })

    return RedirectResponse(f"{settings.frontend_url}/connect?connected=google_sheets")


# ── Google Gmail ───────────────────────────────────────────────────────────────

@router.get("/gmail/start")
async def gmail_start(request: Request):
    user_id = request.state.user_id
    settings = get_settings()
    flow = _google_flow(settings.google_gmail_redirect_uri, _GOOGLE_GMAIL_SCOPES)
    state = secrets.token_urlsafe(16)
    auth_url, _ = flow.authorization_url(
        access_type="offline", include_granted_scopes="true", state=state, prompt="consent"
    )
    await oauth_state_put(state, {"user_id": user_id, "connector": "gmail"})
    return RedirectResponse(auth_url)


@router.get("/gmail/callback")
async def gmail_callback(code: str = Query(...), state: str = Query(...)):
    meta = await _take_state(state, "gmail")

    settings = get_settings()
    flow = _google_flow(settings.google_gmail_redirect_uri, _GOOGLE_GMAIL_SCOPES)
    # google-auth-oauthlib exchanges the code over a blocking socket; run it off
    # the event loop so one slow token endpoint does not stall every other request.
    await asyncio.to_thread(flow.fetch_token, code=code)
    creds = flow.credentials

    factory = get_session_factory()
    async with factory() as session:
        await upsert_credentials(session, meta["user_id"], "gmail", {
            "access_token": creds.token,
            "refresh_token": creds.refresh_token,
            "client_id": settings.google_client_id,
            "client_secret": settings.google_client_secret,
        })

    return RedirectResponse(f"{settings.frontend_url}/connect?connected=gmail")


# ── Notion ─────────────────────────────────────────────────────────────────────

@router.get("/notion/start")
async def notion_start(request: Request):
    user_id = request.state.user_id
    settings = get_settings()
    if not settings.notion_client_id:
        raise HTTPException(status_code=501, detail="Notion OAuth not configured")
    state = secrets.token_urlsafe(16)
    await oauth_state_put(state, {"user_id": user_id, "connector": "notion"})
    auth_url = (
        f"https://api.notion.com/v1/oauth/authorize"
        f"?client_id={settings.notion_client_id}"
        f"&response_type=code"
        f"&owner=user"
        f"&redirect_uri={settings.notion_redirect_uri}"
        f"&state={state}"
    )
    return RedirectResponse(auth_url)


@router.get("/notion/callback")
async def notion_callback(code: str = Query(...), state: str = Query(...)):
    meta = await _take_state(state, "notion")

    settings = get_settings()
    credentials = base64.b64encode(
        f"{settings.notion_client_id}:{settings.notion_client_secret}".encode()
    ).decode()

    async with httpx.AsyncClient() as client:
        resp = await client.post(
            "https://api.notion.com/v1/oauth/token",
            headers={
                "Authorization": f"Basic {credentials}",
                "Content-Type": "application/json",
            },
            json={"grant_type": "authorization_code", "code": code, "redirect_uri": settings.notion_redirect_uri},
        )

    if resp.status_code != 200:
        logger.error("Notion token exchange failed: %s", resp.text)
        raise HTTPException(status_code=400, detail="Notion token exchange failed")

    data = resp.json()
    factory = get_session_factory()
    async with factory() as session:
        await upsert_credentials(session, meta["user_id"], "notion", {
            "access_token": data["access_token"],
            "workspace_id": data.get("workspace_id", ""),
            "workspace_name": data.get("workspace_name", ""),
        })

    return RedirectResponse(f"{settings.frontend_url}/connect?connected=notion")

