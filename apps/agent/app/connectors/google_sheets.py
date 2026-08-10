"""Google Sheets connector using the Sheets v4 API."""
from typing import Any

from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

from app.connectors.google_auth import get_google_credentials
from app.db.crud import get_credentials
from app.db.engine import get_session_factory

_SHEET_MIME = "mimeType='application/vnd.google-apps.spreadsheet'"
_PAGE_SIZE = 50
_MAX_QUERY_TERMS = 5
# Ceiling on a single read. High enough that ordinary business sheets arrive
# whole — the previous 999-row clip lost a quarter of a year's transactions —
# and low enough to bound the Redis payload and the in-memory frame.
_MAX_READ_ROWS = 10_000
_FALLBACK_RANGE = "A1:Z10000"


def _escape(term: str) -> str:
    """Escape a term for a Drive query string literal."""
    return term.replace("\\", "\\\\").replace("'", "\\'")


def _as_resource(f: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": f["id"],
        "title": f["name"],
        "type": "spreadsheet",
        "modified": f.get("modifiedTime"),
    }


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
        creds = await get_google_credentials(user_id, self.name, creds_data)
        drive = build("drive", "v3", credentials=creds, cache_discovery=False)
        result = (
            drive.files()
            .list(
                q=_SHEET_MIME,
                fields="files(id,name,modifiedTime)",
                pageSize=_PAGE_SIZE,
            )
            .execute()
        )
        return [_as_resource(f) for f in result.get("files", [])]

    async def read(self, user_id: str, resource_id: str, **kwargs: Any) -> dict[str, Any]:
        creds_data = await self._creds(user_id)
        creds = await get_google_credentials(user_id, self.name, creds_data)
        service = build("sheets", "v4", credentials=creds, cache_discovery=False)
        try:
            meta = service.spreadsheets().get(spreadsheetId=resource_id).execute()
            # Default to the first tab's *name* as the range, which returns every
            # populated cell. The previous "A1:Z1000" silently dropped everything
            # from row 1000 on — on an 1,800-row transaction log that is a whole
            # quarter missing, with a total that still looks authoritative.
            tabs = meta.get("sheets", [])
            first_tab = tabs[0]["properties"]["title"] if tabs else _FALLBACK_RANGE
            range_name = kwargs.get("range") or first_tab
            values_resp = (
                service.spreadsheets()
                .values()
                .get(spreadsheetId=resource_id, range=range_name)
                .execute()
            )
        except HttpError as exc:
            return {"error": str(exc), "resource_id": resource_id}

        rows = values_resp.get("values", [])
        dropped = 0
        if rows:
            headers = rows[0]
            body = rows[1:]
            dropped = max(0, len(body) - _MAX_READ_ROWS)
            # strict=False is deliberate: trailing empty cells make a row shorter
            # than the header, and the short row is the one we want to keep.
            data = [dict(zip(headers, row, strict=False)) for row in body[:_MAX_READ_ROWS]]
        else:
            data = []

        result = {
            "resource_id": resource_id,
            "title": meta.get("properties", {}).get("title", resource_id),
            "rows": data,
        }
        if dropped:
            result["truncated_rows"] = dropped
        return result

    async def search(self, user_id: str, query: str) -> list[dict[str, Any]]:
        """Full-text search across the user's spreadsheets.

        Drive indexes cell contents, so this matches a sheet whose *data* is
        relevant even when its title is not. The previous implementation
        filtered the same 50-file listing by title substring, which no
        multi-word query could ever match.

        Terms are ANDed rather than passed as one string: in Drive,
        `fullText contains 'a b'` is an exact-phrase match, so a multi-word
        query has to be split to behave like a search. AND can still be too
        strict — the retriever falls back to `list_resources` when this returns
        nothing, which is the recall floor.
        """
        terms = query.split()[:_MAX_QUERY_TERMS]
        if not terms:
            return []

        creds_data = await self._creds(user_id)
        creds = await get_google_credentials(user_id, self.name, creds_data)
        drive = build("drive", "v3", credentials=creds, cache_discovery=False)
        clauses = " and ".join(f"fullText contains '{_escape(t)}'" for t in terms)
        try:
            result = (
                drive.files()
                .list(
                    q=f"{_SHEET_MIME} and {clauses}",
                    fields="files(id,name,modifiedTime)",
                    pageSize=_PAGE_SIZE,
                )
                .execute()
            )
        except HttpError as exc:
            return [{"error": str(exc)}]

        return [_as_resource(f) for f in result.get("files", [])]
