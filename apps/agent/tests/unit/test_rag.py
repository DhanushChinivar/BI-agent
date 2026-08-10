"""RAG layer tests: chunking, embedding, ingest bookkeeping, search shaping.

The project was described as a RAG chatbot while doing no retrieval-augmented
generation at all — no embeddings, no chunking, no vector store. These pin the
behaviours that make the claim true, and the boundaries that keep it honest
(tabular data is *not* embedded; the index is scoped to its owner).
"""
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import httpx
import pytest

from app.config.settings import get_settings
from app.rag import TEXT_CONNECTORS, chunking, embeddings, search

# ── chunking ──────────────────────────────────────────────────────────────────

def _thread(*bodies, subject="Q4 planning", sender="ana@corp.com"):
    return {
        "messages": [
            {"body": b, "subject": subject, "from": sender, "date": "2026-01-05"}
            for b in bodies
        ]
    }


def test_gmail_splits_per_message():
    """One vector per thread matches everything weakly and nothing well."""
    payload = _thread("a" * 200, "b" * 200, "c" * 200)

    chunks = chunking.chunk("gmail", payload, "t1", "Q4 planning")

    assert len(chunks) == 3
    assert [c.chunk_index for c in chunks] == [0, 1, 2]


def test_every_chunk_carries_its_context():
    """A retrieved fragment is read alone; without the subject and sender it is
    a paragraph from nowhere."""
    payload = _thread("We agreed to push the launch to Q3. " * 5)

    content = chunking.chunk("gmail", payload, "t1", "Q4 planning")[0].content

    assert "Q4 planning" in content
    assert "ana@corp.com" in content
    assert "push the launch to Q3" in content


def test_quoted_reply_history_is_stripped():
    """Otherwise every reply re-embeds the conversation beneath it and the
    top-k fills with near-duplicates of one exchange."""
    body = "Agreed, shipping Friday. " * 3 + "\n\nOn Mon, Ana wrote:\n> " + "old text " * 50

    chunks = chunking.chunk("gmail", _thread(body), "t1", "Launch")

    assert "shipping Friday" in chunks[0].content
    assert "old text" not in chunks[0].content


def test_a_long_message_is_windowed_with_overlap():
    payload = _thread("word " * 2000)

    chunks = chunking.chunk("gmail", payload, "t1", "Long")

    assert len(chunks) > 1
    assert all(len(c.content) <= chunking._MAX_CHARS + 200 for c in chunks)


def test_trivial_messages_are_dropped():
    """"Thanks!" embeds to nothing useful and crowds out real hits."""
    assert chunking.chunk("gmail", _thread("Thanks!"), "t1", "x") == []


def test_notion_packs_short_paragraphs_together():
    """A page of one-line bullets should not become one vector per bullet."""
    page = {"content": "\n\n".join(f"Bullet {i} about revenue growth" for i in range(40))}

    chunks = chunking.chunk("notion", page, "p1", "Notes")

    assert 0 < len(chunks) < 40
    assert all(len(c.content) <= chunking._MAX_CHARS + 200 for c in chunks)


def test_notion_keeps_the_page_title_on_every_chunk():
    page = {"content": "\n\n".join(["Revenue rose 12 percent this quarter. " * 10] * 5)}

    chunks = chunking.chunk("notion", page, "p1", "FY25 Review")

    assert all("FY25 Review" in c.content for c in chunks)


def test_an_error_payload_is_not_chunked():
    """Indexing a connector's error string would make it semantically findable."""
    assert chunking.chunk("notion", {"error": "401 Unauthorized"}, "p1", "x") == []


def test_a_connector_with_no_chunker_yields_nothing():
    """Adding a connector must not break ingest for every other one."""
    assert chunking.chunk("google_sheets", {"rows": [{"a": 1}]}, "s1", "Sales") == []


def test_spreadsheets_are_not_indexed():
    """The scope boundary of the whole feature: "what was Q4 revenue?" needs an
    exact sum over every row, not a nearest-neighbour lookup."""
    assert "google_sheets" not in TEXT_CONNECTORS
    assert {"gmail", "notion"} == TEXT_CONNECTORS


# ── embeddings ────────────────────────────────────────────────────────────────

@pytest.fixture
def voyage(monkeypatch):
    monkeypatch.setenv("VOYAGE_API_KEY", "test-key")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def _resp(status: int, body: dict) -> httpx.Response:
    """A response with its request attached.

    `raise_for_status` refuses to run on a Response built without one, so a
    bare `httpx.Response(400, ...)` raises RuntimeError instead of the
    HTTPStatusError the retry logic is written against.
    """
    return httpx.Response(
        status, json=body, request=httpx.Request("POST", embeddings._URL)
    )


def _voyage_response(vectors, shuffle=False):
    data = [{"index": i, "embedding": v} for i, v in enumerate(vectors)]
    if shuffle:
        data = list(reversed(data))
    return {"data": data}


