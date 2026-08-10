"""Unit tests for the retriever node."""
from unittest.mock import AsyncMock, patch

import pytest

from app.graph.nodes import retriever
from app.graph.nodes.retriever import (
    _MAX_RESOURCES,
    _MAX_ROWS,
    _parse_plan_meta,
    _search_query,
    _select_resources,
    _trim,
    _usable,
    retriever_node,
)


@pytest.fixture(autouse=True)
def connector_search():
    """Connector search finds nothing unless a test says otherwise.

    Without this the real `mcp_client.search` attempts a network call, and the
    listing tests below would pass only because `_candidates` swallows the
    failure — exercising the fallback while appearing to test the main path.
    """
    with patch(
        "app.graph.nodes.retriever.mcp_client.search",
        new_callable=AsyncMock,
        return_value=[],
    ) as search:
        yield search


@pytest.fixture(autouse=True)
def cold_cache():
    """The connector cache misses unless a test says otherwise.

    Not optional hygiene: `cache_get` talks to a real Redis at REDIS_URL, so
    with the dev stack running these tests read whatever a previous test — or a
    previous *run* — happened to leave there. That surfaced as a test which
    passed alone and failed in the suite, which is the worst way to find out.
    Tests that are actually about caching patch over this.
    """
    with (
        patch("app.graph.nodes.retriever.cache_get", new_callable=AsyncMock, return_value=None),
        patch("app.graph.nodes.retriever.cache_set", new_callable=AsyncMock),
    ):
        yield


@pytest.fixture(autouse=True)
def vector_index():
    """The vector index is empty unless a test says otherwise.

    Made explicit for the same reason as `connector_search`: with no
    VOYAGE_API_KEY in the environment `rag_search` returns [] on its own, so
    every connector test below would pass while the vector branch was never
    evaluated at all — green for a reason unrelated to what it claims to check.
    """
    with patch(
        "app.graph.nodes.retriever.rag_search",
        new_callable=AsyncMock,
        return_value=[],
    ) as rag:
        yield rag


def test_parse_plan_meta_extracts_connectors():
    plan = ["question_type:aggregation", "connectors:google_sheets,gmail", "retrieve data"]
    connectors, question_type = _parse_plan_meta(plan)
    assert connectors == ["google_sheets", "gmail"]
    assert question_type == "aggregation"


def test_parse_plan_meta_empty_connectors():
    plan = ["question_type:other", "connectors:"]
    connectors, _ = _parse_plan_meta(plan)
    assert connectors == []


def test_trim_reaches_into_dict_payload():
    """Connectors return a dict wrapper, so trimming must reach into `rows`."""
    data = {"resource_id": "s1", "rows": [{"month": f"m{i}"} for i in range(_MAX_ROWS + 40)]}
    trimmed, dropped = _trim(data, "revenue", exhaustive=False)
    assert dropped == 40
    assert len(trimmed["rows"]) == _MAX_ROWS
    assert trimmed["resource_id"] == "s1"  # wrapper keys preserved


def test_trim_leaves_small_payload_untouched():
    data = {"rows": [{"revenue": 100}]}
    trimmed, dropped = _trim(data, "revenue", exhaustive=False)
    assert dropped == 0
    assert trimmed is data


def test_trim_preserves_order_for_aggregation():
    """A sum over relevance-reordered rows is wrong — keep document order."""
    rows = [{"n": i} for i in range(_MAX_ROWS + 10)]
    trimmed, dropped = _trim({"rows": rows}, "total revenue", exhaustive=True)
    assert dropped == 10
    assert trimmed["rows"] == rows[:_MAX_ROWS]


def test_trim_filters_by_relevance_for_lookup():
    rows = [{"note": "filler"} for _ in range(_MAX_ROWS + 5)]
    rows[-1] = {"note": "widget pricing"}  # past the cap — only relevance saves it
    trimmed, _ = _trim({"rows": rows}, "what is widget pricing", exhaustive=False)
    assert {"note": "widget pricing"} in trimmed["rows"]


