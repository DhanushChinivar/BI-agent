"""Connector registry: maps connector names to instances."""
from app.connectors.gmail import GmailConnector
from app.connectors.google_sheets import GoogleSheetsConnector
from app.connectors.mock import MockConnector
from app.connectors.notion import NotionConnector

REGISTRY: dict = {
    "google_sheets": GoogleSheetsConnector(),
    "gmail": GmailConnector(),
    "notion": NotionConnector(),
    "csv_upload": MockConnector(),
    "mock": MockConnector(),
}
