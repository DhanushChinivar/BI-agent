"""Voyage embedding client.

Anthropic sells no embedding model, so Claude answers the question and Voyage
finds the material. Called through httpx rather than the `voyageai` SDK: the
endpoint is one POST, and the SDK's synchronous client would have to be pushed
onto a thread from every async call site anyway.
"""
import asyncio
import logging

import httpx

from app.config.settings import get_settings
from app.db.models import EMBEDDING_DIMS

logger = logging.getLogger(__name__)

_URL = "https://api.voyageai.com/v1/embeddings"

# Voyage caps a request at 128 inputs. Batching matters more than it looks: a
# mailbox backfill is thousands of chunks, and one request per chunk would make
# ingest network-bound for minutes.
_MAX_BATCH = 128
_TIMEOUT = 60.0
_MAX_ATTEMPTS = 4

# Voyage's free tier allows ~3 requests/minute, so a rate-limit retry has to be
# willing to wait on the order of the window itself. The original 1.5**attempt
# gave up after a cumulative ~4.75s and turned every burst into a hard failure.
_BACKOFF_BASE = 4.0
_MAX_BACKOFF = 30.0


class EmbeddingsUnavailableError(RuntimeError):
    """No API key configured, or Voyage could not be reached."""


def enabled() -> bool:
    """Whether embedding is configured at all.

    Checked before indexing and before vector search so the whole feature
    degrades to the provider-search path rather than erroring, which keeps the
    project runnable by someone who has not signed up for Voyage.
    """
    return bool(get_settings().voyage_api_key)


def _batches(items: list[str], size: int = _MAX_BATCH):
    for i in range(0, len(items), size):
        yield items[i : i + size]


def _retry_delay(exc: Exception, attempt: int) -> float:
    """How long to wait before the next attempt.

    Prefers the server's own `Retry-After` when it sends one — guessing longer
    than necessary wastes ingest time, and guessing shorter just burns an
    attempt against a window that has not reset.
    """
    if isinstance(exc, httpx.HTTPStatusError):
        header = exc.response.headers.get("retry-after")
        if header:
            try:
                return min(float(header), _MAX_BACKOFF)
            except ValueError:
                pass  # HTTP-date form; fall through to the backoff below.
    return min(_BACKOFF_BASE * (2**attempt), _MAX_BACKOFF)


async def _post(client: httpx.AsyncClient, payload: dict) -> list[list[float]]:
    """One embeddings call, retrying on rate limits and transient failures."""
    last: Exception | None = None

    for attempt in range(_MAX_ATTEMPTS):
        try:
            resp = await client.post(_URL, json=payload)

            # 429 and 5xx are transient. Everything else in the 4xx range is the
            # request itself being wrong — a bad model name, a revoked key — and
            # no amount of retrying fixes it, so fail immediately rather than
            # spending four attempts and the backoff between them.
            if resp.status_code >= 400 and not (
                resp.status_code == 429 or resp.status_code >= 500
            ):
                raise EmbeddingsUnavailableError(
                    f"Voyage rejected the request ({resp.status_code}): {resp.text[:200]}"
                )
            if resp.status_code >= 400:
                raise httpx.HTTPStatusError(
                    f"voyage {resp.status_code}", request=resp.request, response=resp
                )

            data = resp.json()["data"]
            # Voyage documents the response as ordered, but the vectors are
            # matched back to their inputs positionally — sort by index rather
            # than trust it, because a silent misalignment would attach every
            # chunk to the wrong text and be nearly impossible to spot later.
            return [row["embedding"] for row in sorted(data, key=lambda r: r["index"])]
        except (httpx.HTTPStatusError, httpx.RequestError) as exc:
            last = exc
            if attempt == _MAX_ATTEMPTS - 1:
                break
            await asyncio.sleep(_retry_delay(exc, attempt))

    raise EmbeddingsUnavailableError(f"Voyage request failed: {last}") from last


async def embed(texts: list[str], *, input_type: str) -> list[list[float]]:
    """Embed `texts`. `input_type` is "document" or "query".

    Voyage's models are asymmetric: a passage and a question about that passage
    are embedded differently on purpose, and using one type for both measurably
    degrades recall. Passing it explicitly at every call site makes the mistake
    hard to make silently.
    """
    if input_type not in ("document", "query"):
        raise ValueError(f"input_type must be 'document' or 'query', got {input_type!r}")

    settings = get_settings()
    if not settings.voyage_api_key:
        raise EmbeddingsUnavailableError("VOYAGE_API_KEY is not set")

    # Voyage rejects an empty string; keep positions stable by embedding only
    # the non-empty ones and stitching zero vectors back into the gaps.
    usable = [(i, t) for i, t in enumerate(texts) if t and t.strip()]
    if not usable:
        return [[0.0] * EMBEDDING_DIMS for _ in texts]

    vectors: list[list[float]] = []
    headers = {"Authorization": f"Bearer {settings.voyage_api_key}"}

    async with httpx.AsyncClient(timeout=_TIMEOUT, headers=headers) as client:
        for batch in _batches([t for _, t in usable]):
            vectors.extend(
                await _post(
                    client,
                    {
                        "input": batch,
                        "model": settings.embedding_model,
                        "input_type": input_type,
                    },
                )
            )

    if len(vectors) != len(usable):
        raise EmbeddingsUnavailableError(
            f"Voyage returned {len(vectors)} vectors for {len(usable)} inputs"
        )

    out: list[list[float]] = [[0.0] * EMBEDDING_DIMS for _ in texts]
    for (position, _), vector in zip(usable, vectors, strict=True):
        out[position] = vector
    return out


async def embed_query(text: str) -> list[float]:
    return (await embed([text], input_type="query"))[0]


async def embed_documents(texts: list[str]) -> list[list[float]]:
    return await embed(texts, input_type="document")
