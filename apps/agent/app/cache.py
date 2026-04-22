"""Redis cache helpers for connector reads."""
import json
import logging
from functools import lru_cache
from typing import Any

import redis.asyncio as aioredis

from app.config.settings import get_settings

logger = logging.getLogger(__name__)
_TTL = 300  # 5 minutes


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


async def cache_invalidate(user_id: str, connector: str) -> None:
    """Remove all cached resources for a user+connector (e.g. on disconnect)."""
    try:
        redis = get_redis()
        pattern = _key(user_id, connector, "*")
        keys = await redis.keys(pattern)
        if keys:
            await redis.delete(*keys)
    except Exception as exc:
        logger.warning("Cache invalidation failed: %s", exc)
