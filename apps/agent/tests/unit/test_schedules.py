"""Scheduled-report tests.

The feature previously reported `{"status": "scheduled"}` for work it never did:
`action_node` PATCHed the n8n workflow with `{"active": True}` and a copy of its
own tags — the cron and question went nowhere — then POSTed to
`/api/v1/workflows/{id}/run`, which is not part of n8n's public API. Schedules
now live in Postgres, so the claim is checkable.
"""
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from app.db.schedule_crud import InvalidCronError, next_run, validate_cron
from app.graph.nodes.action import action_node
from app.middleware.auth import _is_exempt

UTC = UTC


# ── cron evaluation ───────────────────────────────────────────────────────────

def test_next_run_is_strictly_in_the_future():
    """Returning "now" for a schedule due now would make it due forever."""
    at = datetime(2026, 8, 10, 8, 0, tzinfo=UTC)

    assert next_run("0 8 * * *", at) == datetime(2026, 8, 11, 8, 0, tzinfo=UTC)


def test_next_run_is_always_utc_aware():
    """A naive datetime compared against a timestamptz column is a silent offset
    bug, and that comparison is what decides whether a report runs."""
    result = next_run("*/5 * * * *")

    assert result.tzinfo is not None
    assert result.utcoffset() == timedelta(0)


def test_next_run_accepts_a_naive_base_as_utc():
    naive = datetime(2026, 8, 10, 8, 0)

    assert next_run("0 9 * * *", naive) == datetime(2026, 8, 10, 9, 0, tzinfo=UTC)


@pytest.mark.parametrize("cron", ["not a cron", "", "0 8 * *", "99 99 * * *", "@bogus"])
def test_invalid_cron_is_rejected(cron):
    with pytest.raises(InvalidCronError):
        validate_cron(cron)


@pytest.mark.parametrize("cron", ["0 8 * * 1", "*/5 * * * *", "0 0 1 * *", "@daily"])
def test_valid_cron_is_accepted(cron):
    validate_cron(cron)  # must not raise


# ── action node ───────────────────────────────────────────────────────────────

def _row(**overrides):
    base = {
        "id": "sched-1",
        "cron": "0 8 * * 1",
        "next_run_at": datetime(2026, 8, 17, 8, 0, tzinfo=UTC),
    }
    return SimpleNamespace(**{**base, **overrides})


@pytest.fixture
def upsert(monkeypatch):
    """Patch persistence, recording what the node tried to store."""
    import app.graph.nodes.action as action

    mock = AsyncMock(return_value=_row())
    monkeypatch.setattr(action, "upsert_schedule", mock)
    monkeypatch.setattr(action, "get_session_factory", lambda: _session_factory())
    return mock


def _session_factory():
    class _Ctx:
        async def __aenter__(self):
            return AsyncMock()

        async def __aexit__(self, *_):
            return False

    return lambda: _Ctx()


@pytest.mark.asyncio
async def test_no_action_means_no_schedule(upsert):
    assert await action_node({"user_id": "u1"}) == {"schedule_result": None}
    upsert.assert_not_awaited()


@pytest.mark.asyncio
async def test_a_scheduled_report_is_persisted(upsert):
    result = await action_node({
        "user_id": "u1",
        "action_type": "schedule_report",
        "action_cron": "0 8 * * 1",
        "action_question": "What was our Q4 revenue?",
    })

    _, user_id, question, cron, action_type = upsert.await_args.args
    assert (user_id, question, cron) == ("u1", "What was our Q4 revenue?", "0 8 * * 1")
    assert action_type == "schedule_report"
    assert result["schedule_result"]["status"] == "scheduled"


@pytest.mark.asyncio
async def test_the_confirmation_reports_a_real_stored_next_run(upsert):
    """The old node returned `status: scheduled` with nothing written anywhere."""
    result = await action_node({
        "user_id": "u1",
        "action_type": "schedule_report",
        "action_cron": "0 8 * * 1",
        "action_question": "Weekly revenue",
    })

    payload = result["schedule_result"]
    assert payload["schedule_id"] == "sched-1"
    assert payload["next_run_at"] == "2026-08-17T08:00:00+00:00"


@pytest.mark.asyncio
async def test_a_missing_cron_falls_back_to_the_default(upsert):
    """"Send me this every week" carries no cron of its own."""
    await action_node({
        "user_id": "u1",
        "action_type": "schedule_report",
        "action_question": "Weekly revenue",
    })

    assert upsert.await_args.args[3] == "0 8 * * 1"


@pytest.mark.asyncio
async def test_a_missing_question_falls_back_to_the_turn(upsert):
    """"Run that every Monday" refers to the question just asked."""
    await action_node({
        "user_id": "u1",
        "action_type": "schedule_report",
        "action_cron": "0 8 * * 1",
        "messages": [{"role": "human", "content": "What was our Q4 revenue?"}],
    })

    assert upsert.await_args.args[2] == "What was our Q4 revenue?"


