"""Unit tests for the retriever node."""
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.graph.nodes.retriever import _parse_plan_meta, retriever_node


def test_parse_plan_meta_extracts_connectors():
    plan = ["question_type:aggregation", "connectors:google_sheets,notion", "retrieve data"]
    connectors, question_type = _parse_plan_meta(plan)
    assert connectors == ["google_sheets", "notion"]
    assert question_type == "aggregation"


def test_parse_plan_meta_empty_connectors():
    plan = ["question_type:other", "connectors:"]
    connectors, _ = _parse_plan_meta(plan)
    assert connectors == []


@pytest.mark.asyncio
async def test_retriever_uses_mock_connector_as_fallback():
    mock_connector = MagicMock()
    mock_connector.name = "mock"
    mock_connector.list_resources = AsyncMock(return_value=[{"id": "r1", "name": "Mock Sheet"}])
    mock_connector.read = AsyncMock(return_value={"rows": [{"revenue": 100}]})

    with (
        patch("app.graph.nodes.retriever.REGISTRY", {"mock": mock_connector}),
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
    mock_connector = MagicMock()
    mock_connector.name = "mock"
    mock_connector.list_resources = AsyncMock(return_value=[{"id": "r1", "name": "Sheet"}])
    mock_connector.read = AsyncMock()

    cached_data = {"rows": [{"revenue": 999}]}

    with (
        patch("app.graph.nodes.retriever.REGISTRY", {"mock": mock_connector}),
        patch("app.graph.nodes.retriever.cache_get", new_callable=AsyncMock, return_value=cached_data),
        patch("app.graph.nodes.retriever.cache_set", new_callable=AsyncMock),
    ):
        state = {"plan": ["connectors:"], "user_id": "u1", "conversation_id": "c1"}
        result = await retriever_node(state)

    # connector.read should NOT be called when cache hit
    mock_connector.read.assert_not_called()
    assert result["retrieved_data"][0]["data"] == cached_data


@pytest.mark.asyncio
async def test_retriever_handles_connector_error():
    mock_connector = MagicMock()
    mock_connector.name = "mock"
    mock_connector.list_resources = AsyncMock(side_effect=RuntimeError("connection refused"))

    with (
        patch("app.graph.nodes.retriever.REGISTRY", {"mock": mock_connector}),
        patch("app.graph.nodes.retriever.cache_get", new_callable=AsyncMock, return_value=None),
    ):
        state = {"plan": ["connectors:"], "user_id": "u1", "conversation_id": "c1"}
        result = await retriever_node(state)

    assert "error" in result["retrieved_data"][0]
