"""Auth-boundary tests.

Two things are pinned here, both of which shipped broken and neither of which
had coverage:

1. Routes that authenticate by their own mechanism (OAuth `state`, Stripe
   signature, n8n HMAC) must not be gated on a Clerk JWT they never carry —
   while everything else stays closed under APP_ENV=production.
2. Conversation history is scoped to its owner, so a caller cannot pass another
   user's conversation_id and have their messages loaded as context.
"""
import base64
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from jose import JWTError, jwt

from app.api.query import _assert_conversation_owned, _load_history
from app.config.settings import get_settings
from app.middleware import auth
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
    """A 400 on the OAuth `state` means the request got past AuthMiddleware.

    The state store is patched because it is Redis now, not a module-level
    dict — without this the test would assert on a connection error instead of
    on the auth boundary it is about.
    """
    with patch(
        "app.api.oauth.oauth_state_take", new_callable=AsyncMock, return_value=None
    ):
        resp = prod_client.get("/v1/oauth/google-sheets/callback?code=x&state=bogus")

    assert resp.status_code == 400
    assert resp.json()["detail"] == "Invalid or expired OAuth state"


def test_oauth_callback_fails_closed_when_the_state_store_is_down(prod_client):
    """503, not "invalid state" — otherwise a Redis outage looks to the user
    like a broken authorisation and sends them round the consent loop forever."""
    from redis.exceptions import ConnectionError as RedisConnectionError

    with patch(
        "app.api.oauth.oauth_state_take",
        new_callable=AsyncMock,
        side_effect=RedisConnectionError("redis down"),
    ):
        resp = prod_client.get("/v1/oauth/gmail/callback?code=x&state=whatever")

    assert resp.status_code == 503


def test_a_state_minted_for_one_connector_is_not_redeemable_at_another(prod_client):
    """Otherwise a Gmail authorisation could be completed at the Sheets
    callback, storing Gmail's tokens under the Sheets connector."""
    with patch(
        "app.api.oauth.oauth_state_take",
        new_callable=AsyncMock,
        return_value={"user_id": "u1", "connector": "gmail"},
    ):
        resp = prod_client.get("/v1/oauth/google-sheets/callback?code=x&state=s1")

    assert resp.status_code == 400


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


# ── JWT verification ──────────────────────────────────────────────────────────

