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
# Anything else (Notion's `content` string, error dicts) passes through as-is.
_PAYLOAD_CAPS = {"rows": _MAX_ROWS, "messages": _MAX_TEXT_CHUNKS}

# Question types whose answer depends on every row — a sum, average, or trend
# computed over a keyword-filtered subset is silently wrong. For these we keep
# the dataset in document order and report anything we had to drop.
_EXHAUSTIVE_TYPES = {"aggregation", "trend", "comparison"}


def _keywords(text: str) -> set[str]:
    return set(re.findall(r"\b\w{3,}\b", text.lower()))


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
    matched = [entry for entry in scored if entry[0] > 0]
    ranked = sorted(matched, key=lambda e: (-e[0], e[1])) if matched else scored
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


def _trim(data: Any, question: str, exhaustive: bool) -> tuple[Any, int]:
    """Cap the list payload inside a connector's read result.

    Every connector returns a dict (`{"rows": [...]}`, `{"messages": [...]}`,
    `{"content": "..."}`), so this must reach into the payload — an
    `isinstance(data, list)` check on the wrapper never matches.
    """
    if not isinstance(data, dict):
        return data, 0

    for key, cap in _PAYLOAD_CAPS.items():
        payload = data.get(key)
        if isinstance(payload, list) and payload:
            kept, dropped = _cap_payload(payload, cap, question, exhaustive)
            if dropped:
                return {**data, key: kept}, dropped
            return data, 0

    return data, 0


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
            resources = await mcp_client.list_resources(name, user_id)
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
    return {"retrieved_data": retrieved, "next_node": "analyst"}
