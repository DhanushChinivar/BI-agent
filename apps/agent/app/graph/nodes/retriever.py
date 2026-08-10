"""Retriever node: calls connectors to fetch data based on the plan."""
import re
import time
from typing import Any

import structlog

from app import mcp_client
from app.cache import cache_get, cache_set
from app.connectors import CONNECTOR_NAMES
from app.graph.message_utils import last_human_message
from app.graph.state import AgentState

log = structlog.get_logger(__name__)

_DEFAULT_CONNECTOR = "mock"
_MAX_RESOURCES = 3     # resources read per connector per question
_MAX_ROWS = 60         # tabular rows sent to the LLM
_MAX_TEXT_CHUNKS = 15  # text chunks (email messages, document sections)

# Connectors return a dict whose list payload lives under one of these keys.
_PAYLOAD_CAPS = {"rows": _MAX_ROWS, "messages": _MAX_TEXT_CHUNKS}

# Free-text payloads, capped by characters rather than item count. ~12k chars is
# roughly 3k tokens — enough for a substantial Notion page while still leaving
# room for the other connectors in the same prompt.
_MAX_TEXT_CHARS = 12_000
_TEXT_KEYS = ("content",)

# Question types whose answer depends on every row — a sum, average, or trend
# computed over a keyword-filtered subset is silently wrong. For these we keep
# the dataset in document order and report anything we had to drop.
_EXHAUSTIVE_TYPES = {"aggregation", "trend", "comparison"}


# Question words and filler that appear in nearly every query. Left in, they
# score against unrelated titles on pure noise — a sheet called "What We Owe"
# ties with "Q4 Sales 2024" for "What was our Q4 revenue?" on the word "what".
_STOPWORDS = frozenset({
    "about", "all", "also", "and", "any", "are", "as", "at", "be", "been", "but",
    "by", "can", "could", "did", "do", "does", "each", "find", "for", "from",
    "get", "give", "had", "has", "have", "here", "how", "if", "in", "into", "is",
    "it", "its", "list", "many", "me", "most", "much", "my", "of", "on", "only",
    "or", "our", "out", "over", "please", "show", "so", "some", "tell", "than",
    "that", "the", "their", "then", "there", "these", "they", "this", "those",
    "to", "us", "was", "we", "were", "what", "when", "where", "which", "who",
    "why", "will", "with", "would", "you", "your",
})


def _keywords(text: str) -> set[str]:
    """Content words in `text`, lowercased.

    Two characters, not three: "Q4", "Q1", "FY", and two-letter product codes
    are usually the most discriminating token in a BI question, and a 3-char
    floor discarded every one of them before scoring ever happened.
    """
    return {w for w in re.findall(r"\b\w{2,}\b", text.lower()) if w not in _STOPWORDS}


def _score_row(row: Any, kw: set[str]) -> int:
    """Count keyword hits in a serialised row."""
    return len(kw & _keywords(str(row)))


def _select_resources(resources: list[dict], question: str) -> list[dict]:
    """Pick the resources most likely to answer the question.

    Reading every resource a connector exposes does not scale: a Drive with 50
    spreadsheets meant 50 sequential reads and a prompt no model can use well.
    Match the question against resource titles instead, and fall back to the
    first few when nothing matches so a poorly-worded question still gets data.
    """
    if len(resources) <= _MAX_RESOURCES:
        return resources

    kw = _keywords(question)
    scored = [
        (len(kw & _keywords(str(r.get("title") or r.get("name") or r))), i, r)
        for i, r in enumerate(resources)
    ]
    # Rank every resource, not just the ones that matched. _MAX_RESOURCES is a
    # budget, not a filter: a single weak title match should still fill it with
    # the next-best candidates rather than reading one file and stopping.
    # Unmatched entries score 0 and sort last in listing order, which preserves
    # the documented "nothing matched -> take the first few" fallback.
    ranked = sorted(scored, key=lambda e: (-e[0], e[1]))
    return [r for _, _, r in ranked[:_MAX_RESOURCES]]


