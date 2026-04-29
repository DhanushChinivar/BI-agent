"""Unit tests for the summarizer node."""
from unittest.mock import AsyncMock, patch

import pytest

from app.graph.nodes.summarizer import _text_content_from_retrieved, summarizer_node


def test_text_content_from_retrieved_extracts_plain_text():
    retrieved = [
        {"source": "csv_upload", "data": {"rows": [{"content": "Q4 revenue was $1.2M"}]}},
        {"source": "google_sheets", "data": {"rows": [{"revenue": 1200000, "region": "APAC"}]}},
    ]
    text = _text_content_from_retrieved(retrieved)
    assert "Q4 revenue was $1.2M" in text
    # Tabular row (two keys) should NOT appear
    assert "APAC" not in text


def test_text_content_from_retrieved_empty():
    assert _text_content_from_retrieved([]) == ""


@pytest.mark.asyncio
async def test_summarizer_returns_non_empty_answer():
    async def fake_stream(**kwargs):
        for token in ["Revenue ", "grew ", "12%."]:
            yield token

    with patch("app.graph.nodes.summarizer.stream", side_effect=fake_stream):
        state = {
            "messages": [{"role": "human", "content": "How did revenue grow?"}],
            "analysis": {
                "insights": ["Revenue grew 12%"],
                "metrics": {"growth": 0.12},
                "trends": [],
                "anomalies": [],
            },
            "retrieved_data": [],
            "user_id": "u1",
            "conversation_id": "c1",
        }
        result = await summarizer_node(state)

    assert result["final_answer"] == "Revenue grew 12%."


@pytest.mark.asyncio
async def test_summarizer_falls_back_on_stream_error():
    async def bad_stream(**kwargs):
        raise RuntimeError("LLM timeout")
        yield  # make it a generator

    with patch("app.graph.nodes.summarizer.stream", side_effect=bad_stream):
        state = {
            "messages": [{"role": "human", "content": "Summarize"}],
            "analysis": {"insights": ["insight A"], "metrics": {}, "trends": [], "anomalies": []},
            "retrieved_data": [],
            "user_id": "u1",
            "conversation_id": "c1",
        }
        result = await summarizer_node(state)

    assert "insight A" in result["final_answer"]
