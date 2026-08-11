"""Vector search over a user's indexed chunks."""
import logging

from sqlalchemy import select

from app.db.engine import get_session_factory
from app.db.models import DocumentChunk
from app.rag.embeddings import EmbeddingsUnavailableError, embed_query, enabled
from app.rag.rerank import rerank

logger = logging.getLogger(__name__)

_TOP_K = 12

# How many chunks the vector index shortlists before reranking. Wider than the
# final k on purpose: the cross-encoder can only promote what the bi-encoder
# handed it, so recall here bounds the quality of everything downstream. The
# extra rows cost one database read, not one API call each.
_CANDIDATE_MULTIPLIER = 4
_MAX_CANDIDATES = 60

# Cosine distance: 0 is identical, 2 is opposite. This is a floor against absurd
# matches, *not* a relevance filter — measured against real voyage-3-lite vectors
# (scripts/calibrate_retrieval.py), the two populations overlap:
#
#   correct matches      0.314 to 0.629
#   nothing-answers-this 0.554 to 0.817
#
# There is no value that keeps every true hit and drops every false one, so the
# cut is placed to keep all of the former. The asymmetry justifies it: a passage
# that reaches the analyst but does not answer the question costs some tokens and
# the analyst says so, while a true answer cut here is simply lost and the agent
# confidently reports it has no data. Making the finer call is `rerank`'s job.
_MAX_DISTANCE = 0.75


async def search(
    user_id: str, question: str, connectors: list[str] | None = None, k: int = _TOP_K
) -> list[dict]:
    """The passages most relevant to `question`, scoped to one user.

    Two stages. pgvector shortlists cheaply by cosine distance over the whole
    index, then a cross-encoder reranks that shortlist by actually reading the
    question and each passage together. The second stage is what makes
    "nothing here answers this" a decision the system can make — measurement
    showed distance alone cannot separate a genuine match from a topically
    adjacent miss.

    Returns [] rather than raising when embeddings are unconfigured or Voyage is
    unreachable, so the retriever falls back to provider search instead of
    failing the whole question.
    """
    if not enabled():
        return []

    try:
        vector = await embed_query(question)
    except EmbeddingsUnavailableError as exc:
        logger.warning("Vector search unavailable: %s", exc)
        return []

    distance = DocumentChunk.embedding.cosine_distance(vector)

    stmt = (
        select(
            DocumentChunk.connector,
            DocumentChunk.resource_id,
            DocumentChunk.resource_title,
            DocumentChunk.chunk_index,
            DocumentChunk.content,
            distance.label("distance"),
        )
        # user_id is not optional and never comes from the request body — one
        # missing predicate here would serve another tenant's email as context.
        .where(DocumentChunk.user_id == user_id)
        .order_by(distance)
        .limit(min(k * _CANDIDATE_MULTIPLIER, _MAX_CANDIDATES))
    )
    if connectors:
        stmt = stmt.where(DocumentChunk.connector.in_(connectors))

    factory = get_session_factory()
    async with factory() as session:
        rows = (await session.execute(stmt)).all()

    candidates = [
        {
            "connector": r.connector,
            "resource_id": r.resource_id,
            "resource_title": r.resource_title,
            "chunk_index": r.chunk_index,
            "content": r.content,
            "distance": float(r.distance),
            # 0 distance → 1.0. Reported alongside the raw distance because
            # "score" is what a reader expects and distance sorts backwards.
            # `rerank` overwrites this with its own calibrated relevance.
            "score": round(1.0 - float(r.distance) / 2.0, 4),
        }
        for r in rows
        if float(r.distance) <= _MAX_DISTANCE
    ]

    # Never raises: a rerank outage returns the vector ordering, which is what
    # this function did before the second stage existed.
    return await rerank(question, candidates, k)


def group_by_resource(hits: list[dict]) -> list[dict]:
    """Collapse chunk hits into one entry per source document.

    The analyst reads better from "this thread, these three relevant passages"
    than from twelve loose fragments, and it makes a citation point at a
    document rather than at an offset.
    """
    grouped: dict[tuple[str, str], dict] = {}

    for hit in hits:
        key = (hit["connector"], hit["resource_id"])
        entry = grouped.get(key)
        if entry is None:
            entry = {
                "connector": hit["connector"],
                "resource_id": hit["resource_id"],
                "title": hit["resource_title"],
                "passages": [],
                "best_score": hit["score"],
            }
            grouped[key] = entry
        entry["passages"].append(
            {"chunk_index": hit["chunk_index"], "text": hit["content"], "score": hit["score"]}
        )
        entry["best_score"] = max(entry["best_score"], hit["score"])

    return sorted(grouped.values(), key=lambda e: -e["best_score"])
