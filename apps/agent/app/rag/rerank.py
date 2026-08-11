"""Cross-encoder reranking of vector-search candidates.

Why this exists, with numbers. `scripts/calibrate_retrieval.py` measured the two
distance populations against real `voyage-3-lite` vectors:

    questions with a correct answer   0.314 to 0.629
    questions nothing in the corpus answers   0.554 to 0.817

They **overlap**. The worst true match sits further away than the best false
one, so no cosine cut separates them — a business-flavoured question about a
topic the corpus does not cover scores closer than a genuine match phrased
unusually. Bi-encoder similarity is the wrong instrument for that last call: it
compares two independently-produced vectors and never sees the question and the
passage together.

A cross-encoder does. It reads (question, passage) as one input and scores their
actual relationship, which is far more expensive — hence running it only over
the handful of candidates the vector index already shortlisted.
"""
import asyncio
import logging

import httpx

from app.config.settings import get_settings
from app.rag.embeddings import EmbeddingsUnavailableError, _retry_delay

logger = logging.getLogger(__name__)

_URL = "https://api.voyageai.com/v1/rerank"
_TIMEOUT = 30.0
_MAX_ATTEMPTS = 3

# Voyage caps a rerank request at 1000 documents; we send far fewer by design —
# the cross-encoder is the expensive stage, so the vector index does the cheap
# culling first.
_MAX_DOCUMENTS = 100

# `relevance_score` is 0..1 and, unlike cosine distance, separates the two
# populations. Measured by `scripts/calibrate_retrieval.py` against the real
# model:
#
#   correct answers       down to 0.535
#   nothing answers this  up to   0.500
#
# So any cut in (0.500, 0.535) separates them — where no cut on distance could.
# This sits deliberately *below* that band rather than in the middle of it, for
# two reasons. The gap is only 0.035 wide and comes from ten hand-written
# questions, which is enough to place a floor and not enough to trust a
# midpoint. And the errors are asymmetric: a weak passage that reaches the
# analyst gets dismissed in the answer, while a true answer cut here is gone and
# the agent reports it has no data.
#
# At 0.48 the two clearest non-answers ("hiring plans" 0.422, "VPN password"
# 0.473) are cut and every true answer is kept with 0.055 to spare. One
# borderline case still gets through, by choice.
#
# An earlier value of 0.35 was a guess and did nothing at all — every non-answer
# scored above it.
_MIN_RELEVANCE = 0.48


def enabled() -> bool:
    settings = get_settings()
    return bool(settings.voyage_api_key and settings.rerank_model)


async def rerank(question: str, candidates: list[dict], top_n: int) -> list[dict]:
    """Reorder `candidates` by cross-encoder relevance and cut the tail.

    Returns the input unchanged when reranking is unconfigured or Voyage is
    unreachable: a degraded ordering is far better than losing the answer, and
    the vector ordering it falls back to is the one that shipped before this.
    """
    if not candidates or not enabled():
        return candidates

    documents = [c["content"] for c in candidates][:_MAX_DOCUMENTS]
    settings = get_settings()

    try:
        scored = await _post(
            {
                "query": question,
                "documents": documents,
                "model": settings.rerank_model,
                "top_k": min(top_n, len(documents)),
            }
        )
    except EmbeddingsUnavailableError as exc:
        logger.warning("Rerank unavailable, keeping vector order: %s", exc)
        return candidates[:top_n]

    out: list[dict] = []
    for entry in scored:
        index = entry.get("index")
        if not isinstance(index, int) or not 0 <= index < len(candidates):
            continue
        relevance = float(entry.get("relevance_score", 0.0))
        if relevance < _MIN_RELEVANCE:
            continue
        # Keep the vector distance alongside: it is what the ordering *was*, and
        # having both makes a surprising result diagnosable rather than magic.
        out.append({**candidates[index], "relevance": round(relevance, 4), "score": round(relevance, 4)})

    return out[:top_n]


async def _post(payload: dict) -> list[dict]:
    settings = get_settings()
    headers = {"Authorization": f"Bearer {settings.voyage_api_key}"}
    last: Exception | None = None

    async with httpx.AsyncClient(timeout=_TIMEOUT, headers=headers) as client:
        for attempt in range(_MAX_ATTEMPTS):
            try:
                resp = await client.post(_URL, json=payload)

                # Same split as the embeddings client: 429 and 5xx are worth
                # waiting on, a 4xx means the request itself is wrong.
                if resp.status_code >= 400 and not (
                    resp.status_code == 429 or resp.status_code >= 500
                ):
                    raise EmbeddingsUnavailableError(
                        f"Voyage rejected the rerank ({resp.status_code}): {resp.text[:200]}"
                    )
                if resp.status_code >= 400:
                    raise httpx.HTTPStatusError(
                        f"voyage {resp.status_code}", request=resp.request, response=resp
                    )

                return resp.json().get("data", [])
            except (httpx.HTTPStatusError, httpx.RequestError) as exc:
                last = exc
                if attempt == _MAX_ATTEMPTS - 1:
                    break
                await asyncio.sleep(_retry_delay(exc, attempt))

    raise EmbeddingsUnavailableError(f"Voyage rerank failed: {last}") from last
