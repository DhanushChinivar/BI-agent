# ADR 0005 — Retrieval: vectors for text, SQL for tables

**Date:** 2026-08-10
**Status:** Accepted

## Context

The project was described as a RAG chatbot while performing no retrieval-augmented generation. `retriever_node` listed a connector's resources, read them, and kept the rows sharing the most word-tokens with the question. That is lexical truncation, and it failed in three ways: it missed any match phrased differently from the question ("turnover" vs "revenue"), it did not scale past a handful of resources, and it dropped data silently.

The obvious fix — "embed everything" — is wrong for half of this system's data, which is what makes the decision worth recording.

## Decision

**Two retrieval paths, chosen by source type.**

| Source | Path | Why |
|---|---|---|
| Gmail, Notion | chunk → embed → pgvector → nearest-neighbour | The question is a semantic lookup. "What did we decide about the launch?" has no reliable keyword overlap with the thread that answers it |
| Google Sheets | provider search → read → DuckDB (`compute_node`) | The question is arithmetic. "What was Q4 revenue?" needs an exact sum over **every** row |

Embedding spreadsheet rows would have been the more impressive-sounding choice and a worse one. Nearest-neighbour search returns the *k* rows most similar to the question, which for an aggregation is an arbitrary subset — and a total computed over an arbitrary subset is wrong while looking authoritative. That failure mode is the one this codebase has already been bitten by twice: the `A1:Z1000` read range that hid a whole quarter, and the analyst summing its 60-row sample and understating the year by 97%.

So `TEXT_CONNECTORS` is `{gmail, notion}` and there is a test asserting Sheets is not in it.

## Supporting choices

**Voyage `voyage-3-lite`, 512 dims.** Anthropic sells no embedding model; Voyage is its recommended partner. Rejected: a local MiniLM (no API key, but ~90MB of image and weaker on domain vocabulary) and OpenAI (cheapest, but an OpenAI dependency in a Claude-native project reads oddly, and 1536 dims triples index size for no gain here).

Embeddings are **asymmetric**: `input_type="document"` on ingest, `"query"` at search. Using one for both measurably degrades recall, and it is an easy mistake to make silently, so the parameter is required at every call site rather than defaulted.

**pgvector in the existing Postgres**, not a separate vector service. One fewer thing to run, and a chunk joins to its owner without a second store to keep consistent.

**HNSW, not IVFFlat.** IVFFlat needs a populated table to assign meaningful lists; the migration creates the table empty, so an IVFFlat index built there is bad until someone remembers to reindex. HNSW builds incrementally. The operator class is `vector_cosine_ops` to match the `<=>` the query uses — a mismatch is silently ignored by the planner and presents as "pgvector is slow" rather than as a mistake.

**Postgres owns the index state.** `indexed_resources.revision` holds Gmail's `historyId` or Notion's `last_edited_time`; unchanged means skip. Without it, every tick re-embeds every resource, which would be the dominant cost of the entire feature.

**A distance cut at 0.75, chosen from measurement rather than taste.** An ANN index has no concept of "nothing relevant here" — ask it for twelve neighbours and it returns the twelve least-irrelevant chunks in the account, however unrelated.

The cut started as a guess at `0.6`. `scripts/calibrate_retrieval.py` embedded four realistic chunks and ten labelled questions with the real model, and the guess turned out to be wrong in an instructive way:

| | cosine distance |
|---|---|
| Questions with a correct answer | 0.314 – 0.629 |
| Questions nothing in the corpus answers | 0.554 – 0.817 |

Two things follow. First, `0.6` would have discarded correct answers — "How did the fourth quarter go?" matched its document at `0.629`. Second, and more useful, **the two populations overlap**: the worst true match is further away than the best false one, because "What were our hiring plans for 2024?" is business-flavoured enough to land at `0.554` from a revenue email. No threshold separates them.

So the cut is set to keep every true hit and only exclude the obviously absurd. The asymmetry justifies the choice: an irrelevant passage that reaches the analyst costs tokens and the analyst says it does not answer the question, whereas a true answer cut here is simply gone and the agent reports it has no data. Separating the near-misses is reranking's job — which is now an argument backed by numbers rather than an assertion.

Ranking itself measured well: 7 of 7 questions with an answer retrieved the right document first.

## Consequences

- One more API key, and it is optional: with `VOYAGE_API_KEY` unset, indexing and search are skipped and the retriever falls back to provider search. Degraded, not broken — the project stays runnable by someone who has not signed up for Voyage.
- Ingest costs money on first backfill (~$0.02/Mtok). Incremental sync keeps steady-state cost near zero.
- The index is durable storage, not a cache, so it must be dropped on disconnect — a longer-lived leak than the Redis cache it sits beside.
- Chunk boundaries move when a document changes, so resync is delete-then-insert per resource rather than a diff. A briefly missing resource beats a half-updated one.
- Answers over text now carry `{connector, resource_id, title}`. **Nothing renders them yet** — the data reaches the analyst, the UI shows prose.

## Amendment — reranking (2026-08-11)

The overlap above is not a tuning problem, and no amount of moving the threshold fixes it. A bi-encoder embeds the question and the passage **independently** and compares the two vectors; it never sees them together. So "What were our hiring plans for 2024?" lands near a revenue email because both are corporate-finance-shaped, and that similarity is real — it is just not *relevance*.

A cross-encoder reads `(question, passage)` as a single input and scores their actual relationship. It costs far more per pair, which is exactly why it runs second: pgvector culls the whole index down to `k × 4` candidates cheaply, and the expensive model only judges those.

Re-running the same ten questions through both stages:

| | correct answers | nothing answers it | separation |
|---|---|---|---|
| Bi-encoder (cosine distance) | up to 0.629 | from 0.554 | **−0.076 — overlap** |
| Cross-encoder (relevance) | down to 0.535 | up to 0.500 | **+0.035 — clean** |

The second stage makes "nothing in your data answers this" a decision the system can actually make. Both stages ranked 7/7 correctly, so the reranker's contribution here is the *threshold*, not the ordering — with a larger and messier corpus the ordering would matter more too.

**`_MIN_RELEVANCE` is 0.48, below the separating band rather than inside it.** The band is only 0.035 wide and comes from ten questions I wrote myself; that is enough evidence to place a floor and not enough to trust a midpoint. The errors are also asymmetric, the same way they were for the distance cut: a weak passage that reaches the analyst gets dismissed in the answer, while a true answer cut here is gone and the agent says it has no data. At 0.48 the two clearest non-answers are dropped, every true answer survives with 0.055 to spare, and one borderline case gets through by choice.

The value shipped at 0.35 first. Every non-answer scored above it — a filter that looked like one and filtered nothing. It was measurement, not review, that caught that.

### Consequences

- One more API round trip per question over text sources. Voyage's free tier allows ~3 requests/minute, and a question already spends one call on the query embedding.
- `rerank()` never raises. Unconfigured or unreachable, it returns the vector ordering — which is precisely what retrieval did before this existed.
- `RERANK_MODEL=` disables it entirely.

## Not done

Retrieval evals (recall@k against a labelled set — `scripts/calibrate_retrieval.py` is the seed, but ten hand-written questions over four documents is a smoke test, not an eval) and citation rendering in the UI.
