"""Notion connector using the Notion API v1 via httpx."""
from typing import Any

import httpx

from app.db.crud import get_credentials
from app.db.engine import get_session_factory

_BASE = "https://api.notion.com/v1"
_NOTION_VERSION = "2022-06-28"


def _extract_title(obj: dict) -> str:
    for prop in obj.get("properties", {}).values():
        if prop.get("type") == "title":
            return "".join(t.get("plain_text", "") for t in prop.get("title", []))
    return ""


def _blocks_to_text(blocks: list[dict]) -> str:
    lines = []
    for block in blocks:
        block_type = block.get("type", "")
        rich_text = block.get(block_type, {}).get("rich_text", [])
        text = "".join(t.get("plain_text", "") for t in rich_text)
        if text:
            lines.append(text)
    return "\n".join(lines)


class NotionConnector:
    name = "notion"

    async def _token(self, user_id: str) -> str:
        factory = get_session_factory()
        async with factory() as session:
            data = await get_credentials(session, user_id, self.name)
        if not data:
            raise PermissionError(f"No Notion credentials for user {user_id!r}")
        return data["access_token"]

    def _client(self, token: str) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            base_url=_BASE,
            headers={"Authorization": f"Bearer {token}", "Notion-Version": _NOTION_VERSION},
            timeout=15,
        )

    async def list_resources(self, user_id: str) -> list[dict[str, Any]]:
        token = await self._token(user_id)
        async with self._client(token) as client:
            resp = await client.post(
                "/search",
                json={"filter": {"value": "page", "property": "object"}, "page_size": 20},
            )
            if resp.status_code != 200:
                return [{"error": resp.text}]
            results = resp.json().get("results", [])

        return [
            {"id": p["id"], "title": _extract_title(p) or "(untitled)", "type": "notion_page", "url": p.get("url", "")}
            for p in results
        ]

    async def read(self, user_id: str, resource_id: str, **kwargs: Any) -> dict[str, Any]:
        token = await self._token(user_id)
        async with self._client(token) as client:
            page_resp = await client.get(f"/pages/{resource_id}")
            if page_resp.status_code != 200:
                return {"error": page_resp.text, "resource_id": resource_id}
            page = page_resp.json()

            blocks_resp = await client.get(f"/blocks/{resource_id}/children")
            blocks = blocks_resp.json().get("results", []) if blocks_resp.status_code == 200 else []

        return {
            "resource_id": resource_id,
            "title": _extract_title(page),
            "content": _blocks_to_text(blocks),
        }

    async def search(self, user_id: str, query: str) -> list[dict[str, Any]]:
        token = await self._token(user_id)
        async with self._client(token) as client:
            # Filter to pages, as `list_resources` does: `read` resolves a
            # resource with /pages/{id}, so an unfiltered search returning a
            # database would hand back an id that cannot be read.
            resp = await client.post(
                "/search",
                json={
                    "query": query,
                    "filter": {"value": "page", "property": "object"},
                    "page_size": 10,
                },
            )
            if resp.status_code != 200:
                return [{"error": resp.text}]
            results = resp.json().get("results", [])

        return [
            {"id": r["id"], "title": _extract_title(r) or "(untitled)", "type": "notion_page", "url": r.get("url", "")}
            for r in results
        ]
