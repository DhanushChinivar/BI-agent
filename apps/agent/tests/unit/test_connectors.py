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


# ── change signals for incremental indexing ───────────────────────────────────

@pytest.mark.asyncio
async def test_gmail_listing_carries_a_history_id(monkeypatch):
    """`indexed_resources.revision` skips unchanged resources. Without a real
    signal it falls back to the title, so an edited thread keeps stale vectors
    until someone renames it — the index goes quietly wrong, not loudly."""
    from app.connectors import gmail

    service = MagicMock()
    threads = service.users.return_value.threads.return_value
    threads.list.return_value.execute.return_value = {"threads": [{"id": "t1"}]}
    threads.get.return_value.execute.return_value = {
        "historyId": "998877",
        "messages": [{"payload": {"headers": [{"name": "Subject", "value": "Q4"}]}}],
    }
    monkeypatch.setattr(gmail, "build", lambda *a, **k: service)
    monkeypatch.setattr(gmail, "get_google_credentials", AsyncMock(return_value=object()))
    monkeypatch.setattr(gmail.GmailConnector, "_creds", AsyncMock(return_value={}))

    resources = await gmail.GmailConnector().list_resources("u1")

    assert resources[0]["historyId"] == "998877"


@pytest.mark.asyncio
async def test_notion_listing_carries_last_edited_time(monkeypatch):
    from app.connectors import notion

    page = {
        "id": "p1",
        "url": "https://notion.so/p1",
        "last_edited_time": "2026-08-10T09:00:00.000Z",
        "properties": {"title": {"type": "title", "title": [{"plain_text": "Roadmap"}]}},
    }

    class _Resp:
        status_code = 200

        @staticmethod
        def json():
            return {"results": [page]}

    class _Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_):
            return False

        async def post(self, *a, **k):
            return _Resp()

    monkeypatch.setattr(notion.NotionConnector, "_token", AsyncMock(return_value="t"))
    monkeypatch.setattr(notion.NotionConnector, "_client", lambda self, token: _Client())

    resources = await notion.NotionConnector().list_resources("u1")

    assert resources[0]["last_edited_time"] == "2026-08-10T09:00:00.000Z"


def test_revision_prefers_a_real_signal_over_the_title_fallback():
    """Ties the connector fields above to the ingest logic that consumes them."""
    from app.rag.ingest import _revision

    gmail_resource = {"id": "t1", "title": "Q4", "historyId": "998877"}
    notion_resource = {"id": "p1", "title": "Roadmap", "last_edited_time": "2026-08-10"}

    assert _revision(gmail_resource) == "998877"
    assert _revision(notion_resource) == "2026-08-10"
    # Only when neither is present does it degrade to the title.
    assert _revision({"id": "x", "title": "Roadmap"}) == "title:Roadmap"
