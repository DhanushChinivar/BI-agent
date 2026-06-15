"""MCP client: the agent's gateway to connector tools served over MCP.

The retriever node calls these helpers instead of touching connector classes
directly. Each call opens a streamable-http session to the FastMCP server
(`MCP_SERVER_URL`), invokes the tool, and returns the structured result.
"""
import json
from contextlib import asynccontextmanager
from typing import Any

from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client

from app.config.settings import get_settings


@asynccontextmanager
async def _session():
    url = get_settings().mcp_server_url
    async with (
        streamable_http_client(url) as (read_stream, write_stream, _),
        ClientSession(read_stream, write_stream) as session,
    ):
        await session.initialize()
        yield session


def _unwrap(result: Any) -> Any:
    """Return the connector payload from a CallToolResult.

    FastMCP returns object payloads as-is and wraps non-object returns (lists)
    as ``{"result": ...}``; normalise both back to the raw connector value.
    """
    sc = getattr(result, "structuredContent", None)
    if sc is not None:
        if isinstance(sc, dict) and set(sc.keys()) == {"result"}:
            return sc["result"]
        return sc

    for block in result.content:
        text = getattr(block, "text", None)
        if text is not None:
            data = json.loads(text)
            if isinstance(data, dict) and set(data.keys()) == {"result"}:
                return data["result"]
            return data
    return None


async def call_tool(name: str, arguments: dict[str, Any]) -> Any:
    async with _session() as session:
        result = await session.call_tool(name, arguments)
        if result.isError:
            raise RuntimeError(f"MCP tool {name!r} failed: {result.content}")
        return _unwrap(result)


async def list_resources(connector: str, user_id: str) -> list[dict[str, Any]]:
    return await call_tool(f"{connector}_list_resources", {"user_id": user_id})


async def read(connector: str, user_id: str, resource_id: str) -> dict[str, Any]:
    return await call_tool(
        f"{connector}_read", {"user_id": user_id, "resource_id": resource_id}
    )


async def search(connector: str, user_id: str, query: str) -> list[dict[str, Any]]:
    return await call_tool(
        f"{connector}_search", {"user_id": user_id, "query": query}
    )
