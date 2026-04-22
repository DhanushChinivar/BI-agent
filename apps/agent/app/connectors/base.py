"""Common interface every data connector (Sheets, Notion, Gmail, ...) must implement.

Keeps agent nodes decoupled from transport details — swap providers without
touching graph code. Concrete adapters land in Phase 2.
"""
from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class Connector(Protocol):
    name: str

    async def list_resources(self, user_id: str) -> list[dict[str, Any]]:
        """Return resources the user has access to (spreadsheets, pages, labels...)."""
        ...

    async def read(self, user_id: str, resource_id: str, **kwargs: Any) -> dict[str, Any]:
        """Read the contents of one resource."""
        ...

    async def search(self, user_id: str, query: str) -> list[dict[str, Any]]:
        """Search within the provider."""
        ...
