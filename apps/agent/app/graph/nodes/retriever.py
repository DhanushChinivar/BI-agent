"""Retriever node: calls connectors to fetch data based on the plan."""
import logging
from typing import Any

from app.cache import cache_get, cache_set
from app.connectors import REGISTRY
from app.graph.state import AgentState

logger = logging.getLogger(__name__)

_DEFAULT_CONNECTOR = "mock"


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
    plan = state.get("plan", [])
    user_id = state.get("user_id", "anonymous")

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
                retrieved.append({"source": connector.name, "resource": resource, "data": data})
        except Exception as exc:
            logger.warning("Connector %s failed: %s", connector.name, exc)
            retrieved.append({"source": connector.name, "error": str(exc)})

    return {"retrieved_data": retrieved, "next_node": "analyst"}