@pytest.mark.asyncio
async def test_a_bad_cron_is_reported_not_silently_replaced(upsert):
    """Rewriting the user's schedule to a default is how you end up emailing
    someone at the wrong time forever."""
    upsert.side_effect = InvalidCronError("Invalid cron expression 'every monday'")

    result = await action_node({
        "user_id": "u1",
        "action_type": "schedule_report",
        "action_cron": "every monday",
        "action_question": "Weekly revenue",
    })

    assert result["schedule_result"]["status"] == "error"
    assert "cron" in result["schedule_result"]["reason"].lower()


@pytest.mark.asyncio
async def test_an_anonymous_user_cannot_schedule(upsert):
    """A schedule with no owner has nobody to deliver to and nobody to bill."""
    result = await action_node({
        "user_id": "anonymous",
        "action_type": "schedule_report",
        "action_question": "Weekly revenue",
    })

    assert result["schedule_result"]["status"] == "error"
    upsert.assert_not_awaited()


@pytest.mark.asyncio
async def test_an_unknown_action_type_is_rejected(upsert):
    result = await action_node({
        "user_id": "u1",
        "action_type": "launch_missiles",
        "action_question": "?",
    })

    assert result["schedule_result"]["status"] == "error"
    upsert.assert_not_awaited()


@pytest.mark.asyncio
async def test_a_storage_failure_does_not_claim_success(upsert):
    upsert.side_effect = RuntimeError("db down")

    result = await action_node({
        "user_id": "u1",
        "action_type": "schedule_report",
        "action_question": "Weekly revenue",
    })

    assert result["schedule_result"]["status"] == "error"


# ── the ticker endpoint ───────────────────────────────────────────────────────

def test_only_run_due_is_exempt_from_the_jwt():
    """It is called by n8n and verified by HMAC. Everything else under
    /v1/schedules is per-user and must stay behind the Clerk token."""
    assert _is_exempt("/v1/schedules/run-due")
    assert not _is_exempt("/v1/schedules")
    assert not _is_exempt("/v1/schedules/sched-1")


def test_run_due_rejects_an_unsigned_caller():
    """It runs LLM work on behalf of arbitrary users — unsigned, anyone could
    drain every account's quota."""
    from app.api.schedules import _verify_signature

    assert not _verify_signature(b"{}", "", "secret")
    assert not _verify_signature(b"{}", "sha256=deadbeef", "secret")


def test_run_due_accepts_a_correct_signature():
    import hashlib
    import hmac

    from app.api.schedules import _verify_signature

    body = b'{"source":"n8n-ticker"}'
    sig = "sha256=" + hmac.new(b"secret", body, hashlib.sha256).hexdigest()

    assert _verify_signature(body, sig, "secret")


@pytest.mark.asyncio
async def test_a_failed_scheduled_run_refunds_and_is_recorded():
    """The owner must not lose a free query to a report they never received."""
    from app.api import schedules

    row = SimpleNamespace(id="s1", user_id="u1", question="Q?")
    recorded: list[tuple] = []
    refunded: list[str] = []

    async def fail(_state):
        raise RuntimeError("pipeline exploded")

    with (
        patch.object(schedules, "get_session_factory", lambda: _session_factory()),
        patch.object(schedules, "check_and_increment", AsyncMock(return_value=(True, ""))),
        patch.object(schedules, "graph", SimpleNamespace(ainvoke=fail)),
        patch.object(
            schedules,
            "record_run",
            AsyncMock(side_effect=lambda s, i, st, e: recorded.append((i, st))),
        ),
        patch.object(schedules, "refund", AsyncMock(side_effect=lambda u, r: refunded.append(u))),
    ):
        result = await schedules._run_one(row)

    assert result["status"] == "error"
    assert refunded == ["u1"]
    assert recorded == [("s1", "error")]


@pytest.mark.asyncio
async def test_a_user_over_quota_is_skipped_not_run():
    from app.api import schedules

    row = SimpleNamespace(id="s1", user_id="u1", question="Q?")
    ran = False

    async def should_not_run(_state):
        nonlocal ran
        ran = True
        return {}

    with (
        patch.object(schedules, "get_session_factory", lambda: _session_factory()),
        patch.object(
            schedules, "check_and_increment", AsyncMock(return_value=(False, "limit reached"))
        ),
        patch.object(schedules, "graph", SimpleNamespace(ainvoke=should_not_run)),
        patch.object(schedules, "record_run", AsyncMock()),
    ):
        result = await schedules._run_one(row)

    assert result["status"] == "skipped"
    assert ran is False