def test_select_resources_prefers_title_match():
    resources = [
        {"id": "a", "title": "Marketing Spend 2024"},
        {"id": "b", "title": "Q4 Sales 2024"},
        {"id": "c", "title": "Employee Handbook"},
        {"id": "d", "title": "Office Supplies"},
    ]
    selected = _select_resources(resources, "what were Q4 sales?")
    assert selected[0]["id"] == "b"
    assert len(selected) <= _MAX_RESOURCES


def test_select_resources_reads_all_when_few():
    resources = [{"id": "a", "title": "Unrelated"}, {"id": "b", "title": "Also unrelated"}]
    assert _select_resources(resources, "revenue") == resources


@pytest.mark.asyncio
async def test_retriever_does_not_read_every_resource():
    """A Drive with many spreadsheets must not become one read per spreadsheet."""
    resources = [{"id": f"r{i}", "title": f"Sheet {i}"} for i in range(50)]
    read_mock = AsyncMock(return_value={"rows": [{"revenue": 1}]})

    with (
        patch(
            "app.graph.nodes.retriever.mcp_client.list_resources",
            new_callable=AsyncMock,
            return_value=resources,
        ),
        patch("app.graph.nodes.retriever.mcp_client.read", read_mock),
        patch("app.graph.nodes.retriever.cache_get", new_callable=AsyncMock, return_value=None),
        patch("app.graph.nodes.retriever.cache_set", new_callable=AsyncMock),
    ):
        state = {
            "plan": ["question_type:lookup", "connectors:"],
            "messages": [{"role": "human", "content": "what is in Sheet 7?"}],
            "user_id": "u1",
            "conversation_id": "c1",
        }
        result = await retriever_node(state)

    assert read_mock.await_count <= _MAX_RESOURCES
    assert len(result["retrieved_data"]) <= _MAX_RESOURCES


@pytest.mark.asyncio
async def test_retriever_flags_truncated_payload():
    """Dropping rows must be reported so the analyst can caveat totals."""
    big = {"rows": [{"revenue": 1} for _ in range(_MAX_ROWS + 25)]}

    with (
        patch(
            "app.graph.nodes.retriever.mcp_client.list_resources",
            new_callable=AsyncMock,
            return_value=[{"id": "r1", "title": "Sales"}],
        ),
        patch(
            "app.graph.nodes.retriever.mcp_client.read",
            new_callable=AsyncMock,
            return_value=big,
        ),
        patch("app.graph.nodes.retriever.cache_get", new_callable=AsyncMock, return_value=None),
        patch("app.graph.nodes.retriever.cache_set", new_callable=AsyncMock),
    ):
        state = {
            "plan": ["question_type:aggregation", "connectors:"],
            "messages": [{"role": "human", "content": "total revenue"}],
            "user_id": "u1",
            "conversation_id": "c1",
        }
        result = await retriever_node(state)

    assert result["retrieved_data"][0]["omitted_items"] == 25


@pytest.mark.asyncio
async def test_retriever_uses_mock_connector_as_fallback():
    with (
        patch(
            "app.graph.nodes.retriever.mcp_client.list_resources",
            new_callable=AsyncMock,
            return_value=[{"id": "r1", "name": "Mock Sheet"}],
        ),
        patch(
            "app.graph.nodes.retriever.mcp_client.read",
            new_callable=AsyncMock,
            return_value={"rows": [{"revenue": 100}]},
        ),
        patch("app.graph.nodes.retriever.cache_get", new_callable=AsyncMock, return_value=None),
        patch("app.graph.nodes.retriever.cache_set", new_callable=AsyncMock),
    ):
        state = {
            "plan": ["question_type:other", "connectors:"],
            "user_id": "u1",
            "conversation_id": "c1",
        }
        result = await retriever_node(state)

    assert len(result["retrieved_data"]) == 1
    assert result["retrieved_data"][0]["source"] == "mock"


