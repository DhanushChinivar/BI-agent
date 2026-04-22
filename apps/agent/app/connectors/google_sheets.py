"""Google Sheets connector using the Sheets v4 API."""
from typing import Any

from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

from app.db.crud import get_credentials
from app.db.engine import get_session_factory

_SCOPES = ["https://www.googleapis.com/auth/spreadsheets.readonly"]


def _build_service(creds_data: dict):
    creds = Credentials(
        token=creds_data.get("access_token"),
        refresh_token=creds_data.get("refresh_token"),
        token_uri="https://oauth2.googleapis.com/token",
        client_id=creds_data.get("client_id"),
        client_secret=creds_data.get("client_secret"),
        scopes=_SCOPES,
    )
    return build("sheets", "v4", credentials=creds, cache_discovery=False)


class GoogleSheetsConnector:
    name = "google_sheets"

    async def _creds(self, user_id: str) -> dict:
        factory = get_session_factory()
        async with factory() as session:
            data = await get_credentials(session, user_id, self.name)
        if not data:
            raise PermissionError(f"No Google Sheets credentials for user {user_id!r}")
        return data

    async def list_resources(self, user_id: str) -> list[dict[str, Any]]:
        creds_data = await self._creds(user_id)
        drive = build(
            "drive",
            "v3",
            credentials=Credentials(
                token=creds_data.get("access_token"),
                refresh_token=creds_data.get("refresh_token"),
                token_uri="https://oauth2.googleapis.com/token",
                client_id=creds_data.get("client_id"),
                client_secret=creds_data.get("client_secret"),
            ),
            cache_discovery=False,
        )
        result = (
            drive.files()
            .list(
                q="mimeType='application/vnd.google-apps.spreadsheet'",
                fields="files(id,name,modifiedTime)",
                pageSize=50,
            )
            .execute()
        )
        return [
            {"id": f["id"], "title": f["name"], "type": "spreadsheet", "modified": f.get("modifiedTime")}
            for f in result.get("files", [])
        ]

    async def read(self, user_id: str, resource_id: str, **kwargs: Any) -> dict[str, Any]:
        creds_data = await self._creds(user_id)
        service = _build_service(creds_data)
        range_name = kwargs.get("range", "A1:Z1000")
        try:
            meta = service.spreadsheets().get(spreadsheetId=resource_id).execute()
            values_resp = (
                service.spreadsheets()
                .values()
                .get(spreadsheetId=resource_id, range=range_name)
                .execute()
            )
        except HttpError as exc:
            return {"error": str(exc), "resource_id": resource_id}

        rows = values_resp.get("values", [])
        if rows:
            headers = rows[0]
            data = [dict(zip(headers, row)) for row in rows[1:]]
        else:
            data = []

        return {
            "resource_id": resource_id,
            "title": meta.get("properties", {}).get("title", resource_id),
            "rows": data,
        }

    async def search(self, user_id: str, query: str) -> list[dict[str, Any]]:
        resources = await self.list_resources(user_id)
        q = query.lower()
        return [r for r in resources if q in r["title"].lower()]
