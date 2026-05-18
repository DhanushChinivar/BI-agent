"""Retriever node: calls connectors to fetch data based on the plan."""
import re
import time
from typing import Any

import structlog

from app.cache import cache_get, cache_set
from app.connectors import REGISTRY
from app.graph.message_utils import last_human_message
from app.graph.state import AgentState

log = structlog.get_logger(__name__)

_DEFAULT_CONNECTOR = "mock"
_MAX_ROWS = 60        # hard cap on tabular rows sent to LLM
_MAX_TEXT_CHUNKS = 15  # hard cap on text/document chunks


def _keywords(text: str) -> set[str]:
    return set(re.findall(r"\b\w{3,}\b", text.lower()))


def _score_row(row: Any, kw: set[str]) -> int:
    """Count keyword hits in a serialised row."""
    return len(kw & _keywords(str(row)))


def _filter_data(data: list[Any], question: str) -> list[Any]:
    """Return the most relevant rows/chunks within token budget."""
    if not data:
        return data

    kw = _keywords(question)
    is_text = isinstance(data[0], dict) and "content" in data[0]
    cap = _MAX_TEXT_CHUNKS if is_text else _MAX_ROWS

    if len(data) <= cap:
        return data

    scored = sorted(enumerate(data), key=lambda t: _score_row(t[1], kw), reverse=True)
    kept_indices = sorted(i for i, _ in scored[:cap])
    return [data[i] for i in kept_indices]


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

    connectors, _ = _parse_plan_meta(plan)

    active = [REGISTRY[c] for c in connectors if c in REGISTRY] or [
        REGISTRY[_DEFAULT_CONNECTOR]
    ]

    retrieved: list[dict[str, Any]] = []
    for connector in active:
        try:
            resources = await connector.list_resources(user_id)
            for resource in resources:
                resource_id = resource["id"]
                cached = await cache_get(user_id, connector.name, resource_id)
                if cached is not None:
                    data = cached
                else:
                    data = await connector.read(user_id, resource_id)
                    await cache_set(user_id, connector.name, resource_id, data)
                filtered = _filter_data(data, question) if isinstance(data, list) else data
                retrieved.append({"source": connector.name, "resource": resource, "data": filtered})
        except Exception as exc:
            bound.warning("connector_failed", connector=connector.name, error=str(exc))
            retrieved.append({"source": connector.name, "error": str(exc), "connector_error": True})

    bound.info("complete", duration_ms=round((time.monotonic() - t0) * 1000), sources=len(retrieved))
    return {"retrieved_data": retrieved, "next_node": "analyst"}
