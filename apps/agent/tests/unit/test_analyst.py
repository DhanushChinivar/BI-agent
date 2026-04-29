"""Unit tests for the analyst node."""
import json
from unittest.mock import AsyncMock, patch

import pytest

from app.graph.nodes.analyst import analyst_node


@pytest.mark.asyncio
async def test_analyst_returns_structured_analysis():
    payload = {
        "insights": ["Revenue grew 12% YoY", "Q4 was the strongest quarter"],
        "metrics": {"total_revenue": 1200000, "growth_rate": 0.12},
        "trends": ["Upward trajectory since Q2"],
        "anomalies": [],
    }
    with patch("app.graph.nodes.analyst.chat", new_callable=AsyncMock) as mock_chat:
        mock_chat.return_value = json.dumps(payload)
        state = {
            "messages": [{"role": "human", "content": "How did revenue grow?"}],
            "plan": ["question_type:trend", "connectors:google_sheets", "retrieve revenue data"],
            "retrieved_data": [{"source": "google_sheets", "data": {"rows": [{"revenue": 1200000}]}}],
            "user_id": "u1",
            "conversation_id": "c1",
        }
        result = await analyst_node(state)

    assert "analysis" in result
    assert len(result["analysis"]["insights"]) == 2
    assert result["analysis"]["metrics"]["total_revenue"] == 1200000


@pytest.mark.asyncio
async def test_analyst_handles_bad_json():
    with patch("app.graph.nodes.analyst.chat", new_callable=AsyncMock) as mock_chat:
        mock_chat.return_value = "malformed"
        state = {
            "messages": [{"role": "human", "content": "Analyze sales"}],
            "plan": [],
            "retrieved_data": [],
            "user_id": "u1",
            "conversation_id": "c1",
        }
        result = await analyst_node(state)

    assert result["analysis"]["insights"] == ["Analysis unavailable"]


@pytest.mark.asyncio
async def test_analyst_handles_empty_retrieved_data():
    payload = {"insights": ["No data available"], "metrics": {}, "trends": [], "anomalies": []}
    with patch("app.graph.nodes.analyst.chat", new_callable=AsyncMock) as mock_chat:
        mock_chat.return_value = json.dumps(payload)
        state = {
            "messages": [{"role": "human", "content": "Show me trends"}],
            "plan": [],
            "retrieved_data": [],
            "user_id": "u1",
            "conversation_id": "c1",
        }
        result = await analyst_node(state)

    assert "insights" in result["analysis"]
