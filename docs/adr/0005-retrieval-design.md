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

## Not done

Reranking (hits are ordered by raw cosine distance), retrieval evals (recall@k against a labelled set), and citation rendering in the UI.
