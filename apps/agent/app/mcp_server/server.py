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
import hmac
import logging
from typing import Any

from mcp.server.fastmcp import FastMCP
from starlette.responses import JSONResponse
from starlette.types import Receive, Scope, Send

from app.config.settings import get_settings
from app.connectors import REGISTRY

logger = logging.getLogger(__name__)
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


class ServiceAuthMiddleware:
    """Pure-ASGI gate requiring the shared service secret on every request.

    The MCP tools trust their ``user_id`` argument, so without this anything that
    can reach the port could call ``gmail_read(user_id="anyone")`` — a classic
    confused deputy. Network isolation is not a control once the server has a
    routable URL. A stronger design would have the agent mint a short-lived
    per-user JWT and the server derive ``user_id`` from it; the shared secret is
    the floor, not the ceiling.
    """

    def __init__(self, app: Any, secret: str) -> None:
        self.app = app
        self.secret = secret

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        headers = dict(scope.get("headers") or [])
        provided = headers.get(b"x-service-secret", b"").decode()
        if not hmac.compare_digest(provided, self.secret):
            await JSONResponse({"detail": "Unauthorized"}, status_code=401)(scope, receive, send)
            return
        await self.app(scope, receive, send)


def build_app() -> Any:
    """Build the streamable-http ASGI app, gated by the service secret if set."""
    app = mcp.streamable_http_app()
    if _settings.mcp_service_secret:
        app.add_middleware(ServiceAuthMiddleware, secret=_settings.mcp_service_secret)
    else:
        logger.warning(
            "MCP_SERVICE_SECRET is unset — the MCP server is UNAUTHENTICATED. "
            "Acceptable for local dev only; production startup requires it."
        )
    return app


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        build_app(),
        host=_settings.mcp_server_host,
        port=_settings.mcp_server_port,
    )