@pytest.mark.asyncio
async def test_retriever_uses_cache_when_available():
    cached_data = {"rows": [{"revenue": 999}]}

    read_mock = AsyncMock()
    with (
        patch(
            "app.graph.nodes.retriever.mcp_client.list_resources",
            new_callable=AsyncMock,
            return_value=[{"id": "r1", "name": "Sheet"}],
        ),
        patch("app.graph.nodes.retriever.mcp_client.read", read_mock),
        patch(
            "app.graph.nodes.retriever.cache_get",
            new_callable=AsyncMock,
            return_value=cached_data,
        ),
        patch("app.graph.nodes.retriever.cache_set", new_callable=AsyncMock),
    ):
        state = {"plan": ["connectors:"], "user_id": "u1", "conversation_id": "c1"}
        result = await retriever_node(state)

    # mcp_client.read should NOT be called when cache hit
    read_mock.assert_not_called()
    assert result["retrieved_data"][0]["data"] == cached_data


def test_search_query_drops_filler():
    """The query handed to Gmail/Notion should be content words only."""
    assert _search_query("What was our Q4 revenue?") == "q4 revenue"
    assert _search_query("Show me the Acme deal") == "acme deal"
    # A question made entirely of stopwords yields nothing to search on.
    assert _search_query("what is it") == ""


def test_usable_drops_connector_error_entries():
    """Connectors report API failures as `[{"error": ...}]` instead of raising."""
    assert _usable([{"error": "403 insufficient scope"}]) == []
    assert _usable([{"id": "a"}, {"error": "boom"}]) == [{"id": "a"}]
    assert _usable(None) == []


@pytest.mark.asyncio
async def test_retriever_prefers_search_over_listing(connector_search):
    """A search hit means the listing is never fetched.

    This is the whole point of Stage 1: Gmail's listing is capped at the 5 most
    recent threads, so a question about anything older is unreachable from it.
    """
    connector_search.return_value = [{"id": "hit", "title": "Acme renewal pricing"}]
    list_mock = AsyncMock(return_value=[{"id": "recent", "title": "Standup notes"}])

    with (
        patch("app.graph.nodes.retriever.mcp_client.list_resources", list_mock),
        patch(
            "app.graph.nodes.retriever.mcp_client.read",
            new_callable=AsyncMock,
            return_value={"rows": []},
        ),
        patch("app.graph.nodes.retriever.cache_get", new_callable=AsyncMock, return_value=None),
        patch("app.graph.nodes.retriever.cache_set", new_callable=AsyncMock),
    ):
        state = {
            "plan": ["question_type:lookup", "connectors:gmail"],
            "messages": [{"role": "human", "content": "what did Acme say about pricing?"}],
            "user_id": "u1",
            "conversation_id": "c1",
        }
        result = await retriever_node(state)

    connector_search.assert_awaited_once()
    # Content words only, sorted for determinism. "what"/"did"/"about" are
    # stopwords; "say" is not — the list covers filler, not every weak verb.
    assert connector_search.await_args.args[2] == "acme pricing say"
    list_mock.assert_not_called()
    assert result["retrieved_data"][0]["resource"]["id"] == "hit"


@pytest.mark.asyncio
async def test_retriever_falls_back_to_listing_when_search_is_empty(connector_search):
    """Sheets' `search` substring-matches titles, so multi-word queries find nothing."""
    connector_search.return_value = []
    list_mock = AsyncMock(return_value=[{"id": "listed", "title": "Q4 Sales 2024"}])

    with (
        patch("app.graph.nodes.retriever.mcp_client.list_resources", list_mock),
        patch(
            "app.graph.nodes.retriever.mcp_client.read",
            new_callable=AsyncMock,
            return_value={"rows": []},
        ),
        patch("app.graph.nodes.retriever.cache_get", new_callable=AsyncMock, return_value=None),
        patch("app.graph.nodes.retriever.cache_set", new_callable=AsyncMock),
    ):
        state = {
            "plan": ["question_type:aggregation", "connectors:google_sheets"],
            "messages": [{"role": "human", "content": "what was our Q4 revenue?"}],
            "user_id": "u1",
            "conversation_id": "c1",
        }
        result = await retriever_node(state)

    list_mock.assert_awaited_once()
    assert result["retrieved_data"][0]["resource"]["id"] == "listed"


