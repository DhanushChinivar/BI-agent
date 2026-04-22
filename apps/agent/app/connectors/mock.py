"""Mock connector — returns synthetic data for local dev and tests."""
from typing import Any


class MockConnector:
    name = "mock"

    _RESOURCES = [
        {"id": "sheet-1", "title": "Q4 Sales 2024", "type": "spreadsheet"},
        {"id": "sheet-2", "title": "Marketing Spend 2024", "type": "spreadsheet"},
        {"id": "page-1", "title": "Product Roadmap", "type": "notion_page"},
    ]

    _DATA: dict[str, dict[str, Any]] = {
        "sheet-1": {
            "resource_id": "sheet-1",
            "title": "Q4 Sales 2024",
            "rows": [
                {"month": "October", "revenue": 120000, "units": 340},
                {"month": "November", "revenue": 145000, "units": 410},
                {"month": "December", "revenue": 198000, "units": 560},
            ],
        },
        "sheet-2": {
            "resource_id": "sheet-2",
            "title": "Marketing Spend 2024",
            "rows": [
                {"month": "October", "spend": 30000, "channel": "paid_search"},
                {"month": "November", "spend": 42000, "channel": "social"},
                {"month": "December", "spend": 55000, "channel": "paid_search"},
            ],
        },
        "page-1": {
            "resource_id": "page-1",
            "title": "Product Roadmap",
            "content": "Q1 2025: launch mobile app. Q2 2025: API integrations. Q3 2025: enterprise tier.",
        },
    }

    async def list_resources(self, user_id: str) -> list[dict[str, Any]]:
        return self._RESOURCES

    async def read(self, user_id: str, resource_id: str, **kwargs: Any) -> dict[str, Any]:
        return self._DATA.get(resource_id, {"error": f"resource {resource_id!r} not found"})

    async def search(self, user_id: str, query: str) -> list[dict[str, Any]]:
        q = query.lower()
        return [r for r in self._RESOURCES if q in r["title"].lower()]
