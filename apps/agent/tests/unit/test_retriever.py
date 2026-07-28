"""Unit tests for the retriever node."""
from unittest.mock import AsyncMock, patch

import pytest

from app.graph.nodes.retriever import (
    _MAX_RESOURCES,
    _MAX_ROWS,
    _parse_plan_meta,
    _select_resources,
    _trim,
    retriever_node,
)


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