@pytest.mark.asyncio
async def test_search_failure_does_not_fail_the_connector(connector_search):
    """A broken search degrades to the listing rather than losing the source."""
    connector_search.side_effect = RuntimeError("MCP tool 'gmail_search' failed")
    list_mock = AsyncMock(return_value=[{"id": "listed", "title": "Inbox thread"}])

    with (
        patch("app.graph.nodes.retriever.mcp_client.list_resources", list_mock),
        patch(
            "app.graph.nodes.retriever.mcp_client.read",
            new_callable=AsyncMock,
            return_value={"messages": []},
        ),
        patch("app.graph.nodes.retriever.cache_get", new_callable=AsyncMock, return_value=None),
        patch("app.graph.nodes.retriever.cache_set", new_callable=AsyncMock),
    ):
        state = {
            "plan": ["question_type:lookup", "connectors:gmail"],
            "messages": [{"role": "human", "content": "what did Acme say?"}],
            "user_id": "u1",
            "conversation_id": "c1",
        }
        result = await retriever_node(state)

    assert "connector_error" not in result["retrieved_data"][0]
    assert result["retrieved_data"][0]["resource"]["id"] == "listed"


@pytest.mark.asyncio
async def test_search_error_payload_falls_back_to_listing(connector_search):
    """`[{"error": ...}]` is a failure the connector chose not to raise."""
    connector_search.return_value = [{"error": "403 insufficient scope"}]
    list_mock = AsyncMock(return_value=[{"id": "listed", "title": "Inbox thread"}])

    with (
        patch("app.graph.nodes.retriever.mcp_client.list_resources", list_mock),
        patch(
            "app.graph.nodes.retriever.mcp_client.read",
            new_callable=AsyncMock,
            return_value={"messages": []},
        ),
        patch("app.graph.nodes.retriever.cache_get", new_callable=AsyncMock, return_value=None),
        patch("app.graph.nodes.retriever.cache_set", new_callable=AsyncMock),
    ):
        state = {
            "plan": ["question_type:lookup", "connectors:gmail"],
            "messages": [{"role": "human", "content": "what did Acme say?"}],
            "user_id": "u1",
            "conversation_id": "c1",
        }
        result = await retriever_node(state)

    list_mock.assert_awaited_once()
    assert result["retrieved_data"][0]["resource"]["id"] == "listed"


@pytest.mark.asyncio
async def test_retriever_handles_connector_error():
    with (
        patch(
            "app.graph.nodes.retriever.mcp_client.list_resources",
            new_callable=AsyncMock,
            side_effect=RuntimeError("connection refused"),
        ),
        patch("app.graph.nodes.retriever.cache_get", new_callable=AsyncMock, return_value=None),
    ):
        state = {"plan": ["connectors:"], "user_id": "u1", "conversation_id": "c1"}
        result = await retriever_node(state)

    assert "error" in result["retrieved_data"][0]


# ── free-text payload cap ─────────────────────────────────────────────────────

def _page(paragraphs: list[str]) -> str:
    return "\n\n".join(paragraphs)


def test_short_content_is_left_alone():
    text = _page(["Revenue grew.", "Costs fell."])

    assert _trim({"content": text}, "revenue", False) == ({"content": text}, 0)


def test_long_notion_content_is_capped():
    """Nothing bounded this string before: one long page went into the prompt
    whole, crowding out every other connector's data."""
    text = _page([f"Paragraph {i} " + "x" * 500 for i in range(100)])

    trimmed, dropped = _trim({"content": text}, "revenue", False)

    assert len(trimmed["content"]) <= retriever._MAX_TEXT_CHARS
    assert dropped > 0


