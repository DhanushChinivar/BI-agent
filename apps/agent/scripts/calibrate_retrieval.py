"""Measure retrieval quality against real Voyage vectors.

Run from apps/agent:  uv run python scripts/calibrate_retrieval.py

Two things it answers. Does the ranking put the right document first? And where
should `search._MAX_DISTANCE` sit — the cut that decides when the index should
admit it has nothing?

The threshold started as a guess at 0.6. This showed the guess would have
discarded correct answers, and that the relevant and irrelevant distance
populations *overlap*, so no cut separates them cleanly. That is the argument
for reranking, and it is worth being able to show rather than assert.

This is the seed of the recall@k eval in PLAN.md Phase 10 — extend CASES with
labelled questions from real connector data to turn it into one.
"""
import asyncio
import math
import sys
from pathlib import Path

from dotenv import load_dotenv

# Run from apps/agent; `app` lives one level up from scripts/.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
load_dotenv(Path(__file__).resolve().parent.parent / ".env")
from app.config.settings import get_settings  # noqa: E402

get_settings.cache_clear()
from app.rag import embeddings, rerank  # noqa: E402

# Chunks shaped the way `chunking.py` actually emits them: title · sender · date
# header, then body. Calibrating on bare sentences would flatter the numbers.
DOCS = [
    "Q4 planning · ana@corp.com · 2026-01-05\n\nWe closed Q4 at 666,173.65 in revenue, "
    "ahead of the 620k target. Chatswood was the strongest store.",
    "Runway discussion · finance@corp.com · 2026-02-11\n\nAt the current burn of 210k a "
    "month we have about 18 months of runway left.",
    "Office move · facilities@corp.com · 2026-03-02\n\nThe new office lease starts in "
    "June. Please pack your desk by the 28th.",
    "Lunch order · sam@corp.com · 2026-03-04\n\nCan everyone reply with their sandwich "
    "preference by 11am today. Thanks!",
]

# (question, index of the document that should answer it, or None if none does)
CASES = [
    ("What was our Q4 revenue?", 0),
    ("How did the fourth quarter go?", 0),
    ("Which store performed best?", 0),
    ("How much runway do we have?", 1),
    ("What is our monthly burn rate?", 1),
    ("When do we run out of money?", 1),
    ("When are we moving offices?", 2),
    # Nothing here answers these — they must fall outside the cut.
    ("What is the capital of Portugal?", None),
    ("How do I reset my VPN password?", None),
    ("What were our hiring plans for 2024?", None),
]


# Voyage's free tier allows ~3 requests/minute and reranking is inherently one
# call per question — there is no batching across queries. Pace the loop rather
# than let it die a third of the way through with a partial picture.
_PACE_SECONDS = 21.0


def cosine_distance(a, b):
    dot = sum(x * y for x, y in zip(a, b, strict=True))
    n = math.sqrt(sum(x * x for x in a)) * math.sqrt(sum(y * y for y in b))
    return 1 - dot / n


async def main():
    doc_vecs = await embeddings.embed_documents(DOCS)
    # One batched call, not one per question. Voyage's free tier is ~3 requests
    # per minute, so a loop of single-item calls trips the rate limit before it
    # gets halfway through the cases.
    q_vecs = await embeddings.embed([q for q, _ in CASES], input_type="query")

    relevant, irrelevant = [], []
    print(f"{'question':<40} {'best d':>7}  {'match':>6}  verdict")
    print("-" * 78)

    for (question, answer_idx), q in zip(CASES, q_vecs, strict=True):
        dists = [cosine_distance(q, d) for d in doc_vecs]
        best = min(range(len(dists)), key=lambda i: dists[i])

        if answer_idx is None:
            # For a question nothing answers, what matters is the *nearest*
            # distance — that is what a threshold has to exclude.
            irrelevant.append(dists[best])
            verdict = "no answer exists"
        else:
            relevant.append(dists[answer_idx])
            verdict = "correct doc" if best == answer_idx else f"WRONG (got {best})"

        print(f"{question:<40} {dists[best]:>7.3f}  {best:>6}  {verdict}")

    print("-" * 78)
    print("STAGE 1 — bi-encoder cosine distance (lower is closer)")
    print(f"  correct answers    : up to {max(relevant):.3f}   (all must be kept)")
    print(f"  nothing answers it : from {min(irrelevant):.3f}  (all must be cut)")
    gap = min(irrelevant) - max(relevant)
    print(f"  separation         : {gap:+.3f}", end="  ")
    print("(clean)" if gap > 0 else "(OVERLAP — no cut separates them)")

    if not rerank.enabled():
        print("\nSTAGE 2 skipped — reranking not configured")
        return

    # Stage 2: score every question against every document with the
    # cross-encoder, one batched call per question.
    print()
    print("STAGE 2 — cross-encoder relevance (higher is better)")
    r_rel, r_irr = [], []
    for n, (question, answer_idx) in enumerate(CASES):
        if n:
            await asyncio.sleep(_PACE_SECONDS)
        try:
            scored = await rerank._post(
                {
                    "query": question,
                    "documents": DOCS,
                    "model": get_settings().rerank_model,
                    "top_k": len(DOCS),
                }
            )
        except Exception as exc:
            # Print what we have rather than losing the whole run.
            print(f"  {question:<40}  rerank failed: {str(exc)[:60]}")
            continue
        by_index = {e["index"]: e["relevance_score"] for e in scored}
        best_idx = max(by_index, key=lambda i: by_index[i])
        best = by_index[best_idx]

        if answer_idx is None:
            r_irr.append(best)
            verdict = "no answer exists"
        else:
            r_rel.append(by_index[answer_idx])
            verdict = "correct doc" if best_idx == answer_idx else f"WRONG (got {best_idx})"
        print(f"  {question:<40} {best:>6.3f}  {best_idx:>3}  {verdict}")

    print("-" * 78)
    if not r_rel or not r_irr:
        print("  not enough successful calls to summarise")
        return
    print(f"  correct answers    : down to {min(r_rel):.3f}  (all must be kept)")
    print(f"  nothing answers it : up to  {max(r_irr):.3f}  (all must be cut)")
    r_gap = min(r_rel) - max(r_irr)
    print(f"  separation         : {r_gap:+.3f}", end="  ")
    print("(clean)" if r_gap > 0 else "(OVERLAP)")
    if r_gap > 0:
        print(f"  → any cut in ({max(r_irr):.3f}, {min(r_rel):.3f}) separates them")
        print(f"  → configured _MIN_RELEVANCE = {rerank._MIN_RELEVANCE}")


asyncio.run(main())
