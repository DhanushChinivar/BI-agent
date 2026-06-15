"""Connector registry: maps connector names to instances."""
from app.connectors.gmail import GmailConnector
from app.connectors.google_sheets import GoogleSheetsConnector
from app.connectors.mock import MockConnector
from app.connectors.notion import NotionConnector

REGISTRY: dict = {
    "google_sheets": GoogleSheetsConnector(),
    "gmail": GmailConnector(),
    "notion": NotionConnector(),
    "mock": MockConnector(),
}

# Connector names the agent (MCP client) and planner know about. The MCP server
# uses REGISTRY to back the tools; the rest of the app only needs the names.
CONNECTOR_NAMES: list[str] = list(REGISTRY)
