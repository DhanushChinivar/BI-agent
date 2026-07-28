"""Unit tests for the summarizer node."""
from unittest.mock import patch

import pytest

from app.graph.nodes.summarizer import build_prompt, summarizer_node

# `_text_content_from_retrieved` was removed in fcc50fa along with the
# CSV/Excel/PDF upload connector it served; its tests went with it.


def test_build_prompt_lists_each_source_once():
    """Three sheets from one connector is one source, not three."""
    retrieved = [
        {"source": "google_sheets", "data": {"rows": []}},
        {"source": "google_sheets", "data": {"rows": []}},
        {"source": "gmail", "data": {"messages": []}},
    ]
    prompt = build_prompt("q", {}, retrieved)
    assert "['google_sheets', 'gmail']" in prompt


def test_build_prompt_excludes_failed_connectors():
    retrieved = [
        {"source": "gmail", "error": "token expired", "connector_error": True},
        {"source": "google_sheets", "data": {"rows": []}},
    ]
    prompt = build_prompt("q", {}, retrieved)
    assert "gmail" not in prompt
    assert "google_sheets" in prompt


def test_build_prompt_handles_no_sources():
    assert "Active data sources: none" in build_prompt("q", {}, [])


def test_streaming_path_uses_the_same_prompt_builder():
    """The SSE endpoint streams tokens itself instead of calling summarizer_node.

    It must not rebuild the prompt inline — the two copies drifted before
    (`"error" not in item` vs `connector_error`), so pin them to one builder.
    """
    from app.api import query

    assert query.build_summarizer_prompt is build_prompt


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
