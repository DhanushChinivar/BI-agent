"""Connector that reads user-uploaded files from Redis storage."""
from typing import Any

from app.upload.storage import get_file, list_files


class CsvUploadConnector:
    name = "csv_upload"

    async def list_resources(self, user_id: str) -> list[dict[str, Any]]:
        files = await list_files(user_id)
        return [
            {"id": f["file_id"], "title": f["title"], "type": "upload"}
            for f in files
        ]

    async def read(self, user_id: str, resource_id: str, **kwargs: Any) -> dict[str, Any]:
        data = await get_file(user_id, resource_id)
        if data is None:
            return {"error": f"Uploaded file {resource_id!r} not found"}
        return data

    async def search(self, user_id: str, query: str) -> list[dict[str, Any]]:
        files = await list_files(user_id)
        q = query.lower()
        return [
            {"id": f["file_id"], "title": f["title"], "type": "upload"}
            for f in files
            if q in f["title"].lower()
        ]
