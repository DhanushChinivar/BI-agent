"""GET /v1/connectors/status — reports connected state per connector."""
from fastapi import APIRouter, HTTPException, Request
from sqlalchemy import select

from app.cache import cache_invalidate
from app.connectors import REGISTRY
from app.db.engine import get_session_factory
from app.db.models import UserConnectorCredential
from app.rag.ingest import drop_connector_index

router = APIRouter(prefix="/v1", tags=["connectors"])


@router.get("/connectors/status")
async def connectors_status(request: Request) -> dict:
    # Use only the verified identity; a query param would let a caller read
    # another user's connector status.
    user_id = request.state.user_id
    factory = get_session_factory()
    async with factory() as session:
        rows = (
            await session.scalars(
                select(UserConnectorCredential).where(
                    UserConnectorCredential.user_id == user_id
                )
            )
        ).all()

    connected = {row.connector: row.updated_at for row in rows}

    statuses = []
    for name in REGISTRY:
        if name == "mock":
            continue
        updated = connected.get(name)
        statuses.append({
            "connector": name,
            "connected": name in connected,
            "last_updated": updated.isoformat() if updated else None,
        })

    return {"user_id": user_id, "connectors": statuses}


@router.delete("/connectors/{connector_name}")
async def disconnect_connector(connector_name: str, request: Request) -> dict:
    # Verified identity only — otherwise a caller could disconnect another
    # user's sources by passing ?user_id=victim.
    user_id = request.state.user_id
    if connector_name not in REGISTRY:
        raise HTTPException(status_code=404, detail=f"Unknown connector: {connector_name}")

    factory = get_session_factory()
    async with factory() as session:
        row = await session.scalar(
            select(UserConnectorCredential).where(
                UserConnectorCredential.user_id == user_id,
                UserConnectorCredential.connector == connector_name,
            )
        )
        if row:
            await session.delete(row)
            await session.commit()

    # Deleting the credential revokes future reads but not past ones: connector
    # payloads live in Redis for `_TTL`, so without this the user's spreadsheet
    # rows and email bodies stay readable for five minutes after they
    # disconnected. `cache_invalidate` existed for exactly this and had no callers.
    cleared = await cache_invalidate(user_id, connector_name)

    # The vector index outlives the cache by design — it is durable storage, not
    # a 5-minute TTL — so without this a disconnected mailbox stays semantically
    # searchable forever. Same reasoning as the cache, longer blast radius.
    chunks_removed = await drop_connector_index(user_id, connector_name)

    return {
        "connector": connector_name,
        "disconnected": row is not None,
        "cache_entries_cleared": cleared,
        "index_chunks_removed": chunks_removed,
    }
