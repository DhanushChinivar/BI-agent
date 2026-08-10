"""Fetch, chunk, embed, and store a user's unstructured connector data.

Runs in two situations: a backfill when a connector is first authorised, and an
incremental resync on the schedule ticker. Both land here, because the only
difference between them is which resources come back needing work.
"""
import logging
from datetime import UTC, datetime

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app import mcp_client
from app.config.settings import get_settings
from app.db.engine import get_session_factory
from app.db.models import DocumentChunk, IndexedResource
from app.rag import TEXT_CONNECTORS
from app.rag.chunking import chunk
from app.rag.embeddings import EmbeddingsUnavailableError, embed_documents, enabled

logger = logging.getLogger(__name__)

# Ceiling per resource. A mailing-list thread or a wiki page with a thousand
# sections would otherwise dominate both the index and the embedding bill.
_MAX_CHUNKS_PER_RESOURCE = 200
# Resources touched in a single sync pass, so one enormous account cannot hold
# the ticker open indefinitely.
_MAX_RESOURCES_PER_PASS = 50


def _revision(resource: dict) -> str | None:
    """The cheapest per-resource change signal the provider gives us.

    Gmail exposes `historyId`, Notion `last_edited_time`. Falling back to the
    title is deliberate: a resource with no revision signal re-indexes only when
    it is renamed, which is wrong but bounded — far better than re-embedding
    every resource on every tick because the field was missing.
    """
    for key in ("historyId", "last_edited_time", "modified", "modifiedTime"):
        value = resource.get(key)
        if value:
            return str(value)[:128]
    title = resource.get("title")
    return f"title:{title}"[:128] if title else None


async def _existing(
    session: AsyncSession, user_id: str, connector: str
) -> dict[str, IndexedResource]:
    rows = await session.scalars(
        select(IndexedResource).where(
            IndexedResource.user_id == user_id,
            IndexedResource.connector == connector,
        )
    )
    return {row.resource_id: row for row in rows.all()}


def _needs_work(record: IndexedResource | None, revision: str | None, model: str) -> bool:
    if record is None:
        return True
    # A model change invalidates the vectors even when the content is identical:
    # embeddings from two different models are not comparable, so a mixed index
    # silently returns nonsense neighbours.
    if record.embedding_model != model:
        return True
    if record.status != "ok":
        return True
    return record.revision != revision


async def _replace_chunks(
    session: AsyncSession,
    user_id: str,
    connector: str,
    resource_id: str,
    title: str,
    contents: list[str],
    vectors: list[list[float]],
    model: str,
) -> None:
    """Swap a resource's chunks for a fresh set, in one transaction.

    Delete-then-insert rather than diffing: chunk boundaries move when the
    document changes, so matching old chunks to new ones is guesswork, and a
    half-updated resource is worse than a briefly missing one.
    """
    await session.execute(
        delete(DocumentChunk).where(
            DocumentChunk.user_id == user_id,
            DocumentChunk.connector == connector,
            DocumentChunk.resource_id == resource_id,
        )
    )
    session.add_all(
        [
            DocumentChunk(
                user_id=user_id,
                connector=connector,
                resource_id=resource_id,
                resource_title=title[:512],
                chunk_index=i,
                content=content,
                embedding=vector,
                embedding_model=model,
            )
            for i, (content, vector) in enumerate(zip(contents, vectors, strict=True))
        ]
    )


async def _record(
    session: AsyncSession,
    user_id: str,
    connector: str,
    resource_id: str,
    title: str,
    revision: str | None,
    chunk_count: int,
    model: str,
    status: str = "ok",
    error: str | None = None,
) -> None:
    record = await session.scalar(
        select(IndexedResource).where(
            IndexedResource.user_id == user_id,
            IndexedResource.connector == connector,
            IndexedResource.resource_id == resource_id,
        )
    )
    if record is None:
        record = IndexedResource(
            user_id=user_id, connector=connector, resource_id=resource_id
        )
        session.add(record)

    record.title = title[:512]
    record.revision = revision
    record.chunk_count = chunk_count
    record.embedding_model = model
    record.status = status
    record.last_error = error
    record.indexed_at = datetime.now(UTC)


