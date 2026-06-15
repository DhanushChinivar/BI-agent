"""FastMCP server exposing every data connector as standardized MCP tools.

Each connector in the registry contributes three tools:
    <connector>_list_resources(user_id)
    <connector>_read(user_id, resource_id)
    <connector>_search(user_id, query)

The tool bodies just delegate to the existing connector classes, which resolve
per-user OAuth credentials from the database themselves. MCP is the transport and
discovery layer; OAuth remains the authorization layer.

Run standalone:
    python -m app.mcp_server.server      # streamable-http on MCP_SERVER_PORT
"""
from typing import Any

from mcp.server.fastmcp import FastMCP

from app.config.settings import get_settings
from app.connectors import REGISTRY

_settings = get_settings()

mcp = FastMCP(
    "bi-agent-connectors",
    host=_settings.mcp_server_host,
    port=_settings.mcp_server_port,
)


def _register(name: str, connector: Any) -> None:
    """Register the three standard tools for one connector.

    A factory captures `connector` per iteration to avoid late-binding closures.
    """

    async def list_resources(user_id: str) -> list[dict[str, Any]]:
        return await connector.list_resources(user_id)

    async def read(user_id: str, resource_id: str) -> dict[str, Any]:
        return await connector.read(user_id, resource_id)

    async def search(user_id: str, query: str) -> list[dict[str, Any]]:
        return await connector.search(user_id, query)

    mcp.add_tool(
        list_resources,
        name=f"{name}_list_resources",
        description=f"List resources the user can access in {name}.",
    )
    mcp.add_tool(
        read,
        name=f"{name}_read",
        description=f"Read the contents of one {name} resource by id.",
    )
    mcp.add_tool(
        search,
        name=f"{name}_search",
        description=f"Search {name} for resources matching a query.",
    )


for _name, _connector in REGISTRY.items():
    _register(_name, _connector)


if __name__ == "__main__":
    mcp.run(transport="streamable-http")
