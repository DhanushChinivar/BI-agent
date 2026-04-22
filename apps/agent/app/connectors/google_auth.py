"""Shared Google OAuth2 credential helper with automatic token refresh."""
import logging

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials

from app.db.crud import upsert_credentials
from app.db.engine import get_session_factory

logger = logging.getLogger(__name__)


async def get_google_credentials(user_id: str, connector: str, creds_data: dict) -> Credentials:
    """Return a valid Credentials object, refreshing the access token if expired."""
    creds = Credentials(
        token=creds_data.get("access_token"),
        refresh_token=creds_data.get("refresh_token"),
        token_uri="https://oauth2.googleapis.com/token",
        client_id=creds_data.get("client_id"),
        client_secret=creds_data.get("client_secret"),
    )

    if creds.expired and creds.refresh_token:
        logger.info("Refreshing Google token for user=%s connector=%s", user_id, connector)
        creds.refresh(Request())
        factory = get_session_factory()
        async with factory() as session:
            await upsert_credentials(session, user_id, connector, {
                **creds_data,
                "access_token": creds.token,
            })

    return creds
