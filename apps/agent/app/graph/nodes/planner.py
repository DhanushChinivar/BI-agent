"""Planner node: decomposes user question into sub-tasks."""
import json
import time

import structlog

from app.graph.message_utils import get_content, get_role, last_human_message
from app.graph.state import AgentState
from app.llm import EmptyCompletionError, chat

log = structlog.get_logger(__name__)

_SYSTEM = """You are the planning module of a Business Intelligence agent.
Decompose the user's data question into an ordered, actionable plan.

You may be given prior conversation turns for context. Use them to resolve follow-up questions,
pronouns ("it", "that", "those"), and implicit references to earlier data or topics.

Available connectors: google_sheets, gmail, notion.
- notion: Notion pages and databases (wikis, docs, project trackers)

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
    t0 = time.monotonic()
    bound = log.bind(
        node="planner",
        conversation_id=state.get("conversation_id"),
        user_id=state.get("user_id"),
    )

    messages = state.get("messages", [])
    if not messages:
        bound.warning("no_messages")
        return {
            "plan": ["no question provided"],
            "action_required": False,
            "action_type": None,
            "action_cron": None,
            "action_question": None,
            "next_node": "retriever",
        }

    user_question = last_human_message(messages)

    # Build multi-turn context: prior turns as alternating user/assistant messages
    # followed by the current question, so the LLM can resolve follow-ups.
    history = [m for m in messages if get_role(m) in ("user", "assistant", "human", "ai")]
    prior = history[:-1]  # everything before the current question
    llm_messages: list[dict] = []
    for m in prior[-8:]:  # keep last 4 turns (8 messages) to stay within token budget
        role = "user" if get_role(m) in ("user", "human") else "assistant"
        llm_messages.append({"role": role, "content": get_content(m)})
    llm_messages.append({"role": "user", "content": user_question})

    try:
        raw = await chat(
            messages=llm_messages,
            system=_SYSTEM,
            max_tokens=512,
        )
        if raw.startswith("```"):
            raw = raw.split("\n", 1)[1].rsplit("```", 1)[0]
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
    except (json.JSONDecodeError, KeyError, EmptyCompletionError) as exc:
        # EmptyCompletionError belongs here for the same reason a bad JSON body does:
        # planning is an optimisation, and the default plan answers the question
        # anyway. Losing the whole request because the planner came back empty
        # would be a strictly worse outcome than retrieving with defaults.
        bound.warning("parse_failed", error=str(exc))
        plan = ["retrieve", "analyze", "summarize"]
        action_type = None
        action_cron = None
        action_question = None

    bound.info("complete", duration_ms=round((time.monotonic() - t0) * 1000), steps=len(plan))
    return {
        "plan": plan,
        "action_required": bool(action_type),
        "action_type": action_type,
        "action_cron": action_cron,
        "action_question": action_question,
        "next_node": "retriever",
    }