def _cap_payload(payload: list, cap: int, question: str, exhaustive: bool) -> tuple[list, int]:
    """Trim a payload to `cap` items. Returns (kept, number dropped)."""
    dropped = len(payload) - cap
    if dropped <= 0:
        return payload, 0

    if exhaustive:
        # Order carries meaning for a total or a trend — never reorder by
        # relevance, and let the caller report what was cut.
        return payload[:cap], dropped

    kw = _keywords(question)
    scored = sorted(enumerate(payload), key=lambda t: _score_row(t[1], kw), reverse=True)
    kept_indices = sorted(i for i, _ in scored[:cap])
    return [payload[i] for i in kept_indices], dropped


def _cap_text(text: str, question: str, exhaustive: bool) -> tuple[str, int]:
    """Trim a free-text payload to `_MAX_TEXT_CHARS`. Returns (kept, paragraphs dropped).

    Notion's `content` is a single string built from every block on the page, and
    nothing capped it: one long runbook or meeting-notes page went into the
    prompt whole, crowding out the other connectors' data or overflowing the
    context outright. Paragraphs are the unit because they survive being
    reordered, which a mid-sentence character cut does not.
    """
    if len(text) <= _MAX_TEXT_CHARS:
        return text, 0

    paragraphs = [p for p in re.split(r"\n\s*\n", text) if p.strip()]
    if not paragraphs:
        # Whitespace-only but over budget: nothing meaningful to keep.
        return text[:_MAX_TEXT_CHARS], 1

    if exhaustive:
        order = list(range(len(paragraphs)))
    else:
        kw = _keywords(question)
        order = sorted(
            range(len(paragraphs)),
            key=lambda i: (-len(kw & _keywords(paragraphs[i])), i),
        )

    kept_indices: list[int] = []
    budget = _MAX_TEXT_CHARS
    for i in order:
        cost = len(paragraphs[i]) + 2  # the "\n\n" that rejoins it
        if cost > budget:
            continue
        kept_indices.append(i)
        budget -= cost

    if not kept_indices:
        # Every paragraph individually exceeds the budget — in practice a page
        # written as one wall of text. Keep a prefix rather than returning
        # nothing, and count that paragraph as omitted: it *was* cut, and
        # reporting 0 here is how a silent truncation gets presented to the
        # analyst as a complete document.
        return paragraphs[order[0]][:_MAX_TEXT_CHARS], len(paragraphs)

    # Document order, so the page still reads as a document.
    kept = "\n\n".join(paragraphs[i] for i in sorted(kept_indices))
    return kept, len(paragraphs) - len(kept_indices)


def _trim(data: Any, question: str, exhaustive: bool) -> tuple[Any, int]:
    """Cap the payloads inside a connector's read result.

    Every connector returns a dict (`{"rows": [...]}`, `{"messages": [...]}`,
    `{"content": "..."}`), so this must reach into the payload — an
    `isinstance(data, list)` check on the wrapper never matches.

    Every key is examined, not just the first that matches: returning early
    meant a result carrying both a list and a text body had only one of them
    capped, and the uncapped one was the reason the caller called `_trim`.
    """
    if not isinstance(data, dict):
        return data, 0

    trimmed = data
    total_dropped = 0

    for key, cap in _PAYLOAD_CAPS.items():
        payload = trimmed.get(key)
        if isinstance(payload, list) and payload:
            kept, dropped = _cap_payload(payload, cap, question, exhaustive)
            if dropped:
                trimmed = {**trimmed, key: kept}
                total_dropped += dropped

    for key in _TEXT_KEYS:
        payload = trimmed.get(key)
        if isinstance(payload, str) and payload:
            kept_text, dropped = _cap_text(payload, question, exhaustive)
            if dropped:
                trimmed = {**trimmed, key: kept_text}
                total_dropped += dropped

    return trimmed, total_dropped


def _search_query(question: str) -> str:
    """Connector-side search string derived from the question's content words.

    Sorted for determinism. The planner could emit a better query, but deriving
    it here keeps search working even when the planner's JSON fails to parse.
    """
    return " ".join(sorted(_keywords(question)))


def _usable(resources: Any) -> list[dict]:
    """Drop entries a connector emitted in place of raising.

    Connectors surface API failures as `[{"error": ...}]` rather than throwing,
    and those entries carry no `id` for `read` to resolve.
    """
    if not isinstance(resources, list):
        return []
    return [r for r in resources if isinstance(r, dict) and r.get("id")]


