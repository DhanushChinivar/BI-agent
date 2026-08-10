"""RAG index endpoints.

`/v1/index/sync-due` is called by the n8n ticker on the same schedule as
`/v1/schedules/run-due`; the rest are for the UI. Indexing is not metered
against the query quota — it costs embedding tokens, not Claude calls, and
charging a user for background upkeep they did not ask for would make the free
plan unusable.
"""
import hashlib
import hmac
import logging

from fastapi import APIRouter, Header, HTTPException, Request
from sqlalchemy import func, select

from app.config.settings import get_settings
from app.db.engine import get_session_factory
from app.db.models import DocumentChunk, IndexedResource, UserConnectorCredential
from app.rag import TEXT_CONNECTORS
from app.rag.embeddings import enabled
from app.rag.ingest import sync_user

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/v1", tags=["index"])

# Users touched per tick. Sync is incremental, so a pass over an unchanged
# account is a listing call and nothing else — but the embedding bill for a
# large first backfill is real, and this bounds how much of it lands at once.
_MAX_USERS_PER_TICK = 20


def _verify_signature(body: bytes, signature: str, secret: str) -> bool:
    if not signature.startswith("sha256="):
        return False
    expected = "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature)


@router.get("/index/status")
async def index_status(request: Request) -> dict:
    """What is indexed for the calling user, and how fresh it is."""
    user_id = request.state.user_id
    factory = get_session_factory()

    async with factory() as session:
        resources = (
            await session.scalars(
                select(IndexedResource)
                .where(IndexedResource.user_id == user_id)
                .order_by(IndexedResource.indexed_at.desc())
            )
        ).all()
        chunk_counts = dict(
            (
                await session.execute(
                    select(DocumentChunk.connector, func.count(DocumentChunk.id))
                    .where(DocumentChunk.user_id == user_id)
                    .group_by(DocumentChunk.connector)
                )
            ).all()
        )

    by_connector: dict[str, dict] = {}
    for row in resources:
        entry = by_connector.setdefault(
            row.connector,
            {
                "connector": row.connector,
                "resources": 0,
                "chunks": chunk_counts.get(row.connector, 0),
                "errors": 0,
                "last_indexed_at": None,
            },
        )
        entry["resources"] += 1
        if row.status != "ok":
            entry["errors"] += 1
        stamp = row.indexed_at.isoformat() if row.indexed_at else None
        if stamp and (entry["last_indexed_at"] is None or stamp > entry["last_indexed_at"]):
            entry["last_indexed_at"] = stamp

    return {
        # Surfaced so the UI can explain an empty index as "not configured"
        # rather than as "nothing found".
        "embeddings_enabled": enabled(),
        "indexable_connectors": sorted(TEXT_CONNECTORS),
        "connectors": [by_connector[k] for k in sorted(by_connector)],
    }


@router.post("/index/sync")
async def sync_now(request: Request) -> dict:
    """Re-index the calling user's connectors immediately."""
    if not enabled():
        raise HTTPException(status_code=503, detail="Embeddings are not configured")
    return {"results": await sync_user(request.state.user_id)}


@router.post("/index/sync-due")
async def sync_due(
    request: Request,
    x_hub_signature_256: str = Header(alias="x-hub-signature-256", default=""),
) -> dict:
    """Re-index every user who has an indexable connector connected.

    HMAC-verified for the same reason `/v1/schedules/run-due` is: the route is
    exempt from the Clerk JWT and spends money on behalf of arbitrary users.
    """
    settings = get_settings()
    body = await request.body()
    if not _verify_signature(body, x_hub_signature_256, settings.webhook_secret):
        raise HTTPException(status_code=401, detail="Invalid webhook signature")

    if not enabled():
        # 200, not an error: a deployment without Voyage is a supported
        # configuration, and the ticker should not be alarming about it.
        return {"synced": 0, "results": [], "skipped": "embeddings not configured"}

    factory = get_session_factory()
    async with factory() as session:
        # Driven by who has a connector authorised, not by who has an index —
        # otherwise a newly connected account is never picked up, because it has
        # no `indexed_resources` rows to find it by.
        user_ids = (
            await session.scalars(
                select(UserConnectorCredential.user_id)
                .where(UserConnectorCredential.connector.in_(sorted(TEXT_CONNECTORS)))
                .distinct()
                .limit(_MAX_USERS_PER_TICK)
            )
        ).all()

    if not user_ids:
        return {"synced": 0, "results": []}

    logger.info("Syncing RAG index for %d user(s)", len(user_ids))
    results = [{"user_id": uid, "connectors": await sync_user(uid)} for uid in user_ids]

    return {"synced": len(results), "results": results}
