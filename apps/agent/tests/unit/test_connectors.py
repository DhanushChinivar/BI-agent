"""Connector query-construction tests.

Focused on the parts that shape a provider-side query, since question text
reaches the Drive `q` parameter and the resource ids a `search` returns must be
ones the matching `read` can resolve.
"""
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.connectors import google_sheets
from app.connectors.google_sheets import (
    _MAX_QUERY_TERMS,
    GoogleSheetsConnector,
    _escape,
)


@pytest.fixture
def drive(monkeypatch):
    """A stubbed Drive client that records the query it was handed."""
    client = MagicMock()
    client.files.return_value.list.return_value.execute.return_value = {"files": []}
    monkeypatch.setattr(google_sheets, "build", lambda *a, **k: client)
    monkeypatch.setattr(
        google_sheets, "get_google_credentials", AsyncMock(return_value=object())
    )
    monkeypatch.setattr(GoogleSheetsConnector, "_creds", AsyncMock(return_value={}))
    return client


def sent_query(client) -> str:
    return client.files.return_value.list.call_args.kwargs["q"]


def test_escape_handles_quotes_and_backslashes():
    assert _escape("it's") == "it\\'s"
    assert _escape("a\\b") == "a\\\\b"
    assert _escape("plain") == "plain"


@pytest.mark.asyncio
async def test_search_ands_one_fulltext_clause_per_term(drive):
    """`fullText contains 'a b'` is an exact-phrase match, so terms are split."""
    await GoogleSheetsConnector().search("u1", "q4 revenue")

    q = sent_query(drive)
    assert "mimeType='application/vnd.google-apps.spreadsheet'" in q
    assert "fullText contains 'q4'" in q
    assert "fullText contains 'revenue'" in q
    assert " and " in q


@pytest.mark.asyncio
async def test_search_escapes_terms_into_the_drive_query(drive):
    """Question text reaches `q` — a quote must not close the string literal."""
    await GoogleSheetsConnector().search("u1", "o'brien")

    q = sent_query(drive)
    assert "fullText contains 'o\\'brien'" in q
    # The apostrophe never appears unescaped, which would terminate the literal.
    assert "'o'brien'" not in q


@pytest.mark.asyncio
async def test_search_caps_the_number_of_terms(drive):
    await GoogleSheetsConnector().search("u1", " ".join(f"term{i}" for i in range(20)))

    assert sent_query(drive).count("fullText contains") == _MAX_QUERY_TERMS


@pytest.mark.asyncio
async def test_search_skips_the_api_entirely_for_a_blank_query(drive):
    """A question of pure stopwords yields no terms — don't spend a round trip."""
    assert await GoogleSheetsConnector().search("u1", "   ") == []
    drive.files.assert_not_called()


@pytest.fixture
def sheets(monkeypatch):
    """A stubbed Sheets client that records the range it was asked for."""
    client = MagicMock()
    client.spreadsheets.return_value.get.return_value.execute.return_value = {
        "properties": {"title": "Sales"},
        "sheets": [{"properties": {"title": "Sales Transactions"}}],
    }
    monkeypatch.setattr(google_sheets, "build", lambda *a, **k: client)
    monkeypatch.setattr(
        google_sheets, "get_google_credentials", AsyncMock(return_value=object())
    )
    monkeypatch.setattr(GoogleSheetsConnector, "_creds", AsyncMock(return_value={}))
    return client


def _values(client):
    return client.spreadsheets.return_value.values.return_value.get


def test_read_defaults_to_the_whole_first_tab(sheets):
    """A fixed A1:Z1000 dropped every row past 999.

    On an 1,800-row transaction sheet that silently removed a full quarter while
    still returning a total that looked authoritative — the worst failure shape
    for a BI agent.
    """
    _values(sheets).return_value.execute.return_value = {"values": [["a"], ["1"]]}

    import asyncio
    asyncio.run(GoogleSheetsConnector().read("u1", "sheet-1"))

    assert _values(sheets).call_args.kwargs["range"] == "Sales Transactions"


def test_read_caps_rows_and_reports_the_drop(sheets):
    header = [["revenue"]]
    body = [[str(i)] for i in range(google_sheets._MAX_READ_ROWS + 25)]
    _values(sheets).return_value.execute.return_value = {"values": header + body}

    import asyncio
    result = asyncio.run(GoogleSheetsConnector().read("u1", "sheet-1"))

    assert len(result["rows"]) == google_sheets._MAX_READ_ROWS
    assert result["truncated_rows"] == 25


def test_read_keeps_rows_with_trailing_empty_cells(sheets):
    """Sheets omits trailing blanks, so a row can be shorter than the header."""
    _values(sheets).return_value.execute.return_value = {
        "values": [["month", "revenue", "note"], ["Oct", "100"]]
    }

    import asyncio
    result = asyncio.run(GoogleSheetsConnector().read("u1", "sheet-1"))

    assert result["rows"] == [{"month": "Oct", "revenue": "100"}]


@pytest.mark.asyncio
async def test_search_returns_readable_resources(drive):
    """Every id a search returns must be one `read` can resolve."""
    drive.files.return_value.list.return_value.execute.return_value = {
        "files": [{"id": "sheet-1", "name": "Q4 Sales", "modifiedTime": "2026-01-01"}]
    }

    resources = await GoogleSheetsConnector().search("u1", "q4")

    assert resources == [
        {
            "id": "sheet-1",
            "title": "Q4 Sales",
            "type": "spreadsheet",
            "modified": "2026-01-01",
        }
    ]