@pytest.mark.asyncio
async def test_query_and_document_use_different_input_types(voyage):
    """Voyage's models are asymmetric; using one type for both degrades recall."""
    sent = []

    async def post(self, url, json=None, **kw):
        sent.append(json["input_type"])
        return _resp(200, _voyage_response([[0.1] * 512]))

    with patch.object(httpx.AsyncClient, "post", post):
        await embeddings.embed_query("what is our runway?")
        await embeddings.embed_documents(["our runway is 18 months"])

    assert sent == ["query", "document"]


@pytest.mark.asyncio
async def test_vectors_are_realigned_by_index(voyage):
    """A response returned out of order would attach every chunk to the wrong
    text — silent, and nearly impossible to spot later."""
    async def post(self, url, json=None, **kw):
        return _resp(
            200, _voyage_response([[1.0] * 512, [2.0] * 512], shuffle=True)
        )

    with patch.object(httpx.AsyncClient, "post", post):
        vectors = await embeddings.embed_documents(["first", "second"])

    assert vectors[0][0] == 1.0
    assert vectors[1][0] == 2.0


@pytest.mark.asyncio
async def test_blank_inputs_keep_their_positions(voyage):
    """Voyage rejects empty strings, so they are skipped — but dropping them
    would shift every later vector onto the wrong chunk."""
    async def post(self, url, json=None, **kw):
        assert "" not in json["input"]
        return _resp(200, _voyage_response([[7.0] * 512]))

    with patch.object(httpx.AsyncClient, "post", post):
        vectors = await embeddings.embed_documents(["", "real text", "   "])

    assert vectors[1][0] == 7.0
    assert vectors[0] == [0.0] * 512
    assert vectors[2] == [0.0] * 512


@pytest.mark.asyncio
async def test_batches_respect_the_api_limit(voyage):
    batch_sizes = []

    async def post(self, url, json=None, **kw):
        batch_sizes.append(len(json["input"]))
        return _resp(200, _voyage_response([[0.5] * 512] * len(json["input"])))

    with patch.object(httpx.AsyncClient, "post", post):
        await embeddings.embed_documents([f"text {i}" for i in range(300)])

    assert max(batch_sizes) <= embeddings._MAX_BATCH
    assert sum(batch_sizes) == 300


@pytest.mark.asyncio
async def test_a_missing_key_raises_rather_than_embedding_nothing(monkeypatch):
    monkeypatch.setenv("VOYAGE_API_KEY", "")
    get_settings.cache_clear()
    try:
        assert embeddings.enabled() is False
        with pytest.raises(embeddings.EmbeddingsUnavailableError):
            await embeddings.embed_documents(["text"])
    finally:
        get_settings.cache_clear()


@pytest.mark.asyncio
async def test_a_bad_input_type_is_rejected(voyage):
    with pytest.raises(ValueError):
        await embeddings.embed(["x"], input_type="passage")


@pytest.mark.asyncio
async def test_rate_limits_are_retried(voyage, monkeypatch):
    monkeypatch.setattr(embeddings.asyncio, "sleep", AsyncMock())
    calls = {"n": 0}

    async def post(self, url, json=None, **kw):
        calls["n"] += 1
        if calls["n"] < 3:
            return _resp(429, {"detail": "slow down"})
        return _resp(200, _voyage_response([[0.3] * 512]))

    with patch.object(httpx.AsyncClient, "post", post):
        vectors = await embeddings.embed_documents(["text"])

    assert calls["n"] == 3
    assert vectors[0][0] == 0.3


@pytest.mark.asyncio
async def test_a_client_error_is_not_retried(voyage, monkeypatch):
    """A 400 means the request is wrong; retrying it only burns time."""
    monkeypatch.setattr(embeddings.asyncio, "sleep", AsyncMock())
    calls = {"n": 0}

    async def post(self, url, json=None, **kw):
        calls["n"] += 1
        return _resp(400, {"detail": "bad model"})

    with (
        patch.object(httpx.AsyncClient, "post", post),
        pytest.raises(embeddings.EmbeddingsUnavailableError),
    ):
        await embeddings.embed_documents(["text"])

    assert calls["n"] == 1


# ── ingest bookkeeping ────────────────────────────────────────────────────────

def test_unchanged_revisions_are_skipped():
    """Re-embedding an untouched mailbox every tick would be the dominant cost
    of the entire feature."""
    from app.rag.ingest import _needs_work

    record = SimpleNamespace(embedding_model="voyage-3-lite", status="ok", revision="r1")

    assert _needs_work(record, "r1", "voyage-3-lite") is False
    assert _needs_work(record, "r2", "voyage-3-lite") is True


def test_a_model_change_invalidates_everything():
    """Vectors from two models are not comparable; a mixed index returns
    nonsense neighbours while looking healthy."""
    from app.rag.ingest import _needs_work

    record = SimpleNamespace(embedding_model="voyage-3-lite", status="ok", revision="r1")

    assert _needs_work(record, "r1", "voyage-3") is True


def test_a_previously_failed_resource_is_retried():
    from app.rag.ingest import _needs_work

    record = SimpleNamespace(embedding_model="voyage-3-lite", status="error", revision="r1")

    assert _needs_work(record, "r1", "voyage-3-lite") is True


