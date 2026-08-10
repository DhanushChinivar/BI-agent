"""Redis cache helpers for connector reads."""
import json
import logging
from functools import lru_cache
from typing import Any

import redis.asyncio as aioredis

from app.config.settings import get_settings

logger = logging.getLogger(__name__)
_TTL = 300  # 5 minutes
_SCAN_BATCH = 500
_OAUTH_STATE_TTL = 600  # an authorisation the user never finished is dead after 10 min


@lru_cache(maxsize=1)
def get_redis() -> aioredis.Redis:
    return aioredis.from_url(get_settings().redis_url, decode_responses=True)


def _key(user_id: str, connector: str, resource_id: str) -> str:
    return f"connector:{connector}:{user_id}:{resource_id}"


async def cache_get(user_id: str, connector: str, resource_id: str) -> Any | None:
    try:
        raw = await get_redis().get(_key(user_id, connector, resource_id))
        return json.loads(raw) if raw else None
    except Exception as exc:
        logger.warning("Cache read failed: %s", exc)
        return None


async def cache_set(user_id: str, connector: str, resource_id: str, data: Any) -> None:
    try:
        await get_redis().set(_key(user_id, connector, resource_id), json.dumps(data), ex=_TTL)
    except Exception as exc:
        logger.warning("Cache write failed: %s", exc)


async def cache_invalidate(user_id: str, connector: str) -> int:
    """Remove all cached resources for a user+connector (e.g. on disconnect).

    Iterates with SCAN rather than KEYS: KEYS walks the entire keyspace in one
    blocking call, and Redis is single-threaded, so on a shared instance it
    stalls every other client for the duration.
    """
    removed = 0
    try:
        redis = get_redis()
        pattern = _key(user_id, connector, "*")
        batch: list[str] = []
        async for key in redis.scan_iter(match=pattern, count=_SCAN_BATCH):
            batch.append(key)
            if len(batch) >= _SCAN_BATCH:
                removed += await redis.delete(*batch)
                batch = []
        if batch:
            removed += await redis.delete(*batch)
    except Exception as exc:
        logger.warning("Cache invalidation failed: %s", exc)
    return removed


# ── OAuth state ───────────────────────────────────────────────────────────────
#
# The pending-authorisation store used to be a module-level dict, which made the
# service single-process by accident: with two Uvicorn workers the callback
# landed on whichever worker the load balancer picked, and half of all OAuth
# attempts failed with "Invalid or expired OAuth state". Redis also gives the
# entry an expiry, so an abandoned flow stops being a usable state token.


def _oauth_key(state: str) -> str:
    return f"oauth:state:{state}"


async def oauth_state_put(state: str, payload: dict) -> None:
    await get_redis().set(_oauth_key(state), json.dumps(payload), ex=_OAUTH_STATE_TTL)


async def oauth_state_take(state: str) -> dict | None:
    """Consume a pending OAuth state. Returns None if unknown or already used.

    GETDEL makes read-and-delete atomic, so a replayed callback cannot redeem
    the same state twice — with a separate GET then DELETE, two concurrent
    callbacks both read the entry before either removed it.
    """
    raw = await get_redis().getdel(_oauth_key(state))
    return json.loads(raw) if raw else None
