"""Cache and OAuth-state store tests.

Both stores shipped with a defect that only shows up outside a single-process
dev run: `cache_invalidate` had no callers at all, and the OAuth pending-state
map was a module-level dict, so a second Uvicorn worker broke the callback.
"""
import json

import pytest

from app import cache


class FakeRedis:
    """Enough of redis.asyncio to exercise the code paths we actually use.

    `keys` is deliberately absent: if `cache_invalidate` regresses to KEYS this
    raises rather than passing quietly.
    """

    def __init__(self) -> None:
        self.store: dict[str, str] = {}
        self.scan_calls = 0
        self.delete_calls: list[tuple[str, ...]] = []

    async def get(self, key):
        return self.store.get(key)

    async def set(self, key, value, ex=None):
        self.store[key] = value

    async def getdel(self, key):
        return self.store.pop(key, None)

    async def delete(self, *keys):
        self.delete_calls.append(keys)
        return sum(self.store.pop(k, None) is not None for k in keys)

    async def scan_iter(self, match=None, count=None):
        self.scan_calls += 1
        prefix = match.rstrip("*")
        for key in list(self.store):
            if key.startswith(prefix):
                yield key


@pytest.fixture
def redis(monkeypatch):
    fake = FakeRedis()
    monkeypatch.setattr(cache, "get_redis", lambda: fake)
    return fake


# ── connector payload cache ───────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_invalidate_removes_only_the_named_users_connector(redis):
    await cache.cache_set("u1", "gmail", "t1", {"a": 1})
    await cache.cache_set("u1", "gmail", "t2", {"a": 2})
    await cache.cache_set("u1", "notion", "p1", {"a": 3})
    await cache.cache_set("u2", "gmail", "t1", {"a": 4})

    removed = await cache.cache_invalidate("u1", "gmail")

    assert removed == 2
    assert await cache.cache_get("u1", "gmail", "t1") is None
    # A disconnect must not evict the user's other sources, or another user's.
    assert await cache.cache_get("u1", "notion", "p1") == {"a": 3}
    assert await cache.cache_get("u2", "gmail", "t1") == {"a": 4}


@pytest.mark.asyncio
async def test_invalidate_uses_scan_not_keys(redis):
    """KEYS blocks single-threaded Redis for the whole keyspace walk."""
    await cache.cache_set("u1", "gmail", "t1", {"a": 1})

    await cache.cache_invalidate("u1", "gmail")

    assert redis.scan_calls == 1


@pytest.mark.asyncio
async def test_invalidate_deletes_in_batches(redis, monkeypatch):
    monkeypatch.setattr(cache, "_SCAN_BATCH", 2)
    for i in range(5):
        await cache.cache_set("u1", "gmail", f"t{i}", {"i": i})

    removed = await cache.cache_invalidate("u1", "gmail")

    assert removed == 5
    # 2 + 2 + 1 — never one DELETE with thousands of arguments.
    assert [len(c) for c in redis.delete_calls] == [2, 2, 1]


@pytest.mark.asyncio
async def test_invalidate_survives_an_unreachable_redis(monkeypatch):
    """A disconnect must still return 200 if the cache is down."""
    class Broken:
        def scan_iter(self, **_):
            raise ConnectionError("redis down")

    monkeypatch.setattr(cache, "get_redis", lambda: Broken())

    assert await cache.cache_invalidate("u1", "gmail") == 0


# ── OAuth state ───────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_oauth_state_round_trips(redis):
    await cache.oauth_state_put("s1", {"user_id": "u1", "connector": "gmail"})

    assert await cache.oauth_state_take("s1") == {"user_id": "u1", "connector": "gmail"}


@pytest.mark.asyncio
async def test_oauth_state_is_single_use(redis):
    """A replayed callback must not redeem the same state twice."""
    await cache.oauth_state_put("s1", {"user_id": "u1", "connector": "gmail"})

    assert await cache.oauth_state_take("s1") is not None
    assert await cache.oauth_state_take("s1") is None


@pytest.mark.asyncio
async def test_unknown_oauth_state_is_none(redis):
    assert await cache.oauth_state_take("never-issued") is None


@pytest.mark.asyncio
async def test_oauth_state_carries_an_expiry(redis, monkeypatch):
    """An abandoned authorisation must stop being a usable state token."""
    seen = {}

    async def spy(key, value, ex=None):
        seen["ex"] = ex
        redis.store[key] = value

    monkeypatch.setattr(redis, "set", spy)
    await cache.oauth_state_put("s1", {"user_id": "u1", "connector": "gmail"})

    assert seen["ex"] == cache._OAUTH_STATE_TTL


@pytest.mark.asyncio
async def test_oauth_state_survives_a_process_restart(redis):
    """The point of the change: state lives in Redis, not in module memory.

    Reloading the module drops any in-process dict; the pending flow must
    survive that, because with two workers the callback lands on a process that
    never ran /start.
    """
    await cache.oauth_state_put("s1", {"user_id": "u1", "connector": "notion"})
    assert json.loads(redis.store["oauth:state:s1"])["user_id"] == "u1"

    import importlib

    importlib.reload(cache)
    try:
        assert redis.store["oauth:state:s1"]  # untouched by the reload
    finally:
        importlib.reload(cache)
