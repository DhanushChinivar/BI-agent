"""Planner node: decomposes user question into sub-tasks."""
import json
import logging

from app.graph.message_utils import last_human_message
from app.graph.state import AgentState
from app.llm import chat

logger = logging.getLogger(__name__)

_SYSTEM = """You are the planning module of a Business Intelligence agent.
Decompose the user's data question into an ordered, actionable plan.

Available connectors: google_sheets, notion, gmail, csv_upload.

Respond with JSON only — no prose, no markdown fences:
{
  "steps": ["<step 1>", "<step 2>", ...],
  "connectors": ["<connector_name>", ...],
  "question_type": "aggregation|lookup|trend|comparison|other"
}

Rules:
- steps: 3–6 concrete actions (e.g. "retrieve Q4 sales data from google_sheets")
- connectors: only those actually needed; empty list if no external data required
- question_type: the dominant analysis pattern"""


async def planner_node(state: AgentState) -> dict:
    messages = state.get("messages", [])
    if not messages:
        return {
            "plan": ["no question provided"],
            "next_node": "retriever",
        }

    user_question = last_human_message(messages)

    try:
        raw = await chat(
            messages=[{"role": "user", "content": user_question}],
            system=_SYSTEM,
            max_tokens=512,
        )
        plan_data = json.loads(raw)
        steps: list[str] = plan_data.get("steps", [])
        connectors: list[str] = plan_data.get("connectors", [])
        question_type: str = plan_data.get("question_type", "other")

        # Encode metadata as the first step so downstream nodes can read it
        plan = [f"question_type:{question_type}", f"connectors:{','.join(connectors)}"] + steps
    except (json.JSONDecodeError, KeyError) as exc:
        logger.warning("Planner failed to parse LLM response: %s", exc)
        plan = ["retrieve", "analyze", "summarize"]

    return {"plan": plan, "next_node": "retriever"}