def test_capped_content_keeps_the_paragraphs_that_match_the_question():
    filler = [f"Unrelated note {i} " + "y" * 800 for i in range(40)]
    target = "Quarterly revenue for the Chatswood store was 91000 dollars."
    text = _page([*filler, target])

    trimmed, _ = _trim({"content": text}, "chatswood revenue", False)

    assert target in trimmed["content"]


def test_capped_content_stays_in_document_order():
    """Relevance decides what survives; the page must still read as a page."""
    first = "Revenue summary for the year. " + "a" * 100
    last = "Revenue outlook for next year. " + "b" * 100
    text = _page([first, *[f"Filler {i} " + "z" * 900 for i in range(30)], last])

    trimmed, _ = _trim({"content": text}, "revenue", False)
    content = trimmed["content"]

    assert content.index(first) < content.index(last)


def test_exhaustive_questions_keep_content_from_the_top():
    """A total or a trend depends on order, so relevance reordering is wrong."""
    text = _page([f"Line {i} " + "q" * 900 for i in range(40)])

    trimmed, dropped = _trim({"content": text}, "total", True)

    assert trimmed["content"].startswith("Line 0 ")
    assert dropped > 0


def test_one_giant_paragraph_is_truncated_rather_than_dropped():
    text = "w" * (retriever._MAX_TEXT_CHARS * 3)

    trimmed, _ = _trim({"content": text}, "revenue", False)

    assert 0 < len(trimmed["content"]) <= retriever._MAX_TEXT_CHARS


def test_trim_caps_every_payload_not_just_the_first():
    """The early return meant a result carrying both a list and a text body had
    only one of them capped — and the uncapped one was why `_trim` was called."""
    result = {
        "rows": [{"i": i} for i in range(retriever._MAX_ROWS + 10)],
        "content": _page([f"Note {i} " + "n" * 900 for i in range(40)]),
    }

    trimmed, dropped = _trim(result, "revenue", False)

    kept_paragraphs = len(trimmed["content"].split("\n\n"))
    assert len(trimmed["rows"]) == retriever._MAX_ROWS
    assert len(trimmed["content"]) <= retriever._MAX_TEXT_CHARS
    # 10 rows plus every paragraph that did not fit — both halves counted.
    assert dropped == 10 + (40 - kept_paragraphs)


# ── vector retrieval ──────────────────────────────────────────────────────────

def _chunk_hit(connector, resource_id, title, idx, score, text):
    return {
        "connector": connector,
        "resource_id": resource_id,
        "resource_title": title,
        "chunk_index": idx,
        "content": text,
        "distance": (1.0 - score) * 2,
        "score": score,
    }


@pytest.mark.asyncio
async def test_text_connectors_answer_from_the_index(vector_index):
    """The point of the whole feature: Gmail is answered from vectors, with no
    live connector round trip at all."""
    vector_index.return_value = [
        _chunk_hit("gmail", "t1", "Runway discussion", 0, 0.91, "We have 18 months of runway."),
    ]

    with patch(
        "app.graph.nodes.retriever.mcp_client.read", new_callable=AsyncMock
    ) as read:
        result = await retriever_node({
            "plan": ["connectors:gmail", "question_type:lookup"],
            "user_id": "u1",
            "messages": [{"role": "human", "content": "How much runway do we have?"}],
        })

    read.assert_not_awaited()
    entry = result["retrieved_data"][0]
    assert entry["source"] == "gmail"
    assert entry["retrieval"] == "vector"
    assert "18 months of runway" in entry["data"]["passages"][0]


@pytest.mark.asyncio
async def test_vector_entries_carry_a_citation(vector_index):
    """A BI answer that cannot say where a figure came from is not usable."""
    vector_index.return_value = [
        _chunk_hit("notion", "p1", "FY25 Plan", 2, 0.8, "Target is 4.2M."),
    ]

    result = await retriever_node({
        "plan": ["connectors:notion"],
        "user_id": "u1",
        "messages": [{"role": "human", "content": "What is the FY25 target?"}],
    })

    citation = result["retrieved_data"][0]["data"]["citation"]
    assert citation == {"connector": "notion", "resource_id": "p1", "title": "FY25 Plan"}


