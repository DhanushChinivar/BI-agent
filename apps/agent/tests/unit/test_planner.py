"""Unit tests for the planner node."""
import json
from unittest.mock import AsyncMock, patch

import pytest

from app.graph.nodes.planner import planner_node


@pytest.mark.asyncio
async def test_planner_returns_plan():
    payload = {
        "steps": ["retrieve sales data", "compute totals"],
        "connectors": ["google_sheets"],
        "question_type": "aggregation",
        "action": None,
        "action_cron": None,
        "action_question": None,
    }
    with patch("app.graph.nodes.planner.chat", new_callable=AsyncMock) as mock_chat:
        mock_chat.return_value = json.dumps(payload)
        state = {
            "messages": [{"role": "human", "content": "What were sales last quarter?"}],
            "user_id": "u1",
            "conversation_id": "c1",
        }
        result = await planner_node(state)

    assert "plan" in result
    assert any("question_type:aggregation" in s for s in result["plan"])
    assert any("connectors:google_sheets" in s for s in result["plan"])
    assert result["action_required"] is False


@pytest.mark.asyncio
async def test_planner_detects_schedule_action():
    payload = {
        "steps": ["schedule weekly report"],
        "connectors": [],
        "question_type": "other",
        "action": "schedule_report",
        "action_cron": "0 8 * * 1",
        "action_question": "What were sales this week?",
    }
    with patch("app.graph.nodes.planner.chat", new_callable=AsyncMock) as mock_chat:
        mock_chat.return_value = json.dumps(payload)
        state = {
            "messages": [{"role": "human", "content": "Send me a sales report every Monday"}],
            "user_id": "u1",
            "conversation_id": "c1",
        }
        result = await planner_node(state)

    assert result["action_required"] is True
    assert result["action_type"] == "schedule_report"
    assert result["action_cron"] == "0 8 * * 1"


@pytest.mark.asyncio
async def test_planner_handles_bad_json():
    with patch("app.graph.nodes.planner.chat", new_callable=AsyncMock) as mock_chat:
        mock_chat.return_value = "not valid json"
        state = {
            "messages": [{"role": "human", "content": "What is revenue?"}],
            "user_id": "u1",
            "conversation_id": "c1",
        }
        result = await planner_node(state)

    assert "plan" in result
    assert result["action_required"] is False


@pytest.mark.asyncio
async def test_planner_handles_empty_messages():
    result = await planner_node({"messages": [], "user_id": "u1", "conversation_id": "c1"})
    assert result["plan"] == ["no question provided"]