async def _candidates(connector: str, user_id: str, question: str, bound: Any) -> list[dict]:
    """Resources worth ranking, best-first.

    `list_resources` is bounded by the connector's own paging — Gmail lists only
    the 5 most recent threads — so anything older is unreachable from the
    listing no matter how good the ranking is. Every connector also implements
    `search`, which delegates to the provider's own index (Gmail's query API,
    Notion's `/search`).

    Search leads; the listing is the fallback when search finds nothing. That
    matters for Sheets, whose `search` is a substring match over the same
    listing and so returns nothing for any multi-word query. Falling back rather
    than merging also avoids Gmail's N+1 listing cost on the common path.
    """
    query = _search_query(question)
    if query:
        try:
            found = _usable(await mcp_client.search(connector, user_id, query))
        except Exception as exc:
            # A failed search must not fail the connector — fall through.
            bound.warning("search_failed", connector=connector, error=str(exc))
            found = []
        if found:
            bound.info("search_hit", connector=connector, query=query, results=len(found))
            return found

    return await mcp_client.list_resources(connector, user_id)


def _parse_plan_meta(plan: list[str]) -> tuple[list[str], str]:
    """Extract connector names and question_type encoded by the planner."""
    connectors: list[str] = []
    question_type = "other"

    for entry in plan:
        if entry.startswith("connectors:"):
            names = entry.removeprefix("connectors:").strip()
            connectors = [c for c in names.split(",") if c]
        elif entry.startswith("question_type:"):
            question_type = entry.removeprefix("question_type:").strip()

    return connectors, question_type


async def retriever_node(state: AgentState) -> dict:
    t0 = time.monotonic()
    bound = log.bind(
        node="retriever",
        conversation_id=state.get("conversation_id"),
        user_id=state.get("user_id"),
    )

    plan = state.get("plan", [])
    user_id = state.get("user_id", "anonymous")
    question = last_human_message(state.get("messages", [])) or ""

    connectors, question_type = _parse_plan_meta(plan)
    exhaustive = question_type in _EXHAUSTIVE_TYPES

    active = [c for c in connectors if c in CONNECTOR_NAMES] or [_DEFAULT_CONNECTOR]

    retrieved: list[dict[str, Any]] = []
    for name in active:
        try:
            resources = await _candidates(name, user_id, question, bound)
            selected = _select_resources(resources, question)
            if len(selected) < len(resources):
                bound.info(
                    "resources_narrowed",
                    connector=name,
                    available=len(resources),
                    selected=[r.get("title") or r.get("id") for r in selected],
                )

            for resource in selected:
                resource_id = resource["id"]
                cached = await cache_get(user_id, name, resource_id)
                if cached is not None:
                    data = cached
                else:
                    data = await mcp_client.read(name, user_id, resource_id)
                    await cache_set(user_id, name, resource_id, data)

                trimmed, dropped = _trim(data, question, exhaustive)
                entry: dict[str, Any] = {"source": name, "resource": resource, "data": trimmed}
                if dropped:
                    # `compute_node` needs every row to produce an exact total;
                    # `data` is the sample the analyst reads. Only kept when the
                    # two actually differ. `analyst._build_analysis_prompt`
                    # picks named keys, so this never reaches a prompt.
                    entry["full_data"] = data
                    # The analyst must know the dataset is partial — otherwise a
                    # "total" is reported as exact when it is not.
                    entry["omitted_items"] = dropped
                    bound.warning(
                        "payload_truncated",
                        connector=name,
                        resource=resource_id,
                        dropped=dropped,
                    )
                retrieved.append(entry)
        except Exception as exc:
            bound.warning("connector_failed", connector=name, error=str(exc))
            retrieved.append({"source": name, "error": str(exc), "connector_error": True})

    bound.info(
        "complete",
        duration_ms=round((time.monotonic() - t0) * 1000),
        sources=len(retrieved),
        question_type=question_type,
    )
    return {
        "retrieved_data": retrieved,
        "question_type": question_type,
        "next_node": "compute",
    }
