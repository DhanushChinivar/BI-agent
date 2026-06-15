"""Unit tests for the retriever node."""
from unittest.mock import AsyncMock, patch

import pytest

from app.graph.nodes.retriever import _parse_plan_meta, retriever_node


def test_parse_plan_meta_extracts_connectors():
    plan = ["question_type:aggregation", "connectors:google_sheets,gmail", "retrieve data"]
    connectors, question_type = _parse_plan_meta(plan)
    assert connectors == ["google_sheets", "gmail"]
    assert question_type == "aggregation"


def test_parse_plan_meta_empty_connectors():
    plan = ["question_type:other", "connectors:"]
    connectors, _ = _parse_plan_meta(plan)
    assert connectors == []


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
        patch("app.graph.nodes.retriever.cache_get", new_callable=AsyncMock, return_value=cached_data),
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
