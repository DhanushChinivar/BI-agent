"""Redis-backed per-user file storage for uploaded datasets."""
from __future__ import annotations

import json
import uuid
from typing import Any

from app.cache import get_redis as _redis

_TTL = 60 * 60 * 24 * 7  # 7 days


def _index_key(user_id: str) -> str:
    return f"uploads:{user_id}:index"


def _data_key(user_id: str, file_id: str) -> str:
    return f"uploads:{user_id}:{file_id}"


async def store_file(
    user_id: str, filename: str, rows: list[dict[str, Any]]
) -> str:
    """Persist parsed rows in Redis; return the new file_id."""
    r = _redis()
    file_id = str(uuid.uuid4())

    payload = json.dumps({"file_id": file_id, "title": filename, "rows": rows})
    await r.setex(_data_key(user_id, file_id), _TTL, payload)

    # Update index
    raw = await r.get(_index_key(user_id))
    index: list[dict] = json.loads(raw) if raw else []
    index.append({"file_id": file_id, "title": filename})
    await r.setex(_index_key(user_id), _TTL, json.dumps(index))

    return file_id


async def list_files(user_id: str) -> list[dict[str, str]]:
    """Return [{file_id, title}, ...] for the user."""
    r = _redis()
    raw = await r.get(_index_key(user_id))
    return json.loads(raw) if raw else []


async def get_file(user_id: str, file_id: str) -> dict[str, Any] | None:
    """Return {file_id, title, rows} or None if not found."""
    r = _redis()
    raw = await r.get(_data_key(user_id, file_id))
    return json.loads(raw) if raw else None


async def delete_file(user_id: str, file_id: str) -> bool:
    """Remove a file and update the index. Returns True if found."""
    r = _redis()
    deleted = await r.delete(_data_key(user_id, file_id))
    if not deleted:
        return False

    index_raw = await r.get(_index_key(user_id))
    index: list[dict] = json.loads(index_raw) if index_raw else []
    index = [e for e in index if e["file_id"] != file_id]
    await r.setex(_index_key(user_id), _TTL, json.dumps(index))
    return True