async def sync_connector(user_id: str, connector: str) -> dict:
    """Bring one connector's index up to date for one user.

    Returns a summary rather than raising: a Notion outage should not abort the
    Gmail half of the same pass.
    """
    if connector not in TEXT_CONNECTORS:
        return {"connector": connector, "skipped": "not a text connector"}
    if not enabled():
        return {"connector": connector, "skipped": "embeddings not configured"}

    model = get_settings().embedding_model
    summary = {"connector": connector, "indexed": 0, "skipped": 0, "failed": 0, "chunks": 0}

    try:
        resources = await mcp_client.list_resources(connector, user_id)
    except Exception as exc:
        logger.warning("Index listing failed for %s/%s: %s", user_id, connector, exc)
        return {**summary, "error": str(exc)[:300]}

    if not isinstance(resources, list):
        return {**summary, "error": "connector returned no resource list"}

    factory = get_session_factory()
    async with factory() as session:
        known = await _existing(session, user_id, connector)

    for resource in resources[:_MAX_RESOURCES_PER_PASS]:
        if not isinstance(resource, dict) or not resource.get("id"):
            continue
        resource_id = str(resource["id"])
        title = str(resource.get("title") or "")
        revision = _revision(resource)

        if not _needs_work(known.get(resource_id), revision, model):
            summary["skipped"] += 1
            continue

        try:
            payload = await mcp_client.read(connector, user_id, resource_id)
            chunks = chunk(connector, payload, resource_id, title)[:_MAX_CHUNKS_PER_RESOURCE]

            if not chunks:
                # An empty resource is still "handled" — record it so the next
                # pass does not retry it forever.
                async with factory() as session:
                    await _replace_chunks(
                        session, user_id, connector, resource_id, title, [], [], model
                    )
                    await _record(
                        session, user_id, connector, resource_id, title, revision, 0, model
                    )
                    await session.commit()
                summary["indexed"] += 1
                continue

            vectors = await embed_documents([c.content for c in chunks])

            async with factory() as session:
                await _replace_chunks(
                    session,
                    user_id,
                    connector,
                    resource_id,
                    title,
                    [c.content for c in chunks],
                    vectors,
                    model,
                )
                await _record(
                    session,
                    user_id,
                    connector,
                    resource_id,
                    title,
                    revision,
                    len(chunks),
                    model,
                )
                await session.commit()

            summary["indexed"] += 1
            summary["chunks"] += len(chunks)

        except EmbeddingsUnavailableError as exc:
            # Voyage being down stops the whole pass — every remaining resource
            # would fail the same way, and retrying them all just burns time.
            logger.error("Embeddings unavailable, aborting sync: %s", exc)
            summary["failed"] += 1
            return {**summary, "error": str(exc)[:300]}
        except Exception as exc:
            logger.warning("Indexing %s/%s failed: %s", connector, resource_id, exc)
            summary["failed"] += 1
            async with factory() as session:
                await _record(
                    session,
                    user_id,
                    connector,
                    resource_id,
                    title,
                    revision,
                    0,
                    model,
                    status="error",
                    error=str(exc)[:500],
                )
                await session.commit()

    return summary


async def sync_user(user_id: str, connectors: list[str] | None = None) -> list[dict]:
    """Sync every indexable connector for a user."""
    targets = [c for c in (connectors or sorted(TEXT_CONNECTORS)) if c in TEXT_CONNECTORS]
    return [await sync_connector(user_id, c) for c in targets]


async def drop_connector_index(user_id: str, connector: str) -> int:
    """Remove a user's index for a connector. Called on disconnect.

    Deleting the credential stops future reads; without this the *content*
    stays queryable indefinitely, which is a longer-lived leak than the Redis
    cache it sits next to.
    """
    factory = get_session_factory()
    async with factory() as session:
        result = await session.execute(
            delete(DocumentChunk).where(
                DocumentChunk.user_id == user_id,
                DocumentChunk.connector == connector,
            )
        )
        await session.execute(
            delete(IndexedResource).where(
                IndexedResource.user_id == user_id,
                IndexedResource.connector == connector,
            )
        )
        await session.commit()
        return result.rowcount or 0