@pytest.mark.parametrize(
    "resource,expected",
    [
        ({"historyId": "998877"}, "998877"),
        ({"last_edited_time": "2026-08-10T09:00:00Z"}, "2026-08-10T09:00:00Z"),
        # No revision signal: fall back to the title so the resource re-indexes
        # on rename rather than on every single tick.
        ({"title": "Roadmap"}, "title:Roadmap"),
        ({}, None),
    ],
)
def test_revision_prefers_the_providers_own_change_signal(resource, expected):
    from app.rag.ingest import _revision

    assert _revision(resource) == expected


@pytest.mark.asyncio
async def test_sheets_are_never_ingested():
    from app.rag.ingest import sync_connector

    result = await sync_connector("u1", "google_sheets")

    assert result["skipped"] == "not a text connector"


@pytest.mark.asyncio
async def test_sync_is_a_no_op_without_a_key(monkeypatch):
    """A deployment without Voyage is supported, not broken."""
    from app.rag.ingest import sync_connector

    monkeypatch.setenv("VOYAGE_API_KEY", "")
    get_settings.cache_clear()
    try:
        result = await sync_connector("u1", "gmail")
    finally:
        get_settings.cache_clear()

    assert result["skipped"] == "embeddings not configured"


# ── search shaping ────────────────────────────────────────────────────────────

def _hit(connector, resource_id, title, idx, score, text="passage"):
    return {
        "connector": connector,
        "resource_id": resource_id,
        "resource_title": title,
        "chunk_index": idx,
        "content": text,
        "distance": (1.0 - score) * 2,
        "score": score,
    }


def test_hits_group_into_one_entry_per_document():
    hits = [
        _hit("gmail", "t1", "Q4 planning", 0, 0.9),
        _hit("gmail", "t1", "Q4 planning", 3, 0.7),
        _hit("notion", "p1", "Roadmap", 0, 0.8),
    ]

    grouped = search.group_by_resource(hits)

    assert [g["resource_id"] for g in grouped] == ["t1", "p1"]
    assert len(grouped[0]["passages"]) == 2
    assert grouped[0]["best_score"] == 0.9


def test_grouping_orders_documents_by_their_best_passage():
    hits = [_hit("gmail", "t1", "A", 0, 0.4), _hit("gmail", "t2", "B", 0, 0.95)]

    assert [g["resource_id"] for g in search.group_by_resource(hits)] == ["t2", "t1"]


@pytest.mark.asyncio
async def test_search_returns_nothing_when_embeddings_are_off(monkeypatch):
    monkeypatch.setenv("VOYAGE_API_KEY", "")
    get_settings.cache_clear()
    try:
        assert await search.search("u1", "anything") == []
    finally:
        get_settings.cache_clear()


@pytest.mark.asyncio
async def test_search_degrades_rather_than_failing_when_voyage_is_down(voyage, monkeypatch):
    """The retriever still has the live provider path; losing the question
    entirely would be a strictly worse outcome."""
    monkeypatch.setattr(
        search,
        "embed_query",
        AsyncMock(side_effect=embeddings.EmbeddingsUnavailableError("down")),
    )

    assert await search.search("u1", "what is our runway?") == []


@pytest.mark.parametrize(
    "attempt,expected",
    [(0, 4.0), (1, 8.0), (2, 16.0), (3, 30.0), (9, 30.0)],
)
def test_backoff_grows_and_is_capped(attempt, expected):
    """Voyage's free tier is ~3 requests/minute, so a retry has to be willing to
    wait on the order of the window. The original 1.5**attempt gave up after a
    cumulative 4.75s and turned every burst into a hard failure."""
    exc = httpx.RequestError("boom")

    assert embeddings._retry_delay(exc, attempt) == expected


def test_retry_after_header_wins_over_the_backoff():
    """Guessing longer than the server asked wastes ingest time; guessing
    shorter burns an attempt against a window that has not reset."""
    resp = httpx.Response(
        429,
        headers={"retry-after": "7"},
        request=httpx.Request("POST", embeddings._URL),
    )
    exc = httpx.HTTPStatusError("429", request=resp.request, response=resp)

    assert embeddings._retry_delay(exc, 0) == 7.0


def test_an_http_date_retry_after_falls_back_to_the_backoff():
    """Retry-After may be an HTTP-date rather than seconds; float() raises on it
    and must not take the whole retry path down with it."""
    resp = httpx.Response(
        429,
        headers={"retry-after": "Wed, 21 Oct 2026 07:28:00 GMT"},
        request=httpx.Request("POST", embeddings._URL),
    )
    exc = httpx.HTTPStatusError("429", request=resp.request, response=resp)

    assert embeddings._retry_delay(exc, 1) == 8.0


def test_a_huge_retry_after_is_capped():
    """A misbehaving proxy asking for an hour must not stall ingest for one."""
    resp = httpx.Response(
        429,
        headers={"retry-after": "3600"},
        request=httpx.Request("POST", embeddings._URL),
    )
    exc = httpx.HTTPStatusError("429", request=resp.request, response=resp)

    assert embeddings._retry_delay(exc, 0) == embeddings._MAX_BACKOFF
