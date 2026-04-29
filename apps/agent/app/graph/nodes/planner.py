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
- csv_upload: user-uploaded CSV, Excel, or PDF files

Respond with JSON only — no prose, no markdown fences:
{
  "steps": ["<step 1>", "<step 2>", ...],
  "connectors": ["<connector_name>", ...],
  "question_type": "aggregation|lookup|trend|comparison|other",
  "action": null,
  "action_cron": null,
  "action_question": null
}

Rules:
- steps: 3–6 concrete actions (e.g. "retrieve Q4 sales data from google_sheets")
- connectors: only those actually needed; empty list if no external data required
- question_type: the dominant analysis pattern
- action: set to "schedule_report" if the user wants a recurring scheduled report,
           "data_alert" if they want to be alerted when data changes, null otherwise
- action_cron: a cron expression (e.g. "0 8 * * 1" for Monday 8am) when action is set, else null
- action_question: the specific BI question to run on the schedule, else null"""


async def planner_node(state: AgentState) -> dict:
    messages = state.get("messages", [])
    if not messages:
        return {
            "plan": ["no question provided"],
            "action_required": False,
            "action_type": None,
            "action_cron": None,
            "action_question": None,
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
        action_type: str | None = plan_data.get("action")
        action_cron: str | None = plan_data.get("action_cron")
        action_question: str | None = plan_data.get("action_question")

        plan = [f"question_type:{question_type}", f"connectors:{','.join(connectors)}"] + steps
        if action_type:
            plan.append(f"action:{action_type}")
    except (json.JSONDecodeError, KeyError) as exc:
        logger.warning("Planner failed to parse LLM response: %s", exc)
        plan = ["retrieve", "analyze", "summarize"]
        action_type = None
        action_cron = None
        action_question = None

    return {
        "plan": plan,
        "action_required": bool(action_type),
        "action_type": action_type,
        "action_cron": action_cron,
        "action_question": action_question,
        "next_node": "retriever",
    }
