"""Quota accounting tests.

The counter is charged before the pipeline runs — it has to be, since the check
is what decides whether the pipeline runs at all. The bug was that nothing ever
gave it back: a connector outage or an LLM error burned one of three free
queries a day and returned nothing, so three failures locked a user out until
the next UTC day.
"""
from unittest.mock import AsyncMock, patch

import pytest

from app.api import query as query_api
from app.middleware import gating
from app.schemas.query import QueryRequest


@pytest.fixture
def refunds(monkeypatch):
    """Record refunds instead of touching the database."""
    calls: list[str] = []

    async def record(user_id: str, reason: str) -> None:
        calls.append(user_id)

    monkeypatch.setattr(gating, "refund_query", AsyncMock())
    monkeypatch.setattr(query_api, "refund", record)
    return calls


async def _collect(gen) -> list[dict]:
    return [event async for event in gen]


def _events_of(events: list[dict], name: str) -> list[dict]:
    return [e for e in events if e["event"] == name]


@pytest.mark.asyncio
async def test_a_failed_stream_refunds_the_quota(refunds, monkeypatch):
    async def boom(user_id, req):
        yield query_api._sse("stage", {"stage": "planning", "message": "…"})
        raise RuntimeError("connector exploded")

    monkeypatch.setattr(query_api, "_stream_pipeline", boom)

    await _collect(query_api._guarded_stream("u1", QueryRequest(message="hi")))

    assert refunds == ["u1"]


@pytest.mark.asyncio
async def test_a_failed_stream_tells_the_client(refunds, monkeypatch):
    """The socket used to just close: the last event the UI received was a
    `stage`, so it sat on "Analyzing results…" indefinitely."""
    async def boom(user_id, req):
        yield query_api._sse("stage", {"stage": "analyzing", "message": "…"})
        raise RuntimeError("boom")

    monkeypatch.setattr(query_api, "_stream_pipeline", boom)

    events = await _collect(query_api._guarded_stream("u1", QueryRequest(message="hi")))

    assert _events_of(events, "error")
    # `done` must still arrive so the frontend runs its cleanup.
    assert _events_of(events, "done")


@pytest.mark.asyncio
async def test_the_error_payload_is_shaped_for_the_frontend(refunds, monkeypatch):
    """useAgentStream dispatches on payload shape and ignores the event name,
    so an `error` event keyed on anything else falls through every branch."""
    import json

    async def boom(user_id, req):
        raise RuntimeError("boom")
        yield  # pragma: no cover — makes this an async generator

    monkeypatch.setattr(query_api, "_stream_pipeline", boom)

    events = await _collect(query_api._guarded_stream("u1", QueryRequest(message="hi")))

    payload = json.loads(_events_of(events, "error")[0]["data"])
    assert payload["error"]
    # `message` alone is how connector warnings are recognised — it must not
    # collide with them.
    assert "connector" not in payload


@pytest.mark.asyncio
async def test_a_successful_stream_refunds_nothing(refunds, monkeypatch):
    async def fine(user_id, req):
        yield query_api._sse("chunk", {"content": "42"})
        yield query_api._sse("done", {"conversation_id": "c1"})

    monkeypatch.setattr(query_api, "_stream_pipeline", fine)

    events = await _collect(query_api._guarded_stream("u1", QueryRequest(message="hi")))

    assert refunds == []
    assert not _events_of(events, "error")


@pytest.mark.asyncio
async def test_a_client_disconnect_is_not_refunded(refunds, monkeypatch):
    """Cancellation means the user hung up. The work was done and partly
    delivered — that is not the deployment's failure to pay for."""
    import asyncio

    async def cancelled(user_id, req):
        yield query_api._sse("chunk", {"content": "par"})
        raise asyncio.CancelledError

    monkeypatch.setattr(query_api, "_stream_pipeline", cancelled)

    with pytest.raises(asyncio.CancelledError):
        await _collect(query_api._guarded_stream("u1", QueryRequest(message="hi")))

    assert refunds == []


# ── refund_query ──────────────────────────────────────────────────────────────

class _Plan:
    def __init__(self, queries_today: int):
        self.queries_today = queries_today


@pytest.mark.asyncio
@pytest.mark.parametrize("before,after", [(3, 2), (1, 0), (0, 0)])
async def test_refund_floors_at_zero(before, after):
    """A refund landing after the daily reset must not go negative and hand out
    a free extra query tomorrow."""
    from app.db import plan_crud

    plan = _Plan(before)
    session = AsyncMock()

    with patch.object(plan_crud, "get_or_create_plan", AsyncMock(return_value=plan)):
        await plan_crud.refund_query(session, "u1")

    assert plan.queries_today == after


@pytest.mark.asyncio
async def test_refund_never_raises(monkeypatch):
    """A failed refund must not turn a recoverable error into a second one."""
    monkeypatch.setattr(
        gating, "get_session_factory", lambda: (_ for _ in ()).throw(ConnectionError("db down"))
    )

    await gating.refund("u1", "test")  # must not raise


# ── which paths are metered ───────────────────────────────────────────────────

def test_the_n8n_webhook_is_not_metered_by_the_middleware():
    """It runs the same pipeline, but it is auth-exempt: at middleware time its
    user_id is the placeholder "system", so charging here would meter every
    scheduled report in the deployment against one shared counter. It charges
    the payload's real user inside its handler instead."""
    assert "/v1/webhooks/n8n" not in gating._GATED_PATHS


def test_both_query_endpoints_are_metered():
    assert {"/v1/query", "/v1/query/stream"} == gating._GATED_PATHS
