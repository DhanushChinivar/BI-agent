"""OAuth2 initiation and callback endpoints for Google Sheets and Gmail."""
import logging
import secrets

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import RedirectResponse
from google_auth_oauthlib.flow import Flow

from app.config.settings import get_settings
from app.db.crud import upsert_credentials
from app.db.engine import get_session_factory

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/v1/oauth", tags=["oauth"])

_GOOGLE_SHEETS_SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets.readonly",
    "https://www.googleapis.com/auth/drive.readonly",
    "openid",
    "email",
]
_GOOGLE_GMAIL_SCOPES = [
    "https://www.googleapis.com/auth/gmail.readonly",
    "openid",
    "email",
]

# In-memory state store (dev only — use Redis in production)
_pending: dict[str, dict] = {}


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
async def google_sheets_start(user_id: str = Query(...)):
    settings = get_settings()
    flow = _google_flow(settings.google_sheets_redirect_uri, _GOOGLE_SHEETS_SCOPES)
    state = secrets.token_urlsafe(16)
    _pending[state] = {"user_id": user_id, "connector": "google_sheets"}
    auth_url, _ = flow.authorization_url(
        access_type="offline", include_granted_scopes="true", state=state, prompt="consent"
    )
    return RedirectResponse(auth_url)


@router.get("/google-sheets/callback")
async def google_sheets_callback(code: str = Query(...), state: str = Query(...)):
    meta = _pending.pop(state, None)
    if not meta:
        raise HTTPException(status_code=400, detail="Invalid or expired OAuth state")

    settings = get_settings()
    flow = _google_flow(settings.google_sheets_redirect_uri, _GOOGLE_SHEETS_SCOPES)
    flow.fetch_token(code=code)
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
async def gmail_start(user_id: str = Query(...)):
    settings = get_settings()
    flow = _google_flow(settings.google_gmail_redirect_uri, _GOOGLE_GMAIL_SCOPES)
    state = secrets.token_urlsafe(16)
    _pending[state] = {"user_id": user_id, "connector": "gmail"}
    auth_url, _ = flow.authorization_url(
        access_type="offline", include_granted_scopes="true", state=state, prompt="consent"
    )
    return RedirectResponse(auth_url)


@router.get("/gmail/callback")
async def gmail_callback(code: str = Query(...), state: str = Query(...)):
    meta = _pending.pop(state, None)
    if not meta:
        raise HTTPException(status_code=400, detail="Invalid or expired OAuth state")

    settings = get_settings()
    flow = _google_flow(settings.google_gmail_redirect_uri, _GOOGLE_GMAIL_SCOPES)
    flow.fetch_token(code=code)
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

