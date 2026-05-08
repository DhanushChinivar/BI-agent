"""Connector registry: maps connector names to instances."""
from app.connectors.csv_upload import CsvUploadConnector
from app.connectors.gmail import GmailConnector
from app.connectors.google_sheets import GoogleSheetsConnector
from app.connectors.mock import MockConnector

REGISTRY: dict = {
    "google_sheets": GoogleSheetsConnector(),
    "gmail": GmailConnector(),
    "csv_upload": CsvUploadConnector(),
    "mock": MockConnector(),
}
