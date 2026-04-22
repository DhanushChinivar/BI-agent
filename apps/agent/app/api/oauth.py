"""OAuth2 initiation and callback endpoints for Google (Sheets + Gmail) and Notion."""
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


# ── Notion ─────────────────────────────────────────────────────────────────────

@router.get("/notion/start")
async def notion_start(user_id: str = Query(...)):
    settings = get_settings()
    state = secrets.token_urlsafe(16)
    _pending[state] = {"user_id": user_id, "connector": "notion"}
    auth_url = (
        f"https://api.notion.com/v1/oauth/authorize"
        f"?client_id={settings.notion_oauth_client_id}"
        f"&response_type=code"
        f"&owner=user"
        f"&redirect_uri={settings.notion_redirect_uri}"
        f"&state={state}"
    )
    return RedirectResponse(auth_url)


@router.get("/notion/callback")
async def notion_callback(code: str = Query(...), state: str = Query(...)):
    meta = _pending.pop(state, None)
    if not meta:
        raise HTTPException(status_code=400, detail="Invalid or expired OAuth state")

    import base64
    import httpx

    settings = get_settings()
    credentials = base64.b64encode(
        f"{settings.notion_oauth_client_id}:{settings.notion_oauth_client_secret}".encode()
    ).decode()

    async with httpx.AsyncClient() as client:
        resp = await client.post(
            "https://api.notion.com/v1/oauth/token",
            headers={"Authorization": f"Basic {credentials}", "Content-Type": "application/json"},
            json={"grant_type": "authorization_code", "code": code, "redirect_uri": settings.notion_redirect_uri},
        )
    if resp.status_code != 200:
        raise HTTPException(status_code=502, detail="Notion token exchange failed")

    token_data = resp.json()
    factory = get_session_factory()
    async with factory() as session:
        await upsert_credentials(session, meta["user_id"], "notion", {
            "access_token": token_data["access_token"],
            "workspace_id": token_data.get("workspace_id"),
        })

    return RedirectResponse(f"{settings.frontend_url}/connect?connected=notion")
