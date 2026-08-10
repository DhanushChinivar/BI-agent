"""Response-parsing tests for the Anthropic wrapper.

`response.content[0].text` assumed the content list is non-empty and that its
first block is text. Neither is guaranteed, and both failure modes surfaced as
an `AttributeError`/`IndexError` from deep inside a graph node.
"""
from types import SimpleNamespace

import pytest

from app.llm import EmptyCompletionError, _text_of


def _block(kind: str, **fields):
    return SimpleNamespace(type=kind, **fields)


def _response(blocks, stop_reason="end_turn"):
    return SimpleNamespace(content=blocks, stop_reason=stop_reason)


def test_returns_the_text_of_a_normal_response():
    assert _text_of(_response([_block("text", text="hello")])) == "hello"


def test_concatenates_multiple_text_blocks():
    response = _response([_block("text", text="a"), _block("text", text="b")])

    assert _text_of(response) == "ab"


def test_skips_a_leading_non_text_block():
    """`content[0]` is not always the answer — a thinking block can precede it,
    and indexing blind either raised AttributeError or returned the wrong thing."""
    response = _response(
        [_block("thinking", thinking="reasoning..."), _block("text", text="the answer")]
    )

    assert _text_of(response) == "the answer"


def test_empty_content_raises_rather_than_indexing():
    with pytest.raises(EmptyCompletionError):
        _text_of(_response([]))


def test_content_with_no_text_block_raises():
    response = _response([_block("tool_use", id="t1", name="x", input={})])

    with pytest.raises(EmptyCompletionError):
        _text_of(response)


def test_the_error_carries_the_stop_reason():
    """`max_tokens` and a refusal need different responses; the caller can only
    tell them apart if the reason survives."""
    with pytest.raises(EmptyCompletionError) as exc:
        _text_of(_response([], stop_reason="max_tokens"))

    assert exc.value.stop_reason == "max_tokens"
    assert "max_tokens" in str(exc.value)


@pytest.mark.asyncio
async def test_planner_falls_back_instead_of_failing_the_request(monkeypatch):
    """Planning is an optimisation: an empty completion must degrade to the
    default plan, not lose the user's question."""
    from app.graph.nodes import planner

    async def empty(*_args, **_kwargs):
        raise EmptyCompletionError("max_tokens")

    monkeypatch.setattr(planner, "chat", empty)

    result = await planner.planner_node(
        {"messages": [{"role": "human", "content": "What was our Q4 revenue?"}]}
    )

    assert result["plan"] == ["retrieve", "analyze", "summarize"]
    assert result["next_node"] == "retriever"
    assert result["action_required"] is False


@pytest.mark.asyncio
async def test_analyst_falls_back_instead_of_failing_the_request(monkeypatch):
    from app.graph.nodes import analyst

    async def empty(*_args, **_kwargs):
        raise EmptyCompletionError(None)

    monkeypatch.setattr(analyst, "chat", empty)

    result = await analyst.analyst_node(
        {
            "messages": [{"role": "human", "content": "What was our Q4 revenue?"}],
            "plan": ["retrieve"],
            "retrieved_data": [{"connector": "google_sheets", "data": {"rows": []}}],
        }
    )

    assert result["analysis"]["insights"] == ["Analysis unavailable"]
    assert result["next_node"] == "summarizer"
