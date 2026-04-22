"""Gmail connector using the Gmail v1 API."""
import base64
from typing import Any

from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

from app.connectors.google_auth import get_google_credentials
from app.db.crud import get_credentials
from app.db.engine import get_session_factory


def _decode_body(payload: dict) -> str:
    body = payload.get("body", {}).get("data", "")
    if body:
        return base64.urlsafe_b64decode(body + "==").decode("utf-8", errors="replace")
    for part in payload.get("parts", []):
        if part.get("mimeType") == "text/plain":
            data = part.get("body", {}).get("data", "")
            if data:
                return base64.urlsafe_b64decode(data + "==").decode("utf-8", errors="replace")
    return ""


def _header(headers: list[dict], name: str) -> str:
    return next((h["value"] for h in headers if h["name"].lower() == name.lower()), "")


class GmailConnector:
    name = "gmail"

    async def _creds(self, user_id: str) -> dict:
        factory = get_session_factory()
        async with factory() as session:
            data = await get_credentials(session, user_id, self.name)
        if not data:
            raise PermissionError(f"No Gmail credentials for user {user_id!r}")
        return data

    async def list_resources(self, user_id: str) -> list[dict[str, Any]]:
        """Return the 20 most recent message thread summaries."""
        creds_data = await self._creds(user_id)
        creds = await get_google_credentials(user_id, self.name, creds_data)
        service = build("gmail", "v1", credentials=creds, cache_discovery=False)
        try:
            threads = service.users().threads().list(userId="me", maxResults=20).execute()
        except HttpError as exc:
            return [{"error": str(exc)}]

        resources = []
        for thread in threads.get("threads", []):
            t = service.users().threads().get(userId="me", id=thread["id"], format="metadata").execute()
            first_msg = t["messages"][0]
            headers = first_msg.get("payload", {}).get("headers", [])
            resources.append({
                "id": thread["id"],
                "title": _header(headers, "Subject") or "(no subject)",
                "from": _header(headers, "From"),
                "date": _header(headers, "Date"),
                "type": "gmail_thread",
            })
        return resources

    async def read(self, user_id: str, resource_id: str, **kwargs: Any) -> dict[str, Any]:
        creds_data = await self._creds(user_id)
        creds = await get_google_credentials(user_id, self.name, creds_data)
        service = build("gmail", "v1", credentials=creds, cache_discovery=False)
        try:
            thread = service.users().threads().get(userId="me", id=resource_id, format="full").execute()
        except HttpError as exc:
            return {"error": str(exc), "resource_id": resource_id}

        messages = []
        for msg in thread.get("messages", []):
            headers = msg.get("payload", {}).get("headers", [])
            messages.append({
                "id": msg["id"],
                "from": _header(headers, "From"),
                "date": _header(headers, "Date"),
                "subject": _header(headers, "Subject"),
                "body": _decode_body(msg.get("payload", {}))[:2000],
            })

        return {"resource_id": resource_id, "messages": messages}

    async def search(self, user_id: str, query: str) -> list[dict[str, Any]]:
        creds_data = await self._creds(user_id)
        creds = await get_google_credentials(user_id, self.name, creds_data)
        service = build("gmail", "v1", credentials=creds, cache_discovery=False)
        try:
            result = service.users().messages().list(userId="me", q=query, maxResults=10).execute()
        except HttpError as exc:
            return [{"error": str(exc)}]

        resources = []
        for msg in result.get("messages", []):
            m = service.users().messages().get(userId="me", id=msg["id"], format="metadata").execute()
            headers = m.get("payload", {}).get("headers", [])
            resources.append({
                "id": msg["id"],
                "title": _header(headers, "Subject") or "(no subject)",
                "from": _header(headers, "From"),
                "date": _header(headers, "Date"),
                "type": "gmail_message",
            })
        return resources