@pytest.mark.asyncio
async def test_an_empty_index_falls_back_to_the_live_connector(vector_index):
    """A cold index must degrade to provider search, not return nothing."""
    vector_index.return_value = []

    with patch(
        "app.graph.nodes.retriever.mcp_client.list_resources",
        new_callable=AsyncMock,
        return_value=[{"id": "t1", "title": "Runway"}],
    ), patch(
        "app.graph.nodes.retriever.mcp_client.read",
        new_callable=AsyncMock,
        return_value={"messages": [{"body": "18 months", "subject": "Runway"}]},
    ) as read:
        result = await retriever_node({
            "plan": ["connectors:gmail"],
            "user_id": "u1",
            "messages": [{"role": "human", "content": "How much runway?"}],
        })

    read.assert_awaited()
    assert result["retrieved_data"][0]["source"] == "gmail"


@pytest.mark.asyncio
async def test_spreadsheets_never_go_through_the_index(vector_index):
    """"What was Q4 revenue?" needs an exact sum over every row. Routing it to
    nearest-neighbour lookup would answer a different question convincingly."""
    with patch(
        "app.graph.nodes.retriever.mcp_client.list_resources",
        new_callable=AsyncMock,
        return_value=[{"id": "s1", "title": "Q4 Sales"}],
    ), patch(
        "app.graph.nodes.retriever.mcp_client.read",
        new_callable=AsyncMock,
        return_value={"rows": [{"revenue": "100"}]},
    ):
        result = await retriever_node({
            "plan": ["connectors:google_sheets", "question_type:aggregation"],
            "user_id": "u1",
            "messages": [{"role": "human", "content": "What was our Q4 revenue?"}],
        })

    vector_index.assert_not_awaited()
    assert result["retrieved_data"][0]["data"] == {"rows": [{"revenue": "100"}]}


@pytest.mark.asyncio
async def test_a_vector_store_failure_does_not_fail_the_question(vector_index):
    vector_index.side_effect = RuntimeError("pgvector unreachable")

    with patch(
        "app.graph.nodes.retriever.mcp_client.list_resources",
        new_callable=AsyncMock,
        return_value=[{"id": "t1", "title": "Runway"}],
    ), patch(
        "app.graph.nodes.retriever.mcp_client.read",
        new_callable=AsyncMock,
        return_value={"messages": [{"body": "18 months", "subject": "Runway"}]},
    ):
        result = await retriever_node({
            "plan": ["connectors:gmail"],
            "user_id": "u1",
            "messages": [{"role": "human", "content": "How much runway?"}],
        })

    assert result["retrieved_data"][0]["source"] == "gmail"
    assert "error" not in result["retrieved_data"][0]


@pytest.mark.asyncio
async def test_a_mixed_question_uses_both_paths(vector_index):
    """Sheets through SQL, Gmail through vectors, in one question."""
    vector_index.return_value = [
        _chunk_hit("gmail", "t1", "Budget thread", 0, 0.85, "Marketing asked for more."),
    ]

    with patch(
        "app.graph.nodes.retriever.mcp_client.list_resources",
        new_callable=AsyncMock,
        return_value=[{"id": "s1", "title": "Q4 Sales"}],
    ), patch(
        "app.graph.nodes.retriever.mcp_client.read",
        new_callable=AsyncMock,
        return_value={"rows": [{"revenue": "100"}]},
    ):
        result = await retriever_node({
            "plan": ["connectors:google_sheets,gmail"],
            "user_id": "u1",
            "messages": [{"role": "human", "content": "Why did Q4 revenue miss budget?"}],
        })

    by_source = {e["source"]: e for e in result["retrieved_data"]}
    assert by_source["gmail"]["retrieval"] == "vector"
    assert "rows" in by_source["google_sheets"]["data"]
