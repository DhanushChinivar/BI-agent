"""Notion connector using the official notion-client SDK."""
from typing import Any

from notion_client import AsyncClient

from app.config.settings import get_settings
from app.db.crud import get_credentials
from app.db.engine import get_session_factory


async def _get_token(user_id: str) -> str:
    """Return per-user OAuth token, fall back to shared integration key."""
    factory = get_session_factory()
    async with factory() as session:
        data = await get_credentials(session, user_id, "notion")
    if data:
        return data["access_token"]
    key = get_settings().notion_api_key
    if key:
        return key
    raise PermissionError(f"No Notion credentials for user {user_id!r}")


def _page_title(page: dict) -> str:
    props = page.get("properties", {})
    for prop in props.values():
        if prop.get("type") == "title":
            parts = prop.get("title", [])
            return "".join(p.get("plain_text", "") for p in parts)
    return page.get("id", "untitled")


class NotionConnector:
    name = "notion"

    async def list_resources(self, user_id: str) -> list[dict[str, Any]]:
        token = await _get_token(user_id)
        client = AsyncClient(auth=token)
        results = await client.search(filter={"property": "object", "value": "page"}, page_size=50)
        return [
            {
                "id": p["id"],
                "title": _page_title(p),
                "type": "notion_page",
                "url": p.get("url"),
            }
            for p in results.get("results", [])
        ]

    async def read(self, user_id: str, resource_id: str, **kwargs: Any) -> dict[str, Any]:
        token = await _get_token(user_id)
        client = AsyncClient(auth=token)
        page = await client.pages.retrieve(page_id=resource_id)
        blocks = await client.blocks.children.list(block_id=resource_id, page_size=100)

        content_parts: list[str] = []
        for block in blocks.get("results", []):
            btype = block.get("type", "")
            rich = block.get(btype, {}).get("rich_text", [])
            text = "".join(r.get("plain_text", "") for r in rich)
            if text:
                content_parts.append(text)

        return {
            "resource_id": resource_id,
            "title": _page_title(page),
            "url": page.get("url"),
            "content": "\n".join(content_parts),
        }

    async def search(self, user_id: str, query: str) -> list[dict[str, Any]]:
        token = await _get_token(user_id)
        client = AsyncClient(auth=token)
        results = await client.search(query=query, filter={"property": "object", "value": "page"}, page_size=20)
        return [
            {
                "id": p["id"],
                "title": _page_title(p),
                "type": "notion_page",
                "url": p.get("url"),
            }
            for p in results.get("results", [])
        ]