class _Signer:
    """An RSA keypair plus the JWKS entry a verifier needs for it.

    Real signatures, not mocks: issuer and audience are checked *by* `jwt.decode`,
    so faking the decode would test nothing.
    """

    def __init__(self, kid: str = "kid-1"):
        from cryptography.hazmat.primitives.asymmetric import rsa

        self.kid = kid
        self._key = rsa.generate_private_key(public_exponent=65537, key_size=2048)

    def sign(self, claims: dict) -> str:
        from cryptography.hazmat.primitives import serialization

        pem = self._key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption(),
        ).decode()
        return jwt.encode(claims, pem, algorithm="RS256", headers={"kid": self.kid})

    def jwks(self) -> dict:
        numbers = self._key.public_key().public_numbers()

        def b64(value: int) -> str:
            raw = value.to_bytes((value.bit_length() + 7) // 8, "big")
            return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()

        return {
            "keys": [
                {
                    "kty": "RSA",
                    "kid": self.kid,
                    "use": "sig",
                    "alg": "RS256",
                    "n": b64(numbers.n),
                    "e": b64(numbers.e),
                }
            ]
        }


ISSUER_HOST = "clerk.example.com"


@pytest.fixture
def signer(monkeypatch):
    monkeypatch.setenv("CLERK_FRONTEND_API", ISSUER_HOST)
    monkeypatch.delenv("CLERK_JWT_AUDIENCE", raising=False)
    get_settings.cache_clear()
    auth._reset_jwks_cache()

    s = _Signer()
    monkeypatch.setattr(auth, "_fetch_jwks", AsyncMock(return_value=s.jwks()))
    yield s

    auth._reset_jwks_cache()
    get_settings.cache_clear()


def _claims(**overrides) -> dict:
    base = {
        "sub": "user_abc",
        "iss": f"https://{ISSUER_HOST}",
        "exp": int(time.time()) + 300,
        "iat": int(time.time()),
    }
    return {**base, **overrides}


@pytest.mark.asyncio
async def test_valid_token_yields_its_subject(signer):
    assert await auth._verify_token(signer.sign(_claims())) == "user_abc"


@pytest.mark.asyncio
async def test_token_from_another_clerk_instance_is_rejected(signer):
    """The bug this pins: a valid signature alone proved nothing about *whose*
    Clerk instance minted the token, so a `sub` from an attacker-controlled
    instance became a user id in our database."""
    token = signer.sign(_claims(iss="https://clerk.attacker.example"))

    with pytest.raises(JWTError):
        await auth._verify_token(token)


@pytest.mark.asyncio
async def test_audience_is_only_enforced_when_configured(signer, monkeypatch):
    """Clerk session tokens carry no `aud` unless a JWT template adds one."""
    assert await auth._verify_token(signer.sign(_claims())) == "user_abc"

    monkeypatch.setenv("CLERK_JWT_AUDIENCE", "bi-agent")
    get_settings.cache_clear()

    with pytest.raises(JWTError):
        await auth._verify_token(signer.sign(_claims()))
    assert await auth._verify_token(signer.sign(_claims(aud="bi-agent"))) == "user_abc"


@pytest.mark.asyncio
async def test_token_without_a_subject_is_rejected(signer):
    with pytest.raises(JWTError):
        await auth._verify_token(signer.sign(_claims(sub="")))


@pytest.mark.asyncio
async def test_expired_token_is_rejected(signer):
    with pytest.raises(JWTError):
        await auth._verify_token(signer.sign(_claims(exp=int(time.time()) - 10)))


@pytest.mark.asyncio
async def test_jwks_is_fetched_once_and_cached(signer):
    for _ in range(3):
        await auth._verify_token(signer.sign(_claims()))

    assert auth._fetch_jwks.await_count == 1


@pytest.mark.asyncio
async def test_unknown_kid_refetches_the_jwks_once(signer, monkeypatch):
    """Key rotation used to need a service restart: the JWKS was `lru_cache`d
    for the process lifetime, so every token signed with a new key 401'd."""
    await auth._verify_token(signer.sign(_claims()))  # warms the cache
    rotated = _Signer(kid="kid-2")
    auth._fetch_jwks.return_value = rotated.jwks()

    assert await auth._verify_token(rotated.sign(_claims())) == "user_abc"
    assert auth._fetch_jwks.await_count == 2


@pytest.mark.asyncio
async def test_a_still_unknown_kid_does_not_refetch_forever(signer):
    await auth._verify_token(signer.sign(_claims()))
    stranger = _Signer(kid="kid-unknown")

    with pytest.raises(JWTError):
        await auth._verify_token(stranger.sign(_claims()))
    assert auth._fetch_jwks.await_count == 2


@pytest.mark.parametrize(
    "configured,expected",
    [
        ("clerk.example.com", "https://clerk.example.com"),
        # A full origin in the env var is a plausible misconfiguration, and
        # naive prefixing would build "https://https://..." and reject everything.
        ("https://clerk.example.com", "https://clerk.example.com"),
        ("clerk.example.com/", "https://clerk.example.com"),
    ],
)
def test_issuer_normalises_the_configured_host(monkeypatch, configured, expected):
    monkeypatch.setenv("CLERK_FRONTEND_API", configured)
    get_settings.cache_clear()
    try:
        assert auth._issuer() == expected
    finally:
        get_settings.cache_clear()


# ── OAuth PKCE round trip ─────────────────────────────────────────────────────

def test_start_persists_the_pkce_code_verifier():
    """The regression this pins.

    Moving OAuth state to Redis stopped storing the `Flow` and rebuilt it in the
    callback, on the reasoning that a Flow is "fully determined by the redirect
    URI and scopes". It is not: `authorization_url()` generates a PKCE verifier
    on the instance and sends only its hash to Google, and `fetch_token` must
    present the original. Losing it fails *after* the user has approved consent,
    with `invalid_grant: Missing code verifier` — about as late and as confusing
    as an OAuth failure can be.
    """
    from types import SimpleNamespace

    from app.api.oauth import _google_pending

    flow = SimpleNamespace(code_verifier="verifier-generated-at-start")

    assert _google_pending("u1", "gmail", flow) == {
        "user_id": "u1",
        "connector": "gmail",
        "code_verifier": "verifier-generated-at-start",
    }


@pytest.mark.asyncio
async def test_the_callback_reattaches_the_verifier_before_exchanging(prod_client):
    """End to end through the route: whatever /start stored must be on the Flow
    that calls fetch_token."""
    from app.api import oauth

    seen: dict = {}

    class _Flow:
        code_verifier = None

        def fetch_token(self, code):
            seen["verifier"] = self.code_verifier
            seen["code"] = code

        @property
        def credentials(self):
            return SimpleNamespace(token="at", refresh_token="rt")

    with (
        patch.object(oauth, "_google_flow", lambda *a, **k: _Flow()),
        patch.object(
            oauth,
            "oauth_state_take",
            new_callable=AsyncMock,
            return_value={
                "user_id": "u1",
                "connector": "gmail",
                "code_verifier": "the-original-verifier",
            },
        ),
        patch.object(oauth, "upsert_credentials", new_callable=AsyncMock),
        patch.object(oauth, "get_session_factory", lambda: _null_session_factory()),
        patch.object(oauth, "sync_connector", new_callable=AsyncMock),
    ):
        prod_client.get(
            "/v1/oauth/gmail/callback?code=auth-code&state=s1", follow_redirects=False
        )

    assert seen["verifier"] == "the-original-verifier"
    assert seen["code"] == "auth-code"


def _null_session_factory():
    class _Ctx:
        async def __aenter__(self):
            return AsyncMock()

        async def __aexit__(self, *_):
            return False

    return lambda: _Ctx()
