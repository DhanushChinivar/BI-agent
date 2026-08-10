"""Auth-boundary tests.

Two things are pinned here, both of which shipped broken and neither of which
had coverage:

1. Routes that authenticate by their own mechanism (OAuth `state`, Stripe
   signature, n8n HMAC) must not be gated on a Clerk JWT they never carry —
   while everything else stays closed under APP_ENV=production.
2. Conversation history is scoped to its owner, so a caller cannot pass another
   user's conversation_id and have their messages loaded as context.
"""
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from app.api.query import _assert_conversation_owned, _load_history
from app.config.settings import get_settings
from app.middleware.auth import _is_exempt


@pytest.fixture
def prod_client(monkeypatch):
    """A TestClient for an app booted as if in production.

    Settings are lru_cached and read at request time, so the cache has to be
    cleared on both sides of the fixture.
    """
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("CREDENTIAL_ENCRYPTION_KEY", "k" * 32)
    monkeypatch.setenv("WEBHOOK_SECRET", "w" * 32)
    monkeypatch.setenv("MCP_SERVICE_SECRET", "m" * 32)
    get_settings.cache_clear()

    from app.main import create_app

    with TestClient(create_app(), raise_server_exceptions=False) as client:
        yield client

    get_settings.cache_clear()


# ── exempt-path predicate ─────────────────────────────────────────────────────

@pytest.mark.parametrize(
    "path,exempt",
    [
        ("/health", True),
        ("/metrics", True),
        ("/v1/stripe/webhook", True),
        ("/v1/webhooks/n8n", True),
        ("/v1/oauth/google-sheets/callback", True),
        ("/v1/oauth/gmail/callback", True),
        ("/v1/oauth/notion/callback", True),
        # /start binds the flow to an identity — it must stay authenticated, or
        # tokens end up on whatever user the BFF falls back to.
        ("/v1/oauth/google-sheets/start", False),
        ("/v1/oauth/notion/start", False),
        ("/v1/query", False),
        ("/v1/query/stream", False),
        ("/v1/conversations", False),
        ("/v1/connectors/status", False),
    ],
)
def test_is_exempt(path, exempt):
    assert _is_exempt(path) is exempt


# ── exempt routes reach their handler in production ───────────────────────────

def test_health_probe_is_reachable_in_production(prod_client):
    assert prod_client.get("/health").status_code == 200


def test_metrics_are_reachable_in_production(prod_client):
    resp = prod_client.get("/metrics")
    assert resp.status_code == 200
    assert b"python_info" in resp.content


def test_oauth_callback_reaches_its_own_state_check(prod_client):
    """A 400 on the OAuth `state` means the request got past AuthMiddleware."""
    resp = prod_client.get("/v1/oauth/google-sheets/callback?code=x&state=bogus")
    assert resp.status_code == 400
    assert resp.json()["detail"] == "Invalid or expired OAuth state"


def test_stripe_webhook_reaches_its_own_signature_check(prod_client):
    """400, not 401 (auth) and not 500.

    The 500 case is its own regression: this handler used to catch
    `stripe.errors.SignatureVerificationError`, which does not exist in the
    pinned stripe release, so a forged signature raised AttributeError.
    """
    resp = prod_client.post(
        "/v1/stripe/webhook",
        headers={"stripe-signature": "t=1,v1=deadbeef"},
        content=b"{}",
    )
    assert resp.status_code == 400
    assert resp.json()["detail"] == "Invalid Stripe signature"


def test_n8n_webhook_reaches_its_own_hmac_check(prod_client):
    """Also a 401 — but the webhook's own, not the middleware's.

    Both return 401, so the body is the only thing that distinguishes "rejected
    by HMAC" from "never got past auth". That ambiguity is why this one hid.
    """
    resp = prod_client.post(
        "/v1/webhooks/n8n",
        headers={"x-hub-signature-256": "sha256=deadbeef"},
        content=b"{}",
    )
    assert resp.status_code == 401
    assert resp.json()["detail"] == "Invalid webhook signature"


# ── everything else stays closed ──────────────────────────────────────────────

@pytest.mark.parametrize(
    "method,path",
    [
        ("post", "/v1/query"),
        ("get", "/v1/conversations"),
        ("get", "/v1/connectors/status"),
        ("get", "/v1/oauth/google-sheets/start"),
    ],
)
def test_protected_routes_still_401_in_production(prod_client, method, path):
    # No body: auth runs ahead of request parsing, so a 401 here is the
    # middleware rejecting the caller rather than a validation error.
    resp = prod_client.request(method.upper(), path)
    assert resp.status_code == 401
    assert resp.json()["detail"] == "Unauthorized"


def test_x_user_id_header_is_not_trusted_in_production(prod_client):
    """The dev fallback must not leak into production."""
    resp = prod_client.get("/v1/conversations", headers={"X-User-Id": "victim"})
    assert resp.status_code == 401


# ── conversation ownership ────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_assert_conversation_owned_rejects_someone_elses_thread():
    with patch("app.api.query.get_conversation", new_callable=AsyncMock, return_value=None):
        with pytest.raises(HTTPException) as exc:
            await _assert_conversation_owned("user-a", "conversation-owned-by-b")
    assert exc.value.status_code == 404


@pytest.mark.asyncio
async def test_assert_conversation_owned_allows_your_own_thread():
    with patch(
        "app.api.query.get_conversation", new_callable=AsyncMock, return_value=object()
    ):
        await _assert_conversation_owned("user-a", "conversation-a")  # must not raise


@pytest.mark.asyncio
async def test_load_history_never_reads_a_foreign_conversation():
    """Defence in depth: the messages must not even be fetched."""
    with (
        patch("app.api.query.get_conversation", new_callable=AsyncMock, return_value=None),
        patch("app.api.query.get_messages", new_callable=AsyncMock) as get_messages,
    ):
        assert await _load_history("user-a", "conversation-owned-by-b") == []
    get_messages.assert_not_called()


@pytest.mark.asyncio
async def test_load_history_returns_owned_messages():
    rows = [
        type("Row", (), {"role": "user", "content": "hello"})(),
        type("Row", (), {"role": "assistant", "content": "hi"})(),
    ]
    with (
        patch(
            "app.api.query.get_conversation", new_callable=AsyncMock, return_value=object()
        ),
        patch("app.api.query.get_messages", new_callable=AsyncMock, return_value=rows),
    ):
        history = await _load_history("user-a", "conversation-a")

    assert history == [
        {"role": "user", "content": "hello"},
        {"role": "assistant", "content": "hi"},
    ]
